import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

from chatmaid.web.jobs import JobRunner


def wait_for(read, done, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out; last value {value!r}")


def stub_services():
    closed = []

    @contextmanager
    def session():
        try:
            yield "store", "connector"
        finally:
            closed.append(1)

    return SimpleNamespace(session=session, flow=lambda s, c: f"flow({s},{c})"), closed


def raises(message):
    def fn(flow):
        raise ValueError(message)

    return fn


def test_jobs_run_one_at_a_time_and_report_failures():
    services, closed = stub_services()
    runner = JobRunner(services)
    try:
        gate = threading.Event()
        first = runner.submit("draft", "Drafting", 7, lambda flow: gate.wait(5) and 11)
        second = runner.submit("regenerate", "Regenerating", 7, lambda flow: 22)
        boom = runner.submit("draft", "Drafting", 8, raises("nope"))
        last = runner.submit("draft", "Drafting", 9, lambda flow: None)
        wait_for(lambda: runner.get(first.id).state, lambda s: s == "running")
        assert runner.get(second.id).state == "queued"
        assert [j.id for j in runner.for_subject("", 7)] == [first.id, second.id]
        gate.set()
        wait_for(lambda: runner.get(last.id).state, lambda s: s == "done")
        assert runner.get(first.id).result == 11 and runner.get(second.id).result == 22
        failed = runner.get(boom.id)
        assert (failed.state, failed.error) == ("failed", "nope")
        assert len(closed) == 4  # every job closed its own session
        assert [j.id for j in runner.for_subject("draft", 7)] == [first.id]
    finally:
        runner.stop()


def test_finished_jobs_are_capped():
    services, _ = stub_services()
    runner = JobRunner(services)
    try:
        jobs = [runner.submit("draft", "d", 1, lambda flow: None) for _ in range(205)]
        wait_for(lambda: runner.get(jobs[-1].id).state, lambda s: s == "done")
        assert runner.get(jobs[0].id) is None and runner.get(jobs[-1].id) is not None
    finally:
        runner.stop()
