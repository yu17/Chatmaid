"""Read-only commands: conversations, senders, show, lookup, related."""

import argparse
import json
import sqlite3
import sys

from datetime import datetime, timezone
from pathlib import Path

from chatmaid.cli import common
from chatmaid.config import ConfigError
from chatmaid.config import load as load_config
from chatmaid.init.categories import CATEGORIES
from chatmaid.llm.base import LLMError
from chatmaid.lookup import related, search


def _conversations_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = common.open_store(cfg)
    try:
        print("id  last  count  category  title  summary")
        for c in store.conversations(
            limit=args.limit, exclude_bulk=not args.all, category=args.category
        ):
            summary = (c["summary"] or "-").replace("\n", " ")[:60]
            print(f"{c['id']}  {common.day(c['last_message_at'])}  {c['message_count']}  {c['category'] or '-'}  {c['title']}  {summary}")
    finally:
        store.close()
    return 0


def register_conversations(subparsers) -> None:
    p = subparsers.add_parser("conversations", help="list indexed conversations")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--all", action="store_true", help="include bulk conversations")
    p.add_argument("--category", choices=CATEGORIES, default=None)
    p.set_defaults(handler=_conversations_command)


def _senders_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = common.open_store(cfg)
    try:
        print("address  messages  convs  known  replied  categories")
        for r in store.sender_stats(limit=args.limit, category=args.category):
            cats = " ".join(f"{k}:{v}" for k, v in json.loads(r["categories_json"] or "{}").items())
            print(f"{r['address']}  {r['messages']}  {r['conversations']}  {r['known']}  {r['answered']}  {cats or '-'}")
    finally:
        store.close()
    return 0


def register_senders(subparsers) -> None:
    p = subparsers.add_parser("senders", help="list senders by message count")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--category", choices=CATEGORIES, default=None)
    p.set_defaults(handler=_senders_command)


def _show_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = common.open_store(cfg)
    try:
        conv = store.conversation(args.id)
        if conv is None:
            print(f"no conversation with id {args.id}", file=sys.stderr)
            return 1
        print(f"Title: {conv['title']}")
        print(f"Category: {conv['category'] or '-'}")
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


def _print_threads(hits) -> None:
    for t in hits:
        print(
            f"{t.conversation_id}  {common.day(t.last_message_at)}  {t.category or '-'}  "
            f"{t.message_count} msg  {t.title}  \u2014  {t.evidence}"
        )


def _lookup_llm(cfg, no_vector: bool):
    if no_vector:
        return None
    try:
        return common.lmstudio_client(cfg, common.read_token(cfg))
    except ConfigError as e:
        print(f"warning: {e}; falling back to full-text search only", file=sys.stderr)
        return None


def _lookup_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    llm = _lookup_llm(cfg, args.no_vector)
    store = None
    try:
        store = common.open_store(cfg)
        hits = search(
            store, llm, query=args.query, embedding_model=cfg.lmstudio.model_embedding,
            contact=args.contact, since=args.since, until=args.until,
            categories=tuple(args.category or ()), limit=args.limit,
        )
    except (LLMError, sqlite3.Error, OSError) as e:
        print(f"lookup failed: {e}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    _print_threads(hits)
    return 0


def register_lookup(subparsers) -> None:
    p = subparsers.add_parser("lookup", help="search conversations by topic")
    p.add_argument("query")
    p.add_argument("--contact", default=None, help="only threads with a sender matching this")
    p.add_argument("--since", type=common.parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--until", type=common.parse_since, default=None, metavar="YYYY-MM-DD")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--no-vector", action="store_true", help="full-text search only")
    p.add_argument("--category", action="append", choices=CATEGORIES, default=None)
    p.set_defaults(handler=_lookup_command)


def _related_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    llm = _lookup_llm(cfg, args.no_vector)
    store = None
    try:
        store = common.open_store(cfg)
        hits = related(
            store, llm, args.conversation, embedding_model=cfg.lmstudio.model_embedding,
            categories=tuple(args.category or ()), limit=args.limit,
        )
    except KeyError:
        print(f"no conversation {args.conversation}", file=sys.stderr)
        return 1
    except (LLMError, sqlite3.Error, OSError) as e:
        print(f"related failed: {e}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    _print_threads(hits)
    return 0


def register_related(subparsers) -> None:
    p = subparsers.add_parser("related", help="conversations on the same topic as one conversation")
    p.add_argument("conversation", type=int, help="conversation id (from conversations/lookup)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--no-vector", action="store_true", help="subject matching only")
    p.add_argument("--category", action="append", choices=CATEGORIES, default=None)
    p.set_defaults(handler=_related_command)
