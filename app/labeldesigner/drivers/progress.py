"""How far a printer is with the job it prints, and how long that still
takes. The estimate comes from the print speed the maker gives for the
model, which printers rarely reach. It is corrected by how long the jobs of
the printer took before, and while printing by the labels the printer
reports printed."""

import threading
import time

from . import devices

# A job takes at most this many times as long as estimated, and at least
# its inverse, as far as later estimates are concerned. A job that waited
# for a closed cover says little about the printer.
SLOWEST = 4

# The job each printer prints, by lock key
_jobs = {}
# How long the jobs of each printer took compared to the estimate, by lock
# key and mode, e.g. printing fast
_factors = {}
_guard = threading.Lock()


class _Job:
    def __init__(self, seconds: list[float], estimated: bool, factor: float, mode):
        # The estimated seconds of each label, corrected by the factor
        self.seconds = [s * factor for s in seconds]
        self.raw = sum(seconds)
        self.estimated = estimated
        self.mode = mode
        self.started = self.printed_at = time.monotonic()
        self.printed = 0
        self.counted = False
        self.stage = None
        self.percent = None


def start(device: str, lengths: list[float], speed: tuple[float, float], mode=None):
    """A job starts on the printer: labels of lengths in mm along the paper,
    printed at speed, mm/s and the seconds each label takes besides, e.g. to
    feed and cut it. A speed of 0 gives no estimate."""
    mm_per_second, besides = speed
    seconds = [besides + length / mm_per_second for length in lengths] if mm_per_second > 0 else [0.0] * len(lengths)
    key = devices.lock_key(device)
    with _guard:
        _jobs[key] = _Job(seconds, mm_per_second > 0, _factors.get((key, mode), 1.0), mode)


def counting(device: str):
    """The printer of the job tells each label it printed, see printed()."""
    with _guard:
        job = _jobs.get(devices.lock_key(device))
        if job:
            job.counted = True


def printed(device: str, count: int):
    """The printer printed count labels of the job so far."""
    with _guard:
        job = _jobs.get(devices.lock_key(device))
        if job and count > job.printed:
            job.printed, job.printed_at = count, time.monotonic()
            job.stage = job.percent = None


def stage(device: str, name: str, percent: int | None = None):
    """What the printer does with the label it prints, e.g. preheating."""
    with _guard:
        job = _jobs.get(devices.lock_key(device))
        if job:
            job.stage, job.percent = name, percent


def finish(device: str, success: bool):
    """The job ended. One that printed all its labels tells how fast the
    printer prints in its mode."""
    key = devices.lock_key(device)
    with _guard:
        job = _jobs.pop(key, None)
        if not (job and success and job.estimated and job.raw > 0):
            return
        factor = min(max((time.monotonic() - job.started) / job.raw, 1 / SLOWEST), SLOWEST)
        before = _factors.get((key, job.mode))
        _factors[(key, job.mode)] = factor if before is None else (before + factor) / 2


def running(device: str) -> bool:
    return devices.lock_key(device) in _jobs


def status_wait(device: str) -> float:
    """How long a status query waits for the printer: not at all while a
    job of this server holds it, the job tells how it is."""
    return 0 if running(device) else devices.STATUS_LOCK_TIMEOUT


def state(device: str) -> dict | None:
    """The job of the printer as /api/printer_status tells it, None if it
    prints none: its labels, the label it prints if it tells, what it does
    with it if it tells, and the seconds left if they can be estimated."""
    with _guard:
        job = _jobs.get(devices.lock_key(device))
        if not job:
            return None
        result = {'labels': len(job.seconds)}
        if job.counted:
            result['label'] = min(job.printed + 1, len(job.seconds))
        if job.stage:
            result['stage'] = job.stage
            if job.percent is not None:
                result['percent'] = job.percent
        if job.estimated:
            result['seconds_left'] = round(_seconds_left(job))
        return result


def _seconds_left(job: _Job) -> float:
    done = job.printed
    # The labels printed tell how much slower or faster than estimated
    # the printer prints this job
    before = sum(job.seconds[:done])
    scale = (job.printed_at - job.started) / before if done and before > 0 else 1
    since = time.monotonic() - job.printed_at
    left = scale * sum(job.seconds[done:]) - since
    # The label being printed may take longer than estimated
    return max(left, scale * sum(job.seconds[done + 1:]))
