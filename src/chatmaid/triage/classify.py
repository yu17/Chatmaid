"""Triage of incoming mail: header rules first, the small local model for the rest."""

import sqlite3
from dataclasses import dataclass

from chatmaid.llm import prompts
from chatmaid.llm.base import LLM
from chatmaid.llm.jsoncall import complete_json
from chatmaid.model import NormalizedMessage

CATEGORIES = ("needs_reply", "informational", "bulk", "system", "unknown")
TIERS = ("family", "work", "finance", "casual", "bulk")


@dataclass(frozen=True)
class Triage:
    category: str
    tier: str | None
    reason: str
    decided_by: str  # "rule" or "model"
    model: str | None = None


def rule_triage(msg: NormalizedMessage, conversation: sqlite3.Row | None) -> Triage | None:
    """Decide the obvious cases from headers and labels; None means ask the model."""
    if msg.body_missing:
        return Triage("unknown", None, "body not cached", "rule")
    if msg.bulk:
        return Triage("bulk", "bulk", "bulk mail headers", "rule")
    if "spam" in msg.labels:
        return Triage("bulk", "bulk", "spam folder", "rule")
    if conversation and conversation["bulk"]:
        return Triage("bulk", "bulk", "bulk conversation", "rule")
    if "trash" in msg.labels:
        return Triage("informational", None, "trash folder", "rule")
    return None


def model_triage(
    llm: LLM, model: str, msg: NormalizedMessage, summary: str | None, *, message_chars: int
) -> Triage:
    """Classify one message with the small model; unknown answers degrade safely."""
    user = (
        f"From: {msg.sender.name} <{msg.sender.email}>\n"
        f"Subject: {msg.subject}\n"
        f"Conversation so far: {summary or '(none)'}\n\n"
        f"{msg.text[:message_chars]}"
    )
    out = complete_json(llm, prompts.load("triage"), user, model, grammar=True, max_tokens=256)
    category, tier, reason = out["category"], out["tier"], out["reason"]
    if category not in CATEGORIES[:4]:
        category, reason = "unknown", f"model returned {category!r}"
    return Triage(category, tier if tier in TIERS else None, reason, "model", model)


def triage_message(
    llm: LLM,
    model: str,
    msg: NormalizedMessage,
    conversation: sqlite3.Row | None,
    *,
    message_chars: int,
) -> Triage:
    """The rule verdict if there is one, otherwise the model's."""
    verdict = rule_triage(msg, conversation)
    if verdict is not None:
        return verdict
    try:
        summary = conversation["summary"] if conversation else None
    except (KeyError, IndexError):  # a bare mapping or row without the column
        summary = None
    return model_triage(llm, model, msg, summary, message_chars=message_chars)
