import json
import sqlite3

import pytest
from conftest import add_row, add_time_folder

from chatmaid.connectors.evolution.events import EventSource
from chatmaid.daemon import Watcher
from chatmaid.init.pipeline import InitOptions, InitPipeline, build_thread_index
from chatmaid.llm.fake import FakeLLM

ANN = dict(sender="ann@x.org", to="me@x.org")


def _pipeline(fake_env, store):
    return InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config)


def test_ingest_builds_conversations_and_identities(fake_env, store):
    st = _pipeline(fake_env, store).ingest(InitOptions(passes=("ingest",)))
    assert st.new_messages >= 3 and st.conversations >= 2
    assert store.counts()["identities"] >= 2 and store.owner_contact_id()


def test_ingest_threads_reply_into_same_conversation(fake_env, store):
    _pipeline(fake_env, store).ingest(InitOptions())
    convs = store.conversations()
    assert any(r["message_count"] == 2 for r in convs)


def test_ingest_is_resumable_and_idempotent(fake_env, store):
    p = _pipeline(fake_env, store)
    first = p.ingest(InitOptions())
    second = p.ingest(InitOptions())
    assert second.new_messages == 0 and store.counts()["messages"] == first.new_messages
    assert store.get_checkpoint("ingest", "a1/INBOX")["last_uid"] == 2


def test_ingest_since_filters_old_messages(fake_env, store):
    _pipeline(fake_env, store).ingest(InitOptions(since=1_650_000_000))
    assert all(
        r["sent_at"] >= 1_650_000_000
        for c in store.conversations()
        for r in store.messages_for(c["id"])
    )


def test_run_rejects_unknown_pass(fake_env, store):
    with pytest.raises(ValueError):
        _pipeline(fake_env, store).run(InitOptions(passes=("bogus",)))


def test_since_run_does_not_hide_older_rows_from_later_run(fake_env, store):
    p = _pipeline(fake_env, store)
    p.ingest(InitOptions(since=1_650_000_000))
    assert store.get_checkpoint("ingest", "a1/INBOX") == {"last_uid": 2, "since": 1_650_000_000}
    p.ingest(InitOptions())
    sent = {r["sent_at"] for c in store.conversations() for r in store.messages_for(c["id"])}
    assert 1_600_000_000 in sent
    assert store.get_checkpoint("ingest", "a1/INBOX")["since"] is None


def test_later_since_reuses_checkpoint(fake_env, store):
    p = _pipeline(fake_env, store)
    p.ingest(InitOptions(since=1_650_000_000))
    again = p.ingest(InitOptions(since=1_660_000_000))
    assert again.new_messages == 0
    assert store.get_checkpoint("ingest", "a1/INBOX")["since"] == 1_650_000_000  # untouched: nothing new was read


def test_time_mode_folder_gets_watermark_checkpoint(fake_env, store):
    add_time_folder(fake_env.db_path, "Exch", [("AQ1", 1_700_000_500), ("AQ2", 1_700_000_600)])
    _pipeline(fake_env, store).ingest(InitOptions())
    assert store.get_checkpoint("ingest", "a1/Exch")["last_uid"] == 1_700_000_600


def test_root_with_larger_id_then_reply_share_one_conversation(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="10", message_id="<zzz@gmail.com>", date=1_700_001_000, body="root", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="11", message_id="<aaa@other.org>", date=1_700_001_100, body="reply", in_reply_to="<zzz@gmail.com>", **ANN)
    _pipeline(fake_env, store).ingest(InitOptions())
    cid = store.conversation_id_for_key("email:a1:<zzz@gmail.com>")
    assert cid is not None and store.conversation_id_for_key("email:a1:<aaa@other.org>") is None
    assert {r["message_id"] for r in store.messages_for(cid)} == {"<zzz@gmail.com>", "<aaa@other.org>"}


def test_reply_then_root_share_one_conversation(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="10", message_id="<aaa@other.org>", date=1_700_001_100, body="reply", in_reply_to="<zzz@gmail.com>", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="11", message_id="<zzz@gmail.com>", date=1_700_001_000, body="root", **ANN)
    _pipeline(fake_env, store).ingest(InitOptions())
    cid = store.conversation_id_for_key("email:a1:<aaa@other.org>")
    assert cid is not None and store.conversation_id_for_key("email:a1:<zzz@gmail.com>") is None
    assert {r["message_id"] for r in store.messages_for(cid)} == {"<zzz@gmail.com>", "<aaa@other.org>"}


def test_rebuilt_thread_index_reproduces_every_conversation_key(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="10", message_id="<r1@x>", date=1_700_001_000, body="one", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="11", message_id="<r2@x>", date=1_700_001_100, body="two", in_reply_to="<r1@x>", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="12", message_id="<q1@x>", date=1_700_001_200, body="three", in_reply_to="<missing@x>", **ANN)
    _pipeline(fake_env, store).ingest(InitOptions())
    index = build_thread_index(store, "email", "a1")
    checked = 0
    for conv in store.conversations(exclude_bulk=False):
        for row in store.messages_for(conv["id"]):
            if row["account"] == "a1" and row["message_id"]:
                refs = json.loads(row["references_json"])
                assert f"email:a1:{index.key_for(row['message_id'], refs, 'fb')}" == conv["key"]
                checked += 1
    assert checked >= 6


def test_message_referencing_two_threads_merges_their_conversations(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="10", message_id="<t1@x>", date=1_700_001_000, body="thread one", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="11", message_id="<t2@x>", date=1_700_001_100, body="thread two", **ANN)
    p = _pipeline(fake_env, store)
    p.ingest(InitOptions())
    keep = store.conversation_id_for_key("email:a1:<t1@x>")
    drop = store.conversation_id_for_key("email:a1:<t2@x>")
    assert keep is not None and drop is not None and keep != drop
    add_row(fake_env.db_path, "INBOX", uid="12", message_id="<t3@x>", date=1_700_001_200, body="both", references=("<t1@x>", "<t2@x>"), **ANN)
    p.ingest(InitOptions())
    assert store.conversation(drop) is None
    assert {r["message_id"] for r in store.messages_for(keep)} == {"<t1@x>", "<t2@x>", "<t3@x>"}
    assert store.conversation(keep)["message_count"] == 3


def test_ingest_title_keeps_subject_case_and_is_set_once(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="10", message_id="<p1@x>", date=1_700_001_000, body="root", subject="Re: Project Alpha", **ANN)
    add_row(fake_env.db_path, "INBOX", uid="11", message_id="<p2@x>", date=1_700_001_100, body="reply", subject="Fwd: project alpha (updated)", in_reply_to="<p1@x>", **ANN)
    _pipeline(fake_env, store).ingest(InitOptions())
    cid = store.conversation_id_for_key("email:a1:<p1@x>")
    assert store.conversation(cid)["title"] == "Project Alpha"


def test_since_checkpoint_covers_rows_the_filter_excluded(fake_env, store):
    add_row(fake_env.db_path, "INBOX", uid="3", message_id="<old3@x>", date=1_600_000_500, body="old but newest uid", **ANN)
    _pipeline(fake_env, store).ingest(InitOptions(since=1_650_000_000))
    assert store.get_checkpoint("ingest", "a1/INBOX")["last_uid"] == 3
    assert not store.has_message_id("email", "a1", "<old3@x>")
    watcher = Watcher(store, fake_env.connector, EventSource([fake_env.db_path], enable_dbus=False))
    assert watcher.handle_trigger("timer") == 0 and store.events() == []


def test_ingest_batch_failure_rolls_back_and_retries_the_folder(fake_env, store, monkeypatch):
    real = store.upsert_message

    def locked(cid, msg, sid):
        if msg.message_id == "<a@x>":
            raise sqlite3.OperationalError("database is locked")
        return real(cid, msg, sid)

    monkeypatch.setattr(store, "upsert_message", locked)
    _pipeline(fake_env, store).ingest(InitOptions())
    assert store.get_checkpoint("ingest", "a1/INBOX") is None
    assert not store.has_message_id("email", "a1", "<a@x>")
    assert store.get_checkpoint("ingest", "a1/News")["last_uid"] == 4
    monkeypatch.setattr(store, "upsert_message", real)
    _pipeline(fake_env, store).ingest(InitOptions())
    assert store.has_message_id("email", "a1", "<a@x>")
    assert store.get_checkpoint("ingest", "a1/INBOX")["last_uid"] == 2
