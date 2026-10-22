"""Candidate replies from the local model, with one optional history-lookup round."""

from dataclasses import dataclass
from datetime import UTC, datetime

from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.llm import prompts
from chatmaid.llm.base import LLM, LLMError
from chatmaid.llm.jsoncall import complete_json
from chatmaid.lookup import Hit, search_history
from chatmaid.reply.context import ReplyContext
from chatmaid.store import Store


@dataclass(frozen=True)
class Candidate:
    text: str
    rationale: str


def _hit_lines(hits: list[Hit]) -> str:
    if not hits:
        return "(nothing found)"
    return "\n".join(
        f"[{datetime.fromtimestamp(h.sent_at, UTC):%Y-%m-%d} {h.sender}] {h.subject} — {h.snippet}"
        for h in hits
    )


def _candidates(out: dict, limit: int) -> list[Candidate]:
    found = [
        Candidate(c["text"], c["rationale"])
        for c in out["candidates"]
        if c["text"].strip()
    ]
    return found[:limit]


def draft_reply(
    store: Store,
    connector: Connector,
    llm: LLM,
    cfg: Config,
    ctx: ReplyContext,
    *,
    sender_email: str,
) -> list[Candidate]:
    """Write candidate replies; may run one history lookup when a fact is missing."""
    prompt = prompts.load("reply_draft")
    user = ctx.render() + f"\n\nWrite {cfg.draft.candidates} candidate replies as the owner."
    out = complete_json(llm, prompt, user, cfg.draft_model, grammar=False, max_tokens=2048)
    found = _candidates(out, cfg.draft.candidates)
    if not found and out["lookup"]:
        query = out["lookup"]
        hits = search_history(
            store,
            connector,
            llm,
            query=query,
            contact=sender_email,
            limit=5,
            embedding_model=cfg.lmstudio.model_embedding,
        )
        user += (
            f"\n\n## History lookup for: {query}\n{_hit_lines(hits)}"
            "\nThe lookup is done; now write the candidates."
        )
        out = complete_json(llm, prompt, user, cfg.draft_model, grammar=False, max_tokens=2048)
        found = _candidates(out, cfg.draft.candidates)
    if not found:
        raise LLMError("reply_draft: no candidates")
    return found


def revise_reply(
    llm: LLM, cfg: Config, ctx: ReplyContext, previous: str, comment: str
) -> Candidate:
    """Rewrite a previous draft applying the owner's feedback."""
    user = ctx.render() + "\n\n## Previous draft\n" + previous + "\n\n## Owner's feedback\n" + comment
    out = complete_json(
        llm,
        prompts.load("reply_revise"),
        user,
        cfg.draft_model,
        grammar=False,
        max_tokens=1024,
    )
    return Candidate(out["text"], out["rationale"])
