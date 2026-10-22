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
             "## Relevant memories", "## Owner's style", "## Message to answer"]
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
