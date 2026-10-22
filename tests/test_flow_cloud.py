import hashlib
import json

import pytest

from chatmaid.cloud.base import CloudError
from chatmaid.cloud.fake import FakeCloud


def setup(env, text="Thanks Ann, Friday works."):
    env.llm.responses["reply_draft"] = json.dumps(
        {"candidates": [{"text": text, "rationale": "r"}, {"text": text + " ok", "rationale": "r"}],
         "lookup": ""}
    )
    env.arrive("100")
    return env.store.drafts()[0]["id"]


def with_backend(env, backend):
    env.flow.backend_factory = lambda name: backend


def test_propose_redacts_participants_and_owner(flow_env):
    env = flow_env
    did = setup(env, "Thanks Ann (ann@x.org, me@x.org), Friday works.")
    p = env.flow.propose_cloud(did)
    payload = env.store.cloud_call(p.call_id)["payload"]
    assert payload == p.payload
    for s in ("Ann", "ann@x.org", "me@x.org"):
        assert s not in payload
    assert "[EMAIL_" in payload or "[PERSON_" in payload
    row = env.store.cloud_call(p.call_id)
    assert row["status"] == "proposed"
    assert "ann@x.org" in json.loads(row["mapping_json"]).values()


def test_payload_contains_sections_and_comment(flow_env):
    env = flow_env
    did = setup(env)
    p = env.flow.propose_cloud(did, comment="shorter")
    assert "## Owner's feedback\nshorter" in p.payload
    assert "## Message being answered" in p.payload
    assert "## Previous draft\n" in p.payload
    assert env.store.cloud_call(p.call_id)["comment"] == "shorter"


def test_send_uses_stored_payload_verbatim_and_rehydrates(flow_env):
    env = flow_env
    did = setup(env)
    backend = FakeCloud("codex", "m", {"text": "[PERSON_1], Friday works.", "rationale": "ok"})
    with_backend(env, backend)
    p = env.flow.propose_cloud(did, comment="shorter")
    assert backend.prompts == []
    vid = env.flow.send_cloud(p.call_id)
    call = env.store.cloud_call(p.call_id)
    assert backend.prompts == [call["payload"]]
    v = env.flow._version(did, vid)
    assert v["origin"] == "cloud:codex"
    assert v["text"] == "Ann, Friday works."
    assert v["comment"] == "shorter"
    assert v["cloud_call_id"] == p.call_id
    assert v["round"] == 2
    assert call["status"] == "sent"
    assert call["consented_at"] == 1_700_001_000
    response = json.dumps(backend.response, ensure_ascii=False)
    assert call["response"] == response
    assert call["response_hash"] == hashlib.sha256(response.encode()).hexdigest()
    assert env.store.draft(did)["status"] == "pending"


def test_send_reports_unknown_placeholders(flow_env):
    env = flow_env
    did = setup(env)
    with_backend(env, FakeCloud("codex", "m", {"text": "[PERSON_7] ok", "rationale": "fine"}))
    p = env.flow.propose_cloud(did)
    vid = env.flow.send_cloud(p.call_id)
    v = env.flow._version(did, vid)
    assert v["text"] == "[PERSON_7] ok"
    assert v["rationale"].endswith("[unknown placeholders: [PERSON_7]]")
    ev = next(e for e in env.store.events(limit=20) if e["kind"] == "sent")
    assert json.loads(ev["payload_json"])["unknown_placeholders"] == ["[PERSON_7]"]


def test_decline_marks_declined_and_adds_no_version(flow_env):
    env = flow_env
    did = setup(env)
    backend = FakeCloud()
    with_backend(env, backend)
    p = env.flow.propose_cloud(did)
    env.flow.decline_cloud(p.call_id)
    assert env.store.cloud_call(p.call_id)["status"] == "declined"
    assert len(env.store.draft_versions(did)) == 2
    assert backend.prompts == []
    assert any(e["kind"] == "declined" for e in env.store.events(limit=20))
    with pytest.raises(ValueError):
        env.flow.send_cloud(p.call_id)


def test_send_twice_raises(flow_env):
    env = flow_env
    did = setup(env)
    p = env.flow.propose_cloud(did)
    env.flow.send_cloud(p.call_id)
    with pytest.raises(ValueError):
        env.flow.send_cloud(p.call_id)
    with pytest.raises(ValueError):
        env.flow.decline_cloud(p.call_id)
    with pytest.raises(ValueError):
        env.flow.send_cloud(9999)


def test_cloud_failure_marks_failed_and_draft_stays_pending(flow_env):
    env = flow_env
    did = setup(env)
    with_backend(env, FakeCloud("codex", "m", CloudError("Not logged in")))
    p = env.flow.propose_cloud(did)
    with pytest.raises(CloudError):
        env.flow.send_cloud(p.call_id)
    call = env.store.cloud_call(p.call_id)
    assert call["status"] == "failed"
    assert "Not logged in" in call["error"]
    assert env.store.draft(did)["status"] == "pending"
    assert len(env.store.draft_versions(did)) == 2
    assert any(e["kind"] == "failed" for e in env.store.events(limit=20))


def test_malformed_backend_output_is_a_failure(flow_env):
    env = flow_env
    did = setup(env)
    with_backend(env, FakeCloud("codex", "m", {"nope": 1}))
    p = env.flow.propose_cloud(did)
    with pytest.raises(CloudError, match="no text"):
        env.flow.send_cloud(p.call_id)
    call = env.store.cloud_call(p.call_id)
    assert call["status"] == "failed"
    assert "no text" in call["error"]
    assert len(env.store.draft_versions(did)) == 2


def test_watcher_never_calls_cloud(flow_env):
    env = flow_env
    backend = FakeCloud()
    with_backend(env, backend)
    env.arrive("100")
    assert backend.prompts == []
    assert env.store.cloud_calls() == []


def test_propose_on_non_pending_raises(flow_env):
    env = flow_env
    did = setup(env)
    env.flow.resolve(did, "dismiss")
    with pytest.raises(ValueError):
        env.flow.propose_cloud(did)
    assert env.store.cloud_calls() == []
