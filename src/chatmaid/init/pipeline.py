"""Init pipeline: ingest (no model calls), digest; style and embed live in passes.py."""

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TextIO

from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.init import passes
from chatmaid.init.categories import (
    allows_events,
    allows_facts,
    most_engaged,
    normalize_category,
    sender_line,
    summary_limit,
)
from chatmaid.init.chunking import chunk_messages, count_words
from chatmaid.init.progress import Progress
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM, LLMError
from chatmaid.llm.jsoncall import complete_json
from chatmaid.model import NormalizedMessage
from chatmaid.quoting import strip_quoted
from chatmaid.stop import StopRequested
from chatmaid.store import Store
from chatmaid.threads import ThreadIndex, strip_subject_prefixes, thread_fallback_key

log = logging.getLogger(__name__)

LOCAL_ACCOUNT = "local"  # Maildir uids are not integers: never checkpointed
INGEST_BATCH = 200  # messages per store transaction


@dataclass(frozen=True)
class InitOptions:
    since: int | None = None
    limit_conversations: int | None = None
    skip_bulk: bool = False
    queue_group: int | None = None  # digest only this priority group of `Store.digest_queue`
    chunk_chars: int = 9000
    passes: tuple[str, ...] = ("ingest", "style", "digest", "embed")


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


def store_message(store: Store, index: ThreadIndex, connector: Connector, msg: NormalizedMessage) -> int:
    """Upsert identities, the conversation, participants and the message; return the conversation id.

    A message that links threads already stored as separate conversations folds the newer
    ones into the oldest (ruling R17). Callers run this inside `store.transaction()` and must
    rebuild `index` from the store when it raises: the index is updated before the writes.
    """
    owner = getattr(connector, "owner", None)

    def identity(addr, fallback: bool) -> int:
        is_owner = owner.matches(addr) if owner is not None else fallback
        return store.upsert_identity("email", addr.email, addr.name or None, is_owner=is_owner)

    ids = ([msg.message_id] if msg.message_id else []) + list(_refs(msg))
    linked_before = index.keys_of(ids)
    index.add(msg.message_id, _refs(msg))
    sender_id = identity(msg.sender, msg.is_owner)
    contact_ids = {store.contact_id_of(sender_id)}
    contact_ids.update(store.contact_id_of(identity(a, False)) for a in msg.recipients)
    key = conversation_key(index, msg)
    title = strip_subject_prefixes(msg.subject) or msg.subject or "(no subject)"
    cid = store.upsert_conversation("email", key, title, "thread")
    for old in linked_before:
        old_key = f"email:{msg.ref.account}:{old}"
        drop = store.conversation_id_for_key(old_key) if old_key != key else None
        if drop is not None and drop != cid:
            moved = store.merge_conversations(cid, drop)
            log.info("merged conversation %s into %s (%s messages)", drop, cid, moved)
    for contact_id in contact_ids:
        store.add_participant(cid, contact_id)
    store.upsert_message(cid, msg, sender_id)
    return cid


def _batches(msgs: Iterable[NormalizedMessage], size: int) -> Iterator[list[NormalizedMessage]]:
    """Consecutive messages of one folder, at most `size` per batch."""
    batch: list[NormalizedMessage] = []
    for msg in msgs:
        if batch and (len(batch) >= size or _folder_key(msg) != _folder_key(batch[0])):
            yield batch
            batch = []
        batch.append(msg)
    if batch:
        yield batch


@dataclass(frozen=True)
class DigestStats:
    conversations: int
    chunks: int
    memories: int
    failed: int
    skipped: int  # no cached message text yet: left pending, nothing sent to the model
    long: int = 0  # chunk summaries over their category's word limit
    categories: dict[str, int] = field(default_factory=dict)  # "unknown" for no category
    stopped: bool = False  # a stop signal ended the pass early; the next run resumes


def _digest_summary(st: DigestStats) -> str:
    cats = " ".join(f"{k}={v}" for k, v in sorted(st.categories.items())) or "-"
    head = "digest stopped" if st.stopped else "digest done"
    return (
        f"{head}: {st.conversations} summarized, {st.failed} failed, {st.skipped} skipped, "
        f"{st.chunks} chunks, {st.memories} memories, {st.long} long; categories {cats}"
    )


class _NoCachedText(Exception):
    """Every message of the conversation exists but none has a body in the cache yet."""


class _SourceUnavailable(Exception):
    """The mail source could not be read (Evolution's folders.db locked, a file unreadable)."""

    def __init__(self, cid: int, cause: Exception) -> None:
        super().__init__(f"conversation {cid}: {cause}")
        self.cid = cid


def render_messages(msgs: list[NormalizedMessage]) -> str:
    lines = []
    for m in msgs:
        when = datetime.fromtimestamp(m.sent_at, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"[{when} {m.sender.name or m.sender.email}] {m.text}")
    return "\n".join(lines)


def _event_time(date: str) -> int | None:
    try:
        day = datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return None
    return int(day.replace(tzinfo=timezone.utc).timestamp())


def add_memories(
    store: Store, out: dict, source_ref: str, cid: int, contact_id: int | None
) -> int:
    """Store the facts and events of one chunk summary as memories; returns how many."""
    rows = [("fact", f"{f['about']}: {f['text']}", None) for f in out["facts"]]
    rows += [("event", e["text"], _event_time(e["date"])) for e in out["events"]]
    for kind, text, observed_at in rows:
        store.add_memory(
            kind=kind, text=text, source_ref=source_ref, observed_at=observed_at,
            contact_id=contact_id, conversation_id=cid,
        )
    return len(rows)


def _usable(state: dict, since: int | None) -> bool:
    """A checkpoint written under a later `since` would hide older rows from this run."""
    stored = state.get("since")
    return stored is None or (since is not None and since >= stored)


def _folder_key(msg: NormalizedMessage) -> str:
    return f"{msg.ref.account}/{msg.ref.folder}"


class InitPipeline:
    def __init__(
        self,
        store: Store,
        connector: Connector,
        llm: LLM,
        cfg: Config,
        *,
        progress_stream: TextIO | None = None,
    ):
        self.store = store
        self.connector = connector
        self.llm = llm
        self.cfg = cfg
        self.progress_stream = progress_stream  # None: stderr
        self.stopped = False  # set by a pass that caught StopRequested

    def _progress(self, name: str, total: int) -> Progress:
        return Progress(name, total, stream=self.progress_stream)

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
            if self.stopped:
                break
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
        top: dict[str, int] = {}  # folder key -> watermark over every row read this run
        failed: set[str] = set()
        seen = skipped = 0  # committed batches only

        # The date filter is applied here rather than in the connector so the watermark also
        # covers the rows `since` excludes: they must not come back as "new" to the watcher.
        messages = self.connector.iter_messages(after=after)
        for batch in _batches(messages, INGEST_BATCH):
            folder = _folder_key(batch[0])
            account = batch[0].ref.account
            batch_seen = batch_skipped = 0
            try:
                with store.transaction():
                    for msg in batch:
                        mark = self._watermark(msg)
                        if mark is not None:
                            top[folder] = max(top.get(folder, 0), mark)
                        if opts.since is not None and msg.sent_at < opts.since:
                            continue
                        batch_seen += 1
                        if msg.message_id and store.has_message_id("email", account, msg.message_id):
                            batch_skipped += 1
                            continue
                        if account not in indexes:
                            indexes[account] = build_thread_index(store, "email", account)
                        touched.add(store_message(store, indexes[account], self.connector, msg))
                    self._save_checkpoint(folder, top, failed, opts.since)
                seen += batch_seen
                skipped += batch_skipped
            except (sqlite3.Error, LLMError) as e:
                log.warning("ingest of %s failed, batch rolled back: %s", folder, e)
                failed.add(folder)
                indexes.pop(account, None)  # may hold links of the rolled-back messages

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

    # -- pass 2: digest -------------------------------------------------
    def digest(self, opts: InitOptions) -> DigestStats:
        store = self.store
        store.refresh_sender_stats()  # the sender lines read these
        rows = store.digest_queue(
            exclude_bulk=opts.skip_bulk, limit=opts.limit_conversations, group=opts.queue_group
        )
        done = chunks = memories = failed = skipped = long = 0
        stopped = False
        categories: dict[str, int] = {}
        progress = self._progress("digest", len(rows))
        summary = "digest interrupted"  # replaced by the stats line on a normal exit
        try:
            for processed, row in enumerate(rows):
                progress.update(processed, f"{row['id']} {row['title']}")
                try:
                    result = self._digest_conversation(row["id"], opts)
                except StopRequested:
                    self.stopped = stopped = True
                    break
                except _NoCachedText:
                    log.info(
                        "digest: conversation %s has no cached message text yet; skipped", row["id"]
                    )
                    skipped += 1
                    continue
                except LLMError as e:
                    log.warning("digest failed for conversation %s: %s", row["id"], e)
                    failed += 1
                    continue
                except _SourceUnavailable as e:  # left pending: the next run retries it
                    log.warning(
                        "digest: source unavailable for conversation %s: %s", e.cid, e.__cause__
                    )
                    failed += 1
                    continue
                n_chunks, n_memories, category, n_long = result
                done += 1
                chunks += n_chunks
                memories += n_memories
                long += n_long
                stored = category if category is not None else row["category"]  # COALESCE on write
                key = stored or "unknown"
                categories[key] = categories.get(key, 0) + 1
            store.refresh_sender_stats()  # picks up the categories just stored
            stats = DigestStats(
                done, chunks, memories, failed, skipped, long, categories, stopped=stopped
            )
            summary = _digest_summary(stats)
        finally:
            progress.finish(summary)
        return stats

    def _complete_json(self, prompt: prompts.Prompt, user: str, model: str) -> dict:
        return complete_json(self.llm, prompt, user, model)

    def _digest_conversation(
        self, cid: int, opts: InitOptions
    ) -> tuple[int, int, str | None, int]:
        """Summarize one conversation; returns (chunks done, memories, category, long summaries)."""
        store = self.store
        fetched = []
        for ref, _ in store.message_refs_for(cid):
            try:
                fetched.append(self.connector.fetch(ref))
            except KeyError:  # moved or deleted in the mail client since ingest
                log.warning("digest: message %s is no longer available", ref.key())
            except (OSError, sqlite3.Error) as e:  # Evolution busy with its own database
                raise _SourceUnavailable(cid, e) from e
        if not fetched:
            store.set_tracked(cid, False)
            raise LLMError(f"conversation {cid} has no fetchable messages; untracked")
        # A body that is not in the mail cache arrives as empty text and would render as a bare
        # timestamp line, which the model turns into invented events; leave those messages out,
        # and leave the whole conversation pending until the owner has downloaded its mail.
        msgs = strip_quoted([m for m in fetched if m.text.strip()])
        if not msgs:
            raise _NoCachedText(cid)
        chunks = chunk_messages(msgs, chunk_chars=opts.chunk_chars)
        chunk_prompt = prompts.load("chunk_summary")
        rolling_prompt = prompts.load("rolling_summary")
        owner = store.owner_contact_id()
        others = [c for c in store.participant_contact_ids(cid) if c != owner]
        contact_id = others[0] if len(others) == 1 else None

        state = store.get_checkpoint("digest", str(cid)) or {}
        start = state["chunk"] + 1 if "chunk" in state else 0
        summary: str = state.get("summary", "")
        events: list[dict] = list(state.get("events", []))
        category: str | None = state.get("category")
        memories = long = 0
        model = self.cfg.lmstudio.model
        context = sender_line(store.sender_context(cid))
        for i in range(start, len(chunks)):
            chunk = chunks[i]
            out = self._complete_json(chunk_prompt, f"{context}\n\n{render_messages(chunk)}", model)
            chunk_category = normalize_category(out["category"])
            category = most_engaged(category, chunk_category)
            if not allows_events(chunk_category):
                out["events"] = []
            if not allows_facts(chunk_category):
                out["facts"] = []
            if count_words(out["summary"]) > summary_limit(chunk_category):
                long += 1
                log.debug("digest: conversation %s has a chunk summary over its word limit", cid)
            new_summary = out["summary"]
            if i > 0:
                merged = self._complete_json(
                    rolling_prompt,
                    f"Previous summary:\n{summary}\n\nNew chunk summary:\n{new_summary}",
                    model,
                )
                new_summary = merged["summary"]
            summary = new_summary
            events.extend(out["events"])
            with store.transaction():  # a chunk's memories and its checkpoint land together
                memories += add_memories(store, out, chunk[0].ref.key(), cid, contact_id)
                store.set_checkpoint(
                    "digest",
                    str(cid),
                    {"chunk": i, "summary": summary, "events": events, "category": category},
                )
        with store.transaction():
            store.set_conversation_summary(cid, summary, events=events, category=category)
            store.set_checkpoint("digest", str(cid), {"done": True})
        return len(chunks) - start, memories, category, long
