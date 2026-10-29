"""Stripping quoted history and signatures from message text."""

import re
from dataclasses import replace

from chatmaid.model import NormalizedMessage

_ATTRIBUTION_RE = re.compile(
    r"On .* wrote:|-----Original Message-----|在.*写道[:：]?|\d{4}.*写道[:：]?|>"
)


def strip_reply_chrome(text: str) -> str:
    """The owner's own words of a sent reply (ruling R16).

    Cuts at the first quoted-history attribution (English or Chinese), the first quoted `>`
    line, or a `-- ` signature separator, whichever comes first.
    """
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if _ATTRIBUTION_RE.match(bare) or bare in ("-- ", "--"):
            break
        kept.append(line)
    return "".join(kept)


def strip_quoted(
    msgs: list[NormalizedMessage], *, keep_first: bool = True
) -> list[NormalizedMessage]:
    """Copies of `msgs` (oldest first) with quoted history cut from their text.

    The first message stays whole when `keep_first`; messages left blank are dropped.
    """
    out: list[NormalizedMessage] = []
    for i, msg in enumerate(msgs):
        if keep_first and i == 0:
            out.append(msg)
            continue
        text = strip_reply_chrome(msg.text)
        if text.strip():
            out.append(replace(msg, text=text))
    return out
