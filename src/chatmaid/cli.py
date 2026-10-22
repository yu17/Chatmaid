"""Command-line entry point. Later tasks add commands via register_<name>(subparsers)."""

import argparse

from pathlib import Path

from chatmaid import __version__
from chatmaid.config import load as load_config
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import load_accounts
from chatmaid.identity import OwnerIdentity


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


def build_parser() -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    parser = argparse.ArgumentParser(prog="chatmaid")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument("--config", default="data/config.toml", help="path to config.toml")
    subparsers = parser.add_subparsers(dest="command")
    register_accounts(subparsers)
    register_scan(subparsers)
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
