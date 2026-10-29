"""Conversation categories decided by the digest, and the sender line its prompt reads."""

import sqlite3
from datetime import datetime, timezone

# Engagement order: a conversation takes the earliest category any of its chunks reports.
CATEGORIES = ("conversation", "event", "transactional", "newsletter", "promotional", "system")


def normalize_category(value: object) -> str | None:
    return value if isinstance(value, str) and value in CATEGORIES else None


def most_engaged(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a if b is None else b
    return min(a, b, key=CATEGORIES.index)


def allows_events(category: str | None) -> bool:
    return category in ("conversation", "event")


def allows_facts(category: str | None) -> bool:
    return category == "conversation"


def summary_limit(category: str | None) -> int:
    """The word limit the prompt sets for a chunk summary of this category."""
    return 120 if category in ("conversation", "event") else 25


def sender_line(stat: sqlite3.Row | None) -> str:
    if stat is None:
        return "Sender context: the owner; outgoing mail with no reply indexed."
    year = datetime.fromtimestamp(stat["first_seen"], timezone.utc).year
    known = "yes" if stat["known"] else "no"
    answered = "yes" if stat["answered"] else "no"
    return (
        f"Sender context: {stat['messages']} message(s) from this sender since {year}; "
        f"owner has written to them: {known}; "
        f"owner replied in a conversation with them: {answered}; "
        f"bulk headers on {round(stat['bulk_share'] * 100)}% of their mail."
    )
