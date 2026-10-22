"""Wake-up triggers for the watcher: D-Bus new-mail signal, cache file changes, and a timer."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import watchfiles

log = logging.getLogger(__name__)

DBUS_INTERFACE = "org.gnome.evolution.mail.dbus.Signal"
DBUS_PATH = "/org/gnome/evolution/mail/newmail"
DBUS_MEMBER = "Newmail"
DBUS_RULE = f"type='signal',interface='{DBUS_INTERFACE}',member='{DBUS_MEMBER}',path='{DBUS_PATH}'"
FILE_DEBOUNCE_S = 2.0


class EventSource:
    def __init__(self, db_paths: list[Path], *, interval_s: int = 60, enable_dbus: bool = True):
        self.db_paths = [Path(p) for p in db_paths]
        self.interval_s = interval_s
        self.enable_dbus = enable_dbus

    async def triggers(self) -> AsyncIterator[str]:
        """Yield "dbus", "file" or "timer"; cancelling the consumer stops every producer."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._timer(queue)),
            asyncio.create_task(self._files(queue)),
        ]
        if self.enable_dbus:
            tasks.append(asyncio.create_task(self._dbus(queue)))
        try:
            while True:
                yield await queue.get()
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _timer(self, queue: asyncio.Queue[str]) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            queue.put_nowait("timer")

    async def _files(self, queue: asyncio.Queue[str]) -> None:
        names = {p.name for p in self.db_paths}
        parents = sorted({str(p.parent) for p in self.db_paths})
        pending: asyncio.Task | None = None

        async def emit_later() -> None:  # trailing edge: one trigger after a burst of writes
            await asyncio.sleep(FILE_DEBOUNCE_S)
            queue.put_nowait("file")

        try:
            async for _changes in watchfiles.awatch(
                *parents,
                recursive=False,  # the accounts' own directories only, not every cur/<hh>/ bucket
                watch_filter=lambda _change, path: Path(path).name in names,
            ):
                if pending is None or pending.done():
                    pending = asyncio.create_task(emit_later())
        except OSError as e:
            log.warning("file watching disabled: %s", e)
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    async def _dbus(self, queue: asyncio.Queue[str]) -> None:
        bus = None
        try:
            from dbus_fast import Message, MessageType
            from dbus_fast.aio import MessageBus

            bus = await MessageBus().connect()

            def on_message(msg) -> None:
                if (
                    msg.message_type == MessageType.SIGNAL
                    and msg.interface == DBUS_INTERFACE
                    and msg.member == DBUS_MEMBER
                ):
                    queue.put_nowait("dbus")

            bus.add_message_handler(on_message)
            reply = await bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[DBUS_RULE],
                )
            )
            if reply is not None and reply.message_type == MessageType.ERROR:
                raise RuntimeError(f"AddMatch failed: {reply.body}")
            await bus.wait_for_disconnect()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # no session bus, no permission, ...: timer and file still work
            log.warning("D-Bus new-mail signal unavailable (%s); using timer and file triggers", e)
        finally:
            if bus is not None:
                with contextlib.suppress(Exception):
                    bus.disconnect()
