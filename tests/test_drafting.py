import json

import pytest

from chatmaid.llm.base import LLMError
from chatmaid.llm.fake import FakeLLM
from chatmaid.reply.context import ReplyContext
from chatmaid.reply.drafting import Candidate, draft_reply, revise_reply

CTX = ReplyContext(
    "Ann <ann@x.org>; tier work",
    "S1",
    [(1_700_000_100, "me", "thanks")],
    ["Ann: likes tea"],
    "terse",
    "en",
    "work",
    "Can we meet Friday?",
)


def answer(*texts):
    return json.dumps({"candidates": [{"text": t, "rationale": "r"} for t in texts], "lookup": ""})


def test_draft_returns_candidates(digested_env):
    llm = FakeLLM({"reply_draft": answer("Yes", "Sure")}, dim=4)
    cfg = digested_env.config
    out = draft_reply(
        digested_env.store, digested_env.connector, llm, cfg, CTX, sender_email="ann@x.org"
    )
    assert out == [Candidate("Yes", "r"), Candidate("Sure", "r")]
    assert llm.calls[0]["model"] == cfg.lmstudio.model
    assert llm.calls[0]["user"].startswith(CTX.render())
    assert f"Write {cfg.draft.candidates} candidate replies as the owner." in llm.calls[0]["user"]


def test_draft_runs_one_lookup_round(digested_env):
    llm = FakeLLM(
        {
            "reply_draft": [
                '{"candidates":[],"lookup":"office"}',
                '{"candidates":[{"text":"ok","rationale":"r"}],"lookup":""}',
            ]
        },
        dim=4,
    )
    out = draft_reply(
        digested_env.store,
        digested_env.connector,
        llm,
        digested_env.config,
        CTX,
        sender_email="ann@x.org",
    )
    assert out == [Candidate("ok", "r")]
    calls = [c for c in llm.calls if c["system"].startswith("# reply_draft")]
    assert len(calls) == 2
    second = calls[1]["user"]
    assert "## History lookup for: office" in second
    assert "me@x.org] test — thanks, see you tomorrow at the office" in second
    assert "The lookup is done; now write the candidates." in second


def test_draft_without_candidates_after_lookup_raises(digested_env):
    llm = FakeLLM({"reply_draft": '{"candidates":[],"lookup":"office"}'}, dim=4)
    with pytest.raises(LLMError, match="no candidates"):
        draft_reply(
            digested_env.store,
            digested_env.connector,
            llm,
            digested_env.config,
            CTX,
            sender_email="ann@x.org",
        )


def test_draft_caps_candidates_and_drops_empty(digested_env):
    cfg = digested_env.config
    n = cfg.draft.candidates
    llm = FakeLLM({"reply_draft": answer("", *[f"t{i}" for i in range(n + 3)])}, dim=4)
    out = draft_reply(
        digested_env.store, digested_env.connector, llm, cfg, CTX, sender_email="ann@x.org"
    )
    assert [c.text for c in out] == [f"t{i}" for i in range(n)]


def test_revise_includes_previous_and_comment(digested_env):
    llm = FakeLLM({"reply_revise": '{"text":"Shorter","rationale":"r"}'}, dim=4)
    out = revise_reply(llm, digested_env.config, CTX, "Long text", "make it shorter")
    assert out == Candidate("Shorter", "r")
    user = llm.calls[0]["user"]
    assert user.startswith(CTX.render())
    assert "## Previous draft\nLong text" in user
    assert "## Owner's feedback\nmake it shorter" in user
    assert llm.calls[0]["model"] == digested_env.config.lmstudio.model
