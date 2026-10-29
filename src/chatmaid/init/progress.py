"""One progress line for a long pass: redrawn in place on a terminal, logged otherwise."""

import sys
import time
from collections import deque
from collections.abc import Callable
from typing import TextIO

BAR_WIDTH = 20
LABEL_WIDTH = 40


def _hours(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m"


class Progress:
    def __init__(
        self,
        name: str,
        total: int,
        *,
        stream: TextIO | None = None,
        every: int = 50,
        window: int = 50,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.total = total
        self.stream = stream if stream is not None else sys.stderr
        self.every = every
        self.clock = clock
        self.tty = self.stream.isatty()
        self._start = clock()
        self._samples: deque[tuple[int, float]] = deque(maxlen=window)
        self._width = 0  # longest line drawn so far, to blank out the tail of a longer one
        self._drawn = False

    def _per_hour(self) -> float | None:
        if len(self._samples) < 2:
            return None
        (d0, t0), (d1, t1) = self._samples[0], self._samples[-1]
        if t1 <= t0:
            return None
        return (d1 - d0) * 3600 / (t1 - t0)

    def _line(self, done: int, label: str = "") -> str:
        frac = done / self.total if self.total else 1.0
        filled = min(BAR_WIDTH, int(frac * BAR_WIDTH))
        bar = "#" * filled + "-" * (BAR_WIDTH - filled)
        rate = self._per_hour()
        if rate is None:
            rate_text = eta = "--"
        else:
            rate_text = f"{rate:.0f}" if rate >= 1 else f"{rate:.2f}"
            left = max(self.total - done, 0)
            eta = _hours(left * 3600 / rate) if rate > 0 else "--"
        elapsed = _hours(self.clock() - self._start)
        return (
            f"{self.name} {done}/{self.total} {int(frac * 100):3d}% [{bar}] "
            f"{rate_text}/h eta {eta} elapsed {elapsed} | {label[:LABEL_WIDTH]}"
        )

    def update(self, done: int, label: str = "") -> None:
        self._samples.append((done, self.clock()))
        if self.tty:
            text = self._line(done, label)
            self._width = max(self._width, len(text))
            self.stream.write("\r" + text.ljust(self._width))
            self._drawn = True
        elif done % self.every == 0 or done == self.total:
            self.stream.write(self._line(done, label) + "\n")
        else:
            return
        self.stream.flush()

    def finish(self, summary: str = "") -> None:
        if self.tty and self._drawn:
            self.stream.write("\n")
        self.stream.write(summary + "\n")
        self.stream.flush()
