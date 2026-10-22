"""Splitting conversations into model-sized chunks, and language detection."""

from dataclasses import replace

from chatmaid.model import NormalizedMessage

_CJK = ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3040, 0x30FF))


def detect_language(text: str) -> str:
    cjk = sum(1 for ch in text if any(lo <= ord(ch) <= hi for lo, hi in _CJK))
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if cjk and cjk >= 0.3 * (cjk + ascii_letters):
        return "zh"
    return "en"


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
