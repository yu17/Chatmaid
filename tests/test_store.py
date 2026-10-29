import dataclasses
import sqlite3
from importlib import resources

import pytest
import sqlite_vec

from chatmaid.model import make_preview
from chatmaid.store import Store


def test_schema_applied(tmp_path):
    s = Store(tmp_path / "i.sqlite", embedding_dim=4)
    try:
        assert s.counts() == {"identities": 0, "contacts": 0, "conversations": 0, "messages": 0, "memories": 0, "embeddings": 0, "events": 0, "drafts": 0, "cloud_calls": 0, "senders": 0}
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
    assert store.has_ref(sample_message.ref.key()) and not store.has_ref("evolution:nope")


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


def test_preview_is_clamped_to_200(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "s@x.org", None)
    store.upsert_message(cid, dataclasses.replace(sample_message, preview="y" * 500), sid)
    assert len(store.messages_for(cid)[0]["preview"]) == 200


def test_conversation_bulk_follows_messages(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "s@x.org", None)
    store.upsert_message(cid, dataclasses.replace(sample_message, bulk=True), sid)
    assert store.conversations()[0:1] == [] and len(store.conversations(exclude_bulk=False)) == 1
    assert store.conversations(exclude_bulk=False)[0]["bulk"] == 1
    other = dataclasses.replace(
        sample_message, bulk=False, message_id="<m8@x.org>", ref=dataclasses.replace(sample_message.ref, uid="8")
    )
    store.upsert_message(cid, other, sid)
    assert store.conversations()[0]["bulk"] == 0


def test_clear_checkpoints(store):
    store.set_checkpoint("ingest", "a", {"last_uid": 1})
    store.set_checkpoint("ingest", "b", {"last_uid": 2})
    store.set_checkpoint("other", "a", {"x": 1})
    assert store.clear_checkpoints("ingest") == 2
    assert store.checkpoints("ingest") == {}
    assert store.get_checkpoint("other", "a") == {"x": 1}


def test_participant_contact_ids(store):
    cid = store.upsert_conversation("email", "k-part", "t", "thread")
    a = store.contact_id_of(store.upsert_identity("email", "a@x.org", "A", is_owner=False))
    b = store.contact_id_of(store.upsert_identity("email", "b@x.org", "B", is_owner=False))
    store.add_participant(cid, b)
    store.add_participant(cid, a)
    assert store.participant_contact_ids(cid) == sorted([a, b])


def test_set_tracked_excludes_from_needing_summary(store):
    cid = store.upsert_conversation("email", "k-trk", "t", "thread")
    assert [r["id"] for r in store.conversations(needing_summary=True)] == [cid]
    store.set_tracked(cid, False)
    assert store.conversations(needing_summary=True) == []
    assert [r["id"] for r in store.conversations()] == [cid]
    store.set_tracked(cid, True)
    assert len(store.conversations(needing_summary=True)) == 1


def test_search_messages_filters_and_accessors(ingested_env):
    s = ingested_env.store
    rows = s.search_messages(text="hello")
    assert rows and all("hello" in (r["preview"] or "").lower() or "hello" in (r["subject"] or "").lower() for r in rows)
    assert s.search_messages(sender="ann@x.org")
    assert s.search_messages(sender="nobody-here") == []
    newest = s.search_messages()[0]["sent_at"]
    assert s.search_messages(since=newest + 1) == []
    assert s.search_messages(until=newest) != s.search_messages()
    cid = s.search_messages()[0]["conversation_id"]
    assert s.conversation(cid)["id"] == cid and s.conversation(99999) is None
    assert s.memory(99999) is None
    assert s.participant_labels(cid)
    assert s.identity_label(None) == "?"


def _add(store, cid, sid, sample_message, uid, subject, sent_at):
    from dataclasses import replace

    from chatmaid.model import SourceRef

    ref = SourceRef("evolution", "acct1", "INBOX", uid)
    store.upsert_message(
        cid,
        replace(sample_message, ref=ref, message_id=f"<{uid}@x>", subject=subject,
                text=subject, preview=subject, sent_at=sent_at),
        sid,
    )


def test_messages_for_newest_first(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "a@x.org", None)
    for i in range(4):
        _add(store, cid, sid, sample_message, str(i), f"m{i}", 1000 + i)
    rows = store.messages_for(cid, limit=3, newest_first=True)
    assert [r["sent_at"] for r in rows] == [1003, 1002, 1001]
    assert [r["sent_at"] for r in store.messages_for(cid, limit=3)] == [1000, 1001, 1002]


def test_search_messages_escapes_like_wildcards(store, sample_message):
    cid = store.upsert_conversation("email", "k1", "Hi", "thread")
    sid = store.upsert_identity("email", "a@x.org", None)
    _add(store, cid, sid, sample_message, "1", "50% off", 1000)
    _add(store, cid, sid, sample_message, "2", "500 off", 1001)
    _add(store, cid, sid, sample_message, "3", "foo_bar", 1002)
    _add(store, cid, sid, sample_message, "4", "fooxbar", 1003)
    assert [r["subject"] for r in store.search_messages(text="50% off")] == ["50% off"]
    assert [r["subject"] for r in store.search_messages(text="foo_bar")] == ["foo_bar"]


def test_transaction_commits_and_rolls_back(store):
    with store.transaction():
        store.log_event("t", "ok", "r1", {})
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.log_event("t", "lost", "r2", {})
            raise RuntimeError("x")
    assert [e["kind"] for e in store.events()] == ["ok"]


def test_conversation_title_is_set_only_on_insert(store):
    cid = store.upsert_conversation("email", "k1", "Plan B", "thread")
    assert store.upsert_conversation("email", "k1", "Re: Plan B", "thread") == cid
    assert store.conversation(cid)["title"] == "Plan B"
    assert store.conversation_id_for_key("k1") == cid and store.conversation_id_for_key("nope") is None


def test_merge_conversations(store, sample_message):
    keep = store.upsert_conversation("email", "k-keep", "Keep", "thread")
    drop = store.upsert_conversation("email", "k-drop", "Drop", "thread")
    a = store.upsert_identity("email", "a@x.org", "A")
    b = store.upsert_identity("email", "b@x.org", "B", is_owner=True)
    ca, cb = store.contact_id_of(a), store.contact_id_of(b)
    store.add_participant(keep, ca)
    store.add_participant(drop, cb)
    store.upsert_message(keep, sample_message, a)
    other = dataclasses.replace(
        sample_message, ref=dataclasses.replace(sample_message.ref, uid="8"), message_id="<m8@x.org>",
        sent_at=1_700_000_500, is_owner=True,
    )
    store.upsert_message(drop, other, b)
    store.set_conversation_summary(keep, "stale", events=[{"a": 1}])
    store.upsert_embedding("conversation", drop, "h", [1.0, 0.0, 0.0, 0.0])
    mem = store.add_memory(kind="fact", text="t", source_ref="r", observed_at=None, conversation_id=drop)
    store.set_checkpoint("digest", str(drop), {"done": True})
    with store.transaction():
        assert store.merge_conversations(keep, drop) == 1
    assert store.conversation(drop) is None and store.conversation_id_for_key("k-drop") is None
    row = store.conversation(keep)
    assert row["message_count"] == 2 and row["last_message_at"] == 1_700_000_500
    assert row["last_owner_at"] == 1_700_000_500 and row["summary"] is None and row["events_json"] is None
    assert {r["message_id"] for r in store.messages_for(keep)} == {"<m7@x.org>", "<m8@x.org>"}
    assert store.participant_contact_ids(keep) == sorted([ca, cb])
    assert store.memory(mem)["conversation_id"] == keep
    assert store.get_checkpoint("digest", str(drop)) is None
    assert store.embedding_hash("conversation", drop) is None and store.counts()["embeddings"] == 0
    assert store.merge_conversations(keep, keep) == 0


def test_merge_resets_tracked(store):
    keep = store.upsert_conversation("email", "k1", "t", "thread")
    drop = store.upsert_conversation("email", "k2", "t", "thread")
    store.set_tracked(keep, False)
    with store.transaction():
        store.merge_conversations(keep, drop)
    assert store.conversation(keep)["tracked"] == 1


def test_store_reports_current_schema_version(store):
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == Store.SCHEMA_VERSION


def test_interrupted_upgrade_is_retried_cleanly(tmp_path, monkeypatch):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(resources.files("chatmaid.store").joinpath("schema_v1.sql").read_text())
    conn.close()
    state = {"raised": False}

    class Flaky(sqlite3.Connection):
        def executescript(self, script):
            if "user_version = 2" in script and not state["raised"]:
                state["raised"] = True
                super().executescript(script.split("CREATE TABLE draft (")[0])  # half applied
                raise sqlite3.OperationalError("disk I/O error")
            return super().executescript(script)

    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: real_connect(*a, factory=Flaky, **k))
    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        Store(path, embedding_dim=4)
    s = Store(path, embedding_dim=4)
    try:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == Store.SCHEMA_VERSION
        assert s._conn.execute("SELECT name FROM sqlite_master WHERE name='draft'").fetchone()
    finally:
        s.close()


def test_v3_upgrade_drops_vectors_and_keeps_summaries(tmp_path):
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True); sqlite_vec.load(conn)
    for n in (1, 2):
        conn.executescript(resources.files("chatmaid.store").joinpath(f"schema_v{n}.sql").read_text())
    conn.execute("CREATE VIRTUAL TABLE vec_embedding USING vec0(item_id INTEGER PRIMARY KEY, embedding float[4])")
    conn.execute("INSERT INTO conversation (platform, key, title, kind, summary) VALUES ('email','k','t','thread','S')")
    conn.execute("INSERT INTO embedding_item (kind, ref_id, text_hash) VALUES ('conversation', 1, 'h')")
    conn.execute("INSERT INTO vec_embedding (item_id, embedding) VALUES (1, ?)", (sqlite_vec.serialize_float32([1,0,0,0]),))
    conn.commit(); conn.close()
    s = Store(path, embedding_dim=4)
    try:
        assert s.counts()["embeddings"] == 0 and s.embedding_hash("conversation", 1) is None
        assert s.conversation(1)["summary"] == "S" and s.conversation(1)["category"] is None
        s.upsert_embedding("conversation", 1, "h", [1.0, 0, 0, 0])  # the partitioned table works
        assert s.search_embeddings([1.0, 0, 0, 0], k=1, kind="conversation")[0][:2] == ("conversation", 1)
        indexes = {r["name"] for r in s._conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"idx_message_sender", "idx_participant_contact"} <= indexes  # refresh_sender_stats
    finally:
        s.close()


def test_search_embeddings_kind_partition_ignores_other_kinds(store):
    for i in range(1, 201):
        store.upsert_embedding("memory", i, f"h{i}", [1.0, 0.0, 0.0, 0.0])
    store.upsert_embedding("owner_message", 7, "o", [0.0, 0.0, 1.0, 0.0])
    assert store.search_embeddings([1.0, 0, 0, 0], k=1, kind="owner_message") == [("owner_message", 7, pytest.approx(1.4142, abs=1e-3))]


def test_set_conversation_summary_category_and_merge_clears_it(store, sample_message):
    a = store.upsert_conversation("email", "ka", "A", "thread")
    b = store.upsert_conversation("email", "kb", "B", "thread")
    store.set_conversation_summary(a, "S", category="event")
    store.set_conversation_summary(a, "S2")  # COALESCE keeps the category
    assert store.conversation(a)["category"] == "event"
    assert [c["id"] for c in store.conversations(category="event", exclude_bulk=False)] == [a]
    store.set_conversation_category(a, None)
    assert store.conversation(a)["category"] is None
    store.set_conversation_category(a, "promotional")
    with store.transaction():
        store.merge_conversations(a, b)
    assert store.conversation(a)["category"] is None
