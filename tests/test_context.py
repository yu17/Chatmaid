from dataclasses import replace

import pytest

from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.model import SourceRef
from chatmaid.reply.context import ReplyContext, build_context

NOW = 1_700_001_000


@pytest.fixture
def env(digested_env):
    msg = digested_env.connector.fetch(SourceRef("evolution", "a1", "INBOX", "1"))
    row = digested_env.store.message_by_ref(msg.ref.key())
    return digested_env, msg, row


def build(e, msg, row, llm=None, now=NOW):
    return build_context(
        e.store, e.connector, llm or FakeLLM(dim=4), e.config, row["conversation_id"], msg, now=now
    )


def test_context_has_summary_recent_memories_and_style(env):
    e, msg, row = env
    e.store.set_style_profile("en", "default", "terse", [])
    ctx = build(e, msg, row)
    assert ctx.summary == "S1"  # single chunk: the chunk summary stands
    assert "likes tea" in ctx.memories[0]
    assert ctx.style == "terse"
    assert ctx.language == "en"
    texts = [t for _, _, t in ctx.recent]
    assert any("thanks, see you tomorrow" in t for t in texts)
    assert ctx.message == msg.text[: e.config.draft.message_chars]
    assert all(t != 1_600_000_000 for t, _, _ in ctx.recent)


def test_recent_excludes_messages_outside_hot_window(env):
    e, msg, row = env
    ctx = build(e, msg, row, now=1_700_000_100 + 400 * 86400)
    assert ctx.recent == []


def test_unfetchable_recent_falls_back_to_preview(env, sample_message):
    e, msg, row = env
    ghost = replace(
        sample_message,
        ref=SourceRef("evolution", "a1", "INBOX", "999"),
        message_id="<ghost@x>",
        sent_at=1_700_000_200,
        preview="ghost preview",
    )
    e.store.upsert_message(row["conversation_id"], ghost, row["sender_identity_id"])
    ctx = build(e, msg, row)
    assert any(t == "ghost preview" for _, _, t in ctx.recent)


def test_embed_failure_does_not_block_context(env, monkeypatch):
    e, msg, row = env
    llm = FakeLLM(dim=4)

    def boom(texts, *, model):
        raise LLMError("down")

    monkeypatch.setattr(llm, "embed", boom)
    ctx = build(e, msg, row, llm=llm)
    assert any("likes tea" in m for m in ctx.memories)


def test_render_sections_order_and_roundtrip(env):
    e, msg, row = env
    e.store.set_style_profile("en", "default", "terse", [])
    ctx = build(e, msg, row)
    out = ctx.render()
    heads = ["## Contact", "## Conversation summary", "## Recent messages",
             "## Relevant memories", "## Owner's style", "## Your similar past replies",
             "## Message to answer"]
    pos = [out.index(h) for h in heads]
    assert pos == sorted(pos)
    assert ReplyContext.from_json(ctx.to_json()) == ctx
    assert "## Owner's style" not in replace_style(ctx).render()


def replace_style(ctx):
    return replace(ctx, style=None)


def test_recent_excludes_the_incoming_message(env, sample_message):
    e, _, row = env
    incoming = replace(
        sample_message,
        ref=SourceRef("evolution", "a1", "INBOX", "777"),
        message_id="<in@x>",
        sent_at=1_700_000_200,
        text="incoming question",
        preview="INCOMING-777",
    )
    e.store.upsert_message(row["conversation_id"], incoming, row["sender_identity_id"])
    assert any(r["ref_key"] == incoming.ref.key() for r in e.store.messages_for(row["conversation_id"]))
    ctx = build(e, incoming, row)
    texts = [t for _, _, t in ctx.recent]
    assert "INCOMING-777" not in texts
    assert any("thanks, see you tomorrow" in t for t in texts)


def test_context_includes_owner_examples_excluding_current_conversation(env):
    e, msg, row = env
    ctx = build(e, msg, row)
    assert 1 <= len(ctx.examples) <= e.config.draft.examples
    assert all(e.store.message_by_ref(x[0])["conversation_id"] != row["conversation_id"] for x in ctx.examples)
    text = ctx.render()
    assert text.index("## Your similar past replies") < text.index("## Message to answer")


def test_context_examples_are_stripped_and_cut_to_800(env, monkeypatch):
    e, msg, row = env
    monkeypatch.setattr(e.connector, "fetch", lambda ref: replace(msg, text="x" * 900 + "\n> quoted", ref=ref))
    ctx = build(e, msg, row)
    assert ctx.examples and all(len(x[3]) == 800 and "quoted" not in x[3] for x in ctx.examples)


def test_context_json_stores_example_previews_only(env):
    e, msg, row = env
    d = build(e, msg, row).to_json()
    assert d["examples"] and all(len(x[3]) <= 200 for x in d["examples"])
    assert ReplyContext.from_json(d).examples == [tuple(x) for x in d["examples"]]


def test_zero_examples_disables_the_section(env):
    e, msg, row = env
    cfg = replace(e.config, draft=replace(e.config.draft, examples=0))
    ctx = build_context(e.store, e.connector, FakeLLM(dim=4), cfg, row["conversation_id"], msg, now=NOW)
    assert ctx.examples == [] and "(none)" in ctx.render().split("## Your similar past replies")[1]
