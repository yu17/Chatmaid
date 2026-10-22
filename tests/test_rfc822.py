import base64
from email.message import EmailMessage

import pytest

from chatmaid.connectors.evolution.rfc822 import ParsedMail, parse_bytes
from chatmaid.model import Address


@pytest.fixture
def multipart_bytes() -> bytes:
    msg = EmailMessage()
    msg["From"] = "a@x.org"
    msg["Subject"] = "multi"
    msg.set_content("plain")
    msg.add_alternative("<b>html</b>", subtype="html")
    msg.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="a.pdf")
    return msg.as_bytes()


def test_parse_plain_with_threading_headers():
    p = parse_bytes(b"Message-ID: <a@x>\r\nIn-Reply-To: <b@x>\r\nReferences: <c@x> <b@x>\r\nFrom: Ann <ANN@X.org>\r\nTo: b@x.org, Cy <c@x.org>\r\nDate: Tue, 01 Oct 2024 10:00:00 +0000\r\nSubject: Re: hi\r\n\r\nbody text\r\n")
    assert p.message_id == "<a@x>" and p.in_reply_to == "<b@x>" and p.references == ("<c@x>", "<b@x>")
    assert p.from_ == Address("Ann", "ann@x.org") and p.to == (Address("", "b@x.org"), Address("Cy", "c@x.org"))
    assert p.date == 1727776800 and p.subject == "Re: hi" and p.text == "body text"


def test_parse_prefers_plain_over_html_and_lists_attachments(multipart_bytes):
    p = parse_bytes(multipart_bytes)
    assert p.text == "plain" and p.attachments == ("a.pdf",)


def test_html_only_is_converted():
    p = parse_bytes(b"Content-Type: text/html\r\n\r\n<p>Hello<br>world</p><script>x()</script>")
    assert p.text == "Hello\nworld"


def test_parse_gbk_body():
    body = "你好".encode("gbk")
    p = parse_bytes(b"Content-Type: text/plain; charset=gbk\r\nSubject: =?gbk?b?" + base64.b64encode("测试".encode("gbk")) + b"?=\r\n\r\n" + body)
    assert p.text == "你好" and p.subject == "测试"


def test_parse_missing_date_is_none():
    p = parse_bytes(b"From: a@x.org\r\n\r\nx")
    assert p.date is None and p.text == "x"


def test_list_headers_collected():
    p = parse_bytes(b"List-Unsubscribe: <mailto:u@x>\r\nPrecedence: bulk\r\n\r\nx")
    assert p.list_headers == {"list-unsubscribe": "<mailto:u@x>", "precedence": "bulk"}


def test_parse_never_raises_on_garbage():
    p = parse_bytes(b"\xff\xfe\x00garbage")
    assert isinstance(p, ParsedMail) and p.message_id is None and p.from_ == Address("", "")
