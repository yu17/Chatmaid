"""Graceful stop for the long synchronous `init` passes."""

import signal
from collections.abc import Iterator
from contextlib import contextmanager

_SIGNALS = (signal.SIGINT, signal.SIGTERM)


class StopRequested(BaseException):
    """The owner asked the run to stop (SIGINT or SIGTERM).

    A BaseException, like KeyboardInterrupt, so retry wrappers and `except Exception` or
    `except ValueError` blocks around a model call never swallow it.
    """


@contextmanager
def stop_on_signal() -> Iterator[None]:
    """Turn the first SIGINT or SIGTERM into `StopRequested` in the main thread.

    The first signal puts the previous handlers back before raising, so a second signal gets
    the default action (exit at once). The previous handlers are restored on exit either way.
    """
    previous = {sig: signal.getsignal(sig) for sig in _SIGNALS}

    def restore() -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    def handler(signum, frame) -> None:
        restore()
        raise StopRequested(signal.Signals(signum).name)

    for sig in _SIGNALS:
        signal.signal(sig, handler)
    try:
        yield
    finally:
        restore()
