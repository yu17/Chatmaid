"""Watcher: routes newly arrived mail into the store and the event log."""

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable

from chatmaid.connectors.base import Connector
from chatmaid.connectors.evolution.events import EventSource
from chatmaid.init.pipeline import build_thread_index, store_message
from chatmaid.model import NormalizedMessage
from chatmaid.store import Store
from chatmaid.threads import ThreadIndex

log = logging.getLogger(__name__)

PENDING = "_pending"
MAX_BODY_WAITS = 3  # triggers a body-less message is held back before it is routed as is


class Watcher:
    def __init__(self, store: Store, connector: Connector, events: EventSource):
        self.store = store
        self.connector = connector
        self.events = events
        self.on_routed: Callable[[str, int, NormalizedMessage], None] | None = None
        self._indexes: dict[str, ThreadIndex] = {}

    # -- routing ---------------------------------------------------------
    def route(self, msg: NormalizedMessage) -> str:
        return "owner" if msg.is_owner else "incoming"

    def on_owner_message(self, msg: NormalizedMessage) -> int:
        return self._record("owner", msg)

    def on_incoming(self, msg: NormalizedMessage) -> int:
        return self._record("incoming", msg)

    def _record(self, kind: str, msg: NormalizedMessage) -> int:
        account = msg.ref.account
        if account not in self._indexes:
            self._indexes[account] = build_thread_index(self.store, "email", account)
        cid = store_message(self.store, self._indexes[account], self.connector, msg)
        self.store.log_event(
            "evolution", kind, msg.ref.key(),
            {"bulk": msg.bulk, "conversation_id": cid, "subject": msg.subject},
        )
        return cid

    # -- scanning --------------------------------------------------------
    def _load_state(self) -> tuple[dict[str, int], dict[str, int]]:
        """(folder watermarks, pending ref attempts); seeded from ingest when no watch state exists."""
        saved = self.store.get_checkpoint("watch", self.connector.name)
        if saved is None:
            ingest = self.store.checkpoints("ingest")
            return {k: int(v["last_uid"]) for k, v in ingest.items()}, {}
        pending = {k: int(v) for k, v in saved.get(PENDING, {}).items()}
        return {k: int(v) for k, v in saved.items() if k != PENDING}, pending

    def _already_stored(self, msg: NormalizedMessage) -> bool:
        return bool(msg.message_id) and self.store.has_message_id(
            "email", msg.ref.account, msg.message_id
        )

    def handle_trigger(self, kind: str) -> int:
        """Scan for new messages and route them; returns how many were routed.

        A locked or failing database aborts the trigger with the saved state untouched.
        """
        try:
            return self._handle(kind)
        except sqlite3.OperationalError as e:
            log.warning("trigger %r skipped: %s", kind, e)
            return 0

    def _handle(self, kind: str) -> int:
        state, pending = self._load_state()
        refs, new_state = self.connector.scan_new(state, baseline_missing=True)
        still_pending: dict[str, int] = {}
        held_back: set[str] = set()  # folder keys whose watermark must not pass a deferred ref
        routed = 0
        for ref in refs:
            if self.store.has_ref(ref.key()):
                continue
            try:
                msg = self.connector.fetch(ref)
            except KeyError:
                log.warning("message %s vanished before it could be read", ref.key())
                continue
            except sqlite3.OperationalError:
                raise
            except Exception as e:
                self._poison(ref, e)
                continue
            attempts = pending.get(ref.key(), 0)
            if msg.body_missing and attempts < MAX_BODY_WAITS:
                still_pending[ref.key()] = attempts + 1  # body file is written after the row
                held_back.add(f"{ref.account}/{ref.folder}")
                continue
            try:
                if self._already_stored(msg):
                    continue
                route = self.route(msg)
                with self.store.transaction():
                    cid = self.on_owner_message(msg) if route == "owner" else self.on_incoming(msg)
            except sqlite3.OperationalError:
                raise
            except Exception as e:
                self._poison(ref, e)
                continue
            routed += 1
            if self.on_routed is not None:
                self.on_routed(route, cid, msg)
        for key in held_back:
            new_state[key] = state[key]
        saved: dict = dict(new_state)
        if still_pending:
            saved[PENDING] = still_pending
        self.store.set_checkpoint("watch", self.connector.name, saved)
        return routed

    def _poison(self, ref, error: Exception) -> None:
        log.error("failed to handle %s: %r", ref.key(), error)
        self.store.log_event("evolution", "error", ref.key(), {"error": repr(error)})

    # -- loop ------------------------------------------------------------
    async def run(self, stop: asyncio.Event) -> None:
        triggers = self.events.triggers()
        stop_task = asyncio.create_task(stop.wait())
        try:
            while not stop.is_set():
                nxt = asyncio.create_task(anext(triggers))
                await asyncio.wait({nxt, stop_task}, return_when=asyncio.FIRST_COMPLETED)
                if not nxt.done():
                    nxt.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                        await nxt
                    break
                await asyncio.to_thread(self.handle_trigger, nxt.result())
        finally:
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            await triggers.aclose()
