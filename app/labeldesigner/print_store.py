"""The print queue and the labels printed, kept on disk in hidden folders of
the folder of the labels: jobs still waiting when the server stops print once
it starts again, and labels printed can be printed again from any browser.
The files hold the contents of labels, only the server may read them.

One process of the server keeps the queue on disk, the first one to lock it;
another one, e.g. a second worker of gunicorn, keeps its queue in memory.
Every process adds the jobs it ended to the history."""

import contextlib
import io
import json
import logging
import os
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
# Jobs of the queue that cannot be read, kept for a look by hand
BROKEN = 'broken'
VERSION = 1
KINDS = ('request', 'image', 'calibration')
# The history keeps the entries of the last year, at most this many. The
# file is cut back once it has half as many again.
HISTORY_KEEP = 1000
HISTORY_DAYS = 365
# Beyond it the images of the oldest entries go, they cannot be printed again
HISTORY_IMAGE_BYTES = 200 * 1024 * 1024
# A job is kept in memory only where the disk has less room
MIN_FREE_BYTES = 50 * 1024 * 1024
LOCK_TIMEOUT = 10

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
# The folder opened, None while the store is closed
_base = None
# The lock of the queue while this process keeps it on disk
_queue_fd = None
_warned = False
# Lines in the history file and bytes of the images kept, as this process
# counted them
_lines = 0
_image_bytes = 0


def start(folder: str) -> list[dict]:
    """Open the store in folder, FOLDER where it is set. The jobs left in the
    queue, oldest first: those waiting and the one that printed. Jobs that
    cannot be read are moved to broken/, never raised."""
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
    fd = None
    try:
        fd = os.open(_queue_path(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o600)
        if fcntl is not None and not flock_within(fd, 0):
            os.close(fd)
            logger.info('Another process of the server keeps the print queue on disk, this one keeps its own in memory')
            return []
    except OSError as e:
        if fd is not None:
            os.close(fd)
        logger.warning('The print queue is kept in memory only, it cannot be locked: %s', e)
        return []
    _queue_fd = fd
    return _left_in_queue()


def close():
    """Close the store, e.g. for the next test."""
    global _base, _queue_fd, _warned, _lines, _image_bytes
    if _queue_fd is not None:
        os.close(_queue_fd)
    _base = _queue_fd = None
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

def queued(record: dict, images: list, preview: bytes | None) -> bool:
    """Keep a job before it is accepted: its images, its preview, then its
    record. Whether it is on disk; a job that is not prints from memory."""
    global _warned
    if _queue_fd is None:
        return False
    job_id, written = record['id'], []
    try:
        if shutil.disk_usage(_queue_path('')).free < MIN_FREE_BYTES:
            raise OSError('less than 50 MB free')
        for number, image in enumerate(images):
            written.append(_queue_path(image_name(job_id, number)))
            _write(written[-1], png(image))
        if preview is not None:
            written.append(_queue_path(preview_name(job_id)))
            _write(written[-1], preview)
        _write(_queue_path(f'{job_id}.json'), json.dumps(record, ensure_ascii=False, default=str).encode('utf-8'))
        return True
    except (OSError, ValueError) as e:
        for path in written:
            with contextlib.suppress(OSError):
                os.remove(path)
        if not _warned:
            _warned = True
            logger.warning('A print job is kept in memory only, it would be lost on a restart: %s', e)
        return False


def started(record: dict):
    """The job of record starts to print."""
    record.update(state='printing', started_at=time.time())
    if _queue_fd is not None and os.path.exists(path := _queue_path(f"{record['id']}.json")):
        try:
            _write(path, json.dumps(record, ensure_ascii=False, default=str).encode('utf-8'))
        except OSError as e:
            logger.warning('The start of print job %s was not kept: %s', record['id'], e)


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
        if keep and record.get('kind') != 'calibration':
            _keep(record, state, message, images, preview)
    except OSError as e:
        logger.warning('Printed label %s was not kept: %s', record['id'], e)
    finally:
        if _queue_fd is not None:
            _remove_job(record['id'])


# --- The history ---------------------------------------------------------------------

def _keep(record: dict, state: str, message: str, images: list | None, preview: bytes | None):
    global _lines, _image_bytes
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
        if _lines > HISTORY_KEEP * 3 // 2:
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
    global _lines, _image_bytes
    cutoff = time.time() - HISTORY_DAYS * 86400
    entries = _read()
    kept = [(entry, line) for entry, line in entries if entry['ended_at'] >= cutoff][-HISTORY_KEEP:]
    if len(kept) < len(entries) or entries and os.path.getsize(_history_path(HISTORY_FILE)) != sum(len(line.encode('utf-8')) for _, line in entries):
        _write(_history_path(HISTORY_FILE), ''.join(line for _, line in kept).encode('utf-8'))
    ids = {entry['id'] for entry, _ in kept}
    for name in os.listdir(_history_path('')):
        if name.endswith('.png') and name.split('-')[0] not in ids:
            with contextlib.suppress(OSError):
                os.remove(_history_path(name))
    _lines = len(kept)
    _image_bytes = _images_size()


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


def image_path(job_id: str, number: int) -> str | None:
    """The file of an image of an entry, None where it is not kept."""
    path = _history_path(image_name(job_id, number)) if _base is not None else None
    return path if path and os.path.exists(path) else None


def preview_path(job_id: str) -> str | None:
    path = _history_path(preview_name(job_id)) if _base is not None else None
    return path if path and os.path.exists(path) else None


def images_kept(entry: dict) -> bool:
    return all(image_path(entry['id'], number) for number in range(entry.get('images', 0)))


def clear():
    """Remove every entry of the history and its images. Raises OSError
    where they cannot be removed."""
    global _lines, _image_bytes
    if _base is None:
        return
    with _history_locked():
        for name in os.listdir(_history_path('')):
            if name != LOCK_FILE:
                os.remove(_history_path(name))
        _lines = _image_bytes = 0


def queued_image(job_id: str, number: int) -> str:
    """The file of an image of a job in the queue."""
    return _queue_path(image_name(job_id, number))


def queued_preview(job_id: str) -> bytes | None:
    try:
        with open(_queue_path(preview_name(job_id)), 'rb') as fh:
            return fh.read()
    except OSError:
        return None
