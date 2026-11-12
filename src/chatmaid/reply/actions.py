"""Owner actions on a pending draft: regenerate, approve, edit, dismiss."""

import json
import logging
import sqlite3

from chatmaid.model import SourceRef
from chatmaid.reply.context import ReplyContext, build_context
from chatmaid.reply.drafting import revise_reply

log = logging.getLogger(__name__)


class DraftActionsMixin:
    def _pending(self, draft_id: int) -> sqlite3.Row:
        draft = self.store.draft(draft_id)
        if draft is None:
            raise ValueError(f"no draft {draft_id}")
        if draft["status"] != "pending":
            raise ValueError(f"draft {draft_id} is {draft['status']}, not pending")
        return draft

    def _settle(self, draft_id: int, status: str, final_text: str | None = None) -> None:
        """Resolve conditionally: a concurrent supersede or second tab must not be overwritten."""
        if not self.store.resolve_pending_draft(draft_id, status, final_text=final_text):
            current = self.store.draft(draft_id)
            raise ValueError(f"draft {draft_id} is {current['status']}, not pending")

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
            self._settle(draft_id, "approved", final)
        elif action == "edit":
            if not text:
                raise ValueError("edit needs the new text")
            with store.transaction():
                store.add_draft_version(
                    draft_id, round=store.next_round(draft_id), candidate=1, origin="owner",
                    text=text, rationale=None,
                )
                self._settle(draft_id, "edited", text)
        elif action == "dismiss":
            self._settle(draft_id, "dismissed")
        else:
            raise ValueError(f"unknown action {action!r}")
