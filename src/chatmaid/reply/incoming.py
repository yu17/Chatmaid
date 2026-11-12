"""Routine B: triage an incoming message and draft a reply."""

import logging

from chatmaid.model import NormalizedMessage, SourceRef
from chatmaid.reply.context import build_context
from chatmaid.reply.drafting import draft_reply
from chatmaid.triage.classify import triage_message


log = logging.getLogger(__name__)


class IncomingMixin:
    def retry_untriaged(self, *, limit: int = 20) -> int:
        """Give watched messages the model failed on another go; returns how many got triaged."""
        store = self.store
        since = self.now() - self.cfg.draft.max_age_days * 86400
        done = 0
        for row in store.untriaged_incoming(since=since, limit=limit):
            ref = row["ref_key"]
            try:
                msg = self.connector.fetch(SourceRef.from_key(ref))
            except KeyError:
                store.log_event(
                    "reply", "draft_failed", ref,
                    {"error": "gone from the mail store", "message_id": row["id"]},
                )
                continue
            try:
                self.dispatch("incoming", row["conversation_id"], msg)
            except Exception as e:  # counts as an attempt, and must not block older messages
                log.exception("retry of message %s failed", row["id"])
                store.log_event(
                    "reply", "draft_failed", ref, {"error": repr(e), "message_id": row["id"]}
                )
                continue
            if store.triage_for(row["id"]) is not None:
                done += 1
        return done

    def on_incoming(self, conversation_id: int, msg: NormalizedMessage) -> int | None:
        store, cfg = self.store, self.cfg
        ref = msg.ref.key()
        row = store.message_by_ref(ref)
        if row is None:
            raise ValueError(f"message {ref} is not stored")
        if msg.is_owner:
            return None
        learned = store.learned_bulk(
            row["sender_identity_id"], min_conversations=cfg.triage.learned_bulk_min
        )
        t = triage_message(
            self.llm, cfg.lmstudio.model, msg, store.conversation(conversation_id),
            message_chars=cfg.draft.message_chars, now=self.now(),
            max_age_days=cfg.draft.max_age_days, learned_bulk=learned,
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

        ctx = build_context(
            store, self.connector, self.llm, cfg, conversation_id, msg, now=self.now()
        )
        cands = draft_reply(
            store, self.connector, self.llm, cfg, ctx, sender_email=msg.sender.email
        )
        with store.transaction():  # a failed draft leaves the previous one pending
            pending = store.pending_draft_for(conversation_id)
            if pending is not None:
                store.set_draft_status(pending["id"], "superseded")
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
