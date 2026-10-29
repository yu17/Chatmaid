from chatmaid.llm.fake import FakeLLM
from chatmaid.lookup import search_history, search_threads


def test_search_history_finds_by_subject_and_snippet(digested_env):
    hits = search_history(digested_env.store, digested_env.connector, None, query="hello")
    assert hits and "hello" in hits[0].snippet.lower()


def test_search_history_vector_path_adds_conversation(digested_env):
    llm = FakeLLM(dim=4)
    hits = search_history(
        digested_env.store, digested_env.connector, llm, query="zzz-not-in-any-subject",
        embedding_model="e",
    )
    assert hits  # from vector neighbours
    assert all(h.score < 1.0 for h in hits)


def test_search_history_sorted_and_limited(digested_env):
    hits = search_history(digested_env.store, digested_env.connector, None, query="s", limit=2)
    assert len(hits) <= 2
    assert [h.sent_at for h in hits] == sorted((h.sent_at for h in hits), reverse=True)


def test_search_history_skips_messages_gone_from_source(digested_env, monkeypatch):
    def gone(ref):
        raise KeyError(ref)

    monkeypatch.setattr(digested_env.connector, "fetch", gone)
    assert search_history(digested_env.store, digested_env.connector, None, query="hello") == []


def test_search_history_vector_path_uses_newest_messages(store, sample_message, tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    from chatmaid.model import SourceRef

    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "a@x.org", None)
    for i in range(4):
        ref = SourceRef("evolution", "acct1", "INBOX", str(i))
        store.upsert_message(
            cid, replace(sample_message, ref=ref, message_id=f"<{i}@x>", sent_at=1000 + i), sid
        )
    llm = FakeLLM(dim=4)
    store.upsert_embedding("conversation", cid, "h", llm.embed(["q"], model="e")[0])

    class Conn:
        def fetch(self, ref):
            return replace(sample_message, ref=ref)

    hits = search_history(store, Conn(), llm, query="zzz-nothing", embedding_model="e")
    assert sorted(h.sent_at for h in hits) == [1001, 1002, 1003]


def test_search_history_skips_candidate_on_sqlite_error(digested_env, monkeypatch):
    import sqlite3

    real = digested_env.connector.fetch
    calls = []

    def flaky(ref):
        calls.append(ref)
        if len(calls) == 1:
            raise sqlite3.OperationalError("locked")
        return real(ref)

    monkeypatch.setattr(digested_env.connector, "fetch", flaky)
    hits = search_history(digested_env.store, digested_env.connector, None, query="s")
    assert len(calls) >= 2 and len(hits) == len(calls) - 1


def test_search_history_filters_by_category(digested_env):
    store = digested_env.store
    hit = search_history(store, digested_env.connector, None, query="hello")[0]
    store.set_conversation_category(hit.conversation_id, "promotional")
    assert search_history(store, digested_env.connector, None, query="hello", categories=("conversation",)) == []
    assert search_history(store, digested_env.connector, None, query="hello", categories=("promotional",))[0].category == "promotional"


def test_search_threads_ranks_conversations(digested_env):
    hits = search_threads(digested_env.store, FakeLLM(dim=4), query="S1", embedding_model="e")
    assert hits and hits[0].summary.startswith("S") and all(h.category == "conversation" for h in hits)
    assert search_threads(digested_env.store, FakeLLM(dim=4), query="S1", categories=("event",), embedding_model="e") == []
