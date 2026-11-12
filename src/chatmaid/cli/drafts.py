"""Draft and cloud commands: cloud-check, drafts, draft, cloud-calls."""

import argparse
import sys

from pathlib import Path

from chatmaid.cli import common
from chatmaid.cloud.base import CLOUD_SCHEMA, CloudError
from chatmaid.config import ConfigError
from chatmaid.config import load as load_config
from chatmaid.llm.base import LLMError
from chatmaid.model import SourceRef
from chatmaid.reply.flow import ReplyFlow
from chatmaid.store import Store


_CLOUD_CHECK_PROMPT = (
    'Reply with text "ok" and a one-word rationale. '
    "Keep placeholders like [PERSON_1] verbatim: [PERSON_1] says hi."
)


def _cloud_check_command(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.config))
    try:
        backend = common.make_backend(cfg, args.backend)
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
    try:
        store, connector, flow = common.open_flow(cfg, need_llm=False)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        return 1
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
            print(f"{d['id']}  {d['conversation_id']}  {d['status']}  {common.day(d['created_at'])}  {msg['subject']}  {text}")
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
        store, connector, flow = common.open_flow(
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
    store = common.open_store(cfg)
    try:
        print("id  draft  backend  model  status  created  chars")
        for c in store.cloud_calls(limit=args.limit):
            print(
                f"{c['id']}  {c['draft_id']}  {c['backend']}  {c['model']}  {c['status']}  "
                f"{common.day(c['created_at'])}  {len(c['payload'])}"
            )
    finally:
        store.close()
    return 0


def register_cloud_calls(subparsers) -> None:
    p = subparsers.add_parser("cloud-calls", help="list cloud proposals and calls")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(handler=_cloud_calls_command)
