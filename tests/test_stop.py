import signal

import pytest

from chatmaid.stop import StopRequested, stop_on_signal


def test_first_signal_raises_stop_requested_and_restores_default():
    before = signal.getsignal(signal.SIGINT)
    with pytest.raises(StopRequested):
        with stop_on_signal():
            signal.raise_signal(signal.SIGINT)
    assert signal.getsignal(signal.SIGINT) is before


def test_handlers_restored_after_normal_exit():
    before = signal.getsignal(signal.SIGTERM)
    with stop_on_signal():
        assert signal.getsignal(signal.SIGTERM) is not before
    assert signal.getsignal(signal.SIGTERM) is before
