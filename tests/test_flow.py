import json

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
