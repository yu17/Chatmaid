import hashlib

import pytest

from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.llm.prompts import load
from chatmaid.stop import StopRequested


def test_style_prompt_loads():
    p = load("style_profile")
    assert p.system.startswith("# style_profile\nDescribe how this person writes")
    assert p.schema["required"] == ["profile"]


def test_style_profile_per_language(ingested_env):
    llm = FakeLLM({"style_profile": '{"profile": "P"}'})
    pipe = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    # the fixture has fewer than 5 samples per language; lower the floor for this check
    out = pipe.style(InitOptions(), min_samples=1)
    assert set(out) >= {"en"} and ingested_env.store.style_profile("en", "default") == "P"
    assert ingested_env.store.style_profile("zh", "default") == "P"
    call = llm.calls[0]
    assert call["model"] == ingested_env.config.lmstudio.model
    assert "max_tokens" not in call
    assert "1. " in call["user"]


def test_style_skips_language_below_five_samples(ingested_env):
    llm = FakeLLM({"style_profile": '{"profile": "P"}'})
    pipe = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    assert pipe.style(InitOptions()) == {} and llm.calls == []


def test_style_llm_error_continues(ingested_env):
    llm = FakeLLM({"style_profile": '{"profile": "P"}'})
    llm.fail_next(1)
    pipe = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    out = pipe.style(InitOptions(), min_samples=1)
    assert len(out) == 1  # one language failed, the other stored


def test_embed_is_incremental(digested_env):
    store = digested_env.store
    p = InitPipeline(store, digested_env.connector, FakeLLM(dim=4), digested_env.config)
    assert p.embed(InitOptions()) == 0  # the fixture already embedded everything
    store.add_memory(kind="fact", text="new fact", source_ref="r", observed_at=None)
    assert p.embed(InitOptions()) == 1 and p.embed(InitOptions()) == 0
    expected = len(store.conversations(needing_summary=False, exclude_bulk=False)) + len(store.memories())
    assert expected >= 2 and store.counts()["embeddings"] == expected


def test_embed_reembeds_changed_text(digested_env):
    store = digested_env.store
    p = InitPipeline(store, digested_env.connector, FakeLLM(dim=4), digested_env.config)
    cid = store.conversations(needing_summary=False)[0]["id"]
    store.set_conversation_summary(cid, "different summary")
    assert p.embed(InitOptions()) == 1
    assert store.embedding_hash("conversation", cid) == hashlib.sha256(b"different summary").hexdigest()


def test_embed_rejects_wrong_dimension(digested_env):
    store = digested_env.store
    store.add_memory(kind="fact", text="x", source_ref="r", observed_at=None)
    p = InitPipeline(store, digested_env.connector, FakeLLM(dim=3), digested_env.config)
    with pytest.raises(LLMError):
        p.embed(InitOptions())


def test_embed_batches_of_32(ingested_env):
    store = ingested_env.store
    for i in range(40):
        store.add_memory(kind="fact", text=f"m{i}", source_ref="r", observed_at=None)
    sizes = []
    llm = FakeLLM(dim=4)
    orig = llm.embed

    def spy(texts, *, model):
        sizes.append(len(texts))
        return orig(texts, model=model)

    llm.embed = spy
    pipe = InitPipeline(store, ingested_env.connector, llm, ingested_env.config)
    assert pipe.embed(InitOptions()) == 40
    assert sizes == [32, 8]


def test_run_executes_style_and_embed(digested_env):
    llm = FakeLLM({"style_profile": '{"profile": "P"}'}, dim=4)
    pipe = InitPipeline(digested_env.store, digested_env.connector, llm, digested_env.config)
    out = pipe.run(InitOptions(passes=("style", "embed")))
    assert set(out) == {"style", "embed"}


def test_store_accessors(store):
    assert store.memories() == [] and store.embedding_hash("memory", 1) is None
    mid = store.add_memory(kind="fact", text="a", source_ref="r", observed_at=None)
    store.add_memory(kind="fact", text="b", source_ref="r", observed_at=None)
    assert store.memories()[0]["id"] == mid and len(store.memories(limit=1)) == 1
    store.upsert_embedding("memory", mid, "h", [1.0, 0.0, 0.0, 0.0])
    assert store.embedding_hash("memory", mid) == "h"


def test_owner_message_refs_newest_first(ingested_env):
    refs = ingested_env.store.owner_message_refs(limit=10)
    assert len(refs) >= 3
    times = [ingested_env.connector.fetch(r).sent_at for r in refs]
    assert times == sorted(times, reverse=True)
    assert len(ingested_env.store.owner_message_refs(limit=1)) == 1


def test_schema_skeleton_chunk_summary():
    from chatmaid.llm.jsoncall import schema_skeleton

    assert schema_skeleton(load("chunk_summary").schema) == (
        '{"category": "<string>", "summary": "<string>", '
        '"events": [{"date": "<string>", "text": "<string>"}], '
        '"facts": [{"about": "<string>", "text": "<string>"}]}'
    )
    assert schema_skeleton(load("style_profile").schema) == '{"profile": "<string>"}'


def test_json_shape_suffix_always_appended(ingested_env):
    llm = FakeLLM({"rolling_summary": '{"summary": "S"}'})
    pipe = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    pipe._complete_json(load("rolling_summary"), "u", "m")
    assert llm.calls[0]["user"] == 'u\n\nReply with exactly this JSON shape: {"summary": "<string>"}'


def test_embed_stop_keeps_completed_batches(ingested_env):
    store = ingested_env.store
    for i in range(40):
        store.add_memory(kind="fact", text=f"m{i}", source_ref="r", observed_at=None)
    llm = FakeLLM(dim=4)
    orig = llm.embed
    n = {"batches": 0}

    def flaky(texts, *, model):
        n["batches"] += 1
        if n["batches"] == 2:
            raise StopRequested()
        return orig(texts, model=model)

    llm.embed = flaky
    pipe = InitPipeline(store, ingested_env.connector, llm, ingested_env.config)
    assert pipe.embed(InitOptions()) == 32 and pipe.stopped
    assert store.counts()["embeddings"] == 32
