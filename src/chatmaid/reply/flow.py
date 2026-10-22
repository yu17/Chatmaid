"""Reply flow: triage incoming mail, draft replies, resolve drafts from the owner's own mail."""

import json
import logging
import sqlite3
import time
from collections.abc import Callable

from chatmaid.cloud.base import CloudBackend
from chatmaid.cloud.registry import make_backend
from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.init.pipeline import add_memories, render_messages
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM, LLMError
from chatmaid.llm.jsoncall import complete_json
from chatmaid.model import NormalizedMessage, SourceRef, make_preview
from chatmaid.reply.context import ReplyContext, build_context
from chatmaid.reply.drafting import draft_reply, revise_reply
from chatmaid.store import Store
from chatmaid.triage.classify import triage_message

log = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return " ".join(text.split())


class ReplyFlow:
    def __init__(
        self,
        store: Store,
        connector: Connector,
        llm: LLM,
        cfg: Config,
        *,
        now: Callable[[], int] | None = None,
        backend_factory: Callable[[str | None], CloudBackend] | None = None,
    ) -> None:
        self.store = store
        self.connector = connector
        self.llm = llm
        self.cfg = cfg
        self.now = now or (lambda: int(time.time()))
        self.backend_factory = backend_factory or (lambda name: make_backend(cfg, name))

    # -- watcher callback ------------------------------------------------

    def dispatch(self, route: str, conversation_id: int, msg: NormalizedMessage) -> None:
        owner = route == "owner"
        try:
            if owner:
                self.on_owner_message(conversation_id, msg)
            else:
                self.on_incoming(conversation_id, msg)
        except LLMError as e:
            kind = "owner_failed" if owner else "draft_failed"
            log.warning("reply flow %s for %s: %s", kind, msg.ref.key(), e)
            self.store.log_event("reply", kind, msg.ref.key(), {"error": str(e)})

    # -- routine B: incoming mail ----------------------------------------

    def on_incoming(self, conversation_id: int, msg: NormalizedMessage) -> int | None:
        store, cfg = self.store, self.cfg
        ref = msg.ref.key()
        row = store.message_by_ref(ref)
        if row is None:
            raise ValueError(f"message {ref} is not stored")
        if msg.is_owner:
            return None
        t = triage_message(
            self.llm, cfg.lmstudio.model_small, msg, store.conversation(conversation_id),
            message_chars=cfg.draft.message_chars,
        )
        store.add_triage(row["id"], t.category, t.reason, decided_by=t.decided_by, model=t.model)
        if t.tier:
            store.set_conversation_tier(conversation_id, t.tier)
        store.log_event(
            "reply", "triage", ref,
            {"category": t.category, "reason": t.reason, "decided_by": t.decided_by},
        )
        if t.category != "needs_reply":
            return None

        pending = store.pending_draft_for(conversation_id)
        if pending is not None:
            store.set_draft_status(pending["id"], "superseded")
        ctx = build_context(
            store, self.connector, self.llm, cfg, conversation_id, msg, now=self.now()
        )
        cands = draft_reply(
            store, self.connector, self.llm, cfg, ctx, sender_email=msg.sender.email
        )
        with store.transaction():
            draft_id = store.create_draft(
                conversation_id, row["id"], language=ctx.language, context=ctx.to_json()
            )
            for i, c in enumerate(cands, start=1):
                store.add_draft_version(
                    draft_id, round=1, candidate=i, origin="local", text=c.text,
                    rationale=c.rationale,
                )
            store.log_event(
                "reply", "draft", ref, {"draft_id": draft_id, "candidates": len(cands)}
            )
        return draft_id

    # -- routine C: the owner's own mail ---------------------------------

    def on_owner_message(self, conversation_id: int, msg: NormalizedMessage) -> None:
        store = self.store
        pending = store.pending_draft_for(conversation_id)
        if pending is not None:
            sent = _norm(msg.text)
            match = next(
                (v["text"] for v in store.draft_versions(pending["id"]) if _norm(v["text"]) == sent),
                None,
            )
            status = "approved" if match is not None else "edited"
            with store.transaction():
                store.set_draft_status(
                    pending["id"], status, final_text=match, outcome_ref=msg.ref.key(),
                    outcome_preview=make_preview(msg.text),
                )
                store.log_event(
                    "reply", "draft_resolved", msg.ref.key(),
                    {"draft_id": pending["id"], "status": status},
                )
        self.update_summary(conversation_id, msg)

    def update_summary(self, conversation_id: int, msg: NormalizedMessage) -> bool:
        """Fold one owner message into the rolling summary; False when there is nothing to update."""
        store, cfg = self.store, self.cfg
        conv = store.conversation(conversation_id)
        if conv is None or conv["summary"] is None or not msg.text.strip():
            return False  # the digest pass will cover it
        out = complete_json(
            self.llm, prompts.load("chunk_summary"), render_messages([msg]),
            cfg.lmstudio.model_small, grammar=True,
        )
        merged = complete_json(
            self.llm,
            prompts.load("rolling_summary"),
            f"Previous summary:\n{conv['summary']}\n\nNew chunk summary:\n{out['summary']}",
            cfg.lmstudio.model_main,
            grammar=False,
        )
        events = json.loads(conv["events_json"]) if conv["events_json"] else []
        owner = store.owner_contact_id()
        others = [c for c in store.participant_contact_ids(conversation_id) if c != owner]
        contact_id = others[0] if len(others) == 1 else None
        with store.transaction():
            store.set_conversation_summary(
                conversation_id, merged["summary"], events=events + out["events"]
            )
            add_memories(store, out, msg.ref.key(), conversation_id, contact_id)
        return True

    # -- owner actions on a draft ----------------------------------------

    def _pending(self, draft_id: int) -> sqlite3.Row:
        draft = self.store.draft(draft_id)
        if draft is None:
            raise ValueError(f"no draft {draft_id}")
        if draft["status"] != "pending":
            raise ValueError(f"draft {draft_id} is {draft['status']}, not pending")
        return draft

    def _latest_first(self, draft_id: int) -> sqlite3.Row:
        versions = self.store.draft_versions(draft_id)
        if not versions:
            raise ValueError(f"draft {draft_id} has no versions")
        last = max(v["round"] for v in versions)
        return next(v for v in versions if v["round"] == last and v["candidate"] == 1)

    def _version(self, draft_id: int, version_id: int) -> sqlite3.Row:
        for v in self.store.draft_versions(draft_id):
            if v["id"] == version_id:
                return v
        raise ValueError(f"draft {draft_id} has no version {version_id}")

    def _context_for(self, draft: sqlite3.Row) -> ReplyContext:
        """Rebuild the live context from the source mail; the stored record has previews only."""
        try:
            row = self.store.message(draft["message_id"])
            msg = self.connector.fetch(SourceRef.from_key(row["ref_key"]))
        except (KeyError, OSError, sqlite3.Error, TypeError) as e:
            log.warning("draft %s: source message unavailable (%s); using stored context", draft["id"], e)
            return ReplyContext.from_json(json.loads(draft["context_json"]))
        return build_context(
            self.store, self.connector, self.llm, self.cfg, draft["conversation_id"], msg,
            now=self.now(),
        )

    def latest_text(self, draft_id: int) -> str:
        return self._latest_first(draft_id)["text"]

    def regenerate(self, draft_id: int, comment: str, *, from_version: int | None = None) -> int:
        draft = self._pending(draft_id)
        previous = (
            self._version(draft_id, from_version)["text"]
            if from_version is not None
            else self.latest_text(draft_id)
        )
        ctx = self._context_for(draft)
        cand = revise_reply(self.llm, self.cfg, ctx, previous, comment)
        store = self.store
        with store.transaction():
            vid = store.add_draft_version(
                draft_id, round=store.next_round(draft_id), candidate=1, origin="local",
                text=cand.text, rationale=cand.rationale, comment=comment,
            )
            store.log_event(
                "reply", "draft_regenerated", str(draft_id),
                {"draft_id": draft_id, "version_id": vid},
            )
        return vid

    def resolve(
        self, draft_id: int, action: str, *, text: str | None = None, version_id: int | None = None
    ) -> None:
        self._pending(draft_id)
        store = self.store
        if action == "approve":
            final = text
            if final is None:
                v = (
                    self._version(draft_id, version_id)
                    if version_id is not None
                    else self._latest_first(draft_id)
                )
                final = v["text"]
            store.set_draft_status(draft_id, "approved", final_text=final)
        elif action == "edit":
            if not text:
                raise ValueError("edit needs the new text")
            with store.transaction():
                store.add_draft_version(
                    draft_id, round=store.next_round(draft_id), candidate=1, origin="owner",
                    text=text, rationale=None,
                )
                store.set_draft_status(draft_id, "edited", final_text=text)
        elif action == "dismiss":
            store.set_draft_status(draft_id, "dismissed")
        else:
            raise ValueError(f"unknown action {action!r}")
