"""History lookup over whole conversations.

Three ranked lists are fused by reciprocal rank (RRF): full-text matches over each thread's
title, summary and message subjects/previews; the nearest conversation summaries; and the
nearest extracted facts, credited to their conversation. A thread found by several lists ranks
above one found by a single list, and no list overrides the others.

Qwen3-Embedding is asymmetric: documents are embedded as they are, queries carry a one-line
task instruction (`embed_query`). On the owner's index that lifted top-1 self-retrieval from
56% to 72% (2026-11-12, 500 conversations, subject -> summary).
"""

import re
from dataclasses import dataclass, field

from chatmaid.llm.base import LLM
from chatmaid.store import Store

SNIPPET_LENGTH = 160
RRF_K = 60
SEARCH_TASK = "Given a search query, retrieve relevant email threads"
RELATED_TASK = "Given an email thread, retrieve other email threads about the same topic"
REPLY_TASK = "Given an incoming email, retrieve facts and past replies relevant to answering it"

_STOPWORDS = frozenset(
    "the and for with from that this are was were you your our has have not but all any can "
    "about into what when who how why which re fw fwd".split()
)
_UNSAFE = re.compile(r'[\x00-\x1f\x7f"]')  # FTS5 ends a quoted string at NUL; quotes delimit
_SUBJECT_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|wg|回复|答复|转发)\s*[:：]\s*)+", re.IGNORECASE)


@dataclass
class ThreadHit:
    conversation_id: int
    title: str
    category: str | None
    last_message_at: int | None
    message_count: int
    summary: str
    evidence: str
    score: float
    found_by: tuple[str, ...] = field(default=())


def embed_query(llm: LLM, text: str, *, model: str, task: str = SEARCH_TASK) -> list[float]:
    """Embed a query with the instruction Qwen3-Embedding expects; documents get none."""
    return llm.embed([f"Instruct: {task}\nQuery:{text}"], model=model)[0]


def text_query(query: str) -> tuple[str | None, tuple[str, ...]]:
    """An FTS5 MATCH expression for `query`, or the short terms to LIKE-match instead.

    The trigram index needs terms of three characters or more; those are OR-ed (bm25 rewards
    threads matching more of them) together with the whole query as a phrase. When every term
    is shorter, e.g. a two-character Chinese word, the short terms are returned instead.
    """
    raw = re.findall(r"\w+", query)
    words = [w for w in raw if w.lower() not in _STOPWORDS]
    long = list(dict.fromkeys(w for w in words if len(w) >= 3))
    phrase = " ".join(_UNSAFE.sub(" ", query).split())
    if long:
        terms = [f'"{w}"' for w in long]
        if len(raw) > 1 and len(phrase) >= 3:
            terms.insert(0, f'"{phrase}"')
        return " OR ".join(terms), ()
    if not raw and len(phrase) >= 3:  # emoji or symbols only: match them as written
        return f'"{phrase}"', ()
    return None, tuple(dict.fromkeys(words))


def normalize_subject(subject: str) -> str:
    return _SUBJECT_PREFIX.sub("", subject or "").strip()


def _fuse(lists: dict[str, list[int]]) -> dict[int, tuple[float, tuple[str, ...]]]:
    fused: dict[int, tuple[float, tuple[str, ...]]] = {}
    for name, ids in lists.items():
        for rank, cid in enumerate(dict.fromkeys(ids), start=1):
            score, found = fused.get(cid, (0.0, ()))
            fused[cid] = (score + 1.0 / (RRF_K + rank), found + (name,))
    return fused


def _hits(
    store: Store,
    fused: dict[int, tuple[float, tuple[str, ...]]],
    evidence: dict[int, str],
    limit: int,
) -> list[ThreadHit]:
    ranked = sorted(fused.items(), key=lambda kv: -kv[1][0])
    out: list[ThreadHit] = []
    for cid, (score, found) in ranked:
        conv = store.conversation(cid)
        if conv is None:
            continue
        summary = conv["summary"] or ""
        out.append(
            ThreadHit(
                conversation_id=cid,
                title=conv["title"] or "",
                category=conv["category"],
                last_message_at=conv["last_message_at"],
                message_count=conv["message_count"] or 0,
                summary=summary,
                evidence=" ".join((evidence.get(cid) or summary[:SNIPPET_LENGTH]).split()),
                score=score,
                found_by=found,
            )
        )
        if len(out) == limit:
            break
    return out


def _pool(limit: int, filtered: bool) -> int:
    """How many candidates each list contributes; more when filters will drop some."""
    return min(1000, max(limit * 20, 200)) if filtered else max(limit * 5, 50)


def search(
    store: Store,
    llm: LLM | None,
    *,
    query: str,
    embedding_model: str | None = None,
    contact: str | None = None,
    since: int | None = None,
    until: int | None = None,
    categories: tuple[str, ...] = (),
    limit: int = 20,
) -> list[ThreadHit]:
    """Conversations about `query`, best first. Without `llm`, full-text matching only."""
    filters = dict(categories=categories, contact=contact, since=since, until=until)
    filtered = bool(categories or contact or since is not None or until is not None)
    k = _pool(limit, filtered)
    evidence: dict[int, str] = {}
    lists: dict[str, list[int]] = {}

    match, like_terms = text_query(query)
    text_hits = store.search_conversations_text(match=match, like_terms=like_terms, limit=k, **filters)
    lists["text"] = [cid for cid, _ in text_hits]
    for cid, snip in text_hits:
        evidence.setdefault(cid, snip)

    if llm is not None and embedding_model is not None:
        vec = embed_query(llm, query, model=embedding_model)
        conv = [cid for _, cid, _ in store.search_embeddings(vec, k=k, kind="conversation")]
        facts: list[tuple[int, str]] = []
        for _, mid, _ in store.search_embeddings(vec, k=k, kind="memory"):
            row = store.memory(mid)
            if row is not None and row["conversation_id"] is not None:
                facts.append((row["conversation_id"], row["text"]))
        keep = store.filter_conversations(
            list(dict.fromkeys(conv + [cid for cid, _ in facts])), **filters
        )
        lists["summary"] = [cid for cid in conv if cid in keep]
        lists["fact"] = [cid for cid, _ in facts if cid in keep]
        for cid, text in facts:
            if cid in keep:
                evidence.setdefault(cid, text)

    return _hits(store, _fuse(lists), evidence, limit)


def related(
    store: Store,
    llm: LLM | None,
    conversation_id: int,
    *,
    embedding_model: str | None = None,
    categories: tuple[str, ...] = (),
    limit: int = 20,
) -> list[ThreadHit]:
    """Other conversations on the same topic as `conversation_id`, best first.

    Uses the thread's stored summary vector when it has one (no model call); a thread the
    digest has not reached yet is embedded from its subject and newest previews. Threads that
    share the subject line (minus Re:/Fwd:) are matched as a phrase.
    """
    conv = store.conversation(conversation_id)
    if conv is None:
        raise KeyError(conversation_id)
    filtered = bool(categories)
    k = _pool(limit, filtered) + 1
    evidence: dict[int, str] = {}
    lists: dict[str, list[int]] = {}

    subject = " ".join(_UNSAFE.sub(" ", normalize_subject(conv["title"] or "")).split())
    if len(subject) >= 3:
        hits = store.search_conversations_text(
            match=f'"{subject}"', limit=k, categories=categories
        )
        lists["subject"] = [cid for cid, _ in hits if cid != conversation_id]
        for cid, snip in hits:
            evidence.setdefault(cid, snip)

    vec = store.embedding_vector("conversation", conversation_id)
    if vec is None and llm is not None and embedding_model is not None:
        recent = store.messages_for(conversation_id, limit=3, newest_first=True)
        text = "\n".join([conv["title"] or ""] + [r["preview"] or "" for r in recent]).strip()
        if text:
            vec = embed_query(llm, text, model=embedding_model, task=RELATED_TASK)
    if vec is not None:
        near = [
            cid
            for _, cid, _ in store.search_embeddings(vec, k=k, kind="conversation")
            if cid != conversation_id
        ]
        keep = store.filter_conversations(near, categories=categories)
        lists["summary"] = [cid for cid in near if cid in keep]

    return _hits(store, _fuse(lists), evidence, limit)
