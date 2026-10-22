"""Conversation threading: subject normalisation and a union-find over message ids."""

from __future__ import annotations

import re
from collections.abc import Iterable

_PREFIX_RE = re.compile(r"^(?:re|fw|fwd|回复|回覆|转发|轉發|答复)\s*[:：]\s*", re.I)


def strip_subject_prefixes(s: str) -> str:
    """Drop reply/forward prefixes and collapse whitespace, keeping the case (for display)."""
    s = s.strip()
    while True:
        stripped = _PREFIX_RE.sub("", s, count=1)
        if stripped == s:
            break
        s = stripped.lstrip()
    return " ".join(s.split())


def normalize_subject(s: str) -> str:
    """Lower-cased `strip_subject_prefixes`: for keys, never for display."""
    return strip_subject_prefixes(s).lower()


def thread_fallback_key(subject: str, participants: Iterable[str]) -> str:
    return "subj:" + normalize_subject(subject) + "|" + ",".join(sorted(set(participants)))


class ThreadIndex:
    """Union-find over message ids with stable component keys (ruling R17).

    A component's key is the first id that was added to it; when two components are joined
    the one created earlier keeps its key. A key therefore never changes once a conversation
    has been stored under it, whatever order later messages arrive in, and replaying the same
    sequence of `add` calls (the store's messages in id order) reproduces every key.
    """

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._size: dict[str, int] = {}
        self._key: dict[str, str] = {}  # root -> the component's key
        self._born: dict[str, int] = {}  # root -> creation sequence of that key
        self._next = 0

    def _find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._size[x] = 1
            self._key[x] = x
            self._born[x] = self._next
            self._next += 1
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
        older = ra if self._born[ra] <= self._born[rb] else rb
        key, born = self._key[older], self._born[older]
        if self._size[ra] < self._size[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        self._key[ra], self._born[ra] = key, born
        del self._key[rb], self._born[rb]

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
        return self._key[self._find(anchor)]

    def keys_of(self, ids: Iterable[str]) -> list[str]:
        """Keys of the existing components holding any of `ids`, oldest component first.

        Unknown ids are ignored; nothing is created or joined.
        """
        born: dict[str, int] = {}
        for x in ids:
            if x in self._parent:
                root = self._find(x)
                born[root] = self._born[root]
        return [self._key[root] for root in sorted(born, key=born.__getitem__)]
