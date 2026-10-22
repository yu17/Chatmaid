"""Tolerant RFC822 parsing into a flat ParsedMail."""

import email
import email.policy
import re
from dataclasses import dataclass
from datetime import timezone
from email.message import Message
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from chatmaid.model import Address

_LIST_HEADERS = ("list-unsubscribe", "list-id", "precedence", "auto-submitted")
_BLOCK_TAGS = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP_TAGS = {"script", "style"}


@dataclass(frozen=True)
class ParsedMail:
    message_id: str | None
    in_reply_to: str | None
    references: tuple[str, ...]
    from_: Address
    to: tuple[Address, ...]
    cc: tuple[Address, ...]
    date: int | None
    subject: str
    text: str
    attachments: tuple[str, ...]
    list_headers: dict[str, str]


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()


def parse_addresses(header: str) -> tuple[Address, ...]:
    return tuple(
        Address(name, addr.lower())
        for name, addr in getaddresses([str(header)])
        if addr
    )


def _decode(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _body(msg: Message) -> tuple[str, Message | None]:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return "", None
    text = _decode(part)
    if part.get_content_subtype() == "html":
        text = html_to_text(text)
    return text.rstrip(), part


def _date(msg: Message) -> int | None:
    raw = msg.get("Date")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse(data: bytes) -> ParsedMail:
    msg = email.message_from_bytes(data, policy=email.policy.default)
    text, body = _body(msg)
    attachments = tuple(
        name
        for part in msg.walk()
        if part is not body and (part.is_attachment() or part.get_filename())
        and (name := part.get_filename())
    )
    message_id = msg.get("Message-ID")
    reply_tokens = str(msg.get("In-Reply-To", "")).split()
    from_ = parse_addresses(msg.get("From", ""))
    return ParsedMail(
        message_id=str(message_id).strip() if message_id else None,
        in_reply_to=reply_tokens[0] if reply_tokens else None,
        references=tuple(str(msg.get("References", "")).split()),
        from_=from_[0] if from_ else Address("", ""),
        to=parse_addresses(msg.get("To", "")),
        cc=parse_addresses(msg.get("Cc", "")),
        date=_date(msg),
        subject=str(msg.get("Subject", "")),
        text=text,
        attachments=attachments,
        list_headers={
            h: str(msg[h]).strip() for h in _LIST_HEADERS if msg.get(h) is not None
        },
    )


def parse_bytes(data: bytes) -> ParsedMail:
    try:
        return _parse(data)
    except Exception:
        return ParsedMail(None, None, (), Address("", ""), (), (), None, "", "", (), {})


def parse_file(path: Path) -> ParsedMail:
    return parse_bytes(path.read_bytes())
