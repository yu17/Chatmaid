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
