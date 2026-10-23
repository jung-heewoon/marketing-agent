from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"
    step: str = ""
    result: dict | None = None
    error: str | None = None
    screenshot: str | None = None
    extra: dict = field(default_factory=dict)


_lock = threading.Lock()
_jobs: dict[str, Job] = {}


def create(kind: str, step: str = "", extra: dict | None = None) -> Job:
    job = Job(id=uuid.uuid4().hex, kind=kind, step=step, extra=extra or {})
    with _lock:
        _jobs[job.id] = job
        if len(_jobs) > 100:
            for key in list(_jobs)[:-100]:
                _jobs.pop(key, None)
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def update(job_id: str, **fields) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def finish(job_id: str, result: dict | None = None, step: str = "") -> None:
    update(job_id, status="done", result=result, step=step, error=None)


def fail(job_id: str, error: str, screenshot: str | None = None, step: str = "") -> None:
    update(job_id, status="error", error=error, screenshot=screenshot, step=step)


def as_dict(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "step": job.step,
        "result": job.result,
        "error": job.error,
        "screenshot": job.screenshot,
    }
