"""Command-line entry point. Command groups live in the sibling modules."""

import argparse

from chatmaid import __version__
from chatmaid.cli.drafts import register_cloud_calls, register_cloud_check, register_draft, register_drafts
from chatmaid.cli.index import (
    register_accounts,
    register_init,
    register_llm_check,
    register_scan,
    register_status,
)
from chatmaid.cli.read import (
    register_conversations,
    register_lookup,
    register_related,
    register_senders,
    register_show,
)
from chatmaid.cli.watch import register_watch


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
    register_senders(subparsers)
    register_show(subparsers)
    register_lookup(subparsers)
    register_related(subparsers)
    register_watch(subparsers)
    register_cloud_check(subparsers)
    register_drafts(subparsers)
    register_draft(subparsers)
    register_cloud_calls(subparsers)
    return parser, subparsers


def main(argv: list[str] | None = None) -> int:
    parser, _subparsers = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(f"chatmaid {__version__}")
        return 0
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)
