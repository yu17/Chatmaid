import io
import json
import sqlite3
from dataclasses import replace

import pytest

from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.init.progress import Progress
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import CANNED, FakeLLM
from chatmaid.llm.prompts import load
from chatmaid.stop import StopRequested

PLAIN = {
    "chunk_summary": '{"category":"conversation","summary":"S1","events":[],"facts":[]}',
    "rolling_summary": '{"summary":"S"}',
}


def pipeline(env, llm):
    return InitPipeline(env.store, env.connector, llm, env.config)


def test_prompts_load():
    p = load("chunk_summary")
    assert p.system.startswith("# chunk_summary\n") and "```" not in p.system
    assert p.schema["required"] == ["category", "summary", "events", "facts"]
    assert load("rolling_summary").schema["required"] == ["summary"]


def test_digest_writes_summary_and_memories(ingested_env):
    llm = FakeLLM(
        {
            "chunk_summary": json.dumps(
                {
                    "category": "conversation",
                    "summary": "S1",
                    "events": [{"date": "2024-10-01", "text": "met"}],
                    "facts": [{"about": "Ann", "text": "likes tea"}],
                }
            ),
            "rolling_summary": json.dumps({"summary": "S"}),
        }
    )
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.conversations >= 1 and st.memories >= 1
    row = ingested_env.store.conversations(needing_summary=False)[0]
    assert row["summary"] in ("S", "S1")
    assert json.loads(row["events_json"]) == [{"date": "2024-10-01", "text": "met"}]
    mems = ingested_env.store._conn.execute("SELECT kind, text, observed_at FROM memory").fetchall()
    assert ("fact", "Ann: likes tea") in [(m["kind"], m["text"]) for m in mems]
    assert any(m["kind"] == "event" and m["observed_at"] == 1727740800 for m in mems)


def test_digest_resumes_after_llm_failure(ingested_env):
    llm = FakeLLM(PLAIN)
    llm.fail_next(1)
    p = pipeline(ingested_env, llm)
    first = p.digest(InitOptions())
    second = p.digest(InitOptions())
    assert first.failed == 1 and second.failed == 0 and second.conversations >= 1


def test_digest_includes_bulk_by_default_and_skip_bulk_option(ingested_env):
    store = ingested_env.store
    pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions(skip_bulk=True))  # fresh index
    bulk = [c for c in store.conversations(exclude_bulk=False) if c["bulk"]]
    assert bulk and all(c["summary"] is None for c in bulk)
    st = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    rows = store.conversations(exclude_bulk=False)
    assert all(c["summary"] is not None for c in rows if c["bulk"])
    # every conversation is summarized except the fixture's one whose body is not cached
    assert st.skipped == 1
    assert sum(c["summary"] is None for c in rows) == st.skipped


def test_digest_resumes_from_chunk_checkpoint(ingested_env):
    store = ingested_env.store
    multi = next(c for c in store.conversations() if len(store.message_refs_for(c["id"])) > 1)
    cid = multi["id"]
    store.set_checkpoint("digest", str(cid), {"chunk": 0, "summary": "partial", "events": []})
    llm = FakeLLM(PLAIN)
    # chunk_chars=1 puts each message in its own chunk, so chunk 0 is the one already done
    pipeline(ingested_env, llm).digest(InitOptions(chunk_chars=1, limit_conversations=None))
    merges = [c for c in llm.calls if c["system"].startswith("# rolling_summary")]
    assert any("Previous summary:\npartial" in c["user"] for c in merges)
    assert store.get_checkpoint("digest", str(cid)) == {"done": True}


def test_digest_resumes_with_category_from_checkpoint(ingested_env):
    store = ingested_env.store
    multi = next(c for c in store.conversations() if len(store.message_refs_for(c["id"])) > 1)
    cid = multi["id"]
    store.set_checkpoint(
        "digest", str(cid), {"chunk": 0, "summary": "partial", "events": [], "category": "conversation"}
    )
    llm = FakeLLM({"chunk_summary": '{"category":"promotional","summary":"b","events":[],"facts":[]}',
                   "rolling_summary": '{"summary":"S"}'})
    pipeline(ingested_env, llm).digest(InitOptions(chunk_chars=1, limit_conversations=1))
    # the most engaged category across chunks survives the resume
    assert store.conversation(cid)["category"] == "conversation"


def test_digest_counts_locked_source_as_failed_and_retries(ingested_env, monkeypatch):
    store = ingested_env.store
    store.refresh_sender_stats()
    cid = store.digest_queue(exclude_bulk=False, limit=1)[0]["id"]
    real = ingested_env.connector.fetch

    def locked(ref):  # Evolution holds its folders.db while the owner reads mail
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(ingested_env.connector, "fetch", locked)
    st = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions(limit_conversations=1))
    assert st.failed == 1 and st.conversations == 0
    row = store.conversation(cid)
    assert row["summary"] is None and row["tracked"] == 1  # left pending, not untracked
    assert store.get_checkpoint("digest", str(cid)) is None
    monkeypatch.setattr(ingested_env.connector, "fetch", real)
    again = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions(limit_conversations=1))
    assert again.conversations == 1 and store.conversation(cid)["summary"] is not None


def test_digest_model_and_retry(ingested_env):
    llm = FakeLLM({"chunk_summary": "not json", "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert st.failed == 1 and st.conversations == 0
    cfg = ingested_env.config.lmstudio
    assert [c["model"] for c in llm.calls] == [cfg.model, cfg.model]
    assert llm.calls[1]["user"].endswith(
        "Your reply was not valid JSON matching the schema; reply with the JSON object only."
    )


def test_digest_drops_malformed_items(ingested_env):
    reply = {
        "category": "conversation",
        "summary": "S1",
        "events": [{"date": "2024-10-01"}, {"date": "2024-10-02", "text": "ok"}],
        "facts": ["junk", {"about": "Ann", "text": "t"}],
    }
    llm = FakeLLM({"chunk_summary": json.dumps(reply), "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert st.failed == 0 and st.memories == 2


def test_digest_untracks_conversation_without_fetchable_messages(ingested_env, monkeypatch):
    store = ingested_env.store

    def gone(ref):
        raise KeyError(ref.key())

    monkeypatch.setattr(ingested_env.connector, "fetch", gone)
    pending = len(store.conversations(needing_summary=True, exclude_bulk=False))
    first = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    assert first.failed == pending and first.conversations == 0
    assert store.conversations(needing_summary=True, exclude_bulk=False) == []
    second = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    assert second.failed == 0 and second.conversations == 0


def test_digest_skips_conversations_without_cached_text(ingested_env, monkeypatch):
    store = ingested_env.store
    real = ingested_env.connector.fetch
    bodies = {"cached": False}

    def fetch(ref):  # a body that is not in the Evolution cache arrives as empty text
        msg = real(ref)
        if bodies["cached"]:
            return replace(msg, text=msg.text or "now cached", body_missing=False)
        return replace(msg, text="", preview="", body_missing=True)

    monkeypatch.setattr(ingested_env.connector, "fetch", fetch)
    pending = len(store.conversations(needing_summary=True, exclude_bulk=False))
    llm = FakeLLM(PLAIN)
    first = pipeline(ingested_env, llm).digest(InitOptions())
    assert (first.skipped, first.conversations, first.failed) == (pending, 0, 0)
    assert llm.calls == []  # nothing is sent to the model for bare timestamps
    # still pending, not untracked
    assert len(store.conversations(needing_summary=True, exclude_bulk=False)) == pending
    assert store.get_checkpoint("digest", str(store.conversations(needing_summary=True)[0]["id"])) is None
    bodies["cached"] = True  # the owner downloads the mail for offline use, then runs the pass again
    second = pipeline(ingested_env, llm).digest(InitOptions())
    assert second.skipped == 0 and second.conversations == pending


def test_digest_drops_empty_messages_from_chunks(ingested_env, monkeypatch):
    store = ingested_env.store
    multi = next(c for c in store.conversations() if len(store.message_refs_for(c["id"])) > 1)
    blank_key = store.message_refs_for(multi["id"])[0][0].key()
    real = ingested_env.connector.fetch

    def fetch(ref):
        msg = real(ref)
        return replace(msg, text="", body_missing=True) if ref.key() == blank_key else msg

    monkeypatch.setattr(ingested_env.connector, "fetch", fetch)
    llm = FakeLLM(PLAIN)
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.failed == 0
    assert store.conversation(multi["id"])["summary"] is not None
    rendered = [line for c in llm.calls for line in c["user"].splitlines() if line.startswith("[")]
    assert rendered and not any(line.rstrip().endswith("]") for line in rendered)


def test_chunk_memories_and_checkpoint_are_written_atomically(ingested_env, monkeypatch):
    store = ingested_env.store
    real = store.set_checkpoint

    def locked(task, key, state):
        if task == "digest" and "chunk" in state:
            raise sqlite3.OperationalError("database is locked")
        return real(task, key, state)

    monkeypatch.setattr(store, "set_checkpoint", locked)
    llm = FakeLLM(
        {
            "chunk_summary": '{"category":"conversation","summary":"S1","events":[],"facts":[{"about":"Ann","text":"likes tea"}]}',
            "rolling_summary": '{"summary":"S"}',
        }
    )
    with pytest.raises(sqlite3.OperationalError):
        pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert store.counts()["memories"] == 0


def test_digest_stores_category_and_drops_junk_memories(ingested_env):
    llm = FakeLLM({"chunk_summary": json.dumps({"category": "promotional", "summary": "sale",
        "events": [{"date": "2024-10-01", "text": "sale ends"}], "facts": [{"about": "Shop", "text": "sells"}]}),
        "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.memories == 0 and st.categories.get("promotional", 0) == st.conversations
    rows = ingested_env.store.conversations(needing_summary=False, exclude_bulk=False)
    assert all(r["category"] == "promotional" and json.loads(r["events_json"]) == [] for r in rows)


def test_digest_invalid_category_is_null(ingested_env):
    llm = FakeLLM({"chunk_summary": '{"category":"spam","summary":"x","events":[],"facts":[]}', "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions())
    assert st.categories == {"unknown": st.conversations}
    assert all(r["category"] is None for r in ingested_env.store.conversations(needing_summary=False, exclude_bulk=False))


def test_digest_category_most_engaged_across_chunks(ingested_env):
    store = ingested_env.store
    multi = next(c for c in store.conversations() if len(store.message_refs_for(c["id"])) > 1)
    llm = FakeLLM({"chunk_summary": ['{"category":"event","summary":"a","events":[],"facts":[]}',
                                     '{"category":"conversation","summary":"b","events":[],"facts":[]}'],
                   "rolling_summary": '{"summary":"S"}'})
    pipeline(ingested_env, llm).digest(InitOptions(chunk_chars=1, limit_conversations=1))
    assert store.conversation(multi["id"])["category"] == "conversation"


def test_digest_strips_quoted_history_except_first(watch_env):
    env = watch_env  # its add_row writes a row and body into the fixture cache
    env.add_row("INBOX", uid="20", message_id="<r20@x>", sender="Ann <ann@x.org>", to="me@x.org",
                date=1_600_000_100, body="Yes.\n> hello there", in_reply_to="<a@x>")
    llm = FakeLLM(PLAIN)
    p = InitPipeline(env.store, env.connector, llm, env.config)
    p.ingest(InitOptions())
    p.digest(InitOptions())
    chunk_calls = [c["user"] for c in llm.calls if c["system"].startswith("# chunk_summary")]
    thread = next(u for u in chunk_calls if "Yes." in u)
    assert "hello there" in thread and "> hello there" not in thread


def test_digest_sender_context_line(ingested_env):
    llm = FakeLLM(PLAIN)
    pipeline(ingested_env, llm).digest(InitOptions())
    users = [c["user"] for c in llm.calls if c["system"].startswith("# chunk_summary")]
    assert all(u.startswith("Sender context: ") for u in users)
    assert any("owner has written to them: yes" in u for u in users)
    assert any(u.startswith("Sender context: the owner; outgoing mail with no reply indexed.") for u in users)


def test_digest_counts_long_summaries(ingested_env):
    llm = FakeLLM({"chunk_summary": json.dumps({"category": "promotional", "summary": " ".join(["w"] * 26), "events": [], "facts": []}),
                   "rolling_summary": '{"summary":"S"}'})
    st = pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert st.long == 1 and st.conversations == 1


def test_digest_stops_between_conversations_and_resumes(ingested_env):
    llm = FakeLLM(PLAIN)
    n = {"calls": 0}
    orig = llm.complete

    def flaky(**kw):
        n["calls"] += 1
        if n["calls"] == 2:
            raise StopRequested()
        return orig(**kw)

    llm.complete = flaky
    p = pipeline(ingested_env, llm)
    st = p.digest(InitOptions())
    assert st.stopped and p.stopped and st.conversations == 1
    st2 = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    assert not st2.stopped and st2.conversations >= 1 and st2.skipped == 1


def test_stop_inside_chunk_transaction_rolls_back(ingested_env, monkeypatch):
    store = ingested_env.store

    def boom(task, key, state):
        raise StopRequested()

    monkeypatch.setattr(store, "set_checkpoint", boom)
    st = pipeline(ingested_env, FakeLLM(dict(CANNED))).digest(InitOptions(limit_conversations=1))
    assert st.stopped and st.conversations == 0 and store.memories() == []
    assert all(c["summary"] is None for c in store.conversations(exclude_bulk=False))


def test_run_stops_after_a_stopped_pass(ingested_env):
    llm = FakeLLM(PLAIN)

    def stop_now(**kw):
        raise StopRequested()

    llm.complete = stop_now
    p = pipeline(ingested_env, llm)
    out = p.run(InitOptions(passes=("digest", "embed")))
    assert list(out) == ["digest"] and p.stopped


def test_progress_is_written_to_the_given_stream(ingested_env):
    out = io.StringIO()
    env = ingested_env
    p = InitPipeline(env.store, env.connector, FakeLLM(PLAIN), env.config, progress_stream=out)
    p.digest(InitOptions())
    assert out.getvalue().startswith("digest ")


class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_digest_finishes_progress_when_refresh_raises(ingested_env, monkeypatch):
    store = ingested_env.store
    calls = {"n": 0}
    orig = store.refresh_sender_stats

    def refresh():
        calls["n"] += 1
        if calls["n"] == 2:
            raise LLMError("boom")
        return orig()

    monkeypatch.setattr(store, "refresh_sender_stats", refresh)
    out = _Tty()
    env = ingested_env
    p = InitPipeline(env.store, env.connector, FakeLLM(PLAIN), env.config, progress_stream=out)
    with pytest.raises(LLMError):
        p.digest(InitOptions())
    assert Progress.active is None and out.getvalue().endswith("\n")
