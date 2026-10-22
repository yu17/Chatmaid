import json

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
    pipeline(ingested_env, FakeLLM(PLAIN)).digest(InitOptions())
    rows = ingested_env.store.conversations(exclude_bulk=False)
    bulk = [c for c in rows if c["bulk"]]
    assert bulk and all(c["summary"] is None for c in bulk)
    assert all(c["summary"] is not None for c in rows if not c["bulk"])


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
