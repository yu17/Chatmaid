"""Splitting conversations into model-sized chunks, language detection and word counts."""

import math
from dataclasses import replace

from chatmaid.model import NormalizedMessage

_CJK = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3040, 0x30FF))


def _is_cjk(ch: str) -> bool:
    return any(lo <= ord(ch) <= hi for lo, hi in _CJK)


def detect_language(text: str) -> str:
    cjk = sum(1 for ch in text if _is_cjk(ch))
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk and cjk >= 0.3 * (cjk + ascii_letters):
        return "zh"
    return "en"


def count_words(text: str) -> int:
    """Whitespace-separated tokens once CJK characters are removed, plus one word per 2.5 of them."""
    cjk = sum(1 for ch in text if _is_cjk(ch))
    rest = "".join(ch for ch in text if not _is_cjk(ch))
    return len(rest.split()) + math.ceil(cjk / 2.5)


def chunk_messages(
    msgs: list[NormalizedMessage], *, chunk_chars: int
) -> list[list[NormalizedMessage]]:
    chunks: list[list[NormalizedMessage]] = []
    current: list[NormalizedMessage] = []
    used = 0
    for msg in msgs:
        if len(msg.text) > chunk_chars:
            msg = replace(msg, text=msg.text[:chunk_chars])
        if current and used + len(msg.text) > chunk_chars:
            chunks.append(current)
            current, used = [], 0
        current.append(msg)
        used += len(msg.text)
    if current:
        chunks.append(current)
    return chunks
