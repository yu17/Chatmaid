from dataclasses import replace

from chatmaid.llm.fake import FakeLLM
from chatmaid.triage.classify import Triage, model_triage, rule_triage, triage_message

ANSWER = '{"category":"needs_reply","tier":"work","reason":"r"}'
RECENT = {"message_chars": 100, "now": 1_700_000_000, "max_age_days": 14}  # sample_message's day


def rule(msg, conversation):
    return rule_triage(msg, conversation, now=RECENT["now"], max_age_days=14)


def test_rule_bulk_header(sample_message):
    t = rule(replace(sample_message, bulk=True), None)
    assert (t.category, t.tier, t.decided_by) == ("bulk", "bulk", "rule")


def test_rule_body_missing_is_unknown(sample_message):
    t = rule(replace(sample_message, body_missing=True), None)
    assert t == Triage("unknown", None, "body not cached", "rule")


def test_rule_spam_label_is_bulk(sample_message):
    t = rule(replace(sample_message, labels=("spam",)), None)
    assert t.category == "bulk" and t.decided_by == "rule"


def test_rule_trash_is_informational(sample_message):
    t = rule(replace(sample_message, labels=("trash",)), None)
    assert t == Triage("informational", None, "trash folder", "rule")


def test_rule_bulk_conversation(sample_message):
    t = rule(sample_message, {"bulk": 1})
    assert t.category == "bulk" and t.decided_by == "rule"


def test_rule_returns_none_for_ordinary_mail(sample_message):
    assert rule(sample_message, None) is None
    assert rule(sample_message, {"bulk": 0}) is None


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
    t = triage_message(llm, "small", replace(sample_message, bulk=True), None, **RECENT)
    assert t.decided_by == "rule" and len(llm.calls) == 0


def test_triage_message_passes_conversation_summary(sample_message):
    llm = FakeLLM({"triage": ANSWER})
    triage_message(llm, "small", sample_message, {"bulk": 0, "summary": "earlier chat"}, **RECENT)
    assert "Conversation so far: earlier chat" in llm.calls[0]["user"]


DAY = 86400


def test_rule_old_mail_is_informational(sample_message):
    now = sample_message.sent_at + 15 * DAY
    t = rule_triage(sample_message, None, now=now, max_age_days=14)
    assert t == Triage("informational", None, "older than 14 days", "rule")
    assert rule_triage(sample_message, None, now=sample_message.sent_at + 13 * DAY, max_age_days=14) is None
    old_missing = replace(sample_message, body_missing=True)
    assert rule_triage(old_missing, None, now=now, max_age_days=14).category == "unknown"
    old_bulk = replace(sample_message, bulk=True)
    assert rule_triage(old_bulk, None, now=now, max_age_days=14).reason == "older than 14 days"


def test_triage_message_skips_the_model_for_old_mail(sample_message):
    llm = FakeLLM({"triage": ANSWER})
    t = triage_message(
        llm, "small", sample_message, None, message_chars=100,
        now=sample_message.sent_at + 30 * DAY, max_age_days=14,
    )
    assert (t.category, t.decided_by) == ("informational", "rule") and llm.calls == []
