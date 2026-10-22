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
    def _state(self) -> dict[str, int]:
        saved = self.store.get_checkpoint("watch", self.connector.name)
        if saved is not None:
            return {k: int(v) for k, v in saved.items()}
        # First run. Folders ingest never checkpointed hold a backlog that is not "new mail":
        # baseline them at their current watermark, and let ingest checkpoints win elsewhere.
        _, baseline = self.connector.scan_new({})
        seeded = {k: int(v["last_uid"]) for k, v in self.store.checkpoints("ingest").items()}
        return {**baseline, **seeded}

    def _already_stored(self, msg: NormalizedMessage) -> bool:
        return bool(msg.message_id) and self.store.has_message_id(
            "email", msg.ref.account, msg.message_id
        )

    def handle_trigger(self, kind: str) -> int:
        """Scan for new messages and route them; returns how many were routed."""
        try:
            refs, new_state = self.connector.scan_new(self._state())
        except sqlite3.OperationalError as e:
            log.warning("scan skipped (%s trigger): %s", kind, e)
            return 0
        routed = 0
        for ref in refs:
            if self.store.has_ref(ref.key()):
                continue
            try:
                msg = self.connector.fetch(ref)
            except KeyError:
                log.warning("message %s vanished before it could be read", ref.key())
                continue
            except sqlite3.OperationalError as e:
                # Keep the old state: unread refs are rescanned, stored ones skipped by has_ref.
                log.warning("scan interrupted at %s: %s", ref.key(), e)
                return routed
            if self._already_stored(msg):
                continue
            route = self.route(msg)
            cid = self.on_owner_message(msg) if route == "owner" else self.on_incoming(msg)
            routed += 1
            if self.on_routed is not None:
                self.on_routed(route, cid, msg)
        self.store.set_checkpoint("watch", self.connector.name, new_state)
        return routed

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
