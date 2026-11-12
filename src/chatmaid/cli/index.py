"""Index commands: accounts, scan, llm-check, init, status."""

import argparse
import logging
import sys

from pathlib import Path

from chatmaid.cli import common
from chatmaid.config import ConfigError
from chatmaid.config import load as load_config
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import load_accounts
from chatmaid.identity import OwnerIdentity
from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.init.progress import ProgressLogHandler
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import CANNED, FakeLLM
from chatmaid.lock import Busy, exclusive
from chatmaid.stop import StopRequested, stop_on_signal


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
        token = common.read_token(cfg)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    client = common.lmstudio_client(cfg, token)
    try:
        for model_id in client.list_models():
            print(model_id)
        reply = client.complete(
            system="Reply with the single word OK.", user="ping", model=cfg.lmstudio.model
        )
    except LLMError as e:
        print(f"llm-check failed: {e}", file=sys.stderr)
        return 1
    print(f"reply: {reply}")
    return 0


def register_llm_check(subparsers) -> None:
    p = subparsers.add_parser("llm-check", help="list LM Studio models and run one completion")
    p.set_defaults(handler=_llm_check_command)

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
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[ProgressLogHandler(sys.stderr)],
    )
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    passes = InitOptions().passes if args.pass_name == "all" else (args.pass_name,)
    if args.fake_llm:
        llm = FakeLLM(dict(CANNED))
    elif all(name == "ingest" for name in passes):
        llm = common.NoLLM()  # the client (and its token) is only built for passes that call a model
    else:
        try:
            llm = common.lmstudio_client(cfg, common.read_token(cfg))
        except ConfigError as e:
            print(str(e), file=sys.stderr)
            return 1
    connector = EvolutionConnector(cfg.email, accounts, owner)
    opts = InitOptions(
        since=args.since,
        limit_conversations=args.limit,
        skip_bulk=args.skip_bulk,
        queue_group=args.queue_group,
        passes=passes,
    )
    store = common.open_store(cfg)
    pipeline = InitPipeline(store, connector, llm, cfg)
    try:
        if args.reset_checkpoints:
            print(f"cleared {store.clear_checkpoints('ingest')} ingest checkpoints")
        with stop_on_signal():
            for name, stats in pipeline.run(opts).items():
                print(f"{name}: {stats}")
    except StopRequested:  # outside a pass's own catch (e.g. mid-ingest); the open batch rolled back
        pipeline.stopped = True
    except LLMError as e:
        print(f"init failed: {e}", file=sys.stderr)
        return 1
    finally:
        store.close()
        connector.close()
    if pipeline.stopped:
        print("stopped; run the same command to resume", file=sys.stderr)
        return 130
    return 0


def register_init(subparsers) -> None:
    p = subparsers.add_parser("init", help="build the index from the connector (resumable)")
    p.add_argument("--since", type=common.parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=None, help="max conversations to process")
    p.add_argument(
        "--pass", dest="pass_name", choices=["ingest", "digest", "style", "embed", "all"], default="all"
    )
    p.add_argument("--reset-checkpoints", action="store_true", help="forget ingest checkpoints first")
    p.add_argument("--skip-bulk", action="store_true", help="digest: leave bulk conversations out")
    p.add_argument(
        "--queue-group", type=int, choices=range(1, 6), default=None, metavar="1..5",
        help="digest: only this priority group of the queue",
    )
    p.add_argument("--fake-llm", action="store_true", help="use the canned FakeLLM")
    p.set_defaults(handler=_init_command)


def _status_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = common.open_store(cfg)
    try:
        for name, n in store.counts().items():
            print(f"{name}={n}")
        with_summary = len(store.conversations(needing_summary=False, exclude_bulk=False))
        without = len(store.conversations(needing_summary=True, exclude_bulk=False))
        print(f"conversations_with_summary={with_summary}")
        print(f"conversations_without_summary={without}")
        for name, n in store.category_counts().items():
            print(f"category_{name}={n}")
    finally:
        store.close()
    return 0


def register_status(subparsers) -> None:
    p = subparsers.add_parser("status", help="print index counts")
    p.set_defaults(handler=_status_command)
