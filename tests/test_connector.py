import dataclasses
import logging
import sqlite3

import pytest

from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.model import SourceRef


def test_iter_messages_dedups_across_folders(fake_env):
    msgs = [m for m in fake_env.connector.iter_messages() if m.ref.account == "a1"]
    assert [m.ref.folder for m in msgs if m.message_id == "<a@x>"] == ["INBOX"]
    assert "[Gmail]/All Mail" not in [m.ref.folder for m in msgs]


def test_folder_order_sent_inbox_rest(fake_env):
    folders = []
    for m in fake_env.connector.iter_messages():
        if m.ref.account == "a1" and m.ref.folder not in folders:
            folders.append(m.ref.folder)
    assert folders == ["[Gmail]/Sent Mail", "INBOX", "News"]


def test_skip_folders_are_not_read(fake_env):
    cfg = dataclasses.replace(fake_env.cfg, skip_folders=("News", "[Gmail]/All Mail"))
    c = EvolutionConnector(cfg, fake_env.accounts, fake_env.owner)
    try:
        folders = {m.ref.folder for m in c.iter_messages()}
        refs, _ = c.scan_new({})
    finally:
        c.close()
    assert "News" not in folders and "[Gmail]/All Mail" not in folders
    assert all(r.folder not in cfg.skip_folders for r in refs)


def test_sent_folder_marks_owner(fake_env):
    m = next(m for m in fake_env.connector.iter_messages() if m.ref.folder == "[Gmail]/Sent Mail")
    assert m.is_owner and m.sender.email == "me@x.org"


def test_bulk_and_since(fake_env):
    msgs = list(fake_env.connector.iter_messages(since=1_700_000_150))
    news = [m for m in msgs if m.ref.folder == "News"]
    assert news and news[0].bulk
    assert all(m.sent_at >= 1_700_000_150 for m in msgs)


def test_fetch_missing_body(fake_env):
    m = fake_env.connector.fetch(SourceRef("evolution", "a1", "INBOX", "2"))
    assert m.body_missing and m.text == "" and m.subject == "row subject" and m.sent_at == 1700000000


def test_fetch_with_body_and_local(fake_env):
    m = fake_env.connector.fetch(SourceRef("evolution", "a1", "INBOX", "1"))
    assert not m.body_missing and "hello there" in m.text and m.message_id == "<a@x>"
    local = next(x for x in fake_env.connector.iter_messages() if x.ref.account == "local")
    again = fake_env.connector.fetch(local.ref)
    assert again.text.strip() == "sent from local" and again.is_owner


@pytest.mark.parametrize(
    "account,folder,uid",
    [("a1", "INBOX", "99"), ("a1", "Nope", "1"), ("zz", "INBOX", "1"), ("local", "Sent", "nope")],
)
def test_fetch_missing_raises_keyerror(fake_env, account, folder, uid):
    with pytest.raises(KeyError):
        fake_env.connector.fetch(SourceRef("evolution", account, folder, uid))


def test_scan_new_returns_only_newer_uids(fake_env):
    refs, state = fake_env.connector.scan_new({})
    assert len(refs) >= 3 and state["a1/INBOX"] == 2
    refs2, _ = fake_env.connector.scan_new(state)
    assert refs2 == []


def test_scan_new_skips_folder_without_table(fake_env, caplog):
    conn = sqlite3.connect(fake_env.db_path)
    conn.execute("DROP TABLE messages_4")
    conn.commit()
    conn.close()
    with caplog.at_level(logging.WARNING):
        refs, state = fake_env.connector.scan_new({})
    assert "a1/News" not in state and refs
    assert caplog.records


def test_local_maildir_sent_is_included(fake_env):
    assert any(m.ref.account == "local" and m.is_owner for m in fake_env.connector.iter_messages())


def test_iter_messages_after_skips_checkpointed_uids(fake_env):
    msgs = fake_env.connector.iter_messages(after={"a1/INBOX": 1})
    inbox = [m.ref.uid for m in msgs if m.ref.folder == "INBOX"]
    assert inbox == ["2"]
