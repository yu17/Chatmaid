import json
import sqlite3
from dataclasses import replace

import pytest

from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.prompts import load

PLAIN = {
    "chunk_summary": '{"summary":"S1","events":[],"facts":[]}',
    "rolling_summary": '{"summary":"S"}',
}


def pipeline(env, llm):
    return InitPipeline(env.store, env.connector, llm, env.config)


def test_prompts_load():
    p = load("chunk_summary")
    assert p.system.startswith("# chunk_summary\n") and "```" not in p.system
    assert p.schema["required"] == ["summary", "events", "facts"]
    assert load("rolling_summary").schema["required"] == ["summary"]


def test_digest_writes_summary_and_memories(ingested_env):
    llm = FakeLLM(
        {
            "chunk_summary": json.dumps(
                {
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


def test_digest_skips_bulk_conversations(ingested_env):
    st = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    rows = ingested_env.store.conversations(exclude_bulk=False)
    bulk = [c for c in rows if c["bulk"]]
    assert bulk and all(c["summary"] is None for c in bulk)
    # every non-bulk conversation is summarized except the fixture's one whose body is not cached
    assert st.skipped == 1
    assert sum(c["summary"] is None for c in rows if not c["bulk"]) == st.skipped


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
    pending = len(store.conversations(needing_summary=True))
    first = pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    assert first.failed == pending and first.conversations == 0
    assert store.conversations(needing_summary=True) == []
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
    pending = len(store.conversations(needing_summary=True))
    llm = FakeLLM(PLAIN)
    first = pipeline(ingested_env, llm).digest(InitOptions())
    assert (first.skipped, first.conversations, first.failed) == (pending, 0, 0)
    assert llm.calls == []  # nothing is sent to the model for bare timestamps
    assert len(store.conversations(needing_summary=True)) == pending  # still pending, not untracked
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
            "chunk_summary": '{"summary":"S1","events":[],"facts":[{"about":"Ann","text":"likes tea"}]}',
            "rolling_summary": '{"summary":"S"}',
        }
    )
    with pytest.raises(sqlite3.OperationalError):
        pipeline(ingested_env, llm).digest(InitOptions(limit_conversations=1))
    assert store.counts()["memories"] == 0
