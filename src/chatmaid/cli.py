"""Command-line entry point. Later tasks add commands via register_<name>(subparsers)."""

import argparse
import asyncio
import json
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
from chatmaid.init.progress import ProgressLogHandler
from chatmaid.llm.fake import FakeLLM
from chatmaid.lock import Busy, exclusive
from chatmaid.init.categories import CATEGORIES
from chatmaid.lookup import search_history, search_threads
from chatmaid.llm.fake import CANNED
from chatmaid.model import SourceRef
from chatmaid.reply.flow import ReplyFlow
from chatmaid.stop import StopRequested, stop_on_signal
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


def _lmstudio_client(cfg, token: str) -> LMStudioClient:
    lm = cfg.lmstudio
    return LMStudioClient(lm.base_url, token, timeout_s=lm.timeout_s, think=lm.think)


def _llm_check_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    try:
        token = read_token(cfg)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    client = _lmstudio_client(cfg, token)
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
        llm = _NoLLM()  # the client (and its token) is only built for passes that call a model
    else:
        try:
            llm = _lmstudio_client(cfg, read_token(cfg))
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
    store = _open_store(cfg)
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
    p.add_argument("--since", type=_parse_since, default=None, metavar="YYYY-MM-DD")
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
    store = _open_store(cfg)
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


def _day(ts: int | None) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%Y-%m-%d")


def _conversations_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = _open_store(cfg)
    try:
        print("id  last  count  category  title  summary")
        for c in store.conversations(
            limit=args.limit, exclude_bulk=not args.all, category=args.category
        ):
            summary = (c["summary"] or "-").replace("\n", " ")[:60]
            print(f"{c['id']}  {_day(c['last_message_at'])}  {c['message_count']}  {c['category'] or '-'}  {c['title']}  {summary}")
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
    store = _open_store(cfg)
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
    store = _open_store(cfg)
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


def _lookup_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    categories = tuple(args.category or ())
    llm = None
    if not args.no_vector:
        try:
            llm = _lmstudio_client(cfg, read_token(cfg))
        except ConfigError as e:
            print(f"warning: {e}; falling back to metadata search only", file=sys.stderr)
    if args.threads and llm is None:
        print("thread search needs the embedding model", file=sys.stderr)
        return 2
    connector = None
    store = None
    try:
        accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
        owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
        connector = EvolutionConnector(cfg.email, accounts, owner)
        store = _open_store(cfg)
        if args.threads:
            thread_hits = search_threads(
                store, llm, query=args.query, categories=categories, limit=args.limit,
                embedding_model=cfg.lmstudio.model_embedding,
            )
        else:
            hits = search_history(
                store, connector, llm, query=args.query, contact=args.contact,
                since=args.since, until=args.until, limit=args.limit,
                embedding_model=cfg.lmstudio.model_embedding, categories=categories,
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
    if args.threads:
        for t in thread_hits:
            summary = t.summary.replace("\n", " ")
            print(f"{t.conversation_id}  {_day(t.last_message_at)}  {t.category or '-'}  {t.title}  \u2014  {summary}")
        return 0
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
    p.add_argument("--category", action="append", choices=CATEGORIES, default=None)
    p.add_argument("--threads", action="store_true", help="search conversation summaries")
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


def _open_flow(
    cfg, *, need_llm: bool, fake_llm: bool = False
) -> tuple[Store, EvolutionConnector, ReplyFlow]:
    """Build store, connector and ReplyFlow; ConfigError (missing token) propagates before anything opens."""
    if fake_llm:
        llm = FakeLLM(dict(CANNED))
    elif not need_llm:
        llm = _NoLLM()
    else:
        llm = _lmstudio_client(cfg, read_token(cfg))
    accounts = load_accounts(cfg.email.evolution_config_dir, cfg.email.accounts)
    owner = OwnerIdentity.from_accounts(accounts, cfg.owner.extra_addresses, cfg.owner.aliases)
    connector = EvolutionConnector(cfg.email, accounts, owner)
    try:
        store = _open_store(cfg)
    except BaseException:
        connector.close()
        raise
    flow = ReplyFlow(store, connector, llm, cfg, backend_factory=lambda name: make_backend(cfg, name))
    return store, connector, flow


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
        store, connector, flow = _open_flow(cfg, need_llm=not args.no_draft)
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


def _drafts_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store, connector, flow = _open_flow(cfg, need_llm=False)
    try:
        print("id  conv  status  created  subject  text")
        status = None if args.status == "all" else args.status
        for d in store.drafts(status=status, limit=args.limit):
            msg = store.message(d["message_id"])
            try:
                latest = flow.latest_text(d["id"])  # what `draft approve` approves
            except ValueError:  # no versions
                latest = ""
            text = latest.replace("\n", " ")[:60]
            print(f"{d['id']}  {d['conversation_id']}  {d['status']}  {_day(d['created_at'])}  {msg['subject']}  {text}")
    finally:
        store.close()
        connector.close()
    return 0


def register_drafts(subparsers) -> None:
    p = subparsers.add_parser("drafts", help="list reply drafts")
    p.add_argument("--status", choices=["pending", "all"], default="pending")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(handler=_drafts_command)


def _draft_show(store: Store, draft_id: int) -> int:
    d = store.draft(draft_id)
    if d is None:
        print(f"no draft {draft_id}", file=sys.stderr)
        return 1
    print(f"status: {d['status']}")
    print(f"language: {d['language'] or '-'}")
    print(f"conversation: {d['conversation_id']}")
    t = store.triage_for(d["message_id"])
    print(f"triage: {t['category']} ({t['reason']})" if t else "triage: -")
    for v in store.draft_versions(draft_id):
        comment = f"  comment: {v['comment']}" if v["comment"] else ""
        print(f"v{v['id']}  round {v['round']}.{v['candidate']}  {v['origin']}{comment}")
        for line in v["text"].splitlines() or [""]:
            print(f"    {line}")
    for c in store.cloud_calls(limit=10**9):
        if c["draft_id"] == draft_id:
            print(f"call {c['id']}  {c['backend']}/{c['model']}  {c['status']}")
    return 0


def _draft_run(flow: ReplyFlow, store: Store, connector, args: argparse.Namespace) -> int:
    row = store.message(args.message)
    if row is None:
        print(f"no message with id {args.message}", file=sys.stderr)
        return 1
    if row["is_owner"]:
        print(f"message {args.message} is the owner's", file=sys.stderr)
        return 1
    try:
        msg = connector.fetch(SourceRef.from_key(row["ref_key"]))
    except KeyError:
        print(f"message {args.message} is no longer in the mail cache", file=sys.stderr)
        return 1
    draft_id = flow.on_incoming(row["conversation_id"], msg)
    if draft_id is not None:
        print(f"draft {draft_id}")
    else:
        t = store.triage_for(row["id"])
        print(f"no draft: {t['category']} ({t['reason']})" if t else "no draft")
    return 0


def _print_version(store: Store, version_id: int, draft_id: int) -> None:
    print(f"v{version_id}")
    for v in store.draft_versions(draft_id):
        if v["id"] == version_id:
            print(v["text"])


def _send_call(flow: ReplyFlow, store: Store, call_id: int) -> None:
    call = store.cloud_call(call_id)
    draft_id = call["draft_id"] if call else None
    version_id = flow.send_cloud(call_id)
    _print_version(store, version_id, draft_id)


def _draft_cloud(flow: ReplyFlow, store: Store, args: argparse.Namespace) -> int:
    p = flow.propose_cloud(args.id, backend=args.backend, comment=args.comment, from_version=args.from_version)
    print(f"----- payload to {p.backend} ({p.model}), {len(p.payload)} chars -----")
    print(p.payload)
    print(f"----- end of payload; proposal {p.call_id} -----")
    if not sys.stdin.isatty():
        print(f"run: chatmaid draft send {p.call_id}  (or: chatmaid draft decline {p.call_id})")
        return 0
    try:
        answer = input(f"Send this to {p.backend}? [y/N] ")
    except (EOFError, KeyboardInterrupt):  # no answer is no consent
        print()
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        _send_call(flow, store, p.call_id)
    else:
        flow.decline_cloud(p.call_id)
        print("declined")
    return 0


def _draft_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    cmd = args.draft_command
    try:
        store, connector, flow = _open_flow(
            cfg,
            need_llm=cmd in ("run", "regenerate", "cloud"),
            fake_llm=getattr(args, "fake_llm", False),
        )
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
    try:
        if cmd == "show":
            return _draft_show(store, args.id)
        if cmd == "run":
            return _draft_run(flow, store, connector, args)
        if cmd == "cloud":
            return _draft_cloud(flow, store, args)
        if cmd == "regenerate":
            vid = flow.regenerate(args.id, args.comment, from_version=args.from_version)
            _print_version(store, vid, args.id)
        elif cmd == "approve":
            flow.resolve(args.id, "approve", version_id=args.version)
        elif cmd == "edit":
            text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
            flow.resolve(args.id, "edit", text=text)
        elif cmd == "dismiss":
            flow.resolve(args.id, "dismiss")
        elif cmd == "send":
            _send_call(flow, store, args.id)
        elif cmd == "decline":
            flow.decline_cloud(args.id)
            print("declined")
    except (ValueError, CloudError, LLMError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        store.close()
        connector.close()
    return 0


def register_draft(subparsers) -> None:
    p = subparsers.add_parser("draft", help="work on one reply draft")
    sub = p.add_subparsers(dest="draft_command", required=True)

    def add(name: str, help_: str, *, id_name: str | None = "id", fake: bool = False):
        sp = sub.add_parser(name, help=help_)
        if id_name:
            sp.add_argument(id_name, type=int)
        if fake:
            sp.add_argument("--fake-llm", action="store_true", help="use canned model answers (testing)")
        sp.set_defaults(handler=_draft_command)
        return sp

    add("show", "show a draft with its versions and cloud calls")
    r = add("run", "triage and draft one stored message", id_name=None, fake=True)
    r.add_argument("--message", type=int, required=True, help="message row id")
    g = add("regenerate", "write a new round of candidates from a comment", fake=True)
    g.add_argument("--comment", required=True)
    g.add_argument("--from", dest="from_version", type=int, default=None)
    a = add("approve", "approve a version")
    a.add_argument("--version", type=int, default=None)
    e = add("edit", "replace the draft with your own text")
    grp = e.add_mutually_exclusive_group(required=True)
    grp.add_argument("--text")
    grp.add_argument("--file")
    add("dismiss", "dismiss a draft")
    c = add("cloud", "propose a cloud improvement and show the exact payload", fake=True)
    c.add_argument("--backend", choices=["codex", "claude"], default=None)
    c.add_argument("--comment", default=None)
    c.add_argument("--from", dest="from_version", type=int, default=None)
    add("send", "send a proposed payload to the cloud backend", id_name="id")
    add("decline", "decline a proposed cloud call", id_name="id")


def _cloud_calls_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    store = _open_store(cfg)
    try:
        print("id  draft  backend  model  status  created  chars")
        for c in store.cloud_calls(limit=args.limit):
            print(
                f"{c['id']}  {c['draft_id']}  {c['backend']}  {c['model']}  {c['status']}  "
                f"{_day(c['created_at'])}  {len(c['payload'])}"
            )
    finally:
        store.close()
    return 0


def register_cloud_calls(subparsers) -> None:
    p = subparsers.add_parser("cloud-calls", help="list cloud proposals and calls")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(handler=_cloud_calls_command)


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
    handler =getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)
