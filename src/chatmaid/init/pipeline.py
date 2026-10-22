"""Init pipeline. Pass 1 (ingest) reads the connector into the store; no model calls."""

import logging
import sqlite3
from dataclasses import dataclass

from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.llm.base import LLM, LLMError
from chatmaid.model import NormalizedMessage
from chatmaid.store import Store
from chatmaid.threads import ThreadIndex, normalize_subject, thread_fallback_key

log = logging.getLogger(__name__)

LOCAL_ACCOUNT = "local"  # Maildir uids are not integers: never checkpointed


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
            if name != "ingest":
                raise NotImplementedError(f"pass {name!r} is not implemented yet")
            results[name] = self.ingest(opts)
        return results

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
