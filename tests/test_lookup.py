from dataclasses import replace

import pytest

from chatmaid.llm.fake import FakeLLM
from chatmaid.lookup import (
    RELATED_TASK,
    SEARCH_TASK,
    normalize_subject,
    related,
    search,
    text_query,
)
from chatmaid.model import SourceRef


class VecLLM:
    """Embeds by keyword: a text containing a key gets that key's vector; records every input."""

    def __init__(self, table: dict[str, list[float]]):
        self.table = table
        self.seen: list[str] = []

    def embed(self, texts, *, model):
        self.seen += texts
        out = []
        for t in texts:
            vec = next((v for k, v in self.table.items() if k in t), [0.25, 0.25, 0.25, 0.25])
            out.append(vec)
        return out


def _thread(store, sample_message, key, title, *, summary=None, preview="", sender="ann@x.org",
            sent_at=1_700_000_000, category="conversation"):
    cid = store.upsert_conversation("email", key, title, "thread")
    sid = store.upsert_identity("email", sender, None)
    ref = SourceRef("evolution", "acct1", "INBOX", key)
    store.upsert_message(
        cid,
        replace(sample_message, ref=ref, message_id=f"<{key}@x>", subject=title, preview=preview,
                sent_at=sent_at),
        sid,
    )
    if summary is not None:
        store.set_conversation_summary(cid, summary, category=category)
    return cid


# --- query shaping -------------------------------------------------------------------------

def test_text_query_ors_terms_and_adds_the_phrase():
    match, short = text_query("visa application")
    assert match == '"visa application" OR "visa" OR "application"' and short == ()


def test_text_query_drops_stopwords_and_falls_back_to_short_terms():
    assert text_query("the visa")[0] == '"the visa" OR "visa"'
    assert text_query("签证") == (None, ("签证",))
    assert text_query("the re") == (None, ())


def test_text_query_strips_quotes():
    match, _ = text_query('say "hi" there')
    assert '""' not in match and match.startswith('"say hi there"')


def test_text_query_survives_control_characters_and_symbols(store, sample_message):
    cid = _thread(store, sample_message, "a", "🎉🎉🎉 party", summary="S")
    assert search(store, None, query="abc\x00def\ttab") == []  # no FTS5 syntax error
    assert [h.conversation_id for h in search(store, None, query="🎉🎉🎉")] == [cid]
    nul = _thread(store, sample_message, "b", 'Odd "subject"\x00 here', summary="S")
    related(store, None, nul)  # the subject phrase is sanitized too


def test_normalize_subject_strips_reply_prefixes():
    assert normalize_subject("Re: Fwd: 回复：Lab move") == "Lab move"
    assert normalize_subject("RE:re: x") == "x"


# --- search --------------------------------------------------------------------------------

def test_search_full_text_only_finds_title_summary_and_preview(store, sample_message):
    a = _thread(store, sample_message, "a", "Visa appointment", summary="Consulate booking")
    b = _thread(store, sample_message, "b", "Lunch", summary="About the visa interview")
    c = _thread(store, sample_message, "c", "Hello", preview="bring your visa documents")
    _thread(store, sample_message, "d", "Unrelated", summary="nothing here")
    hits = search(store, None, query="visa")
    assert [h.conversation_id for h in hits][:1] == [a]  # title weighs most
    assert {h.conversation_id for h in hits} == {a, b, c}
    assert all(h.found_by == ("text",) for h in hits)
    assert "[" in hits[0].evidence  # the snippet marks the match


def test_search_matches_chinese_substrings(store, sample_message):
    cid = _thread(store, sample_message, "a", "关于签证材料的通知", summary="S")
    assert [h.conversation_id for h in search(store, None, query="签证材料")] == [cid]
    assert [h.conversation_id for h in search(store, None, query="签证")] == [cid]  # short: LIKE


def test_search_query_carries_the_instruction(store, sample_message):
    _thread(store, sample_message, "a", "x", summary="S")
    llm = VecLLM({})
    search(store, llm, query="trip plans", embedding_model="e")
    assert llm.seen == [f"Instruct: {SEARCH_TASK}\nQuery:trip plans"]


def test_search_fuses_lists_so_agreement_wins(store, sample_message):
    both = _thread(store, sample_message, "a", "Conference trip", summary="travel to the conference")
    text_only = _thread(store, sample_message, "b", "Conference newsletter", summary="digest")
    vec_only = _thread(store, sample_message, "c", "Flights", summary="booking flights")
    store.upsert_embedding("conversation", both, "h", [1.0, 0, 0, 0])
    store.upsert_embedding("conversation", vec_only, "h", [0.9, 0.1, 0, 0])
    store.upsert_embedding("conversation", text_only, "h", [0, 0, 0, 1.0])
    llm = VecLLM({"conference": [1.0, 0, 0, 0]})
    hits = search(store, llm, query="conference", embedding_model="e")
    assert hits[0].conversation_id == both and set(hits[0].found_by) == {"text", "summary"}
    assert {h.conversation_id for h in hits} >= {text_only, vec_only}


def test_search_credits_facts_to_their_conversation(store, sample_message):
    cid = _thread(store, sample_message, "a", "Misc", summary="S")
    mid = store.add_memory(kind="fact", text="Ann moves to Berlin in May", source_ref="r",
                           observed_at=None, conversation_id=cid)
    store.upsert_embedding("memory", mid, "h", [0, 1.0, 0, 0])
    hits = search(store, VecLLM({"relocat": [0, 1.0, 0, 0]}), query="relocation", embedding_model="e")
    assert hits[0].conversation_id == cid and "fact" in hits[0].found_by
    assert hits[0].evidence == "Ann moves to Berlin in May"


def test_search_filters_apply_to_every_list(store, sample_message):
    old = _thread(store, sample_message, "a", "Grant report", summary="S", sent_at=1_000, sender="bob@y.org")
    new = _thread(store, sample_message, "b", "Grant report v2", summary="S", sent_at=2_000,
                  category="event")
    for cid in (old, new):
        store.upsert_embedding("conversation", cid, "h", [1.0, 0, 0, 0])
    llm = VecLLM({"grant": [1.0, 0, 0, 0]})
    kw = dict(query="grant", embedding_model="e")
    assert [h.conversation_id for h in search(store, llm, since=1_500, **kw)] == [new]
    assert [h.conversation_id for h in search(store, llm, until=1_500, **kw)] == [old]
    assert [h.conversation_id for h in search(store, llm, contact="bob@", **kw)] == [old]
    assert [h.conversation_id for h in search(store, llm, categories=("event",), **kw)] == [new]


def test_search_limit_and_empty(store, sample_message):
    for i in range(5):
        _thread(store, sample_message, f"k{i}", f"Budget {i}", summary="S")
    assert len(search(store, None, query="budget", limit=2)) == 2
    assert search(store, None, query="zzzz") == []


# --- related -------------------------------------------------------------------------------

def test_related_uses_the_stored_vector_and_the_subject(store, sample_message):
    me = _thread(store, sample_message, "a", "Re: Lab move schedule", summary="S")
    same_subject = _thread(store, sample_message, "b", "Lab move schedule", summary="S")
    near = _thread(store, sample_message, "c", "Packing boxes", summary="S")
    far = _thread(store, sample_message, "d", "Newsletter", summary="S")
    store.upsert_embedding("conversation", me, "h", [1.0, 0, 0, 0])
    store.upsert_embedding("conversation", near, "h", [0.95, 0.05, 0, 0])
    store.upsert_embedding("conversation", far, "h", [0, 0, 0, 1.0])
    store.upsert_embedding("conversation", same_subject, "h", [0, 0, 1.0, 0])
    hits = related(store, None, me, limit=2)  # no model needed
    assert me not in [h.conversation_id for h in hits]
    assert {h.conversation_id for h in hits} == {same_subject, near}


def test_related_embeds_a_thread_the_digest_has_not_reached(store, sample_message):
    fresh = _thread(store, sample_message, "a", "Housing offer", preview="the apartment on Elm")
    other = _thread(store, sample_message, "b", "Apartment tour", summary="S")
    store.upsert_embedding("conversation", other, "h", [1.0, 0, 0, 0])
    llm = VecLLM({"apartment": [1.0, 0, 0, 0]})
    hits = related(store, llm, fresh, embedding_model="e")
    assert [h.conversation_id for h in hits] == [other]
    assert llm.seen[0].startswith(f"Instruct: {RELATED_TASK}\nQuery:Housing offer")


def test_related_unknown_conversation(store):
    with pytest.raises(KeyError):
        related(store, None, 999)


# --- the full-text index stays current -----------------------------------------------------

def _fts(store, cid):
    row = store._conn.execute("SELECT title, summary, body FROM conversation_fts WHERE rowid = ?", (cid,)).fetchone()
    return None if row is None else tuple(row)


def test_fts_follows_messages_summaries_merges_and_deletes(store, sample_message):
    a = _thread(store, sample_message, "a", "Alpha", preview="first body")
    b = _thread(store, sample_message, "b", "Beta", preview="second body")
    assert _fts(store, a) == ("Alpha", "", "Alpha first body")
    store.set_conversation_summary(a, "summary A")
    assert _fts(store, a)[1] == "summary A"
    with store.transaction():
        store.merge_conversations(a, b)
    assert _fts(store, b) is None
    assert set(_fts(store, a)[2].splitlines()) == {"Alpha first body", "Beta second body"}
    assert _fts(store, a)[1] == ""  # the merge cleared the summary


def test_digested_env_is_searchable(digested_env):
    hits = search(digested_env.store, FakeLLM(dim=4), query="hello", embedding_model="e")
    assert hits and "text" in hits[0].found_by
