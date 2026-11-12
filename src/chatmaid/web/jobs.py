"""One background worker for model jobs: the UI submits, the pages poll."""

import itertools
import logging
import queue
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

from chatmaid.cloud.base import CloudError
from chatmaid.config import ConfigError
from chatmaid.llm.base import LLMError
from chatmaid.reply.flow import ReplyFlow
from chatmaid.web.services import Services

log = logging.getLogger(__name__)
KEEP_FINISHED = 200
UNFINISHED = ("queued", "running")
EXPECTED_FAILURES = (ValueError, LLMError, CloudError, ConfigError)


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    label: str
    subject_id: int
    state: str  # queued | running | done | failed
    result: int | None
    error: str | None
    created_at: float


class JobRunner:
    def __init__(self, services: Services) -> None:
        self._services = services
        self._jobs: dict[str, Job] = {}  # insertion order is submission order
        self._fns: queue.Queue[tuple[str, Callable[[ReplyFlow], int | None]] | None] = queue.Queue()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._token = secrets.token_hex(3)  # an old page cannot match a new process's job
        self._stopping = threading.Event()
        self._worker = threading.Thread(target=self._run, name="chatmaid-jobs", daemon=True)
        self._worker.start()

    def submit(
        self, kind: str, label: str, subject_id: int, fn: Callable[[ReplyFlow], int | None]
    ) -> Job:
        with self._lock:
            job = Job(
                f"{self._token}-{next(self._ids)}", kind, label, subject_id, "queued", None, None, time.time()
            )
            self._jobs[job.id] = job
        self._fns.put((job.id, fn))
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def for_subject(self, kind_prefix: str, subject_id: int) -> list[Job]:
        """Jobs of this kind for the subject: unfinished first (oldest first, as they will run),
        then the finished ones, newest first."""
        with self._lock:
            mine = [
                j for j in self._jobs.values()
                if j.kind.startswith(kind_prefix) and j.subject_id == subject_id
            ]
        waiting = [j for j in mine if j.state in UNFINISHED]
        return waiting + [j for j in reversed(mine) if j.state not in UNFINISHED]

    def busy(self, kind_prefix: str, subject_id: int) -> bool:
        return any(j.state in UNFINISHED for j in self.for_subject(kind_prefix, subject_id))

    def stop(self) -> None:
        self._stopping.set()
        self._fns.put(None)
        self._worker.join(timeout=5)

    def _update(self, job_id: str, **changes) -> None:
        with self._lock:
            self._jobs[job_id] = replace(self._jobs[job_id], **changes)
            finished = [i for i, j in self._jobs.items() if j.state not in UNFINISHED]
            for i in finished[:-KEEP_FINISHED]:
                del self._jobs[i]

    def _run(self) -> None:
        while (item := self._fns.get()) is not None:
            job_id, fn = item
            if self._stopping.is_set():
                break
            self._update(job_id, state="running")
            try:
                with self._services.session() as (store, connector):
                    result = fn(self._services.flow(store, connector))
                self._update(job_id, state="done", result=result)
            except Exception as e:  # the worker must outlive any single job
                log.warning("job %s failed: %s", job_id, e, exc_info=not isinstance(e, EXPECTED_FAILURES))
                self._update(job_id, state="failed", error=str(e) or type(e).__name__)
