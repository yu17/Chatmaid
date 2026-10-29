import pytest

from chatmaid.init.chunking import chunk_messages, count_words, detect_language
from chatmaid.model import Address, NormalizedMessage, SourceRef


@pytest.fixture
def msgs_of_len():
    def build(lengths):
        return [
            NormalizedMessage(
                ref=SourceRef("evolution", "a1", "INBOX", str(i)),
                message_id=f"<{i}@x>",
                in_reply_to=None,
                references=(),
                sender=Address("Ann", "ann@x.org"),
                recipients=(),
                sent_at=1_700_000_000 + i,
                subject="s",
                text="x" * n,
                preview="",
                has_attachments=False,
                attachments=(),
                size=n,
                is_owner=False,
                bulk=False,
                labels=(),
            )
            for i, n in enumerate(lengths)
        ]

    return build


def test_detect_language():
    assert detect_language("你好，明天见") == "zh" and detect_language("see you tomorrow") == "en"


def test_count_words_cjk():
    assert count_words("one two  three") == 3
    assert count_words("明天见谢谢") == 2       # 5 CJK characters: ceil(5 / 2.5)
    assert count_words("明天见，谢谢") == 3      # the full-width comma is left over as one token
    assert count_words("meet 明天 at 3") == 4    # three tokens plus ceil(2 / 2.5)
    assert count_words("") == 0


def test_chunk_messages_respects_budget(msgs_of_len):
    chunks = chunk_messages(msgs_of_len([4000, 4000, 4000, 20000]), chunk_chars=9000)
    assert [len(c) for c in chunks] == [2, 1, 1] and len(chunks[2][0].text) == 9000
