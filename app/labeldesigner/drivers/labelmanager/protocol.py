"""The D1 tape commands of DYMO's LabelManager printers, as DYMO's
"LabelWriter 450 Series Printers Technical Reference Manual" (Rev. 10/09,
here "the reference") describes them for the tape side of the LabelWriter
Duo in its Appendix B, and as labelle (https://github.com/labelle-org/labelle,
src/labelle/lib/devices/dymo_labeler.py of commit 5e8f1e5d, Apache-2.0),
LPrint (lprint-dymo.c of commit 54f1c462, Apache-2.0) and thermal-label's
d1-core (https://github.com/thermal-label/d1-core, src/protocol.ts and
src/status.ts of commit 16126795, MIT) send them to a LabelManager: the tape
type, the pins skipped and the bytes of each row, each row after SYN, blank
rows that feed the print past the cutter, and ESC A for the status byte,
which also paces a job."""

import time

from .. import backends
from ..dymo.protocol import Connection

ESC = b'\x1b'
NUL = b'\x00'
# NUL that LPrint sends before a job to clear the input (rstartjob)
SYNC = NUL * 23
# Heat for the tape type, 0 black on white or clear (the reference, p. 24),
# as labelle (_tape_color(0)) and LPrint send it for any tape
TAPE_TYPE = ESC + b'C' + bytes([0])
# Pins skipped, in bytes, before the data of each row (p. 23); always sent,
# a printer keeps the last one otherwise (thermal-label, protocol.ts)
DOT_TAB = ESC + b'B'
# Bytes of each row; at 0 each SYN feeds a blank row (p. 23)
BYTES_PER_LINE = ESC + b'D'
# A row of dots follows (p. 24)
SYN = b'\x16'
# Get the status byte (p. 25)
STATUS = ESC + b'A'

# How long the printer may take to answer ESC A, in s
ANSWER_WAIT = 2
# How long it may take to print the rows sent before an ESC A, in s: labelle
# waits a second for 64 rows (synwait=64, read() at PyUSB's default timeout)
PRINT_WAIT = 5
# How long a status request left unanswered counts as outstanding: queries
# meanwhile send none, they only read whether its answer came
ASK_AGAIN = 20
# Rows at most before the printer is asked again within a job, so that no
# write waits on it for long (labelle, dymo_labeler.py, _send_command)
ROWS_PER_ANSWER = 64
# In ms
WRITE_TIMEOUT = 15000

# Bits of the status byte (p. 25; thermal-label checked them on a PnP,
# status.ts): a cassette is in, the cutter is jammed, any other error, e.g.
# a stalled motor or jammed tape
CASSETTE = 0x40
CUTTER_JAM = 0x10
ERROR = 0x04
NO_TAPE_TEXT = 'No tape cassette is in the printer'
CUTTER_TEXT = 'The cutter is jammed, mind its blade as you free it'
ERROR_TEXT = 'The tape is stuck, open the printer and check the cassette'
# A printer that shows only the USB drive it is set up from, see UsbConnection
USB_DRIVE_TEXT = 'The printer shows up as a USB drive only and cannot print like that'
TEXTS = (NO_TAPE_TEXT, CUTTER_TEXT, ERROR_TEXT, USB_DRIVE_TEXT)


class NoAnswer(TimeoutError):
    """The printer did not answer ESC A."""


class UsbDrive(OSError):
    """A printer that shows neither a printer nor a HID interface."""

    def __init__(self):
        super().__init__(USB_DRIVE_TEXT)


def label(rows: list[bytes], dot_tab: int, feed: int) -> list[bytes]:
    """One label as its parts: the tape type, the dot tab and the bytes of
    its rows, each row after SYN, then feed blank rows (labelle,
    _raw_print_label() and _skip_lines(); thermal-label,
    buildPrinterStream())."""
    width = len(rows[0]) if rows else 0
    return ([TAPE_TYPE + DOT_TAB + bytes([dot_tab]) + BYTES_PER_LINE + bytes([width])] + [SYN + row for row in rows]
            + [BYTES_PER_LINE + bytes([0])] + [SYN] * feed)


def pieces(labels: list[list[bytes]]) -> list[bytes]:
    """The labels in pieces of ROWS_PER_ANSWER rows at most, blank ones too,
    each sent once the printer answered the ESC A after the one before, as
    labelle sends a job."""
    found, piece, rows = [], [], 0
    for part in (part for parts in labels for part in parts):
        if part.startswith(SYN):
            if rows == ROWS_PER_ANSWER:
                found.append(b''.join(piece))
                piece, rows = [], 0
            rows += 1
        piece.append(part)
    return found + [b''.join(piece)]


def errors(status: int) -> list[str]:
    """What the status byte tells is wrong, the general error only where no
    other bit tells why (as thermal-label reads it, parseStatus())."""
    found = [] if status & CASSETTE else [NO_TAPE_TEXT]
    if status & CUTTER_JAM:
        found.append(CUTTER_TEXT)
    return found or ([ERROR_TEXT] if status & ERROR else [])


class UsbConnection(Connection):
    """The printer through PyUSB, usb://<vendor>:<product>[/<serial>]: its
    printer interface, else its HID interface, which takes the same bytes, as
    labelle opens it (usb_device.py, _setup()). A PnP shows the HID one also
    while it is not switched to printing, as 0x1001, which dymoprint, labelle
    before, printed on (its commit e9a2010); one that shows neither is
    UsbDrive."""

    def __init__(self, device: str):
        import usb.core
        import usb.util
        self._usb = usb
        self.dev = backends.find_device(usb, device)
        try:
            config = self.dev.get_active_configuration()
        except usb.core.USBError:
            self.dev.set_configuration()
            config = self.dev.get_active_configuration()
        interface = (usb.util.find_descriptor(config, bInterfaceClass=7)
                     or usb.util.find_descriptor(config, bInterfaceClass=3))
        if interface is None:
            raise UsbDrive()
        # Only the interface used, never the USB drive's
        self.number, self.detached = interface.bInterfaceNumber, False
        try:
            if self.dev.is_kernel_driver_active(self.number):
                self.dev.detach_kernel_driver(self.number)
                self.detached = True
        except NotImplementedError:
            pass
        direction = usb.util.endpoint_direction
        self.out = usb.util.find_descriptor(interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        self.into = usb.util.find_descriptor(interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        if self.out is None or self.into is None:
            self._dispose()
            raise UsbDrive()

    def __exit__(self, *args):
        self._dispose()

    def _dispose(self):
        self._usb.util.dispose_resources(self.dev)
        if self.detached:
            self.dev.attach_kernel_driver(self.number)
            self.detached = False

    def write(self, data: bytes):
        self.out.write(data, WRITE_TIMEOUT)

    def read(self, wait: float) -> bytes:
        try:
            return bytes(self.into.read(64, max(1, round(wait * 1000))))
        except self._usb.core.USBTimeoutError:
            return b''


def connect(device: str) -> Connection:
    return UsbConnection(device) if device.startswith('usb://') else Connection(device)


# When each printer was asked the request it left unanswered, by the key
# status() is given
_asked: dict[str, float] = {}
# By the same key: True for a printer that answered once, False for one that
# was asked and never answered
_answered: dict[str, bool] = {}


def _answer(c: Connection, deadline: float) -> bytes:
    # usblp also tells empty USB packets, read until an answer comes
    while (left := deadline - time.monotonic()) > 0:
        if reply := c.read(left):
            return reply
    return b''


def status(c: Connection, key: str, fresh: bool = False) -> int:
    """The status byte of the printer, NoAnswer if it did not answer. key,
    the printer's lock key: a request it leaves unanswered counts as
    outstanding for ASK_AGAIN s, in which no other is sent unless fresh, an
    answer that came late is taken. The byte comes first of the answer,
    which labelle reads as up to 512 bytes, thermal-label as 8."""
    if not fresh and key in _asked and time.monotonic() - _asked[key] < ASK_AGAIN:
        if late := c.read(0):
            del _asked[key]
            _answered[key] = True
            return late[0]
        raise NoAnswer('no answer to ESC A')
    # An answer from before is not taken for this one
    backends.discard(lambda: c.read(0))
    sent = time.monotonic()
    c.write(STATUS)
    reply = _answer(c, sent + ANSWER_WAIT)
    if not reply:
        _asked[key] = sent
        _answered.setdefault(key, False)
        raise NoAnswer('no answer to ESC A')
    _asked.pop(key, None)
    _answered[key] = True
    return reply[0]


def printed(c: Connection) -> int:
    """The status byte the printer answers ESC A with once it printed what
    came before, NoAnswer if it did not within PRINT_WAIT s."""
    c.write(STATUS)
    reply = _answer(c, time.monotonic() + PRINT_WAIT)
    if not reply:
        raise NoAnswer('no answer to ESC A')
    return reply[0]


def never_answered(key: str) -> bool:
    """Whether the printer of the key was asked and never answered."""
    return _answered.get(key) is False
