"""Read models for the web UI: plain dataclasses shaped from the index and the connector."""

import sqlite3
import time

from dataclasses import dataclass

from chatmaid.model import SourceRef
from chatmaid.quoting import strip_reply_chrome


@dataclass(frozen=True)
class InboxRow:
    message_id: int
    conversation_id: int
    sent_at: int
    sender: str
    subject: str
    account: str
    category: str | None
    triage: str | None
    draft_id: int | None


@dataclass(frozen=True)
class MessageView:
    message_id: int
    conversation_id: int
    sent_at: int
    sender: str
    recipients: list[str]
    subject: str
    own_text: str
    full_text: str
    attachments: list[str]
    available: bool
    preview: str


@dataclass(frozen=True)
class ConversationView:
    conversation_id: int
    title: str
    category: str | None
    summary: str | None
    participants: list[str]
    messages: list[InboxRow]
    older_before: int | None


def account_labels(connector) -> dict[str, str]:
    """Account uid -> the owner's address on it."""
    return {
        a.uid: a.identity_addresses[0] for a in connector.accounts if a.identity_addresses
    }


def _row(r: sqlite3.Row, labels: dict[str, str]) -> InboxRow:
    return InboxRow(
        message_id=r["message_id"], conversation_id=r["conversation_id"], sent_at=r["sent_at"],
        sender=r["sender"], subject=r["subject"] or "", account=labels.get(r["account"], r["account"]),
        category=r["category"], triage=r["triage"], draft_id=r["draft_id"],
    )


def inbox(
    store, *, filter: str = "default", before: int | None = None, limit: int = 50,
    account_labels: dict[str, str],
) -> list[InboxRow]:
    rows = store.inbox_rows(filter=filter, before=before, limit=limit)
    return [_row(r, account_labels) for r in rows]


def message_view(store, connector, message_id: int) -> MessageView:
    row = store.message(message_id)
    if row is None:
        raise KeyError(message_id)
    sender = store.identity_label(row["sender_identity_id"])
    base = dict(
        message_id=message_id, conversation_id=row["conversation_id"], sent_at=row["sent_at"],
        sender=sender, subject=row["subject"] or "", preview=row["preview"] or "",
    )
    try:
        msg = connector.fetch(SourceRef.from_key(row["ref_key"]))
    except (KeyError, OSError, sqlite3.Error):
        return MessageView(
            **base, recipients=[], own_text="", full_text="", attachments=[], available=False
        )
    return MessageView(
        **base,
        recipients=[a.name or a.email for a in msg.recipients],
        own_text=strip_reply_chrome(msg.text),
        full_text=msg.text,
        attachments=list(msg.attachments),
        available=True,
    )


def conversation_view(
    store, conversation_id: int, *, before: int | None = None, limit: int = 50
) -> ConversationView:
    conv = store.conversation(conversation_id)
    if conv is None:
        raise KeyError(conversation_id)
    rows = store.conversation_messages(conversation_id, before=before, limit=limit + 1)
    more = len(rows) > limit
    shown = [_row(r, {}) for r in rows[:limit]]
    return ConversationView(
        conversation_id=conversation_id, title=conv["title"] or "", category=conv["category"],
        summary=conv["summary"], participants=store.participant_labels(conversation_id),
        messages=shown, older_before=shown[-1].sent_at if more and shown else None,
    )


_FAILURE_KINDS = ("error", "draft_failed", "owner_failed")


def untriaged_list(store, *, max_age_days: int, now: int) -> list:
    """The Status page's "waiting for triage" list: no retry backoff, so all of them show."""
    return store.untriaged_incoming(since=now - max_age_days * 86400)


def status_view(store, *, max_age_days: int, now: int | None = None) -> dict:
    now = int(time.time()) if now is None else now
    return {
        "counts": store.counts(),
        "with_summary": len(store.conversations(needing_summary=False, exclude_bulk=False)),
        "without_summary": len(store.conversations(needing_summary=True, exclude_bulk=False)),
        "categories": store.category_counts(),
        "recent_events": store.events(limit=50),
        "failures": store.events(limit=50, kinds=_FAILURE_KINDS),
        "untriaged": untriaged_list(store, max_age_days=max_age_days, now=now),
    }
