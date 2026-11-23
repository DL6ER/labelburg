"""The print queue and the labels printed, kept on disk in hidden folders of
the folder of the labels: jobs still waiting when the server stops print once
it starts again, and labels printed can be printed again from any browser.
The files hold the contents of labels, only the server may read them.

One process of the server keeps the queue on disk, the first one to lock it;
another one, e.g. a second worker of gunicorn, keeps its queue in memory.
Every process adds the jobs it ended to the history."""

import concurrent.futures
import contextlib
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid

from .drivers.devices import flock_within

try:
    import fcntl
except ImportError:
    # Windows: one process
    fcntl = None

logger = logging.getLogger(__name__)

QUEUE = '.print-queue'
HISTORY = '.print-history'
HISTORY_FILE = 'history.jsonl'
LOCK_FILE = '.lock'
# Jobs of the queue that cannot be read, kept for a look by hand for a week
BROKEN = 'broken'
BROKEN_DAYS = 7
VERSION = 1
KINDS = ('request', 'image', 'calibration', 'feed')
# The history keeps the entries of the last year, at most this many. The
# file is cut back once it has half as many again.
HISTORY_KEEP = 1000
HISTORY_DAYS = 365
# Beyond it the images of the oldest entries go, they cannot be printed again
HISTORY_IMAGE_BYTES = 200 * 1024 * 1024
# A job is kept in memory only where the disk has less room
MIN_FREE_BYTES = 50 * 1024 * 1024
LOCK_TIMEOUT = 10
# How often a process without the queue tries to take it, e.g. the new worker
# of gunicorn while the one it replaces still prints
LOCK_RETRY = 10

# Off in the tests, unless a test opens it
ENABLED = True
# Where both folders are, None in the folder of the labels
FOLDER = None

PRINTING_AT_RESTART = 'The label designer was restarted while it printed'
NO_SUCH_ENTRY = 'No such printed label'
NO_IMAGE = 'Its image is no longer kept'
NOT_MADE_HERE = 'Only labels made here can be opened'
NOT_KEPT = 'Printed labels are not kept'
NOT_CLEARED = 'The printed labels could not be cleared, the log of the server names the reason'
# At most this many entries are read at a time
MAX_LIMIT = 200
LIMIT_REFUSED = f'limit must be a whole number from 1 to {MAX_LIMIT}'

_lock = threading.Lock()
# Writes the images of jobs while their requests draw their labels
_writer = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix='print-store')
# The folder opened, None while the store is closed
_base = None
# The lock of the queue while this process keeps it on disk
_queue_fd = None
_warned = False
# Changes with each start and close, a retry of an earlier one gives up
_opened = 0
# Lines in the history file, bytes of the images kept and when the oldest
# entry ended, as this process counted them
_lines = 0
_image_bytes = 0
_oldest = None


def start(folder: str, restore=None) -> list[dict]:
    """Open the store in folder, FOLDER where it is set. The jobs left in the
    queue, oldest first: those waiting and the one that printed. Jobs that
    cannot be read are moved to broken/, never raised. Where another process
    keeps the queue, this one takes it once that one left, and calls
    restore() with the jobs left then."""
    global _base, _queue_fd
    if not ENABLED:
        return []
    close()
    base = FOLDER or folder
    try:
        for name in (QUEUE, HISTORY):
            os.makedirs(os.path.join(base, name), mode=0o700, exist_ok=True)
    except OSError as e:
        logger.warning('Print jobs and printed labels are not kept, %s cannot take them: %s', base, e)
        return []
    _base = base
    _count_history()
    try:
        fd = _locked_queue()
    except OSError as e:
        logger.warning('The print queue is kept in memory only, it cannot be locked: %s', e)
        return []
    if fd is None:
        logger.info('Another process of the server keeps the print queue on disk, this one keeps its own in memory until it leaves')
        if restore is not None:
            threading.Thread(target=_take_queue, args=(_opened, restore), name='print-store-lock', daemon=True).start()
        return []
    # Listed before jobs of this process are kept, they are not left over
    left = _left_in_queue()
    _queue_fd = fd
    return left


def _locked_queue() -> int | None:
    """The lock of the queue opened and taken, None where another process
    holds it."""
    fd = os.open(_queue_path(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if fcntl is None or flock_within(fd, 0):
            return fd
    except BaseException:
        os.close(fd)
        raise
    os.close(fd)
    return None


def _take_queue(opened: int, restore):
    """Take the queue once the process that holds it left, while the store
    stays as it was opened, and restore() the jobs left in it."""
    global _queue_fd
    while True:
        time.sleep(LOCK_RETRY)
        with _lock:
            if opened != _opened:
                return
            try:
                fd = _locked_queue()
            except OSError as e:
                logger.warning('The print queue is kept in memory only, it cannot be locked: %s', e)
                return
            if fd is not None:
                # Requests meanwhile keep their jobs in memory, they are not left over
                left = _left_in_queue()
                _queue_fd = fd
                break
    logger.info('This process keeps the print queue on disk now')
    restore(left)


def close():
    """Close the store, e.g. for the next test."""
    global _base, _queue_fd, _warned, _lines, _image_bytes, _oldest, _opened
    _opened += 1
    if _queue_fd is not None:
        os.close(_queue_fd)
    _base = _queue_fd = _oldest = None
    _warned = False
    _lines = _image_bytes = 0


def is_open() -> bool:
    return _base is not None


def _queue_path(name: str) -> str:
    return os.path.join(_base, QUEUE, name)


def _history_path(name: str) -> str:
    return os.path.join(_base, HISTORY, name)


def _write(path: str, content: bytes):
    """Write a file readable by the server only, in one step and on disk:
    it either has the old content or the new one."""
    staged = os.path.join(os.path.dirname(path), f'.{uuid.uuid4().hex}.tmp')
    fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(staged, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(staged)
        raise


def png(image) -> bytes:
    """image as PNG, quickly made: it is written while the request waits."""
    out = io.BytesIO()
    try:
        image.save(out, 'PNG', compress_level=1)
    except OSError:
        # E.g. CMYK, which PNG has not
        out = io.BytesIO()
        image.convert('RGBA' if 'A' in image.getbands() else 'RGB').save(out, 'PNG', compress_level=1)
    return out.getvalue()


def _job_files(directory: str, job_id: str) -> list[str]:
    return [name for name in os.listdir(directory) if name == f'{job_id}.json' or name.startswith(f'{job_id}-')]


def image_name(job_id: str, number: int) -> str:
    return f'{job_id}-{number}.png'


def preview_name(job_id: str) -> str:
    return f'{job_id}-preview.png'


# --- The queue -----------------------------------------------------------------------

def stage(job_id: str, images: list) -> concurrent.futures.Future | None:
    """Start to write the images of a job before it is queued, e.g. while its
    request draws its label, see queued(). None where nothing is written."""
    if _queue_fd is None or not images:
        return None
    return _writer.submit(_write_images, job_id, list(images))


def _write_images(job_id: str, images: list) -> list[str]:
    """The files of the images written, none where one cannot be."""
    written = []
    try:
        if shutil.disk_usage(_queue_path('')).free < MIN_FREE_BYTES:
            raise OSError('less than 50 MB free')
        for number, image in enumerate(images):
            written.append(_queue_path(image_name(job_id, number)))
            _write(written[-1], png(image))
    except BaseException:
        _remove(written)
        raise
    return written


def _remove(paths: list[str]):
    for path in paths:
        with contextlib.suppress(OSError):
            os.remove(path)


def discard(job_id: str, staged: concurrent.futures.Future | None):
    """The images stage() wrote for a job that is not queued."""
    if staged is not None:
        with contextlib.suppress(Exception):
            _remove(staged.result())


def queued(record: dict, images: list, preview: bytes | None, staged: concurrent.futures.Future | None = None) -> bool:
    """Keep a job before it is accepted: its images, written by stage() where
    it started them, its preview, then its record. Whether it is on disk; a
    job that is not prints from memory."""
    global _warned
    if _queue_fd is None:
        return False
    job_id, written = record['id'], []
    try:
        written = staged.result() if staged is not None else _write_images(job_id, images)
        if preview is not None:
            written.append(_queue_path(preview_name(job_id)))
            _write(written[-1], preview)
        _write(_queue_path(f'{job_id}.json'), json.dumps(record, ensure_ascii=False, default=str).encode('utf-8'))
        return True
    except Exception as e:
        # Also an image that cannot be saved, e.g. one cut off
        _remove(written)
        if not _warned:
            _warned = True
            logger.warning('A print job is kept in memory only, it would be lost on a restart: %s', e)
        return False


def started(record: dict):
    """The job of record starts to print. Where that cannot be kept, it leaves
    the queue on disk: kept as waiting it would print again after a crash."""
    record.update(state='printing', started_at=time.time())
    if _queue_fd is not None and os.path.exists(path := _queue_path(f"{record['id']}.json")):
        try:
            _write(path, json.dumps(record, ensure_ascii=False, default=str).encode('utf-8'))
        except OSError as e:
            logger.warning('The start of print job %s was not kept, it is no longer kept on disk: %s', record['id'], e)
            with contextlib.suppress(OSError):
                os.remove(path)


def _left_in_queue() -> list[dict]:
    directory = _queue_path('')
    kept_ids = _history_ids()
    records = []
    for name in sorted(os.listdir(directory)):
        if name.startswith('.') and name.endswith('.tmp'):
            with contextlib.suppress(OSError):
                os.remove(os.path.join(directory, name))
            continue
        if not name.endswith('.json'):
            continue
        job_id = name[:-len('.json')]
        try:
            with open(os.path.join(directory, name), encoding='utf-8') as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            record = None
        if job_id in kept_ids:
            # It ended, its end was not cleaned up
            _remove_job(job_id)
        elif not _complete(record, job_id):
            _broken(job_id)
        else:
            records.append(record)
    _drop_broken(time.time() - BROKEN_DAYS * 86400)
    # Images of jobs that ended without their record
    known = {record['id'] for record in records}
    for name in os.listdir(directory):
        if name.endswith('.png') and name.split('-')[0] not in known:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(directory, name))
    return sorted(records, key=lambda record: record['queued_at'])


def _complete(record, job_id: str) -> bool:
    """Whether record can be printed: of this version, with all it needs and
    its images."""
    if not isinstance(record, dict) or record.get('v') != VERSION or record.get('id') != job_id:
        return False
    printer = record.get('printer')
    if (record.get('kind') not in KINDS or record.get('state') not in ('waiting', 'printing')
            or not isinstance(printer, dict) or not all(isinstance(printer.get(key), str) for key in ('device', 'model', 'label_size'))
            or not isinstance(record.get('queued_at'), (int, float)) or not isinstance(record.get('images', 0), int)):
        return False
    return all(os.path.exists(_queue_path(image_name(job_id, number))) for number in range(record.get('images', 0)))


def _broken(job_id: str):
    logger.warning('Print job %s cannot be read, it is moved to %s', job_id, os.path.join(QUEUE, BROKEN))
    broken = _queue_path(BROKEN)
    try:
        os.makedirs(broken, mode=0o700, exist_ok=True)
        for name in _job_files(_queue_path(''), job_id):
            os.replace(_queue_path(name), os.path.join(broken, name))
    except OSError as e:
        logger.warning('Print job %s was not moved: %s', job_id, e)
        _remove_job(job_id)


def _drop_broken(before: float):
    """The files of jobs that could not be read, those written before."""
    with contextlib.suppress(OSError):
        for entry in os.scandir(_queue_path(BROKEN)):
            if entry.stat().st_mtime < before:
                with contextlib.suppress(OSError):
                    os.remove(entry.path)


def _remove_job(job_id: str):
    """The files of a job that ended, its record first: images left behind
    go at the next start."""
    directory = _queue_path('')
    with contextlib.suppress(OSError):
        os.remove(os.path.join(directory, f'{job_id}.json'))
    for name in _job_files(directory, job_id):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(directory, name))


def ended(record: dict, state: str, message: str, keep: bool, images: list | None = None, preview: bytes | None = None):
    """The job of record ended: added to the history where keep, its files
    leave the queue. images and preview are those of a job that is not on
    disk."""
    if _base is None:
        return
    try:
        if keep and record.get('kind') not in ('calibration', 'feed'):
            _keep(record, state, message, images, preview)
    except OSError as e:
        logger.warning('Printed label %s was not kept: %s', record['id'], e)
    finally:
        if _queue_fd is not None:
            _remove_job(record['id'])


# --- The history ---------------------------------------------------------------------

def _keep(record: dict, state: str, message: str, images: list | None, preview: bytes | None):
    global _lines, _image_bytes, _oldest
    job_id = record['id']
    entry = {**record, 'state': state, 'message': message, 'ended_at': time.time()}
    with _history_locked():
        size = 0
        try:
            for number in range(record.get('images', 0)):
                name = image_name(job_id, number)
                if _queue_fd is not None and os.path.exists(_queue_path(name)):
                    os.replace(_queue_path(name), _history_path(name))
                elif images is not None:
                    _write(_history_path(name), png(images[number]))
                size += os.path.getsize(_history_path(name))
            name = preview_name(job_id)
            if _queue_fd is not None and os.path.exists(_queue_path(name)):
                os.replace(_queue_path(name), _history_path(name))
            elif preview is not None:
                _write(_history_path(name), preview)
        except OSError as e:
            # Listed without them, it cannot be printed again
            logger.warning('The images of printed label %s were not kept: %s', job_id, e)
        fd = os.open(_history_path(HISTORY_FILE), os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            # On a line of its own after one cut off, e.g. by a crash
            end = os.fstat(fd).st_size
            torn = end > 0 and os.pread(fd, 1, end - 1) != b'\n'
            os.write(fd, (('\n' if torn else '') + json.dumps(entry, ensure_ascii=False, default=str) + '\n').encode('utf-8'))
        finally:
            os.close(fd)
        _lines += 1
        _image_bytes += size
        _oldest = entry['ended_at'] if _oldest is None else _oldest
        if _lines > HISTORY_KEEP * 3 // 2 or _oldest < time.time() - HISTORY_DAYS * 86400:
            _compact()
        if _image_bytes > HISTORY_IMAGE_BYTES:
            _drop_images()


@contextlib.contextmanager
def _history_locked():
    """The history to change: in threads by a lock, in processes by locking
    a file next to it. Raises OSError where another process holds it for
    longer than LOCK_TIMEOUT."""
    with _lock:
        fd = os.open(_history_path(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None and not flock_within(fd, LOCK_TIMEOUT):
                raise OSError('the history is locked by another process')
            yield
        finally:
            os.close(fd)


def _read() -> list[tuple[dict, str]]:
    """Every entry of the history file that can be read, oldest first, with
    its line. A line cut off, e.g. by a crash, is left out."""
    try:
        with open(_history_path(HISTORY_FILE), encoding='utf-8', errors='replace') as fh:
            lines = fh.readlines()
    except FileNotFoundError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get('id'), str) and isinstance(entry.get('ended_at'), (int, float)):
            entries.append((entry, line if line.endswith('\n') else line + '\n'))
    return entries


def _count_history():
    global _lines, _image_bytes
    _lines = len(_read())
    _image_bytes = _images_size()
    with contextlib.suppress(OSError):
        with _history_locked():
            _compact()
            if _image_bytes > HISTORY_IMAGE_BYTES:
                _drop_images()


def _images_size() -> int:
    return sum(entry.stat().st_size for entry in os.scandir(_history_path('')) if entry.name.endswith('.png') and not entry.name.endswith('-preview.png'))


def _compact():
    """The history cut back to the newest HISTORY_KEEP entries of the last
    HISTORY_DAYS days, also the files of the entries left out. The caller
    holds _history_locked()."""
    global _lines, _image_bytes, _oldest
    cutoff = time.time() - HISTORY_DAYS * 86400
    entries = _read()
    kept = [(entry, line) for entry, line in entries if entry['ended_at'] >= cutoff][-HISTORY_KEEP:]
    if len(kept) < len(entries) or entries and os.path.getsize(_history_path(HISTORY_FILE)) != sum(len(line.encode('utf-8')) for _, line in entries):
        _write(_history_path(HISTORY_FILE), ''.join(line for _, line in kept).encode('utf-8'))
    ids = {entry['id'] for entry, _ in kept}
    for name in os.listdir(_history_path('')):
        # Also files a crash left half written
        if name.endswith('.tmp') or name.endswith('.png') and name.split('-')[0] not in ids:
            with contextlib.suppress(OSError):
                os.remove(_history_path(name))
    _lines = len(kept)
    _image_bytes = _images_size()
    _oldest = min((entry['ended_at'] for entry, _ in kept), default=None)


def _drop_images():
    """The images of the oldest entries removed until those left take at most
    HISTORY_IMAGE_BYTES. The caller holds _history_locked()."""
    global _image_bytes
    for entry, _ in _read():
        if _image_bytes <= HISTORY_IMAGE_BYTES:
            break
        for number in range(entry.get('images', 0) if isinstance(entry.get('images'), int) else 0):
            path = _history_path(image_name(entry['id'], number))
            with contextlib.suppress(OSError):
                size = os.path.getsize(path)
                os.remove(path)
                _image_bytes -= size


def _history_ids() -> set:
    return {entry['id'] for entry, _ in _read()}


def entries(limit: int, before: str | None = None) -> list[dict]:
    """The entries of the history of this version, newest first: limit of
    them, those after the entry before where it is given."""
    if _base is None:
        return []
    cutoff = time.time() - HISTORY_DAYS * 86400
    newest = [entry for entry, _ in reversed(_read()) if entry.get('v') == VERSION and entry['ended_at'] >= cutoff]
    if before is not None:
        ids = [entry['id'] for entry in newest]
        newest = newest[ids.index(before) + 1:] if before in ids else []
    return newest[:limit]


def entry(job_id: str) -> dict | None:
    """The entry of the job, the last one where it is there twice."""
    if _base is None:
        return None
    return next((entry for entry in entries(HISTORY_KEEP * 2) if entry['id'] == job_id), None)


def history_folder() -> str:
    return _history_path('')


def _kept_file(name: str, job_id: str) -> str | None:
    """The file name of the history, None where it is not kept. job_id may
    come from an address, it names no other file."""
    if _base is None or not re.fullmatch(r'\w{1,64}', job_id):
        return None
    # Normalized and checked to stay in the folder, as CodeQL knows it
    folder = os.path.normpath(_history_path(''))
    path = os.path.normpath(_history_path(name))
    return path if path.startswith(folder + os.sep) and os.path.exists(path) else None


def image_path(job_id: str, number: int) -> str | None:
    """The file of an image of an entry, None where it is not kept."""
    return _kept_file(image_name(job_id, number), job_id)


def preview_path(job_id: str) -> str | None:
    return _kept_file(preview_name(job_id), job_id)


def images_kept(entry: dict) -> bool:
    return all(image_path(entry['id'], number) for number in range(entry.get('images', 0)))


def clear():
    """Remove every entry of the history and its images, also the jobs of
    the queue that could not be read. Raises OSError where they cannot be
    removed."""
    global _lines, _image_bytes, _oldest
    if _base is None:
        return
    with _history_locked():
        for name in os.listdir(_history_path('')):
            if name != LOCK_FILE:
                os.remove(_history_path(name))
        _lines = _image_bytes = 0
        _oldest = None
        if os.path.isdir(_queue_path(BROKEN)):
            for name in os.listdir(_queue_path(BROKEN)):
                os.remove(os.path.join(_queue_path(BROKEN), name))


def queued_image(job_id: str, number: int) -> str:
    """The file of an image of a job in the queue."""
    return _queue_path(image_name(job_id, number))


def queued_preview(job_id: str) -> bytes | None:
    try:
        with open(_queue_path(preview_name(job_id)), 'rb') as fh:
            return fh.read()
    except OSError:
        return None
