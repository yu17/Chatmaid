import dataclasses
import sqlite3
from importlib import resources

import pytest

from chatmaid.model import SourceRef
from chatmaid.store import Store


@pytest.fixture
def ids(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "t", "thread")
    sid = store.upsert_identity("email", "sender@x.org", "Sender")
    mid = store.upsert_message(cid, sample_message, sid)
    return cid, mid


def test_v1_database_is_upgraded_to_v2(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(resources.files("chatmaid.store").joinpath("schema_v1.sql").read_text())
    conn.close()
    s = Store(path, embedding_dim=4)
    try:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == Store.SCHEMA_VERSION
        assert s._conn.execute("SELECT name FROM sqlite_master WHERE name='draft'").fetchone()
    finally:
        s.close()


def test_triage_upsert_replaces(store, ids):
    _, mid = ids
    store.add_triage(mid, "ignore", "r1", decided_by="rule")
    store.add_triage(mid, "reply", "r2", decided_by="llm", model="m")
    row = store.triage_for(mid)
    assert row["category"] == "reply" and row["decided_by"] == "llm" and row["model"] == "m"
    assert store._conn.execute("SELECT COUNT(*) FROM triage").fetchone()[0] == 1


def test_draft_lifecycle(store, ids):
    cid, mid = ids
    draft_id = store.create_draft(cid, mid, language="en", context={"a": 1})
    assert store.draft(draft_id)["status"] == "pending"
    store.add_draft_version(draft_id, round=1, candidate=1, origin="local", text="a", rationale=None)
    store.add_draft_version(draft_id, round=1, candidate=2, origin="local", text="b", rationale="r")
    assert [v["candidate"] for v in store.draft_versions(draft_id)] == [1, 2]
    assert store.next_round(draft_id) == 2
    assert store.pending_draft_for(cid)["id"] == draft_id
    store.set_draft_status(draft_id, "approved", final_text="x")
    row = store.draft(draft_id)
    assert row["final_text"] == "x" and row["outcome_ref"] is None
    assert store.pending_draft_for(cid) is None
    assert len(store.drafts(status=None)) == 1
    assert store.drafts() == []


def test_next_round_is_one_without_versions(store, ids):
    cid, mid = ids
    assert store.next_round(store.create_draft(cid, mid, language="en", context={})) == 1


def test_cloud_call_lifecycle(store):
    call_id = store.add_cloud_call(
        draft_id=None, backend="b", model=None, payload="p", mapping={"<P1>": "Bob"}, comment=None
    )
    row = store.cloud_call(call_id)
    assert row["status"] == "proposed"
    assert '"Bob"' in row["mapping_json"]
    assert row["finished_at"] is None
    store.set_cloud_call_status(call_id, "sent", consented_at=1, response="r", response_hash="h")
    row = store.cloud_call(call_id)
    assert row["finished_at"] is not None and row["consented_at"] == 1 and row["response"] == "r"
    assert [r["id"] for r in store.cloud_calls()] == [call_id]


def test_memories_for_filters_and_orders(store, ids):
    cid, _ = ids
    a = store.contact_id_of(store.upsert_identity("email", "sender@x.org", None))
    b = store.contact_id_of(store.upsert_identity("email", "other@x.org", "O"))
    m1 = store.add_memory(kind="fact", text="1", source_ref="s", observed_at=None, contact_id=a)
    m2 = store.add_memory(kind="fact", text="2", source_ref="s", observed_at=5, conversation_id=cid)
    store.add_memory(kind="fact", text="3", source_ref="s", observed_at=1, contact_id=b)
    assert [r["id"] for r in store.memories_for(contact_id=a)] == [m1]
    assert [r["id"] for r in store.memories_for(contact_id=a, conversation_id=cid)] == [m2, m1]
    assert store.memories_for() == []


def test_set_conversation_tier_only_if_unset(store, ids):
    cid, _ = ids
    store.set_conversation_tier(cid, "casual")
    store.set_conversation_tier(cid, "formal")
    assert store.conversation(cid)["tier"] == "casual"
    store.set_conversation_tier(cid, "formal", only_if_unset=False)
    assert store.conversation(cid)["tier"] == "formal"


def test_lookups(store, ids):
    _, mid = ids
    row = store.message(mid)
    assert store.message_by_ref(row["ref_key"])["id"] == mid
    assert store.message(999) is None and store.message_by_ref("nope") is None
    contact_id = store.contact_id_of(row["sender_identity_id"])
    assert store.contact(contact_id) is not None
    assert [i["address"] for i in store.identities_of(contact_id)] == ["sender@x.org"]


def test_counts_include_drafts_and_cloud_calls(store, ids):
    cid, mid = ids
    assert store.counts()["drafts"] == 0 and store.counts()["cloud_calls"] == 0
    store.create_draft(cid, mid, language="en", context={})
    store.add_cloud_call(draft_id=None, backend="b", model=None, payload="p", mapping={}, comment=None)
    assert store.counts()["drafts"] == 1 and store.counts()["cloud_calls"] == 1


def test_merge_moves_drafts_and_keeps_one_pending(store, sample_message):
    sid = store.upsert_identity("email", "sender@x.org", "Sender")
    keep = store.upsert_conversation("email", "k1", "t", "thread")
    drop = store.upsert_conversation("email", "k2", "t", "thread")
    m1 = store.upsert_message(keep, sample_message, sid)
    other = dataclasses.replace(
        sample_message, ref=SourceRef("evolution", "acct1", "INBOX", "8"), message_id="<m8@x.org>"
    )
    m2 = store.upsert_message(drop, other, sid)
    older = store.create_draft(drop, m2, language="en", context={})
    newer = store.create_draft(keep, m1, language="en", context={})
    with store.transaction():
        store.merge_conversations(keep, drop)
    assert store.conversation(drop) is None
    assert {store.draft(d)["conversation_id"] for d in (older, newer)} == {keep}
    assert store.draft(older)["status"] == "superseded"
    assert store.draft(newer)["status"] == "pending"
    assert store.pending_draft_for(keep)["id"] == newer
