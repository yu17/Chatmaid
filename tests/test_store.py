import dataclasses

from chatmaid.model import make_preview
from chatmaid.store import Store


def test_schema_applied(tmp_path):
    s = Store(tmp_path / "i.sqlite", embedding_dim=4)
    try:
        assert s.counts() == {"identities": 0, "contacts": 0, "conversations": 0, "messages": 0, "memories": 0, "embeddings": 0, "events": 0}
    finally:
        s.close()


def test_owner_identities_share_one_contact(store):
    a = store.upsert_identity("email", "a@x.org", "A", is_owner=True)
    b = store.upsert_identity("email", "b@x.org", "B", is_owner=True)
    assert a != b and store.owner_contact_id() == store.contact_id_of(a) == store.contact_id_of(b)


def test_upsert_identity_is_idempotent(store):
    assert store.upsert_identity("email", "c@x.org", None) == store.upsert_identity("email", "c@x.org", "C")


def test_upsert_message_unique_on_ref_and_never_stores_text(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", sample_message.sender.email, None)
    m1 = store.upsert_message(cid, sample_message, sid)
    m2 = store.upsert_message(cid, sample_message, sid)
    assert m1 == m2
    row = store.messages_for(cid)[0]
    assert row["preview"] == make_preview(sample_message.text) and "text" not in row.keys()
    assert store.has_message_id("email", sample_message.ref.account, sample_message.message_id)


def test_conversation_counts_and_last_message(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", sample_message.sender.email, None)
    store.upsert_message(cid, sample_message, sid)
    row = store.conversations()[0]
    assert row["message_count"] == 1 and row["last_message_at"] == sample_message.sent_at


def test_checkpoint_roundtrip(store):
    assert store.get_checkpoint("ingest", "acct") is None
    store.set_checkpoint("ingest", "acct", {"INBOX": "42"})
    assert store.get_checkpoint("ingest", "acct") == {"INBOX": "42"}


def test_embedding_search_returns_nearest(store):
    store.upsert_embedding("memory", 1, "h1", [1.0, 0.0, 0.0, 0.0])
    store.upsert_embedding("memory", 2, "h2", [0.0, 1.0, 0.0, 0.0])
    assert store.search_embeddings([0.9, 0.1, 0.0, 0.0], k=1)[0][:2] == ("memory", 1)


def test_make_preview_collapses_and_truncates():
    assert make_preview("a\n\n  b" + "x" * 500) == ("a b" + "x" * 500)[:200]


def test_embedding_upsert_replaces_and_kind_filter(store):
    store.upsert_embedding("memory", 1, "h1", [1.0, 0.0, 0.0, 0.0])
    store.upsert_embedding("memory", 1, "h2", [0.0, 1.0, 0.0, 0.0])
    store.upsert_embedding("message", 1, "h3", [0.0, 0.0, 1.0, 0.0])
    assert store.counts()["embeddings"] == 2
    assert store.search_embeddings([0.0, 1.0, 0.0, 0.0], k=1)[0][:2] == ("memory", 1)
    assert [r[:2] for r in store.search_embeddings([0.0, 0.0, 1.0, 0.0], k=5, kind="message")] == [("message", 1)]


def test_misc_store_methods(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "s@x.org", None)
    msg = dataclasses.replace(sample_message, in_reply_to="<p@x.org>", references=("<r@x.org>",))
    mid = store.upsert_message(cid, msg, sid)
    assert store.message_ids_for_account("email", "acct1") == [("<m7@x.org>", ("<r@x.org>", "<p@x.org>"))]
    assert store.message_refs_for(cid) == [(msg.ref, mid)]
    assert len(store.conversations(needing_summary=True)) == 1
    store.set_conversation_summary(cid, "sum", events=[{"a": 1}])
    assert store.conversations(needing_summary=True) == [] and len(store.conversations(needing_summary=False)) == 1
    store.add_participant(cid, store.contact_id_of(sid))
    store.add_memory(kind="fact", text="t", source_ref="r", observed_at=None)
    store.set_style_profile("en", "friend", "casual", ["a"])
    assert store.style_profile("en", "friend") == "casual" and store.style_profile("de", "x") is None
    eid = store.log_event("s", "k", "r", {"x": 1})
    assert store.events()[0]["id"] == eid
    c = store.counts()
    assert c["messages"] == 1 and c["memories"] == 1 and c["events"] == 1
