import mailbox
import re
import sqlite3
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from chatmaid import config as chatmaid_config
from chatmaid.connectors.evolution.connector import EvolutionConnector
from chatmaid.connectors.evolution.sources import load_accounts
from chatmaid.identity import OwnerIdentity

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


def add_time_folder(db_path, name, rows):
    """Add (or extend) a folder with opaque uids; rows are (uid, dreceived) with dsent = dreceived."""
    conn = sqlite3.connect(db_path)
    try:
        found = conn.execute("SELECT folder_id FROM folders WHERE folder_name = ?", (name,)).fetchone()
        if found:
            fid = found[0]
        else:
            fid = conn.execute("SELECT MAX(folder_id) FROM folders").fetchone()[0] + 1
            conn.execute(
                "INSERT INTO folders (folder_name, version, flags, nextuid, time, saved_count,"
                " unread_count, deleted_count, junk_count, visible_count, jnd_count, bdata, folder_id)"
                " VALUES (?, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, '', ?)",
                (name, fid),
            )
            conn.execute(
                f"CREATE TABLE messages_{fid} (uid TEXT, flags INTEGER, msg_type INTEGER,"
                " dirty INTEGER, size INTEGER, dsent NUMERIC, dreceived NUMERIC, subject TEXT,"
                " mail_from TEXT, mail_to TEXT, mail_cc TEXT, mlist TEXT, part TEXT, labels TEXT,"
                " usertags TEXT, cinfo TEXT, bdata TEXT, userheaders TEXT, preview TEXT,"
                " PRIMARY KEY (uid))"
            )
        for uid, received in rows:
            conn.execute(
                f"INSERT INTO messages_{fid} (uid, flags, msg_type, dirty, size, dsent, dreceived,"
                " subject, mail_from, mail_to, mail_cc) VALUES (?, 0, 0, 0, 0, ?, ?, ?, ?, ?, '')",
                (uid, received, received, f"subj {uid}", "Zed <zed@x.org>", "me@x.org"),
            )
        conn.commit()
    finally:
        conn.close()


def _mail(msg_id, frm, to, date, body, extra=""):
    when = format_datetime(datetime.fromtimestamp(date, timezone.utc))
    return (
        f"Message-ID: {msg_id}\nFrom: {frm}\nTo: {to}\n{extra}Date: {when}\n"
        "Subject: test\nContent-Type: text/plain; charset=utf-8\n\n" + body + "\n"
    )


def _set_toml(text, section, key, value):
    pattern = re.compile(
        rf"(^\[{section}\]\n(?:(?!^\[).*\n)*?{key}\s*=\s*)((?s:\[\n.*?^\])|[^\n]*)", re.M
    )
    new, n = pattern.subn(lambda m: m.group(1) + value, text, count=1)
    assert n == 1, (section, key)
    return new


@pytest.fixture
def fake_env(tmp_path, request):
    sources = make_sources(
        tmp_path,
        [
            dict(
                uid="a1", display_name="me@x.org", backend="imapx", identity_uid="i1",
                address="me@x.org",
                sent_folder="folder://a1/%5bGmail%5d/Sent%20Mail",
                drafts_folder="folder://a1/%5bGmail%5d/Drafts",
            )
        ],
    )
    one = _mail("<a@x>", "Ann <ann@x.org>", "me@x.org", 1_600_000_000, "hello there")
    cache_dir = make_cache(
        tmp_path,
        "a1",
        {
            "INBOX": [
                dict(uid="1", subject="s1", mail_from="Ann <ann@x.org>", mail_to="me@x.org", body=one),
                dict(uid="2", subject="row subject", dsent=1_700_000_000,
                     mail_from="Bob <bob@x.org>", mail_to="me@x.org"),
            ],
            "[Gmail]/Sent Mail": [
                dict(uid="3", mail_from="me@x.org", mail_to="ann@x.org", dsent=1_700_000_100,
                     body=_mail("<s3@x>", "me@x.org", "ann@x.org", 1_700_000_100,
                                "thanks, see you tomorrow at the office", "In-Reply-To: <a@x>\n")),
                dict(uid="5", mail_from="me@x.org", mail_to="li@x.org", dsent=1_700_000_300,
                     body=_mail("<s5@x>", "me@x.org", "li@x.org", 1_700_000_300,
                                "明天见，谢谢你的帮助，我们到时候再详细讨论项目的安排。")),
            ],
            "[Gmail]/All Mail": [
                dict(uid="9", mail_from="Ann <ann@x.org>", mail_to="me@x.org", body=one),
            ],
            "News": [
                dict(uid="4", mail_from="news@x.org", mail_to="me@x.org", dsent=1_700_000_200,
                     body=_mail("<n4@x>", "news@x.org", "me@x.org", 1_700_000_200, "weekly digest",
                                "List-Unsubscribe: <mailto:u@x>\n")),
            ],
        },
    )
    maildir = tmp_path / "local" / ".Sent"
    maildir.parent.mkdir(parents=True, exist_ok=True)
    md = mailbox.Maildir(maildir, factory=None, create=True)
    md.add(_mail("<l1@x>", "me@x.org", "ann@x.org", 1_700_000_400, "sent from local").encode())
    md.close()

    text = (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")
    text = _set_toml(text, "owner", "extra_addresses", '["me2@x.org"]')
    text = _set_toml(text, "email", "evolution_config_dir", f'"{sources}"')
    text = _set_toml(text, "email", "evolution_cache_dir", f'"{cache_dir}"')
    text = _set_toml(text, "email", "accounts", '["a1"]')
    text = _set_toml(text, "email", "skip_folders", "[]")
    text = _set_toml(text, "email", "local_maildir_sent", f'"{maildir}"')
    text = _set_toml(text, "lmstudio", "embedding_dim", "4")
    config_path = tmp_path / "data" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")

    config = chatmaid_config.load(config_path, project_root=tmp_path)
    cfg = config.email
    accounts = load_accounts(cfg.evolution_config_dir, cfg.accounts)
    owner = OwnerIdentity.from_accounts(accounts, config.owner.extra_addresses, config.owner.aliases)
    connector = EvolutionConnector(cfg, accounts, owner)
    request.addfinalizer(connector.close)
    return SimpleNamespace(
        config=config, config_path=config_path, cfg=cfg, accounts=accounts, owner=owner,
        connector=connector, cache_dir=cache_dir, db_path=cache_dir / "a1" / "folders.db",
    )


@pytest.fixture
def ingested_env(fake_env, store):
    from chatmaid.init.pipeline import InitOptions, InitPipeline
    from chatmaid.llm.fake import FakeLLM

    InitPipeline(store, fake_env.connector, FakeLLM(), fake_env.config).ingest(InitOptions())
    return SimpleNamespace(**vars(fake_env), store=store)


@pytest.fixture
def digested_env(ingested_env):
    from chatmaid.init.pipeline import InitOptions, InitPipeline
    from chatmaid.llm.fake import FakeLLM

    llm = FakeLLM(
        {
            "chunk_summary": '{"summary":"S1","events":[],"facts":[{"about":"Ann","text":"likes tea"}]}',
            "rolling_summary": '{"summary":"S"}',
        },
        dim=4,
    )
    p = InitPipeline(ingested_env.store, ingested_env.connector, llm, ingested_env.config)
    p.digest(InitOptions())
    p.embed(InitOptions())
    return ingested_env
