"""Reply flow: triage incoming mail, draft replies, resolve drafts from the owner's own mail."""

import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from chatmaid.cloud.base import CLOUD_SCHEMA, CloudBackend, CloudError
from chatmaid.cloud.pseudonym import Pseudonymizer, deny_list_for, tag_persons
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


@dataclass(frozen=True)
class CloudProposal:
    call_id: int
    backend: str
    model: str
    payload: str


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

    # -- owner-consented cloud improvement -------------------------------

    def propose_cloud(
        self,
        draft_id: int,
        *,
        backend: str | None = None,
        comment: str | None = None,
        from_version: int | None = None,
    ) -> CloudProposal:
        """Store the exact pseudonymized payload for the owner to inspect; sends nothing."""
        draft = self._pending(draft_id)
        b = self.backend_factory(backend)
        previous = (
            self._version(draft_id, from_version)["text"]
            if from_version is not None
            else self.latest_text(draft_id)
        )
        ctx = self._context_for(draft)
        deny = deny_list_for(self.store, self.cfg, draft["conversation_id"])
        try:
            tag_text = "\n".join([previous, comment or "", ctx.summary, ctx.message[:1500]])
            names = tag_persons(self.llm, self.cfg.lmstudio.model_small, tag_text)
        except LLMError as e:
            log.warning("draft %s: person tagging failed (%s); using the deny list only", draft_id, e)
            names = []
        deny += [(n, "PERSON") for n in names]
        p = Pseudonymizer(deny, redact_amounts=self.cfg.cloud.redact_amounts)
        payload = self._cloud_payload(p, previous, comment, ctx)
        store = self.store
        with store.transaction():
            call_id = store.add_cloud_call(
                draft_id=draft_id, backend=b.name, model=b.model, payload=payload,
                mapping=p.mapping, comment=comment,
            )
            store.log_event(
                "cloud", "proposed", str(call_id),
                {"backend": b.name, "draft_id": draft_id, "chars": len(payload)},
            )
        return CloudProposal(call_id, b.name, b.model, payload)

    @staticmethod
    def _cloud_payload(
        p: Pseudonymizer, previous: str, comment: str | None, ctx: ReplyContext
    ) -> str:
        """Assemble the payload; the message is redacted whole, then cut, so no fragment leaks."""
        excerpt = p.redact(ctx.message)[:1500]
        if excerpt.rfind("[") > excerpt.rfind("]"):
            excerpt = excerpt[: excerpt.rfind("[")]  # the cut landed inside a placeholder
        return (
            prompts.load("cloud_refine").system
            + "\n\n## Previous draft\n" + p.redact(previous)
            + "\n\n## Owner's feedback\n" + (p.redact(comment) if comment else "(none)")
            + "\n\n## Conversation summary\n" + (p.redact(ctx.summary) or "(none)")
            + "\n\n## Message being answered\n" + excerpt
        )

    def _proposed_call(self, call_id: int) -> sqlite3.Row:
        call = self.store.cloud_call(call_id)
        if call is None:
            raise ValueError(f"no cloud call {call_id}")
        if call["status"] != "proposed":
            raise ValueError(f"cloud call {call_id} is {call['status']}, not proposed")
        return call

    def send_cloud(self, call_id: int) -> int:
        """Send the stored payload, byte for byte, and store the re-hydrated result as a version."""
        call = self._proposed_call(call_id)
        store = self.store
        draft = store.draft(call["draft_id"])
        if draft is None or draft["status"] != "pending":
            store.set_cloud_call_status(call_id, "declined", error="draft no longer pending")
            store.log_event(
                "cloud", "declined", str(call_id), {"reason": "draft no longer pending"}
            )
            raise ValueError("draft no longer pending")
        b = self.backend_factory(call["backend"])
        try:
            out = b.complete(call["payload"], CLOUD_SCHEMA)
            if not (
                isinstance(out, dict)
                and isinstance(out.get("text"), str)
                and isinstance(out.get("rationale"), str)
            ):
                raise CloudError("cloud backend returned no text")
        except CloudError as e:
            store.set_cloud_call_status(call_id, "failed", error=str(e))
            store.log_event(
                "cloud", "failed", str(call_id), {"backend": call["backend"], "error": str(e)}
            )
            raise
        mapping = json.loads(call["mapping_json"] or "{}")
        response = json.dumps(out, ensure_ascii=False)
        text, unknown = Pseudonymizer.rehydrate(out["text"], mapping)
        rationale, _ = Pseudonymizer.rehydrate(out["rationale"], mapping)
        if unknown:
            rationale += f" [unknown placeholders: {', '.join(unknown)}]"
        draft_id = call["draft_id"]
        try:
            with store.transaction():
                store.set_cloud_call_status(
                    call_id, "sent", consented_at=self.now(), response=response,
                    response_hash=hashlib.sha256(response.encode()).hexdigest(),
                )
                vid = store.add_draft_version(
                    draft_id, round=store.next_round(draft_id), candidate=1,
                    origin=f"cloud:{call['backend']}", text=text, rationale=rationale,
                    comment=call["comment"], cloud_call_id=call_id,
                )
                store.log_event(
                    "cloud", "sent", str(call_id), {"version_id": vid, "unknown_placeholders": unknown}
                )
        except Exception as e:
            # the payload already left the machine: keep the audit trail, forbid a re-send
            store.set_cloud_call_status(call_id, "failed", error=f"stored after send failed: {e!r}")
            store.log_event(
                "cloud", "failed", str(call_id),
                {"backend": call["backend"], "error": f"stored after send failed: {e!r}"},
            )
            raise
        return vid

    def decline_cloud(self, call_id: int) -> None:
        self._proposed_call(call_id)
        with self.store.transaction():
            self.store.set_cloud_call_status(call_id, "declined")
            self.store.log_event("cloud", "declined", str(call_id), {})
