"""Brother raster printers on USB: jobs and their status messages, and
status queries."""

import logging
import threading
import time

from .. import devices, progress
from ..backends import open_printer
from .status import HEADER, LENGTH, StatusError, parse_status

logger = logging.getLogger(__name__)

# How long a USB printer may stay silent after a job was sent and between
# its status messages: as long as a page of the job takes to print at the
# slowest printers, about 10 mm/s, at least STATUS_WAIT seconds.
STATUS_WAIT = 10
SLOWEST_PRINT_SPEED = 10
# A job is written in pieces: pyusb gives up on a single write after 15 s,
# a long label that the printer prints while it receives it takes longer
WRITE_CHUNK = 16384
# Reads of what came before at most: enough for a QL-1100 answering every
# poll it held an error through, a bound for a printer that keeps sending
DRAIN_READS = 100
# How long a reply owed to an earlier status request is waited for: the
# printer has it already, it may be a round trip to a USB/IP host away
REPLY_WAIT = 0.5
# How long a job that is not confirmed waits for the reply to the status
# request it holds, as get_status() waits for one
JOB_REPLY_WAIT = 2.0
# Status requests counted at most, also through an error held for hours: a
# reply that does not come ends the wait for the others at once
MOST_UNANSWERED = 1000
# How long a query reads owed replies at most: it holds the device
# meanwhile, the others are read next time
OWED_WAIT = 5
# How long a status request left unanswered counts as outstanding: queries
# meanwhile write nothing and only read whether its answer came
ASK_AGAIN = 20

# The status requests each printer has not answered yet, by devices.lock_key()
_unanswered: dict[str, int] = {}
# When each printer was asked the requests that are outstanding, by
# devices.lock_key()
_asked: dict[str, float] = {}
# The printers whose outstanding request was answered only by the retry
# after it, e.g. a QL-560: that answer is their status meanwhile
_retry_answered: set[str] = set()


def send_job(data: bytes, device_specifier: str, page_lengths: list, confirmed: bool = True) -> dict:
    """Send a print job to a USB printer and read its status messages until
    it reports every page printed and that it is ready for the next job, an
    error, or until it stays silent for longer than a page takes to print.
    A long label is printed while it is received, a job of several labels
    sends messages for each of them.

    page_lengths: the length of each page in mm. Without confirmed, the job
    counts as printed unless the reply to its status request tells an error.
    Returns, as print_failure() reads it: did_print, ready_for_next_job, the
    last printer_state, and the pages of the job and how many the printer
    confirmed."""
    info = {'did_print': False, 'ready_for_next_job': False, 'printer_state': None,
            'pages': len(page_lengths), 'confirmed': 0}
    key = devices.lock_key(device_specifier)
    owed = unanswered = _unanswered.get(key, 0)
    printer = get_printer(device_specifier)
    if confirmed:
        progress.counting(device_specifier)
    try:
        # Not waited for, a reply owed that came is read
        unanswered = _drain(printer, unanswered, key=key)
        received = _write_reading(printer, data)
        if not confirmed:
            reply, unanswered = _job_reply(printer, received, unanswered)
            info['printer_state'] = reply
            if reply and reply['errors']:
                devices.status_cache[key] = reply
            else:
                info.update(did_print=True, ready_for_next_job=True, confirmed=len(page_lengths))
            return info
        # A page may take as long as the longest one, the whole job as long
        # as all its pages
        page_wait = STATUS_WAIT + max(page_lengths, default=0) / SLOWEST_PRINT_SPEED
        job_wait = sum(STATUS_WAIT + length / SLOWEST_PRINT_SPEED for length in page_lengths)
        give_up = time.monotonic() + job_wait
        deadline = min(time.monotonic() + page_wait, give_up)
        printing = False
        while time.monotonic() < deadline:
            states, received = _status_messages(received + (printer.read(32) or b''))
            if not states:
                time.sleep(0.02)
                continue
            for state in states:
                if unanswered:
                    if state['status_code'] == 0x00:
                        # Owed replies come before the job's messages, the
                        # job has not begun meanwhile
                        unanswered -= 1
                        give_up = time.monotonic() + job_wait
                        continue
                    if not state['errors']:
                        # The job's messages began, the others will not come.
                        # A printer holding an error answers them once cleared.
                        unanswered = 0
                info['printer_state'] = state
                if state['errors']:
                    # The status the printer holds, see query_status()
                    devices.status_cache[devices.lock_key(device_specifier)] = state
                    return info
                if state['status_type'] == 'Printing completed':
                    info['confirmed'] += 1
                    printing = False
                elif state['phase_type'] == 'Printing state':
                    printing = True
                elif printing:
                    # The QL-800 and QL-1100 report only the last page of a job
                    # completed, the others end with them waiting to receive again
                    info['confirmed'] += 1
                    printing = False
                if state['status_type'] == 'Phase change':
                    # "Printing" before each page, "waiting to receive" after it
                    info['ready_for_next_job'] = state['phase_type'] == 'Waiting to receive'
            deadline = min(time.monotonic() + page_wait, give_up)
            progress.printed(device_specifier, info['confirmed'])
            info['did_print'] = info['confirmed'] >= info['pages']
            if info['did_print'] and info['ready_for_next_job']:
                return info
        return info
    finally:
        _unanswered[key] = unanswered
        if unanswered < owed:
            # Answered, or the replies will not come
            _asked.pop(key, None)
        if info['did_print'] and info['printer_state']:
            # In place of e.g. an error told before the job, which is gone
            devices.status_cache[key] = info['printer_state']
        _release(printer)


def _job_reply(printer, received: bytes, unanswered: int) -> tuple[dict | None, int]:
    """The reply to the status request in a job, or an error the printer
    tells unasked, None if neither comes within JOB_REPLY_WAIT. The replies
    of the unanswered requests before come first, they are counted off."""
    until = time.monotonic() + JOB_REPLY_WAIT
    while True:
        states, received = _status_messages(received)
        for state in states:
            if state['status_code'] == 0x00 and unanswered:
                unanswered -= 1
            elif state['status_code'] == 0x00 or state['errors']:
                return state, unanswered
        if time.monotonic() >= until:
            return None, unanswered
        time.sleep(0.02)
        received += printer.read(32) or b''


def _write_reading(printer, data: bytes) -> bytes:
    """Write a job in pieces and return what the printer said meanwhile. A
    QL-700 takes a job only as fast as it prints it and keeps just two status
    messages that are not read, so they are read while the job is written."""
    received, failed = [], []
    written = threading.Event()

    def read():
        try:
            while not written.is_set():
                data = printer.read(32)
                if data:
                    received.append(data)
                else:
                    written.wait(0.005)
        except Exception as e:
            failed.append(e)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        for start in range(0, len(data), WRITE_CHUNK):
            printer.write(data[start:start + WRITE_CHUNK])
    finally:
        written.set()
        reader.join()
    if failed:
        raise failed[0]
    return b''.join(received)


def _drain(printer, unanswered: int = 0, key: str | None = None) -> int:
    """Read what the printer still has to say, e.g. the status messages of an
    earlier job or late replies to status requests, so that they are not
    taken for those of this one. Returns how many of the replies to the
    unanswered requests were not read."""
    for _ in range(DRAIN_READS):
        data = printer.read(32)
        if not data:
            break
        if _is_reply(data):
            unanswered -= 1
        else:
            _keep_error(data, key)
    return max(0, unanswered)


def _read_ahead(printer, owed: int, until: float, key: str | None = None) -> tuple[bytes, int, bool]:
    """Read the replies owed to earlier status requests, each waited for up
    to REPLY_WAIT, until one does not come or the time until is reached.
    Returns the last reply read, how many were not read, and whether the
    time ran out while they were still coming."""
    last = b''
    while owed:
        if time.monotonic() >= until:
            return last, owed, True
        reply = _read_reply(printer, REPLY_WAIT, key)
        if not _is_reply(reply):
            break
        last, owed = reply, owed - 1
    return last, owed, False


def _arrived(printer, owed: int, until: float, key: str | None = None) -> tuple[bytes, int, bool]:
    """The replies owed that came, the first read without waiting for it:
    the last one read, how many are still owed and whether they were still
    coming when the time until ran out, see _read_ahead()."""
    first = _read_reply(printer, 0, key)
    if not _is_reply(first):
        return b'', owed, False
    last, owed, more = _read_ahead(printer, owed - 1, until, key)
    return last or first, owed if more else 0, more


def _is_reply(message: bytes) -> bool:
    """Whether a message is the reply to a status request (status type 00h),
    not one the printer sends unasked, e.g. a phase change."""
    return len(message) == LENGTH and message.startswith(HEADER) and message[18] == 0x00


def _keep_error(message: bytes, key: str | None):
    """Keep an error the printer tells unasked (status type 02h), e.g. while
    it is idle: a QL-1100 then answers no status request, see _held_error()."""
    if key is None or len(message) != LENGTH or not message.startswith(HEADER) or message[18] != 0x02:
        return
    try:
        devices.status_cache[key] = parse_status(message)
    except StatusError:
        logger.warning('Could not read a status message of the printer: %s', message.hex())


def _status_messages(received: bytes) -> tuple[list, bytes]:
    """The status messages in what was read from the printer, and the rest,
    which may be the beginning of the next one. Anything before a message is
    skipped."""
    states = []
    while True:
        start = received.find(HEADER)
        if start < 0:
            # The end may be the beginning of a header
            return states, received[-(len(HEADER) - 1):]
        received = received[start:]
        if len(received) < 32:
            return states, received
        try:
            states.append(parse_status(received[:LENGTH]))
        except Exception:
            logger.warning('Could not read a status message of the printer: %s', received[:32].hex())
        received = received[32:]


def print_failure(info: dict) -> str:
    """Why a print job was not confirmed, from the status read back from the
    printer while printing."""
    state = info.get('printer_state') or {}
    if state.get('errors'):
        return 'The printer reports: ' + ', '.join(state['errors'])
    if 0 < info.get('confirmed', 0) < info.get('pages', 0):
        return f"The printer confirmed {info['confirmed']} of {info['pages']} labels, check whether the others were printed"
    if info.get('did_print'):
        return 'The label was printed, but the printer did not report that it is ready for the next one'
    if state:
        return f"The printer did not confirm that the label was printed (last status: {state.get('status_type')}, {state.get('phase_type')})"
    return 'The printer did not answer after receiving the label, check whether it was printed'


def get_printer(device_specifier: str):
    """A connection to the printer, see backends.open_printer()."""
    return open_printer(device_specifier)


def _read_reply(printer, timeout: float, key: str | None = None) -> bytes:
    """The next reply to a status request, or what came until the timeout.
    Messages the printer sends unasked, e.g. phase changes, are skipped, an
    error among them is kept, see _keep_error()."""
    data = b''
    deadline = time.monotonic() + timeout
    # Read once at least, also without a timeout
    while True:
        data += printer.read(32 - len(data)) or b''
        if len(data) == 32 and data.startswith(HEADER) and not _is_reply(data):
            _keep_error(data, key)
            data = b''
        elif len(data) == 32:
            return data
        elif time.monotonic() < deadline:
            time.sleep(0.02)
        if time.monotonic() >= deadline:
            return data


def _read_status(printer, timeout: float, key: str | None = None) -> bytes:
    """Ask for the status, the 32-byte answer or what came until the timeout.
    Printers such as the QL-1100 need about 100 ms to answer and return empty
    reads until then."""
    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    return _read_reply(printer, timeout, key)


def get_status(printer, timeout: float = 2.0, key: str | None = None):
    """Request and parse the printer status. With key, the printer's lock key,
    the requests it leaves unanswered are counted and count as outstanding
    for ASK_AGAIN seconds, in which no other is sent: a QL-1100 holding an
    error answers them all once the error is cleared, the latest reply read
    is the answer."""
    until = time.monotonic() + OWED_WAIT
    owed = _unanswered.get(key, 0)
    asked = _outstanding(key)
    if owed:
        last, owed, more = _arrived(printer, owed, until, key)
        if last and (more or asked):
            # The answer, the others still coming are read next time
            _unanswered[key] = owed
            _asked.pop(key, None)
            return parse_status(last)
        if asked:
            raise TimeoutError('The status request sent before is not answered yet')
    owed = _drain(printer, owed, key=key)
    sent = time.monotonic()
    data = _read_status(printer, timeout, key)
    unanswered = int(len(data) < 32)
    if unanswered:
        # The QL-560 answers only the first request after power-on. Invalidate
        # and initialize only then, at most once in ASK_AGAIN s: "ESC @" also
        # cancels printing. The QL-560 sends no reply to the first request,
        # it is not waited for here.
        unanswered = _drain(printer, unanswered, key=key)
        printer.write(b"\x00" * 200 + b"\x1b\x40")
        data = _read_status(printer, timeout, key)
    if len(data) == 32:
        # It may be an owed reply, e.g. of an error cleared meanwhile: the last
        # of those following is the answer, ones that do not come are
        # forgotten. A request of which only the retry was answered, e.g. by
        # a QL-560, stays outstanding.
        last, ahead, more = _read_ahead(printer, owed + unanswered, until, key)
        data = last or data
        owed, unanswered = (ahead, 0) if more else (0, min(unanswered, ahead))
    else:
        # A printer holding an error answers them all once it is cleared
        unanswered += 1
    if key is not None:
        _unanswered[key] = min(MOST_UNANSWERED, owed + unanswered)
        _asked.pop(key, None)
        _retry_answered.discard(key)
        if unanswered:
            _asked[key] = sent
            if len(data) == 32:
                _retry_answered.add(key)
    if len(data) < 32:
        raise TimeoutError(f"No complete status response within {timeout} s")
    return parse_status(data)


def _release(printer):
    """Close the printer handle right away, also when the status query failed
    and a traceback still references the printer. The backend closes the
    handle again when the object is garbage collected, which would close an
    unrelated file if the descriptor number was reused in the meantime, so
    that second close is turned into a no-op."""
    printer.dispose()
    printer.dispose = lambda: None


def _held_error(key: str) -> dict | None:
    """The error a printer stopped a job with or told unasked, which it
    holds: a QL-1100 answers no status request until the error is cleared at
    the printer."""
    known = devices.status_cache.get(key, {})
    if known.get('status_type') != 'Error occurred' or not known.get('errors'):
        return None
    return dict(known, errors=[f"{', '.join(known['errors'])}, clear it at the printer"])


def _outstanding(key: str | None) -> bool:
    """Whether a status request of the printer is outstanding, see ASK_AGAIN."""
    return key in _asked and time.monotonic() - _asked[key] < ASK_AGAIN


def _switched_off(key: str):
    """A printer that cannot be reached, e.g. switched off, holds no request
    once it is back: it is asked at once then."""
    _asked.pop(key, None)


def query_status(device_specifier: str, default_model: str = 'Unknown') -> dict:
    """Query the printer status without waiting for a running print job. While
    a job holds the device, the last known status is returned instead."""
    key = devices.lock_key(device_specifier)
    with devices.device_lock(device_specifier, timeout=progress.status_wait(device_specifier)) as acquired:
        if acquired:
            try:
                printer = get_printer(device_specifier)
            except OSError:
                _switched_off(key)
                raise
            try:
                state = get_status(printer, key=key)
            except TimeoutError:
                held = _held_error(key)
                if held is not None:
                    return held
                if key in _retry_answered and _outstanding(key) and key in devices.status_cache:
                    return dict(devices.status_cache[key])
                raise
            except OSError:
                _switched_off(key)
                raise
            finally:
                # Release the device before giving up the lock
                _release(printer)
            devices.status_cache[key] = state
            return dict(state)
    logger.debug('Printer %s is busy, returning last known status', device_specifier)
    # Model and media from the last query, but the state of a running job:
    # errors or the idle state from before the job no longer apply
    return devices.busy_state(device_specifier, {'model': default_model, 'media_width': None, 'media_length': 0})
