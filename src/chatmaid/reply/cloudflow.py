"""Owner-consented cloud improvement: propose, send, decline."""

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass

from chatmaid.cloud.base import CLOUD_SCHEMA, CloudError
from chatmaid.cloud.pseudonym import Pseudonymizer, deny_list_for, tag_persons
from chatmaid.llm import prompts
from chatmaid.llm.base import LLMError
from chatmaid.reply.context import ReplyContext

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudProposal:
    call_id: int
    backend: str
    model: str
    payload: str


class CloudMixin:
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
            # the whole message (ruling R19): redaction shortens URLs and addresses, so the
            # excerpt cut after redaction reaches further into the raw text than 1,500 chars
            tag_text = "\n".join([previous, comment or "", ctx.summary, ctx.message])
            names = tag_persons(self.llm, self.cfg.lmstudio.model, tag_text)
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
        # consent is on record before anything leaves the machine (ruling R15); a `sending`
        # row is never `proposed` again, so even a hard kill cannot lead to a second send
        store.set_cloud_call_status(call_id, "sending", consented_at=self.now())
        try:
            out = b.complete(call["payload"], CLOUD_SCHEMA)
            if not (
                isinstance(out, dict)
                and isinstance(out.get("text"), str)
                and isinstance(out.get("rationale"), str)
            ):
                raise CloudError("cloud backend returned no text")
        except BaseException as e:
            error = str(e) if isinstance(e, CloudError) else f"interrupted: {type(e).__name__}"
            store.set_cloud_call_status(call_id, "failed", error=error)
            store.log_event(
                "cloud", "failed", str(call_id), {"backend": call["backend"], "error": error}
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
                store.set_cloud_call_status(  # consented_at stays as the `sending` write set it
                    call_id, "sent", response=response,
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
