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
