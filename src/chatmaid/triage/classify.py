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


def rule_triage(
    msg: NormalizedMessage, conversation: sqlite3.Row | None, *, now: int, max_age_days: int
) -> Triage | None:
    """Decide the obvious cases from age, headers and labels; None means ask the model.

    Mail sent more than `max_age_days` before `now` is informational (ruling R17): a reply
    that late is the owner's call, not a draft the watcher should write.
    """
    if msg.body_missing:
        return Triage("unknown", None, "body not cached", "rule")
    if msg.sent_at < now - max_age_days * 86400:
        return Triage("informational", None, f"older than {max_age_days} days", "rule")
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
    """Classify one message with the local model; unknown answers degrade safely."""
    user = (
        f"From: {msg.sender.name} <{msg.sender.email}>\n"
        f"Subject: {msg.subject}\n"
        f"Conversation so far: {summary or '(none)'}\n\n"
        f"{msg.text[:message_chars]}"
    )
    out = complete_json(llm, prompts.load("triage"), user, model)
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
    now: int,
    max_age_days: int,
) -> Triage:
    """The rule verdict if there is one, otherwise the model's."""
    verdict = rule_triage(msg, conversation, now=now, max_age_days=max_age_days)
    if verdict is not None:
        return verdict
    try:
        summary = conversation["summary"] if conversation else None
    except (KeyError, IndexError):  # a bare mapping or row without the column
        summary = None
    return model_triage(llm, model, msg, summary, message_chars=message_chars)
