from chatmaid.llm.fake import FakeLLM
from chatmaid.lookup import search_history


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
