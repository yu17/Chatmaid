"""Connector protocol: a source of NormalizedMessages."""

from collections.abc import Iterator
from typing import Protocol

from chatmaid.model import NormalizedMessage, SourceRef


class Connector(Protocol):
    name: str

    def iter_messages(
        self, *, since: int | None = None, after: dict[str, int] | None = None
    ) -> Iterator[NormalizedMessage]: ...

    def fetch(self, ref: SourceRef) -> NormalizedMessage: ...

    def scan_new(self, state: dict[str, int]) -> tuple[list[SourceRef], dict[str, int]]: ...
