"""Init pipeline: ingest (no model calls), digest; style and embed live in passes.py."""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.init import passes
from chatmaid.init.chunking import chunk_messages
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM, LLMError
from chatmaid.model import NormalizedMessage
from chatmaid.store import Store
from chatmaid.threads import ThreadIndex, normalize_subject, thread_fallback_key

log = logging.getLogger(__name__)

LOCAL_ACCOUNT = "local"  # Maildir uids are not integers: never checkpointed
DIGEST_MAX_TOKENS = 2048
_RETRY_NOTE = "Your reply was not valid JSON matching the schema; reply with the JSON object only."


@dataclass(frozen=True)
class InitOptions:
    since: int | None = None
    limit_conversations: int | None = None
    skip_bulk: bool = True
    chunk_chars: int = 9000
    passes: tuple[str, ...] = ("ingest", "digest", "style", "embed")


@dataclass(frozen=True)
class IngestStats:
    messages: int
    new_messages: int
    conversations: int
    identities: int
    skipped_dup: int


def _refs(msg: NormalizedMessage) -> tuple[str, ...]:
    return msg.references + ((msg.in_reply_to,) if msg.in_reply_to else ())


def build_thread_index(store: Store, platform: str, account: str) -> ThreadIndex:
    index = ThreadIndex()
    for message_id, references in store.message_ids_for_account(platform, account):
        index.add(message_id, references)
    return index


def conversation_key(index: ThreadIndex, msg: NormalizedMessage) -> str:
    participants = [msg.sender.email, *(a.email for a in msg.recipients)]
    fallback = thread_fallback_key(msg.subject, participants)
    return f"email:{msg.ref.account}:{index.key_for(msg.message_id, _refs(msg), fallback)}"


@dataclass(frozen=True)
class DigestStats:
    conversations: int
    chunks: int
    memories: int
    failed: int


def _render(msgs: list[NormalizedMessage]) -> str:
    lines = []
    for m in msgs:
        when = datetime.fromtimestamp(m.sent_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"[{when} {m.sender.name or m.sender.email}] {m.text}")
    return "\n".join(lines)


def _validated(value, schema: dict):
    """Check `value` against the small JSON-schema subset our prompts use.

    Raises ValueError on a wrong type or missing required key; malformed array items are dropped.
    """
    kind = schema.get("type")
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("expected string")
        return value
    if kind == "array":
        if not isinstance(value, list):
            raise ValueError("expected array")
        items = []
        for item in value:
            try:
                items.append(_validated(item, schema["items"]))
            except ValueError:
                continue
        return items
    if kind == "object":
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return {
            key: _validated(value[key], sub) if key in value else _missing(key)
            for key, sub in schema["properties"].items()
        }
    return value


def schema_skeleton(schema: dict) -> str:
    """Render the JSON shape of a schema (the subset our prompts use) as an example."""
    kind = schema.get("type")
    if kind == "object":
        inner = ", ".join(f'"{k}": {schema_skeleton(v)}' for k, v in schema["properties"].items())
        return "{" + inner + "}"
    if kind == "array":
        return "[" + schema_skeleton(schema["items"]) + "]"
    return '"<string>"'


def _missing(key: str):
    raise ValueError(f"missing required key {key!r}")


def _event_time(date: str) -> int | None:
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    return int(day.replace(tzinfo=timezone.utc).timestamp())


def _usable(state: dict, since: int | None) -> bool:
    """A checkpoint written under a later `since` would hide older rows from this run."""
    stored = state.get("since")
    return stored is None or (since is not None and since >= stored)


def _folder_key(msg: NormalizedMessage) -> str:
    return f"{msg.ref.account}/{msg.ref.folder}"


class InitPipeline:
    def __init__(self, store: Store, connector: Connector, llm: LLM, cfg: Config):
        self.store = store
        self.connector = connector
        self.llm = llm
        self.cfg = cfg

    def run(self, opts: InitOptions) -> dict[str, object]:
        results: dict[str, object] = {}
        for name in opts.passes:
            if name == "ingest":
                results[name] = self.ingest(opts)
            elif name == "digest":
                results[name] = self.digest(opts)
            elif name == "style":
                results[name] = self.style(opts)
            elif name == "embed":
                results[name] = self.embed(opts)
            else:
                raise ValueError(f"unknown pass {name!r}")
        return results

    # -- passes 3 and 4 (see passes.py) ----------------------------------
    def style(self, opts: InitOptions, *, min_samples: int = passes.MIN_STYLE_SAMPLES) -> dict[str, int]:
        return passes.run_style(self, opts, min_samples=min_samples)

    def embed(self, opts: InitOptions) -> int:
        return passes.run_embed(self, opts)

    # -- pass 1: ingest -------------------------------------------------
    def ingest(self, opts: InitOptions) -> IngestStats:
        store = self.store
        before = store.counts()["messages"]
        after = {
            k: int(v["last_uid"])
            for k, v in store.checkpoints("ingest").items()
            if _usable(v, opts.since)
        }
        indexes: dict[str, ThreadIndex] = {}
        touched: set[int] = set()
        top: dict[str, int] = {}  # folder key -> max integer uid seen this run
        failed: set[str] = set()
        seen = skipped = 0
        current: str | None = None

        for msg in self.connector.iter_messages(since=opts.since, after=after):
            folder = _folder_key(msg)
            if current is not None and folder != current:
                self._save_checkpoint(current, top, failed, opts.since)
            current = folder
            seen += 1
            account = msg.ref.account
            if msg.message_id and store.has_message_id("email", account, msg.message_id):
                skipped += 1
            else:
                if account not in indexes:
                    indexes[account] = build_thread_index(store, "email", account)
                try:
                    touched.add(self._store_message(indexes[account], msg))
                except (sqlite3.OperationalError, LLMError) as e:
                    log.warning("ingest failed for %s: %s", msg.ref.key(), e)
                    failed.add(folder)
                    continue
            mark = self._watermark(msg)
            if mark is not None:
                top[folder] = max(top.get(folder, 0), mark)
        if current is not None:
            self._save_checkpoint(current, top, failed, opts.since)

        counts = store.counts()
        return IngestStats(
            messages=seen,
            new_messages=counts["messages"] - before,
            conversations=len(touched),
            identities=counts["identities"],
            skipped_dup=skipped,
        )

    def _watermark(self, msg: NormalizedMessage) -> int | None:
        if msg.ref.account == LOCAL_ACCOUNT:
            return None
        fn = getattr(self.connector, "watermark_for", None)
        return fn(msg.ref) if fn is not None else None

    def _save_checkpoint(
        self, folder: str, top: dict[str, int], failed: set[str], since: int | None
    ) -> None:
        if folder in top and folder not in failed:
            # "last_uid" holds the folder watermark: max int uid, or max dreceived for opaque uids.
            state = {"last_uid": top[folder], "since": since}
            self.store.set_checkpoint("ingest", folder, state)

    def _store_message(self, index: ThreadIndex, msg: NormalizedMessage) -> int:
        store = self.store
        owner = getattr(self.connector, "owner", None)

        def identity(addr, fallback: bool) -> int:
            is_owner = owner.matches(addr) if owner is not None else fallback
            return store.upsert_identity("email", addr.email, addr.name or None, is_owner=is_owner)

        index.add(msg.message_id, _refs(msg))
        sender_id = identity(msg.sender, msg.is_owner)
        contact_ids = {store.contact_id_of(sender_id)}
        contact_ids.update(store.contact_id_of(identity(a, False)) for a in msg.recipients)
        title = normalize_subject(msg.subject) or msg.subject or "(no subject)"
        cid = store.upsert_conversation("email", conversation_key(index, msg), title, "thread")
        for contact_id in contact_ids:
            store.add_participant(cid, contact_id)
        store.upsert_message(cid, msg, sender_id)
        return cid

    # -- pass 2: digest -------------------------------------------------
    def digest(self, opts: InitOptions) -> DigestStats:
        rows = self.store.conversations(
            needing_summary=True, exclude_bulk=opts.skip_bulk, limit=opts.limit_conversations
        )
        done = chunks = memories = failed = 0
        for row in rows:
            try:
                n_chunks, n_memories = self._digest_conversation(row["id"], opts)
            except LLMError as e:
                log.warning("digest failed for conversation %s: %s", row["id"], e)
                failed += 1
                continue
            done += 1
            chunks += n_chunks
            memories += n_memories
        return DigestStats(done, chunks, memories, failed)

    def _complete_json(
        self,
        prompt: prompts.Prompt,
        user: str,
        model: str,
        *,
        think: bool,
        max_tokens: int = DIGEST_MAX_TOKENS,
    ) -> dict:
        """One reasoning-free call whose reply is validated against the prompt schema.

        Invalid JSON or a missing required key gets one retry that shows the model its reply.
        """
        if not think:  # no grammar: the model must be told the keys
            user = f"{user}\n\nReply with exactly this JSON shape: {schema_skeleton(prompt.schema)}"
        message = user
        for attempt in range(2):
            raw = self.llm.complete(
                system=prompt.system,
                user=message,
                model=model,
                json_schema=prompt.schema,
                max_tokens=max_tokens,
                think=think,
            )
            try:
                return _validated(json.loads(raw), prompt.schema)
            except ValueError:  # JSONDecodeError is a ValueError
                message = f"{user}\n\nYour previous reply:\n{raw}\n\n{_RETRY_NOTE}"
        raise LLMError(f"{prompt.name}: model did not return valid JSON for the schema")

    def _digest_conversation(self, cid: int, opts: InitOptions) -> tuple[int, int]:
        store = self.store
        msgs = []
        for ref, _ in store.message_refs_for(cid):
            try:
                msgs.append(self.connector.fetch(ref))
            except KeyError:  # moved or deleted in the mail client since ingest
                log.warning("digest: message %s is no longer available", ref.key())
        chunks = chunk_messages(msgs, chunk_chars=opts.chunk_chars)
        if not chunks:
            store.set_tracked(cid, False)
            raise LLMError(f"conversation {cid} has no fetchable messages; untracked")
        chunk_prompt = prompts.load("chunk_summary")
        rolling_prompt = prompts.load("rolling_summary")
        owner = store.owner_contact_id()
        others = [c for c in store.participant_contact_ids(cid) if c != owner]
        contact_id = others[0] if len(others) == 1 else None

        state = store.get_checkpoint("digest", str(cid)) or {}
        start = state["chunk"] + 1 if "chunk" in state else 0
        summary: str = state.get("summary", "")
        events: list[dict] = list(state.get("events", []))
        memories = 0
        for i in range(start, len(chunks)):
            chunk = chunks[i]
            # model_small is non-thinking: it takes the strict schema, and the empty-think
            # prefill (think=False) makes it degenerate, so chunk calls use think=True.
            out = self._complete_json(
                chunk_prompt, _render(chunk), self.cfg.lmstudio.model_small, think=True
            )
            new_summary = out["summary"]
            if i > 0:
                merged = self._complete_json(
                    rolling_prompt,
                    f"Previous summary:\n{summary}\n\nNew chunk summary:\n{new_summary}",
                    self.cfg.lmstudio.model_main,
                    think=False,
                )
                new_summary = merged["summary"]
            summary = new_summary
            events.extend(out["events"])
            memories += self._add_memories(out, chunk[0].ref.key(), cid, contact_id)
            store.set_checkpoint(
                "digest", str(cid), {"chunk": i, "summary": summary, "events": events}
            )
        store.set_conversation_summary(cid, summary, events=events)
        store.set_checkpoint("digest", str(cid), {"done": True})
        return len(chunks) - start, memories

    def _add_memories(self, out: dict, source_ref: str, cid: int, contact_id: int | None) -> int:
        rows = [("fact", f"{f['about']}: {f['text']}", None) for f in out["facts"]]
        rows += [("event", e["text"], _event_time(e["date"])) for e in out["events"]]
        for kind, text, observed_at in rows:
            self.store.add_memory(
                kind=kind, text=text, source_ref=source_ref, observed_at=observed_at,
                contact_id=contact_id, conversation_id=cid,
            )
        return len(rows)
