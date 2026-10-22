import pytest
from conftest import add_time_folder

from chatmaid.init.pipeline import InitOptions, InitPipeline
from chatmaid.llm.fake import FakeLLM


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


def test_run_rejects_unimplemented_pass(fake_env, store):
    with pytest.raises(NotImplementedError):
        _pipeline(fake_env, store).run(InitOptions(passes=("style",)))


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
