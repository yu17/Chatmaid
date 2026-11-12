import json
import sqlite3

import pytest

SENT = "[Gmail]/Sent Mail"


def kinds(env):
    return [e["kind"] for e in env.store.events(limit=500)]


def calls(env, key):
    return [c for c in env.llm.calls if c["system"].startswith(f"# {key}")]


def owner_reply(env, uid, body, in_reply_to):
    return env.arrive(
        uid, sender="me@x.org", to="ann@x.org", folder=SENT, body=body, in_reply_to=in_reply_to
    )


def test_incoming_needs_reply_creates_draft_with_two_candidates(flow_env):
    env = flow_env
    row = env.arrive("100")
    drafts = env.store.drafts(status=None)
    assert len(drafts) == 1
    versions = env.store.draft_versions(drafts[0]["id"])
    assert len(versions) == 2
    assert {v["origin"] for v in versions} == {"local"}
    assert env.store.triage_for(row["id"])["category"] == "needs_reply"
    assert env.store.conversation(row["conversation_id"])["tier"] == "work"
    assert {"triage", "draft"} <= set(kinds(env))


def test_bulk_incoming_is_recorded_by_rule_without_model_call(flow_env):
    env = flow_env
    row = env.arrive(
        "100", sender="news@x.org", extra_headers="List-Unsubscribe: <mailto:u@x>\n"
    )
    t = env.store.triage_for(row["id"])
    assert (t["category"], t["decided_by"]) == ("bulk", "rule")
    assert env.store.drafts(status=None) == []
    assert calls(env, "triage") == []


def test_body_missing_incoming_makes_no_draft(flow_env):
    env = flow_env
    env.add_row(
        "INBOX", uid="100", message_id="<m100@x>", sender="Ann <ann@x.org>", to="me@x.org",
        date=1_700_000_500, body="x",
    )
    (env.cache_dir / "a1" / "folders" / "INBOX" / "cur" / "00" / "100").unlink()
    for _ in range(4):
        env.watcher._handle("timer")
    from chatmaid.model import SourceRef

    row = env.store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "100").key())
    assert env.store.triage_for(row["id"])["category"] == "unknown"
    assert env.store.triage_for(row["id"])["decided_by"] == "rule"
    assert env.store.drafts(status=None) == []
    assert calls(env, "triage") == []


def test_old_incoming_is_not_drafted(flow_env):
    env = flow_env
    env.flow.now = lambda: 1_700_000_500 + 30 * 86400
    row = env.arrive("100", date=1_700_000_500)
    t = env.store.triage_for(row["id"])
    assert (t["category"], t["decided_by"], t["reason"]) == (
        "informational", "rule", "older than 14 days"
    )
    assert calls(env, "triage") == [] and calls(env, "reply_draft") == []
    assert env.store.drafts(status=None) == []


def test_informational_makes_no_draft(flow_env):
    env = flow_env
    env.llm.responses["triage"] = '{"category":"informational","tier":"work","reason":"fyi"}'
    row = env.arrive("100")
    assert env.store.triage_for(row["id"])["category"] == "informational"
    assert env.store.drafts(status=None) == []


def test_new_incoming_supersedes_pending_draft(flow_env):
    env = flow_env
    env.arrive("100")
    env.arrive("101", in_reply_to="<m100@x>")
    drafts = env.store.drafts(status=None)
    assert sorted(d["status"] for d in drafts) == ["pending", "superseded"]


def test_owner_reply_matching_candidate_resolves_approved(flow_env):
    env = flow_env
    env.arrive("100")
    sent = owner_reply(env, "200", "Sure,   Friday works.", "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert d["status"] == "approved"
    assert d["outcome_ref"] == sent["ref_key"]
    assert d["outcome_preview"] == "Sure, Friday works."
    assert d["final_text"] == "Sure, Friday works."
    assert "draft_resolved" in kinds(env)


def test_owner_reply_with_other_text_resolves_edited(flow_env):
    env = flow_env
    env.arrive("100")
    sent = owner_reply(env, "200", "Friday is bad, how about Monday?", "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert d["status"] == "edited"
    assert d["final_text"] is None
    assert d["outcome_ref"] == sent["ref_key"]
    assert d["outcome_preview"] == "Friday is bad, how about Monday?"


def test_owner_reply_with_quoted_history_and_signature_is_approved(flow_env):
    env = flow_env
    env.arrive("100")
    body = "Sure, Friday works.\n\nOn Mon, Ann wrote:\n> Can we meet Friday?\n-- \nJiaqi"
    owner_reply(env, "200", body, "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert (d["status"], d["final_text"]) == ("approved", "Sure, Friday works.")


def test_owner_reply_extending_a_candidate_is_approved(flow_env):
    env = flow_env
    env.arrive("100")
    owner_reply(env, "200", "Friday is fine, thanks.\n--\nJiaqi\nSent from my phone", "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert (d["status"], d["final_text"]) == ("approved", "Friday is fine, thanks.")


def test_failed_draft_leaves_previous_draft_pending(flow_env):
    env = flow_env
    env.arrive("100")
    first = env.store.drafts()[0]["id"]
    env.llm.responses["reply_draft"] = '{"candidates":[],"lookup":""}'
    env.arrive("101", in_reply_to="<m100@x>")
    assert env.store.draft(first)["status"] == "pending"
    assert [d["id"] for d in env.store.drafts(status=None)] == [first]
    assert "draft_failed" in kinds(env)


def test_owner_message_strips_quoted_history_and_sets_category(flow_env):
    env = flow_env
    cid = env.arrive("100")["conversation_id"]
    env.store.set_conversation_summary(cid, "S0", category="event")
    owner_reply(env, "200", "Sure.\n\nOn Mon, Ann wrote:\n> Can we meet Friday?", "<m100@x>")
    user = calls(env, "chunk_summary")[0]["user"]
    assert "Sure." in user and "Can we meet Friday?" not in user
    assert env.store.conversation(cid)["category"] == "conversation"


def test_owner_message_updates_summary_and_memories(flow_env):
    env = flow_env
    row = env.arrive("100")
    cid = row["conversation_id"]
    env.store.set_conversation_summary(cid, "S0")
    before = len(env.store.memories())
    owner_reply(env, "200", "Sure, Friday works.", "<m100@x>")
    assert env.store.conversation(cid)["summary"] == "S"
    assert len(env.store.memories()) == before + 1


def test_owner_message_without_summary_does_not_update(flow_env):
    env = flow_env
    row = env.arrive("100")
    cid = row["conversation_id"]
    assert env.store.conversation(cid)["summary"] is None
    before = len(env.store.memories())
    owner_reply(env, "200", "Sure, Friday works.", "<m100@x>")
    assert env.store.conversation(cid)["summary"] is None
    assert len(env.store.memories()) == before
    assert calls(env, "chunk_summary") == []


def test_regenerate_adds_round_two_with_comment(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    vid = env.flow.regenerate(d["id"], "shorter")
    v = [x for x in env.store.draft_versions(d["id"]) if x["id"] == vid][0]
    assert (v["round"], v["candidate"], v["origin"], v["comment"]) == (2, 1, "local", "shorter")
    assert v["text"] == "Sure, Friday works. Thanks for the reminder."
    user = calls(env, "reply_revise")[-1]["user"]
    assert "## Previous draft" in user and "shorter" in user
    assert "draft_regenerated" in kinds(env)
    assert env.flow.latest_text(d["id"]) == v["text"]


def test_regenerate_from_version(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    second = env.store.draft_versions(d["id"])[1]
    env.flow.regenerate(d["id"], "x", from_version=second["id"])
    assert "Friday is fine, thanks." in calls(env, "reply_revise")[-1]["user"]


def test_resolve_edit_adds_owner_version_and_final_text(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    env.flow.resolve(d["id"], "edit", text="My own words")
    row = env.store.draft(d["id"])
    assert (row["status"], row["final_text"]) == ("edited", "My own words")
    assert env.store.draft_versions(d["id"])[-1]["origin"] == "owner"
    with pytest.raises(ValueError):
        env.flow.resolve(env.store.drafts(status=None)[0]["id"], "edit", text="again")


def test_resolve_edit_requires_text(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    with pytest.raises(ValueError):
        env.flow.resolve(d["id"], "edit")
    assert env.store.draft(d["id"])["status"] == "pending"


def test_resolve_approve_uses_chosen_version(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    second = env.store.draft_versions(d["id"])[1]
    env.flow.resolve(d["id"], "approve", version_id=second["id"])
    row = env.store.draft(d["id"])
    assert (row["status"], row["final_text"]) == ("approved", "Friday is fine, thanks.")


def test_resolve_approve_defaults_to_latest_first_candidate(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    env.flow.resolve(d["id"], "approve")
    assert env.store.draft(d["id"])["final_text"] == "Sure, Friday works."


def test_resolve_dismiss(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    env.flow.resolve(d["id"], "dismiss")
    assert env.store.draft(d["id"])["status"] == "dismissed"


def test_resolve_non_pending_raises(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    env.flow.resolve(d["id"], "dismiss")
    with pytest.raises(ValueError):
        env.flow.resolve(d["id"], "approve")
    with pytest.raises(ValueError):
        env.flow.regenerate(d["id"], "c")
    with pytest.raises(ValueError):
        env.flow.resolve(99999, "dismiss")


def test_resolve_unknown_action_raises(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    with pytest.raises(ValueError):
        env.flow.resolve(d["id"], "bogus")


def test_llm_failure_logs_draft_failed_and_keeps_message(flow_env):
    env = flow_env
    env.llm.fail_next(2)
    env.add_row(
        "INBOX", uid="100", message_id="<m100@x>", sender="Ann <ann@x.org>", to="me@x.org",
        date=1_700_000_500, body="Can we meet Friday?",
    )
    assert env.watcher._handle("timer") == 1
    from chatmaid.model import SourceRef

    assert env.store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "100").key()) is not None
    assert env.store.drafts(status=None) == []
    assert "draft_failed" in kinds(env)
    ev = next(e for e in env.store.events(limit=50) if e["kind"] == "draft_failed")
    row = env.store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "100").key())
    assert json.loads(ev["payload_json"])["message_id"] == row["id"]


def test_dispatch_owner_route_never_drafts(flow_env):
    env = flow_env
    owner_reply(env, "200", "hello", None)
    assert env.store.drafts(status=None) == []
    assert calls(env, "triage") == []
    assert calls(env, "reply_draft") == []


def test_draft_context_roundtrips_for_regenerate(flow_env):
    env = flow_env
    env.arrive("100")
    d = env.store.drafts()[0]
    assert json.loads(d["context_json"])


LONG = "".join(f"w{i:03d} " for i in range(60)).strip()  # 299 chars, tail past 200


def test_context_json_holds_previews_only(flow_env):
    env = flow_env
    env.arrive("099", body=LONG, date=1_700_000_400)
    env.arrive("100", body=LONG, in_reply_to="<m099@x>")
    ctx = json.loads(env.store.drafts()[0]["context_json"])
    assert len(ctx["message"]) <= 200
    assert ctx["recent"] and all(len(text) <= 200 for _, _, text in ctx["recent"])


def test_regenerate_rebuilds_context_from_source(flow_env):
    env = flow_env
    env.arrive("100", body=LONG)
    env.flow.regenerate(env.store.drafts()[0]["id"], "shorter")
    assert LONG[200:] in calls(env, "reply_revise")[-1]["user"]


def test_regenerate_falls_back_to_stored_context_when_message_is_gone(flow_env, monkeypatch):
    env = flow_env
    env.arrive("100", body=LONG)

    def gone(ref):
        raise KeyError(ref)

    monkeypatch.setattr(env.connector, "fetch", gone)
    env.flow.regenerate(env.store.drafts()[0]["id"], "shorter")
    user = calls(env, "reply_revise")[-1]["user"]
    assert LONG[:200] in user and LONG[200:] not in user


def test_reply_linking_two_drafted_conversations_merges_without_error(flow_env):
    env = flow_env
    a = env.arrive("100")
    b = env.arrive("101", sender="Bob <bob@x.org>", subject="other topic")
    assert a["conversation_id"] != b["conversation_id"]
    first = {d["id"] for d in env.store.drafts(status=None)}
    assert len(first) == 2
    env.add_row(
        "INBOX", uid="102", message_id="<m102@x>", sender="Ann <ann@x.org>", to="me@x.org",
        date=1_700_000_600, body="Both, please.", in_reply_to="<m101@x>",
        references=("<m100@x>", "<m101@x>"), subject="meeting",
    )
    assert env.watcher._handle("timer") == 1
    assert "error" not in kinds(env)
    from chatmaid.model import SourceRef

    row = env.store.message_by_ref(SourceRef("evolution", "a1", "INBOX", "102").key())
    drafts = env.store.drafts(status=None)
    assert first <= {d["id"] for d in drafts}
    assert {d["conversation_id"] for d in drafts} == {row["conversation_id"]}
    assert [d["status"] for d in drafts].count("pending") == 1


def test_learned_bulk_sender_is_triaged_by_rule(flow_env, monkeypatch):
    env = flow_env
    monkeypatch.setattr(env.store, "learned_bulk", lambda identity_id, *, min_conversations: True)
    row = env.arrive("100")
    t = env.store.triage_for(row["id"])
    assert (t["category"], t["decided_by"], t["reason"]) == ("bulk", "rule", "learned bulk sender")
    assert calls(env, "triage") == [] and env.store.pending_draft_for(row["conversation_id"]) is None


def one_candidate_draft(env, text="Friday works."):
    env.llm.responses["reply_draft"] = json.dumps(
        {"candidates": [{"text": text, "rationale": "r"}], "lookup": ""}
    )
    env.arrive("100")
    d = env.store.drafts()[0]
    # drafts are stamped with the wall clock; the fixture's mail is dated in 2023
    env.store._conn.execute("UPDATE draft SET created_at = 1700000100 WHERE id = ?", (d["id"],))
    return d["id"]


def test_sent_mail_that_only_starts_with_the_draft_is_edited(flow_env):
    env = flow_env
    one_candidate_draft(env)
    owner_reply(env, "200", "Friday works. " + "Also, something else entirely. " * 10, "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert (d["status"], d["final_text"]) == ("edited", None)


def test_short_addition_still_counts_as_approved(flow_env):
    env = flow_env
    one_candidate_draft(env)
    owner_reply(env, "200", "Friday works. See you!", "<m100@x>")
    d = env.store.drafts(status=None)[0]
    assert (d["status"], d["final_text"]) == ("approved", "Friday works.")


def test_sent_mail_after_ui_approval_records_the_outcome(flow_env):
    env = flow_env
    draft_id = one_candidate_draft(env)
    env.flow.resolve(draft_id, "approve")
    sent = owner_reply(env, "200", "Friday works.", "<m100@x>")
    d = env.store.draft(draft_id)
    assert d["status"] == "approved"
    assert d["outcome_ref"] == sent["ref_key"]
    assert d["outcome_preview"] == "Friday works."
    assert "draft_outcome" in kinds(env)


def _fail_triage(env):
    env.llm.fail_next(1)  # the triage call is the first model call for a needs-reply message


def test_untriaged_incoming_skips_init_messages_answered_threads_and_spent_attempts(flow_env):
    env = flow_env
    _fail_triage(env)
    failed = env.arrive("100")
    ref = failed["ref_key"]
    assert env.store.triage_for(failed["id"]) is None
    assert [r["id"] for r in env.store.untriaged_incoming(since=0)] == [failed["id"]]
    assert env.store.untriaged_incoming(since=failed["sent_at"] + 1) == []  # too old
    # a message init indexed has no `incoming` event
    env.store._conn.execute("DELETE FROM event_log WHERE kind = 'incoming'")
    assert env.store.untriaged_incoming(since=0) == []
    env.store.log_event("evolution", "incoming", ref, {})
    assert len(env.store.untriaged_incoming(since=0)) == 1
    # spent attempts: the arrival's own failure counts as one
    env.store.log_event("reply", "draft_failed", ref, {})
    assert len(env.store.untriaged_incoming(since=0)) == 1
    env.store.log_event("reply", "draft_failed", ref, {})
    assert env.store.untriaged_incoming(since=0) == []
    assert len(env.store.untriaged_incoming(since=0, max_attempts=4)) == 1
    # an owner reply later in the thread answers it
    env.store._conn.execute("DELETE FROM event_log WHERE kind = 'draft_failed'")
    env.arrive(
        "200", sender="me@x.org", to="ann@x.org", folder=SENT, body="Friday works.",
        in_reply_to=failed["message_id"], date=1_700_000_900,
    )
    assert env.store.untriaged_incoming(since=0) == []


@pytest.fixture
def clock(flow_env, monkeypatch):
    """One injected clock for the flow and the event log, starting at the fixtures' time."""
    t = [1_700_001_000]
    monkeypatch.setattr("chatmaid.store.db._now", lambda: t[0])
    flow_env.flow.now = lambda: t[0]

    def advance(seconds):
        t[0] += seconds

    return advance


def test_retry_untriaged_triages_after_the_model_recovers(flow_env, clock):
    env = flow_env
    _fail_triage(env)
    row = env.arrive("100")
    assert env.store.triage_for(row["id"]) is None
    assert kinds(env).count("draft_failed") == 1
    clock(600)
    assert env.flow.retry_untriaged() == 1
    assert env.store.triage_for(row["id"])["category"] == "needs_reply"
    assert len(env.store.drafts(status=None)) == 1
    assert env.flow.retry_untriaged() == 0


def test_retry_gives_up_after_three_failures(flow_env, clock):
    env = flow_env
    _fail_triage(env)
    row = env.arrive("100")
    env.llm.fail_next(10)
    clock(600)
    assert env.flow.retry_untriaged() == 0
    clock(1200)
    assert env.flow.retry_untriaged() == 0
    assert kinds(env).count("draft_failed") == 3
    env.llm.fail_next(0)
    clock(86400)
    assert env.flow.retry_untriaged() == 0
    assert env.store.triage_for(row["id"]) is None


def test_retry_backs_off_600_seconds_per_attempt(flow_env, clock):
    env = flow_env
    _fail_triage(env)
    row = env.arrive("100")  # attempt 1 failed at t0
    env.llm.fail_next(10)
    clock(599)
    env.flow.retry_untriaged()
    assert kinds(env).count("draft_failed") == 1  # attempt 2 waits 10 minutes
    clock(1)
    env.flow.retry_untriaged()
    assert kinds(env).count("draft_failed") == 2  # attempt 2 failed just now
    env.flow.retry_untriaged()  # the same trigger's hook never retries what just failed
    clock(1199)
    env.flow.retry_untriaged()
    assert kinds(env).count("draft_failed") == 2  # attempt 3 waits 20 minutes
    env.llm.fail_next(0)
    clock(1)
    assert env.flow.retry_untriaged() == 1
    assert env.store.triage_for(row["id"]) is not None


def test_backoff_applies_to_the_retry_queue_not_the_status_list(flow_env, clock):
    env = flow_env
    _fail_triage(env)
    row = env.arrive("100")
    assert env.store.untriaged_incoming(since=0, now=1_700_001_000, backoff_s=600) == []
    assert [r["id"] for r in env.store.untriaged_incoming(since=0)] == [row["id"]]
    assert len(env.store.untriaged_incoming(since=0, now=1_700_001_600, backoff_s=600)) == 1


def test_retry_handles_at_most_five_messages_per_call(flow_env, clock):
    env = flow_env
    for i in range(7):
        _fail_triage(env)
        env.arrive(str(100 + i), sender=f"P{i} <p{i}@x.org>", subject=f"s{i}", date=1_700_000_100 + i)
    clock(600)
    env.llm.fail_next(100)
    env.flow.retry_untriaged()
    assert kinds(env).count("draft_failed") == 7 + 5


def test_retry_stops_between_messages_when_asked(flow_env, clock):
    env = flow_env
    for i in range(3):
        _fail_triage(env)
        env.arrive(str(100 + i), sender=f"P{i} <p{i}@x.org>", subject=f"s{i}", date=1_700_000_100 + i)
    clock(600)
    asked = []

    def should_stop():
        asked.append(1)
        return len(asked) > 1  # stop once the first message is done

    assert env.flow.retry_untriaged(should_stop=should_stop) == 1
    assert len([r for r in env.store.untriaged_incoming(since=0)]) == 2


def test_retry_logs_a_message_gone_from_the_mail_store(flow_env, clock, monkeypatch):
    env = flow_env
    _fail_triage(env)
    row = env.arrive("100")

    def gone(ref):
        raise KeyError(ref.key())

    monkeypatch.setattr(env.connector, "fetch", gone)
    clock(600)
    assert env.flow.retry_untriaged() == 0
    ev = [e for e in env.store.events(limit=50) if e["kind"] == "draft_failed"][0]
    assert json.loads(ev["payload_json"]) == {
        "error": "gone from the mail store", "message_id": row["id"],
    }


@pytest.mark.parametrize("error", [OSError("disk gone"), sqlite3.OperationalError("locked")])
def test_retry_counts_an_unreadable_message_as_an_attempt_and_goes_on(flow_env, clock, monkeypatch, error):
    env = flow_env
    _fail_triage(env)
    older = env.arrive("100", date=1_700_000_500)
    _fail_triage(env)
    newer = env.arrive("101", date=1_700_000_600, sender="Bob <bob@x.org>", subject="other")
    real = env.connector.fetch

    def flaky(ref):
        if ref.uid == "101":
            raise error
        return real(ref)

    monkeypatch.setattr(env.connector, "fetch", flaky)
    clock(600)
    assert env.flow.retry_untriaged() == 1  # the older one still gets its go
    assert env.store.triage_for(older["id"]) is not None
    fails = [e for e in env.store.events(limit=50)
             if e["kind"] == "draft_failed" and e["ref"] == newer["ref_key"]]
    assert len(fails) == 2
    assert json.loads(fails[0]["payload_json"])["message_id"] == newer["id"]
    assert str(error) in json.loads(fails[0]["payload_json"])["error"]


def test_retry_contains_a_non_llm_error_and_stops_after_three_attempts(flow_env, clock, monkeypatch):
    env = flow_env
    env.llm.fail_next(1)
    older = env.arrive("100", date=1_700_000_500)
    env.llm.fail_next(1)
    newer = env.arrive("101", date=1_700_000_600, sender="Bob <bob@x.org>", subject="other")
    real = env.flow.on_incoming

    def flaky(cid, msg):
        if msg.ref.uid == "101":
            raise RuntimeError("boom")
        return real(cid, msg)

    monkeypatch.setattr(env.flow, "on_incoming", flaky)
    clock(600)
    assert env.flow.retry_untriaged() == 1  # the older one is still triaged
    assert env.store.triage_for(older["id"]) is not None
    assert env.store.triage_for(newer["id"]) is None
    fails = [e for e in env.store.events(limit=50)
             if e["kind"] == "draft_failed" and e["ref"] == newer["ref_key"]]
    assert len(fails) == 2 and "RuntimeError" in fails[0]["payload_json"]
    clock(1200)
    env.flow.retry_untriaged()
    clock(86400)
    assert env.flow.retry_untriaged() == 0
    assert env.store.untriaged_incoming(since=0) == []  # three attempts spent


def test_draft_for_message_drafts_without_triage(flow_env):
    env = flow_env
    env.llm.responses["triage"] = '{"category":"informational","tier":"work","reason":"fyi"}'
    row = env.arrive("100")
    assert env.store.drafts(status=None) == []
    did = env.flow.draft_for_message(row["id"])
    d = env.store.draft(did)
    assert d["status"] == "pending" and d["message_id"] == row["id"]
    assert len(env.store.draft_versions(did)) == 2
    assert env.store.triage_for(row["id"])["category"] == "informational"  # unchanged
    ev = [e for e in env.store.events(limit=50) if e["kind"] == "draft"][0]
    assert json.loads(ev["payload_json"]) == {"draft_id": did, "candidates": 2, "on_demand": True}
    again = env.flow.draft_for_message(row["id"])  # supersedes the pending one
    assert env.store.draft(did)["status"] == "superseded"
    assert env.store.pending_draft_for(row["conversation_id"])["id"] == again


def test_draft_for_message_rejects_owner_messages(flow_env):
    env = flow_env
    env.arrive("100")
    own = owner_reply(env, "200", "Friday works", "<m100@x>")
    with pytest.raises(ValueError):
        env.flow.draft_for_message(own["id"])
    with pytest.raises(ValueError):
        env.flow.draft_for_message(99999)
