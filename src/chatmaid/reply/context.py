"""The context header the drafting model sees: contact, summary, recent mail, memories, style."""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from chatmaid.config import Config
from chatmaid.connectors.base import Connector
from chatmaid.init.chunking import detect_language
from chatmaid.llm.base import LLM, LLMError
from chatmaid.model import NormalizedMessage, SourceRef, make_preview
from chatmaid.quoting import strip_reply_chrome
from chatmaid.store import Store

log = logging.getLogger(__name__)

RECENT_CHARS = 1500


@dataclass
class ReplyContext:
    contact_card: str
    summary: str
    recent: list[tuple[int, str, str]]  # (sent_at, who, text), oldest first
    memories: list[str]
    style: str | None
    language: str
    tier: str | None
    message: str
    examples: list[tuple[str, int, str, str]] = field(default_factory=list)  # (ref_key, sent_at, to, text)

    def to_json(self) -> dict:
        """Stored record: previews only (200 chars), never full message bodies."""
        return {
            "contact_card": self.contact_card,
            "summary": self.summary,
            "recent": [[t, who, make_preview(text)] for t, who, text in self.recent],
            "memories": list(self.memories),
            "style": self.style,
            "examples": [[k, t, to, make_preview(text)] for k, t, to, text in self.examples],
            "language": self.language,
            "tier": self.tier,
            "message": make_preview(self.message),
        }

    @classmethod
    def from_json(cls, d: dict) -> ReplyContext:
        return cls(
            contact_card=d["contact_card"],
            summary=d["summary"],
            recent=[(int(t), who, text) for t, who, text in d["recent"]],
            memories=list(d["memories"]),
            style=d["style"],
            examples=[(k, int(t), to, text) for k, t, to, text in d.get("examples", [])],
            language=d["language"],
            tier=d["tier"],
            message=d["message"],
        )

    def render(self) -> str:
        def day(ts: int) -> str:
            return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")

        recent = "\n".join(f"[{day(t)} {who}] {text}" for t, who, text in self.recent) or "(none)"
        memories = "\n".join(f"- {m}" for m in self.memories) or "(none)"
        parts = [
            f"## Contact\n{self.contact_card}",
            f"## Conversation summary\n{self.summary or '(none)'}",
            f"## Recent messages\n{recent}",
            f"## Relevant memories\n{memories}",
        ]
        if self.style is not None:
            parts.append(f"## Owner's style\n{self.style}")
        examples = "\n".join(f"[{day(t)} to {to}] {text}" for _k, t, to, text in self.examples) or "(none)"
        parts.append(f"## Your similar past replies\n{examples}")
        parts.append(f"## Message to answer\n{self.message}")
        return "\n\n".join(parts)


def _contact_card(store: Store, msg: NormalizedMessage, contact_id: int | None) -> str:
    contact = store.contact(contact_id) if contact_id is not None else None
    if contact is None:
        return f"{msg.sender.name or msg.sender.email} <{msg.sender.email}>; tier ?"
    addrs = [i["address"] for i in store.identities_of(contact_id)]
    name = contact["name"] or (addrs[0] if addrs else msg.sender.email)
    card = f"{name} <{', '.join(addrs)}>; tier {contact['tier'] or '?'}"
    if contact["notes"]:
        card += f"; notes: {contact['notes']}"
    return card


def _recent(
    store: Store, connector: Connector, cfg: Config, conversation_id: int,
    msg: NormalizedMessage, now: int,
) -> list[tuple[int, str, str]]:
    own_key = msg.ref.key()
    rows = store.messages_for(
        conversation_id,
        since=now - cfg.data.hot_window_days * 86400,
        limit=cfg.draft.recent_messages,
        newest_first=True,
    )
    out: list[tuple[int, str, str]] = []
    for row in rows:
        if row["ref_key"] == own_key:
            continue
        text = row["preview"] or ""
        if not row["body_missing"]:
            try:
                text = connector.fetch(SourceRef.from_key(row["ref_key"])).text
            except (KeyError, OSError, sqlite3.Error):
                text = row["preview"] or ""
        who = store.identity_label(row["sender_identity_id"])
        out.append((row["sent_at"], who, text[:RECENT_CHARS]))
    out.reverse()
    return out


def _query_vector(llm: LLM, cfg: Config, msg: NormalizedMessage) -> list[float] | None:
    try:
        return llm.embed([msg.text[:2000]], model=cfg.lmstudio.model_embedding)[0]
    except LLMError as e:
        log.warning("embedding the incoming message failed, skipping vector memories and examples: %s", e)
        return None


def _memories(
    store: Store, cfg: Config, conversation_id: int, contact_id: int | None,
    vec: list[float] | None,
) -> list[str]:
    texts = [
        r["text"]
        for r in store.memories_for(
            contact_id=contact_id, conversation_id=conversation_id, limit=cfg.draft.memories
        )
    ]
    if vec is not None:
        for _kind, ref_id, _dist in store.search_embeddings(vec, k=cfg.draft.memories, kind="memory"):
            row = store.memory(ref_id)
            if row is not None:
                texts.append(row["text"])
    return list(dict.fromkeys(texts))[: 2 * cfg.draft.memories]


def _examples(
    store: Store, connector: Connector, cfg: Config, conversation_id: int,
    vec: list[float] | None,
) -> list[tuple[str, int, str, str]]:
    """The owner's own past messages nearest the incoming one, from other conversations."""
    if vec is None or cfg.draft.examples == 0:
        return []
    out: list[tuple[str, int, str, str]] = []
    hits = store.search_embeddings(vec, k=cfg.draft.examples * 3, kind="owner_message")
    for _kind, ref_id, _dist in hits:
        row = store.message(ref_id)
        if row is None or row["conversation_id"] == conversation_id:
            continue
        try:
            sent = connector.fetch(SourceRef.from_key(row["ref_key"]))
        except (KeyError, OSError, sqlite3.Error):
            continue
        text = strip_reply_chrome(sent.text).strip()[:800]
        if not text:
            continue
        to = (sent.recipients[0].name or sent.recipients[0].email) if sent.recipients else "?"
        out.append((row["ref_key"], sent.sent_at, to, text))
        if len(out) >= cfg.draft.examples:
            break
    return out


def build_context(
    store: Store,
    connector: Connector,
    llm: LLM,
    cfg: Config,
    conversation_id: int,
    msg: NormalizedMessage,
    *,
    now: int | None = None,
) -> ReplyContext:
    """Assemble what the model needs to answer `msg`; reads the store, never writes."""
    now = int(time.time()) if now is None else now
    language = detect_language(msg.text)
    conversation = store.conversation(conversation_id)
    tier = conversation["tier"]
    stored = store.message_by_ref(msg.ref.key())
    contact_id = None
    if stored is not None and stored["sender_identity_id"] is not None:
        try:
            contact_id = store.contact_id_of(stored["sender_identity_id"])
        except KeyError:
            contact_id = None
    vec = _query_vector(llm, cfg, msg)
    return ReplyContext(
        contact_card=_contact_card(store, msg, contact_id),
        summary=conversation["summary"] or "",
        recent=_recent(store, connector, cfg, conversation_id, msg, now),
        memories=_memories(store, cfg, conversation_id, contact_id, vec),
        style=store.style_profile(language, tier or "default")
        or store.style_profile(language, "default"),
        examples=_examples(store, connector, cfg, conversation_id, vec),
        language=language,
        tier=tier,
        message=msg.text[: cfg.draft.message_chars],
    )
