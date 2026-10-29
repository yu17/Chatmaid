"""History lookup: metadata search plus vector neighbours, verified against the source."""

import logging
import sqlite3
from dataclasses import dataclass

from chatmaid.connectors.base import Connector
from chatmaid.llm.base import LLM
from chatmaid.model import SourceRef
from chatmaid.store import Store

SNIPPET_LENGTH = 160
log = logging.getLogger(__name__)


@dataclass
class Hit:
    ref: SourceRef
    sent_at: int
    sender: str
    subject: str
    snippet: str
    score: float
    conversation_id: int
    category: str | None = None


@dataclass
class ThreadHit:
    conversation_id: int
    last_message_at: int
    category: str | None
    title: str
    summary: str
    score: float


def _snippet(text: str, query: str, preview: str) -> str:
    flat = " ".join(text.split())
    pos = flat.lower().find(query.lower()) if query else -1
    if pos < 0:
        return preview
    start = max(0, pos + len(query) // 2 - SNIPPET_LENGTH // 2)
    return flat[start : start + SNIPPET_LENGTH]


def search_history(
    store: Store,
    connector: Connector,
    llm: LLM | None,
    *,
    query: str,
    contact: str | None = None,
    since: int | None = None,
    until: int | None = None,
    limit: int = 20,
    embedding_model: str | None = None,
    categories: tuple[str, ...] = (),
) -> list[Hit]:
    candidates: dict[str, tuple[object, float]] = {}

    def add(row, score: float) -> None:
        key = row["ref_key"]
        if key not in candidates or candidates[key][1] < score:
            candidates[key] = (row, score)

    for row in store.search_messages(
        text=query, sender=contact, since=since, until=until, limit=limit * 3
    ):
        add(row, 1.0)

    if llm is not None and embedding_model is not None:
        vec = llm.embed([query], model=embedding_model)[0]
        for kind, ref_id, distance in store.search_embeddings(vec, k=10):
            score = 1.0 / (1.0 + distance)
            cid = None
            if kind == "conversation":
                cid = ref_id
            elif kind == "memory":
                memory = store.memory(ref_id)
                cid = memory["conversation_id"] if memory else None
            if cid is None:
                continue
            for row in store.messages_for(cid, limit=3, newest_first=True):
                add(row, score)

    hits: list[Hit] = []
    categories_by_conversation: dict[int, str | None] = {}
    for row, score in candidates.values():
        cid = row["conversation_id"]
        if cid not in categories_by_conversation:
            conv = store.conversation(cid)
            categories_by_conversation[cid] = conv["category"] if conv else None
        category = categories_by_conversation[cid]
        if categories and category not in categories:
            continue
        try:
            msg = connector.fetch(SourceRef.from_key(row["ref_key"]))
        except (KeyError, OSError, sqlite3.Error) as e:
            log.warning("skipping %s: %s", row["ref_key"], e)
            continue
        hits.append(
            Hit(
                ref=msg.ref,
                sent_at=row["sent_at"] or msg.sent_at,
                sender=msg.sender.name or msg.sender.email,
                subject=msg.subject,
                snippet=_snippet(msg.text, query, row["preview"] or msg.preview),
                score=score,
                conversation_id=cid,
                category=category,
            )
        )
    hits.sort(key=lambda h: (-h.score, -h.sent_at))
    return hits[:limit]


def search_threads(
    store: Store,
    llm: LLM,
    *,
    query: str,
    categories: tuple[str, ...] = (),
    limit: int = 20,
    embedding_model: str,
) -> list[ThreadHit]:
    vec = llm.embed([query], model=embedding_model)[0]
    k = limit * 3 if categories else limit
    hits: list[ThreadHit] = []
    for _kind, cid, distance in store.search_embeddings(vec, k=k, kind="conversation"):
        conv = store.conversation(cid)
        if conv is None or (categories and conv["category"] not in categories):
            continue
        hits.append(
            ThreadHit(
                conversation_id=cid,
                last_message_at=conv["last_message_at"],
                category=conv["category"],
                title=conv["title"],
                summary=(conv["summary"] or "")[:SNIPPET_LENGTH],
                score=1.0 / (1.0 + distance),
            )
        )
    hits.sort(key=lambda h: -h.score)
    return hits[:limit]
