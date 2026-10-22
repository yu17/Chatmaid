"""Single-writer guard: `init` and `watch` must never run at the same time."""

import contextlib
import fcntl
from collections.abc import Iterator
from pathlib import Path


class Busy(Exception):
    """Another chatmaid process holds the writer lock."""


@contextlib.contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Hold an exclusive, non-blocking flock on `path` for the duration of the block."""
    with open(path, "a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(f"{path} is held by another chatmaid process") from None
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
