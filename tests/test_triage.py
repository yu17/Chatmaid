from dataclasses import replace

from chatmaid.llm.fake import FakeLLM
from chatmaid.triage.classify import Triage, model_triage, rule_triage, triage_message

ANSWER = '{"category":"needs_reply","tier":"work","reason":"r"}'


def test_rule_bulk_header(sample_message):
    t = rule_triage(replace(sample_message, bulk=True), None)
    assert (t.category, t.tier, t.decided_by) == ("bulk", "bulk", "rule")


def test_rule_body_missing_is_unknown(sample_message):
    t = rule_triage(replace(sample_message, body_missing=True), None)
    assert t == Triage("unknown", None, "body not cached", "rule")


def test_rule_spam_label_is_bulk(sample_message):
    t = rule_triage(replace(sample_message, labels=("spam",)), None)
    assert t.category == "bulk" and t.decided_by == "rule"


def test_rule_trash_is_informational(sample_message):
    t = rule_triage(replace(sample_message, labels=("trash",)), None)
    assert t == Triage("informational", None, "trash folder", "rule")


def test_rule_bulk_conversation(sample_message):
    t = rule_triage(sample_message, {"bulk": 1})
    assert t.category == "bulk" and t.decided_by == "rule"


def test_rule_returns_none_for_ordinary_mail(sample_message):
    assert rule_triage(sample_message, None) is None
    assert rule_triage(sample_message, {"bulk": 0}) is None


def test_model_triage_parses(sample_message):
    llm = FakeLLM({"triage": ANSWER})
    t = model_triage(llm, "small", sample_message, None, message_chars=100)
    assert t == Triage("needs_reply", "work", "r", "model", "small")
    assert "Subject: Hi" in llm.calls[0]["user"]
    assert "Conversation so far: (none)" in llm.calls[0]["user"]
    assert llm.calls[0]["grammar"] is True


def test_model_invalid_category_is_unknown(sample_message):
    llm = FakeLLM({"triage": '{"category":"spammy","tier":"nope","reason":"r"}'})
    t = model_triage(llm, "small", sample_message, "sum", message_chars=100)
    assert t.category == "unknown" and t.tier is None
    assert t.reason == "model returned 'spammy'"


def test_model_not_called_when_rule_decides(sample_message):
    llm = FakeLLM({"triage": ANSWER})
    t = triage_message(llm, "small", replace(sample_message, bulk=True), None, message_chars=100)
    assert t.decided_by == "rule" and len(llm.calls) == 0


def test_triage_message_passes_conversation_summary(sample_message):
    llm = FakeLLM({"triage": ANSWER})
    triage_message(llm, "small", sample_message, {"bulk": 0, "summary": "earlier chat"}, message_chars=100)
    assert "Conversation so far: earlier chat" in llm.calls[0]["user"]
