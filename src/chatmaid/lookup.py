"""History lookup: metadata search plus vector neighbours, verified against the source."""

from dataclasses import dataclass

from chatmaid.connectors.base import Connector
from chatmaid.llm.base import LLM
from chatmaid.model import SourceRef
from chatmaid.store import Store

SNIPPET_LENGTH = 160


@dataclass
class Hit:
    ref: SourceRef
    sent_at: int
    sender: str
    subject: str
    snippet: str
    score: float
    conversation_id: int


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
            for row in store.messages_for(cid, limit=3):
                add(row, score)

    hits: list[Hit] = []
    for row, score in candidates.values():
        try:
            msg = connector.fetch(SourceRef.from_key(row["ref_key"]))
        except KeyError:
            continue
        hits.append(
            Hit(
                ref=msg.ref,
                sent_at=row["sent_at"] or msg.sent_at,
                sender=msg.sender.name or msg.sender.email,
                subject=msg.subject,
                snippet=_snippet(msg.text, query, row["preview"] or msg.preview),
                score=score,
                conversation_id=row["conversation_id"],
            )
        )
    hits.sort(key=lambda h: (-h.score, -h.sent_at))
    return hits[:limit]
