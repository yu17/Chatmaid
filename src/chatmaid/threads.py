"""Conversation threading: subject normalisation and a union-find over message ids."""

from __future__ import annotations

import re
from collections.abc import Iterable

_PREFIX_RE = re.compile(r"^(?:re|fw|fwd|回复|回覆|转发|轉發|答复)\s*[:：]\s*", re.I)


def normalize_subject(s: str) -> str:
    s = s.strip()
    while True:
        stripped = _PREFIX_RE.sub("", s, count=1)
        if stripped == s:
            break
        s = stripped.lstrip()
    return " ".join(s.split()).lower()


def thread_fallback_key(subject: str, participants: Iterable[str]) -> str:
    return "subj:" + normalize_subject(subject) + "|" + ",".join(sorted(set(participants)))


class ThreadIndex:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._size: dict[str, int] = {}
        self._min: dict[str, str] = {}

    def _find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._size[x] = 1
            self._min[x] = x
            return x
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def _union(self, a: str, b: str) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        self._min[ra] = min(self._min[ra], self._min[rb])

    def _link(self, message_id: str | None, references: Iterable[str]) -> str | None:
        ids = ([message_id] if message_id else []) + list(references)
        for other in ids[1:]:
            self._union(ids[0], other)
        if ids:
            self._find(ids[0])
            return ids[0]
        return None

    def add(self, message_id: str | None, references: Iterable[str]) -> None:
        self._link(message_id, references)

    def key_for(self, message_id: str | None, references: Iterable[str], fallback: str) -> str:
        anchor = self._link(message_id, references)
        if anchor is None:
            return fallback
        root = self._find(anchor)
        return self._min[root]
