"""Command-line entry point. Later tasks add commands via register_<name>(subparsers)."""

import argparse
import asyncio
import logging
import signal
import sqlite3
import sys

from datetime import datetime, timezone
from pathlib import Path

from chatmaid import __version__
from chatmaid.cloud.base import CLOUD_SCHEMA, CloudError
from chatmaid.cloud.registry import make_backend
from chatmaid.config import ConfigError, read_token
from chatmaid.config import load as load_config
from chatmaid.llm.base import LLMError
from chatmaid.llm.lmstudio import LMStudioClient
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.events import EventSource
from chatmaid.connectors.evolution.sources import load_accounts
from chatmaid.daemon import Watcher
from chatmaid.identity import OwnerIdentity
from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM
from chatmaid.lock import Busy, exclusive
from chatmaid.lookup import search_history
from chatmaid.store import Store


def _accounts_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    for a in load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts):
        print(
            f"{a.uid[:8]}  {a.backend}  {a.display_name}  "
            f"sent={a.sent_folder or '-'}  identities={len(a.identity_addresses)}"
        )
    return 0


def register_accounts(subparsers) -> None:
    p = subparsers.add_parser("accounts", help="list configured Evolution mail accounts")
    p.set_defaults(handler=_accounts_command)


def _scan_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    connector = EvolutionConnector(cfg.email, accounts, owner)
    try:
        for uid, folder, rows, bodies, owned, bulk in connector.folder_stats(args.account):
            print(f"{uid[:8]}  {folder}  rows={rows} bodies={bodies} owner={owned} bulk={bulk}")
    finally:
        connector.close()
    return 0


def register_scan(subparsers) -> None:
    p = subparsers.add_parser("scan", help="count messages per Evolution account and folder")
    p.add_argument("--account", default=None, help="only this account uid")
    p.add_argument("--dry-run", action="store_true", help="do not touch the index")
    p.set_defaults(handler=_scan_command)


def _llm_check_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    try:
        token = read_token(cfg)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    client = LMStudioClient(cfg.lmstudio.base_url, token, timeout_s=cfg.lmstudio.timeout_s)
    try:
        for model_id in client.list_models():
            print(model_id)
        reply = client.complete(
            system="Reply with the single word OK.",
            user="ping",
            model=cfg.lmstudio.model_small,
            max_tokens=8,
            grammar=True,  # grammar mode: what digest uses on model_small
        )
    except LLMError as e:
        print(f"llm-check failed: {e}", file=sys.stderr)
        return 1
    print(f"reply: {reply}")
    return 0


def register_llm_check(subparsers) -> None:
    p = subparsers.add_parser("llm-check", help="list LM Studio models and run one completion")
    p.set_defaults(handler=_llm_check_command)


def _parse_since(text: str) -> int:
    day = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(day.timestamp())


def _open_store(cfg) -> Store:
    cfg.data.dir.mkdir(parents=True, exist_ok=True)
    return Store(cfg.index_path, embedding_dim=cfg.lmstudio.embedding_dim)


class _NoLLM:
    """Stands in when no requested pass talks to a model (ingest only): no token is needed."""

    def complete(self, **_) -> str:
        raise LLMError("this run was started without an LM Studio client")

    def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        raise LLMError("this run was started without an LM Studio client")


def _init_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    cfg.data.dir.mkdir(parents=True, exist_ok=True)
    try:
        with exclusive(cfg.data.dir / "chatmaid.lock"):
            return _init_locked(args, cfg)
    except Busy as e:
        print(str(e), file=sys.stderr)
        return 1


def _init_locked(args: argparse.Namespace, cfg) -> int:
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    passes = ("ingest", "digest", "style", "embed") if args.pass_name == "all" else (args.pass_name,)
    if args.fake_llm:
        llm = FakeLLM()
    elif all(name == "ingest" for name in passes):
        llm = _NoLLM()  # the client (and its token) is only built for passes that call a model
    else:
        try:
            llm = LMStudioClient(cfg.lmstudio.base_url, read_token(cfg), timeout_s=cfg.lmstudio.timeout_s)
        except ConfigError as e:
            print(str(e), file=sys.stderr)
            return 1
    connector = EvolutionConnector(cfg.email, accounts, owner)
    opts = InitOptions(since=args.since, limit_conversations=args.limit, passes=passes)
    store = _open_store(cfg)
    try:
        if args.reset_checkpoints:
            print(f"cleared {store.clear_checkpoints('ingest')} ingest checkpoints")
        for name, stats in InitPipeline(store, connector, llm, cfg).run(opts).items():
            print(f"{name}: {stats}")
    except LLMError as e:
        print(f"init failed: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
        connector.close()
    return 0


def register_init(subparsers) -> None:
    p = subparsers.add_parser("init", help="build the index from the connector (resumable)")
    p.add_argument("--since", type=_parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=None, help="max conversations to process")
    p.add_argument(
        "--pass", dest="pass_name", choices=["ingest", "digest", "style", "embed", "all"], default="all"
    )
    p.add_argument("--reset-checkpoints", action="store_true", help="forget ingest checkpoints first")
    p.add_argument("--fake-llm", action="store_true", help="use the canned FakeLLM")
    p.set_defaults(handler=_init_command)


def _status_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = _open_store(cfg)
    try:
        for name, n in store.counts().items():
            print(f"{name}={n}")
        with_summary = len(store.conversations(needing_summary=False, exclude_bulk=False))
        without = len(store.conversations(needing_summary=True, exclude_bulk=False))
        print(f"conversations_with_summary={with_summary}")
        print(f"conversations_without_summary={without}")
    finally:
        store.close()
    return 0


def register_status(subparsers) -> None:
    p = subparsers.add_parser("status", help="print index counts")
    p.set_defaults(handler=_status_command)


def _day(ts: int | None) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%Y-%m-%d")


def _conversations_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = _open_store(cfg)
    try:
        print("id  last  count  title  summary")
        for c in store.conversations(limit=args.limit, exclude_bulk=not args.all):
            summary = (c["summary"] or "-").replace("\n", " ")[:60]
            print(f"{c['id']}  {_day(c['last_message_at'])}  {c['message_count']}  {c['title']}  {summary}")
    finally:
        store.close()
    return 0


def register_conversations(subparsers) -> None:
    p = subparsers.add_parser("conversations", help="list indexed conversations")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--all", action="store_true", help="include bulk conversations")
    p.set_defaults(handler=_conversations_command)


def _show_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = _open_store(cfg)
    try:
        conv = store.conversation(args.id)
        if conv is None:
            print(f"no conversation with id {args.id}", file=sys.stderr)
            return 1
        print(f"Title: {conv['title']}")
        print(f"Summary: {conv['summary'] or '-'}")
        print(f"Participants: {', '.join(store.participant_labels(args.id))}")
        print()
        recent = store.messages_for(args.id, limit=10**9)[-args.limit :]
        for m in recent:
            when = datetime.fromtimestamp(m["sent_at"] or 0, timezone.utc).strftime("%Y-%m-%d %H:%M")
            print(f"{when}  {store.identity_label(m['sender_identity_id'])}  {m['preview']}")
    finally:
        store.close()
    return 0


def register_show(subparsers) -> None:
    p = subparsers.add_parser("show", help="show one conversation")
    p.add_argument("id", type=int)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(handler=_show_command)


def _lookup_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    llm = None
    if not args.no_vector:
        try:
            llm = LMStudioClient(cfg.lmstudio.base_url, read_token(cfg), timeout_s=cfg.lmstudio.timeout_s)
        except ConfigError as e:
            print(f"warning: {e}; falling back to metadata search only", file=sys.stderr)
    connector = None
    store = None
    try:
        accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
        owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
        connector = EvolutionConnector(cfg.email, accounts, owner)
        store = _open_store(cfg)
        hits = search_history(
            store, connector, llm, query=args.query, contact=args.contact,
            since=args.since, until=args.until, limit=args.limit,
            embedding_model=cfg.lmstudio.model_embedding,
        )
    except LLMError as e:
        print(f"lookup failed: {e}", file=sys.stderr)
        return 1
    except (sqlite3.Error, OSError) as e:
        print(f"lookup failed: {e}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
        if connector is not None:
            connector.close()
    for h in hits:
        snippet = h.snippet.replace("\n", " ")
        print(f"{_day(h.sent_at)}  {h.sender}  {h.subject}  \u2014  {snippet}")
    return 0


def register_lookup(subparsers) -> None:
    p = subparsers.add_parser("lookup", help="search message history")
    p.add_argument("query")
    p.add_argument("--contact", default=None)
    p.add_argument("--since", type=_parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--until", type=_parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--no-vector", action="store_true", help="metadata search only")
    p.set_defaults(handler=_lookup_command)


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
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    connector = EvolutionConnector(cfg.email, accounts, owner)
    store = _open_store(cfg)
    try:
        db_paths = [cfg.email.evolution_cache_dir / a.uid / "folders.db" for a in accounts]
        events = EventSource(db_paths, interval_s=args.interval, enable_dbus=not args.no_dbus)
        watcher = Watcher(store, connector, events)

        def show(kind: str, cid: int, msg) -> None:
            print(f"{kind}  {cid}  {msg.sender.email}  {msg.subject}", flush=True)

        watcher.on_routed = show
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
    p.set_defaults(handler=_watch_command)


_CLOUD_CHECK_PROMPT = (
    'Reply with text "ok" and a one-word rationale. '
    "Keep placeholders like [PERSON_1] verbatim: [PERSON_1] says hi."
)


def _cloud_check_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    try:
        backend = make_backend(cfg, args.backend)
        result = backend.complete(_CLOUD_CHECK_PROMPT, CLOUD_SCHEMA)
    except CloudError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"backend={backend.name} model={backend.model}")
    print(f"text: {result.get('text')}")
    print(f"rationale: {result.get('rationale')}")
    return 0


def register_cloud_check(subparsers) -> None:
    p = subparsers.add_parser("cloud-check", help="send a harmless canned prompt through a cloud CLI")
    p.add_argument("--backend", choices=["codex", "claude"], default=None)
    p.set_defaults(handler=_cloud_check_command)


def build_parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    parser = argparse.ArgumentParser(prog="chatmaid")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--config", default="data/config.toml", help="path to config.toml")
    subparsers = parser.add_subparsers(dest="command")
    register_accounts(subparsers)
    register_scan(subparsers)
    register_llm_check(subparsers)
    register_init(subparsers)
    register_status(subparsers)
    register_conversations(subparsers)
    register_show(subparsers)
    register_lookup(subparsers)
    register_watch(subparsers)
    register_cloud_check(subparsers)
    return parser, subparsers


def main(argv: list[str] | None = None) -> int:
    parser, _subparsers = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"chatmaid {__version__}")
        return 0
    handler =getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)
