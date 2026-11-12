"""Routine C: resolve a pending draft from the owner's own mail and fold it into the summary."""

import json

from chatmaid.init.categories import allows_events, allows_facts, normalize_category
from chatmaid.init.pipeline import add_memories, render_messages
from chatmaid.llm import prompts
from chatmaid.llm.jsoncall import complete_json
from chatmaid.model import NormalizedMessage, make_preview
from chatmaid.quoting import strip_quoted, strip_reply_chrome


APPROVE_SLACK_CHARS = 200  # a sign-off or a line added to an approved draft
APPROVE_SLACK_RATIO = 0.2


def _norm(text: str) -> str:
    return " ".join(text.split())


class OwnerMixin:
    def on_owner_message(self, conversation_id: int, msg: NormalizedMessage) -> None:
        store = self.store
        pending = store.pending_draft_for(conversation_id)
        if pending is not None:
            sent = _norm(strip_reply_chrome(msg.text))
            matches = []
            for v in store.draft_versions(pending["id"]):
                text = _norm(v["text"])
                slack = max(APPROVE_SLACK_CHARS, APPROVE_SLACK_RATIO * len(text))
                if text and sent.startswith(text) and len(sent) - len(text) <= slack:
                    matches.append(v["text"])
            match = max(matches, key=lambda t: len(_norm(t))) if matches else None
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
        else:
            resolved = store.unmatched_resolved_draft(conversation_id, before=msg.sent_at)
            if resolved is not None:  # approved or edited in the UI; the mail went out later
                with store.transaction():
                    store.set_draft_outcome(
                        resolved["id"], msg.ref.key(), make_preview(msg.text)
                    )
                    store.log_event(
                        "reply", "draft_outcome", msg.ref.key(), {"draft_id": resolved["id"]}
                    )
        self.update_summary(conversation_id, msg)

    def update_summary(self, conversation_id: int, msg: NormalizedMessage) -> bool:
        """Fold one owner message into the rolling summary; False when there is nothing to update."""
        store, cfg = self.store, self.cfg
        conv = store.conversation(conversation_id)
        if conv is None or conv["summary"] is None:
            return False  # the digest pass will cover it
        fresh = strip_quoted([msg], keep_first=False)
        if not fresh:
            return False
        out = complete_json(
            self.llm, prompts.load("chunk_summary"), render_messages(fresh), cfg.lmstudio.model
        )
        reply_category = normalize_category(out["category"])
        if not allows_events(reply_category):
            out["events"] = []
        if not allows_facts(reply_category):
            out["facts"] = []
        merged = complete_json(
            self.llm,
            prompts.load("rolling_summary"),
            f"Previous summary:\n{conv['summary']}\n\nNew chunk summary:\n{out['summary']}",
            cfg.lmstudio.model,
        )
        events = json.loads(conv["events_json"]) if conv["events_json"] else []
        owner = store.owner_contact_id()
        others = [c for c in store.participant_contact_ids(conversation_id) if c != owner]
        contact_id = others[0] if len(others) == 1 else None
        with store.transaction():
            store.set_conversation_summary(
                conversation_id, merged["summary"], events=events + out["events"]
            )
            store.set_conversation_category(conversation_id, "conversation")
            add_memories(store, out, msg.ref.key(), conversation_id, contact_id)
        return True
