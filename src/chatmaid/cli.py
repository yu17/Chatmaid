"""Command-line entry point. Later tasks add commands via register_<name>(subparsers)."""

import argparse
import sys

from datetime import datetime, timezone
from pathlib import Path

from chatmaid import __version__
from chatmaid.config import ConfigError, read_token
from chatmaid.config import load as load_config
from chatmaid.llm.base import LLMError
from chatmaid.llm.lmstudio import LMStudioClient
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import load_accounts
from chatmaid.identity import OwnerIdentity
from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM
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


def _init_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    connector = EvolutionConnector(cfg.email, accounts, owner)
    if args.fake_llm:
        llm = FakeLLM()
    else:
        try:
            llm = LMStudioClient(cfg.lmstudio.base_url, read_token(cfg), timeout_s=cfg.lmstudio.timeout_s)
        except ConfigError as e:
            print(str(e), file=sys.stderr)
            return 1
    passes = ("ingest", "digest", "style", "embed") if args.pass_name == "all" else (args.pass_name,)
    opts = InitOptions(since=args.since, limit_conversations=args.limit, passes=passes)
    store = _open_store(cfg)
    try:
        if args.reset_checkpoints:
            print(f"cleared {store.clear_checkpoints('ingest')} ingest checkpoints")
        for name, stats in InitPipeline(store, connector, llm, cfg).run(opts).items():
            print(f"{name}: {stats}")
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
