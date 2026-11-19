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
# Status requests counted at most, also through an error held for hours: a
# reply that does not come ends the wait for the others at once
MOST_UNANSWERED = 1000
# How long a query or job reads owed replies at most: it holds the device
# meanwhile, the others are read next time
OWED_WAIT = 5

# The status requests each printer has not answered yet, by devices.lock_key()
_unanswered: dict[str, int] = {}


def send_job(data: bytes, device_specifier: str, page_lengths: list) -> dict:
    """Send a print job to a USB printer and read its status messages until
    it reports every page printed and that it is ready for the next job, an
    error, or until it stays silent for longer than a page takes to print.
    A long label is printed while it is received, a job of several labels
    sends messages for each of them.

    page_lengths: the length of each page in mm. Returns, as print_failure()
    reads it: did_print, ready_for_next_job, the last printer_state, and the
    pages of the job and how many the printer confirmed."""
    info = {'did_print': False, 'ready_for_next_job': False, 'printer_state': None,
            'pages': len(page_lengths), 'confirmed': 0}
    key = devices.lock_key(device_specifier)
    unanswered = _unanswered.get(key, 0)
    printer = get_printer(device_specifier)
    progress.counting(device_specifier)
    try:
        unanswered = _drain(printer, unanswered, key=key)
        received = _write_reading(printer, data)
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
                    # The job's messages began, the others will not come
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
        if info['did_print']:
            # In place of e.g. an error told before the job, which is gone
            devices.status_cache[key] = info['printer_state']
        _release(printer)


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


def _drain(printer, unanswered: int = 0, wait: bool = True, key: str | None = None) -> int:
    """Read what the printer still has to say, e.g. the status messages of an
    earlier job or late replies to status requests, so that they are not
    taken for those of this one. With wait, the replies to the unanswered
    requests are waited for first, see _read_ahead(). Returns how many of
    them were not read."""
    if wait:
        _, unanswered, _ = _read_ahead(printer, unanswered, time.monotonic() + OWED_WAIT, key)
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
    while time.monotonic() < deadline:
        data += printer.read(32 - len(data)) or b''
        if len(data) < 32:
            time.sleep(0.02)
        elif data.startswith(HEADER) and not _is_reply(data):
            _keep_error(data, key)
            data = b''
        else:
            return data
    return data


def _read_status(printer, timeout: float, key: str | None = None) -> bytes:
    """Ask for the status, the 32-byte answer or what came until the timeout.
    Printers such as the QL-1100 need about 100 ms to answer and return empty
    reads until then."""
    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    return _read_reply(printer, timeout, key)


def get_status(printer, timeout: float = 2.0, key: str | None = None):
    """Request and parse the printer status. With key, the printer's lock key,
    the requests it leaves unanswered are counted and their replies read
    first next time: a QL-1100 holding an error answers them once the error
    is cleared, and the answer read would be that of an older request."""
    until = time.monotonic() + OWED_WAIT
    last, owed, more = _read_ahead(printer, _unanswered.get(key, 0), until, key)
    if more and last:
        # Too many to read now: the latest reply is the answer, the printer
        # sent it after it held the requests, the others are read next time
        if key is not None:
            _unanswered[key] = owed
        return parse_status(last)
    owed = _drain(printer, owed, wait=False, key=key)
    data = _read_status(printer, timeout, key)
    unanswered = int(len(data) < 32)
    if unanswered:
        # The QL-560 answers only the first request after power-on. Invalidate
        # and initialize only then: "ESC @" also cancels printing. The QL-560
        # sends no reply to the first request, it is not waited for here.
        unanswered = _drain(printer, unanswered, wait=False, key=key)
        printer.write(b"\x00" * 200 + b"\x1b\x40")
        data = _read_status(printer, timeout, key)
    if len(data) == 32:
        # It may be an owed reply, e.g. of an error cleared meanwhile: the last
        # of those following is the answer, ones that do not come are forgotten
        last, ahead, more = _read_ahead(printer, owed + unanswered, until, key)
        data = last or data
        owed, unanswered = (ahead if more else 0), 0
    else:
        # A printer holding an error answers them all once it is cleared
        unanswered += 1
    if key is not None:
        _unanswered[key] = min(MOST_UNANSWERED, owed + unanswered)
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


def query_status(device_specifier: str, default_model: str = 'Unknown') -> dict:
    """Query the printer status without waiting for a running print job. While
    a job holds the device, the last known status is returned instead."""
    key = devices.lock_key(device_specifier)
    with devices.device_lock(device_specifier, timeout=progress.status_wait(device_specifier)) as acquired:
        if acquired:
            printer = get_printer(device_specifier)
            try:
                state = get_status(printer, key=key)
            except TimeoutError:
                held = _held_error(key)
                if held is None:
                    raise
                return held
            finally:
                # Release the device before giving up the lock
                _release(printer)
            devices.status_cache[key] = state
            return dict(state)
    logger.debug('Printer %s is busy, returning last known status', device_specifier)
    # Model and media from the last query, but the state of a running job:
    # errors or the idle state from before the job no longer apply
    return devices.busy_state(device_specifier, {'model': default_model, 'media_width': None, 'media_length': 0})
