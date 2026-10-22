import sqlite3

import pytest

from chatmaid.model import Address, NormalizedMessage, SourceRef
from chatmaid.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "i.sqlite", embedding_dim=4)
    yield s
    s.close()


@pytest.fixture
def sample_message():
    return NormalizedMessage(
        ref=SourceRef("evolution", "acct1", "INBOX", "7"),
        message_id="<m7@x.org>",
        in_reply_to=None,
        references=(),
        sender=Address("Sender", "sender@x.org"),
        recipients=(Address("Me", "me@x.org"),),
        sent_at=1_700_000_000,
        subject="Hi",
        text="Hello there",
        preview="Hello there",
        has_attachments=False,
        attachments=(),
        size=123,
        is_owner=False,
        bulk=False,
        labels=(),
    )


def make_sources(tmp_path, accounts):
    """Write Evolution-style <uid>.source and <identity_uid>.source files; return the directory."""
    d = tmp_path / "sources"
    d.mkdir(exist_ok=True)
    for a in accounts:
        (d / f"{a['uid']}.source").write_text(
            "[Data Source]\n"
            f"DisplayName={a['display_name']}\n"
            "Enabled=true\n"
            "Parent=\n\n"
            "[Mail Account]\n"
            f"BackendName={a['backend']}\n"
            f"IdentityUid={a['identity_uid']}\n",
            encoding="utf-8",
        )
        (d / f"{a['identity_uid']}.source").write_text(
            "[Mail Identity]\n"
            f"Address={a['address']}\n"
            "Name=Me\n\n"
            "[Mail Submission]\n"
            f"SentFolder={a['sent_folder']}\n"
            "TransportUid=t1\n\n"
            "[Mail Composition]\n"
            f"DraftsFolder={a['drafts_folder']}\n",
            encoding="utf-8",
        )
    return d


def make_cache(tmp_path, account_uid, folders):
    """Build a fake Evolution cache; return the cache root (parent of <account_uid>/)."""
    acct = tmp_path / account_uid
    acct.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(acct / "folders.db")
    try:
        conn.execute("CREATE TABLE keys (key TEXT PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO keys VALUES (?, ?)",
            [("csdb::folders_version", "1"), ("csdb::messages_version", "1")],
        )
        conn.execute(
            "CREATE TABLE folders (folder_name TEXT PRIMARY KEY, version INTEGER, flags INTEGER,"
            " nextuid INTEGER, time NUMERIC, saved_count INTEGER, unread_count INTEGER,"
            " deleted_count INTEGER, junk_count INTEGER, visible_count INTEGER, jnd_count INTEGER,"
            " bdata TEXT, folder_id INTEGER)"
        )
        for folder_id, (name, rows) in enumerate(folders.items(), start=1):
            conn.execute(
                "INSERT INTO folders (folder_name, version, flags, nextuid, time, saved_count,"
                " unread_count, deleted_count, junk_count, visible_count, jnd_count, bdata, folder_id)"
                " VALUES (?, 0, 0, 1, 0, ?, 0, 0, 0, ?, 0, '', ?)",
                (name, len(rows), len(rows), folder_id),
            )
            conn.execute(
                f"CREATE TABLE messages_{folder_id} (uid TEXT, flags INTEGER, msg_type INTEGER,"
                " dirty INTEGER, size INTEGER, dsent NUMERIC, dreceived NUMERIC, subject TEXT,"
                " mail_from TEXT, mail_to TEXT, mail_cc TEXT, mlist TEXT, part TEXT, labels TEXT,"
                " usertags TEXT, cinfo TEXT, bdata TEXT, userheaders TEXT, preview TEXT,"
                " PRIMARY KEY (uid))"
            )
            body_dir = acct / "folders" / name.replace("/", "/subfolders/") / "cur" / "00"
            for r in rows:
                conn.execute(
                    f"INSERT INTO messages_{folder_id} (uid, flags, msg_type, dirty, size, dsent,"
                    " dreceived, subject, mail_from, mail_to, mail_cc)"
                    " VALUES (?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r["uid"], r.get("flags", 0), r.get("size", 0), r.get("dsent", 0),
                        r.get("dreceived", 0), r.get("subject", ""), r.get("mail_from", ""),
                        r.get("mail_to", ""), r.get("mail_cc", ""),
                    ),
                )
                if "body" in r:
                    body_dir.mkdir(parents=True, exist_ok=True)
                    (body_dir / r["uid"]).write_text(r["body"], encoding="utf-8")
        conn.commit()
    finally:
        conn.close()
    return tmp_path
