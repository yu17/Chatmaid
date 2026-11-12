"""The watch command."""

import argparse
import asyncio
import logging
import signal
import sys

from pathlib import Path

from chatmaid.cli import common
from chatmaid.config import ConfigError
from chatmaid.config import load as load_config
from chatmaid.connectors.evolution.events import EventSource
from chatmaid.daemon import Watcher
from chatmaid.lock import Busy, exclusive


async def _watch(watcher: Watcher) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await watcher.run(stop)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)

def _watch_command(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config(Path(args.config))
    cfg.data.dir.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive(cfg.data.dir / "chatmaid.lock"):
            return _watch_locked(args, cfg)
    except Busy as e:
        print(str(e), file=sys.stderr)
        return 1


def _watch_locked(args: argparse.Namespace, cfg) -> int:
    try:
        store, connector, flow = common.open_flow(cfg, need_llm=not args.no_draft)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    try:
        accounts = connector.accounts
        db_paths = [cfg.email.evolution_cache_dir / a.uid / "folders.db" for a in accounts]
        events = EventSource(db_paths, interval_s=args.interval, enable_dbus=not args.no_dbus)
        watcher = Watcher(store, connector, events)

        def show(kind: str, cid: int, msg) -> None:
            row = store.message_by_ref(msg.ref.key())
            mid = row["id"] if row is not None else "-"
            print(f"{kind}  conv {cid}  msg {mid}  {msg.sender.email}  {msg.subject}", flush=True)
            if not args.no_draft:
                flow.dispatch(kind, cid, msg)

        watcher.on_routed = show
        if not args.no_draft:
            flow.retry_untriaged()
            watcher.after_scan = flow.retry_untriaged
        dbus = "off" if args.no_dbus else "on"
        print(f"watching {len(accounts)} accounts (interval {args.interval}s, dbus {dbus})", flush=True)
        asyncio.run(_watch(watcher))
    finally:
        store.close()
        connector.close()
    return 0


def register_watch(subparsers) -> None:
    p = subparsers.add_parser("watch", help="route newly arrived mail until interrupted")
    p.add_argument("--interval", type=int, default=60, help="seconds between timer scans")
    p.add_argument("--no-dbus", action="store_true", help="do not subscribe to Evolution's D-Bus signal")
    p.add_argument("--no-draft", action="store_true", help="route only; do not triage or draft replies")
    p.set_defaults(handler=_watch_command)
