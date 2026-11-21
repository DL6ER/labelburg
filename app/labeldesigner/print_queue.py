"""One first-in-first-out line of print jobs per printer.

Every line has a worker thread that prints one job after the other, so
a second job waits instead of being refused while the printer is busy. A job
that reports it did not print holds the jobs behind it until the line is
resumed or nothing waits anymore.
"""
import contextvars
import logging
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from . import printer
from .drivers import devices

logger = logging.getLogger(__name__)

START = uuid.uuid4().hex
MAX_WAITING = 20
PREVIEW_KEPT = 600.0
OUTCOME_KEPT = 86400.0

REMOVED = 'Removed from the queue'
PRINTING_ALREADY = 'It is printing already'
NOT_WAITING = 'It is not waiting in the queue anymore'
NO_SUCH_JOB = 'No such print job'
NO_PREVIEW = 'The preview of this print job is no longer kept'
PRINTER_REQUIRED = 'printer is required'

FAILED = 'It was not printed'

# The name of each worker thread starts with it, the key of its line follows
WORKER_PREFIX = 'print-queue-'
# Free-threaded builds of Python 3.14+ start a thread in a copy of the context
# of the one that starts it, the others in an empty one
THREADS_INHERIT_CONTEXT = bool(getattr(sys.flags, 'thread_inherit_context', 0))

SOURCES = {'designer': '', 'api': 'the API', 'homebox': 'Homebox', 'webhook': 'the webhook', 'mcp': 'MCP', 'calibration': ''}


class Full(Exception):
    """The line of the printer has MAX_WAITING jobs waiting."""


@dataclass
class Job:
    id: str
    key: str
    title: str
    labels: int
    source: str
    state: str = 'waiting'
    message: str = ''
    preview: bytes | None = None
    created: float = field(default_factory=time.monotonic)
    ended: float | None = None
    done: threading.Event = field(default_factory=threading.Event)
    run: Callable[[], str] | None = field(default=None, repr=False)
    explain: Callable[[Exception], str] | None = field(default=None, repr=False)

    def told(self) -> dict:
        return {'id': self.id, 'title': self.title, 'labels': self.labels, 'from': SOURCES.get(self.source, ''),
                'state': self.state, 'message': self.message, 'preview': self.preview is not None}


class _Line:
    def __init__(self, device: str = ''):
        # As the first job of the line was added, for lines()
        self.device = device
        self.waiting = deque()
        self.printing = None
        self.held = None
        self.worker = None


_lock = threading.Lock()
_lines = {}
_jobs = {}


def key_of(device: str) -> str:
    return 'simulation' if printer.is_simulation(device) else devices.lock_key(device)


def _prune():
    now = time.monotonic()
    for job_id, job in list(_jobs.items()):
        if job.ended is None:
            continue
        if now - job.ended > OUTCOME_KEPT:
            del _jobs[job_id]
        elif now - job.ended > PREVIEW_KEPT:
            job.preview = None


def _start_worker(key, line):
    if line.worker is None and line.held is None and line.waiting:
        # Empty, it would keep the request that starts it otherwise. Before
        # Python 3.14 Thread() takes no context.
        empty = {'context': contextvars.Context()} if THREADS_INHERIT_CONTEXT else {}
        worker = threading.Thread(target=_work, args=(key, line), name=f'{WORKER_PREFIX}{key}', daemon=True, **empty)
        worker.start()
        line.worker = worker


def _end(job, state, message):
    job.message, job.ended = message, time.monotonic()
    job.state = state
    job.run = job.explain = None
    job.done.set()


def _work(key, line):
    try:
        _print_all(key, line)
    except BaseException:
        logger.exception('The print worker of %s stopped', key)
    finally:
        with _lock:
            if line.worker is threading.current_thread():
                line.worker = None
                # Jobs added while this worker was stopping still need one
                if _lines.get(key) is line:
                    try:
                        _start_worker(key, line)
                    except Exception:
                        logger.exception('No print worker for %s could be started', key)
                        while line.waiting:
                            _end(line.waiting.popleft(), 'failed', FAILED)


def _print_all(key, line):
    while True:
        with _lock:
            if _lines.get(key) is not line or line.held is not None or not line.waiting:
                line.worker = None
                return
            job = line.printing = line.waiting.popleft()
            job.state = 'printing'
        message, error = FAILED, True
        try:
            try:
                message, error = job.run(), False
            except Exception as e:
                logger.exception('Print job %s failed', job.id)
                message = job.explain(e)
        except Exception:
            logger.exception('Explaining the failure of print job %s failed', job.id)
        finally:
            with _lock:
                _end(job, 'failed' if error or message else 'printed', message)
                line.printing = None
                if message and not error and line.waiting:
                    line.held = message


def add(device: str, run: Callable[[], str], *, title: str, labels: int, source: str, preview: bytes | None = None,
        explain: Callable[[Exception], str]) -> Job:
    """Queue a job. run() prints and returns '' or the reason it did not
    print, explain() words an error it raises."""
    key = key_of(device)
    with _lock:
        _prune()
        line = _lines.setdefault(key, _Line(device))
        if len(line.waiting) >= MAX_WAITING:
            raise Full()
        job = Job(uuid.uuid4().hex, key, title, labels, source, preview=preview, run=run, explain=explain)
        _jobs[job.id] = job
        line.waiting.append(job)
        try:
            _start_worker(key, line)
        except BaseException:
            line.waiting.remove(job)
            del _jobs[job.id]
            raise
        return job


def full(device: str) -> bool:
    """Whether the line of the device has MAX_WAITING jobs waiting, which
    add() refuses."""
    with _lock:
        current = _lines.get(key_of(device))
        return current is not None and len(current.waiting) >= MAX_WAITING


def get(job_id: str) -> Job | None:
    with _lock:
        _prune()
        return _jobs.get(job_id)


def _release_if_empty(line):
    if line.held is not None and not line.waiting:
        line.held = None


def line(device: str) -> dict:
    with _lock:
        _prune()
        current = _lines.get(key_of(device))
        if current is None:
            return {'printing': None, 'waiting': [], 'held': None}
        return {'printing': current.printing.told() if current.printing else None,
                'waiting': [job.told() for job in current.waiting], 'held': current.held}


def lines() -> list[dict]:
    """Every line with a printing job, waiting jobs or a hold, as line()
    tells it, with the device path it was added under as printer."""
    with _lock:
        _prune()
        return [{'printer': current.device, 'printing': current.printing.told() if current.printing else None,
                 'waiting': [job.told() for job in current.waiting], 'held': current.held}
                for current in _lines.values() if current.printing or current.waiting or current.held is not None]


def summary(device: str) -> dict:
    told = line(device)
    return {'printing': told['printing'] is not None, 'waiting': len(told['waiting']), 'held': told['held'] is not None}


def remove(job_id: str) -> str | None:
    with _lock:
        _prune()
        job = _jobs.get(job_id)
        if job is None:
            return None
        if job.state == 'printing':
            return PRINTING_ALREADY
        if job.state != 'waiting':
            return NOT_WAITING
        current = _lines[job.key]
        current.waiting.remove(job)
        _end(job, 'removed', REMOVED)
        _release_if_empty(current)
        return ''


def resume(device: str) -> None:
    with _lock:
        _prune()
        key = key_of(device)
        current = _lines.get(key)
        if current is not None:
            current.held = None
            _start_worker(key, current)


def reset() -> None:
    """For the tests: let the running jobs end, then forget everything."""
    with _lock:
        workers = [current.worker for current in _lines.values() if current.worker is not None]
    deadline = time.monotonic() + 30
    for worker in workers:
        worker.join(max(0.0, deadline - time.monotonic()))
    with _lock:
        _lines.clear()
        _jobs.clear()
