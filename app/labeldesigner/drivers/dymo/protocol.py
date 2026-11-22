"""The raster commands of DYMO's LabelWriter 450 series, as in DYMO's
"LabelWriter 450 Series Printers Technical Reference Manual" (Rev. 10/09,
here "the reference") and its "LabelWriter SE450 Technical Reference"
(7/26/2010): each row of dots after SYN, the label length and the bytes of
a row before each label, a short form feed between labels and a form feed
after the last, and the status byte the printer answers ESC A with. The
same as https://thermal-label.github.io/labelwriter/protocol/lw-raster and
the DYMO parts of CUPS filter/rastertolabel.c (OpenPrinting, commit
24451a18) and of LPrint's lprint-dymo.c (commit 54f1c462) send."""

import os
import select
import time

from .. import backends

ESC = b'\x1b'
# Reset all settings to their defaults (the reference, p. 17)
RESET = ESC + b'@'
# 300 x 300 dpi text speed mode and normal print density, both the default
# (p. 18 f.)
TEXT_SPEED = ESC + b'h'
NORMAL_DENSITY = ESC + b'e'
# A row of dots follows (p. 18)
SYN = b'\x16'
# Feed the next label into print position, the label printed stays partly
# inside; feed the last one to the tear bar (p. 16)
SHORT_FORM_FEED = ESC + b'G'
FORM_FEED = ESC + b'E'
# The roll of a Twin Turbo: ASCII 1 the left one, 2 the right one (p. 16)
SELECT_ROLL = ESC + b'q'
ROLLS = {'left': b'1', 'right': b'2'}
# Get the status byte (p. 17)
STATUS = ESC + b'A'

# How long the printer may take to answer ESC A, in s
ANSWER_WAIT = 2
# How long a status request left unanswered counts as outstanding: queries
# meanwhile send none, they only read whether its answer came
ASK_AGAIN = 20
# A job goes to a printer on PyUSB in pieces: PyUSB gives up on a single
# write after 15 s
WRITE_CHUNK = 16384

# Bits of the status byte (p. 17): no labels, which also sets the error bit,
# labels jammed, and an error, e.g. a jam or a sequence it did not take. The
# SE450 tells no jam, the bit is reserved there (SE450 reference, GS S).
NO_LABELS = 0x20
JAM = 0x40
ERROR = 0x80
NO_LABELS_TEXT = 'No labels are in the printer'
JAM_TEXT = 'The labels are jammed, open the printer and pull them out'
ERROR_TEXT = 'The printer reports an error, switch it off and on'
TEXTS = (NO_LABELS_TEXT, JAM_TEXT, ERROR_TEXT)


class NoAnswer(TimeoutError):
    """The printer did not answer ESC A."""


def sync(head_bytes: int) -> bytes:
    """ESC characters one more than a row of the printhead takes, so that a
    printer waiting for the rest of a row, e.g. of a job broken off, takes
    the commands after them as commands again (the reference, p. 9; 57 on
    the SE450, SE450 reference, SYN)."""
    return ESC * (head_bytes + 1)


def request(head_bytes: int, roll: str | None = None) -> bytes:
    """ESC A after sync(), so that a printer left in the middle of a row
    takes it as a command (SE450 reference, SYN), after the roll of a Twin
    Turbo where roll is given: it tells the status of the roll selected."""
    return sync(head_bytes) + (SELECT_ROLL + ROLLS[roll] if roll else b'') + STATUS


def label(rows: list[bytes], width: int) -> bytes:
    """One label of rows of packed dots, width bytes each, MSB left, 1
    printed: the bytes of each row (ESC D, p. 15), the label length in rows,
    big-endian (ESC L, p. 16), and each row after SYN."""
    length = len(rows)
    return (ESC + b'D' + bytes([width]) + ESC + b'L' + bytes([length >> 8, length & 0xff])
            + b''.join(SYN + row for row in rows))


def job(labels: list[tuple[list[bytes], int]], head_bytes: int, short_feed: bool = True, roll: str | None = None) -> bytes:
    """The labels, (rows, bytes per row) each, as one job: a reset, the
    roll of a Twin Turbo where roll is given, and a short form feed between
    the labels where the printer takes it, else a form feed, as after the
    last one."""
    head = sync(head_bytes) + RESET + (SELECT_ROLL + ROLLS[roll] if roll else b'') + TEXT_SPEED + NORMAL_DENSITY
    feeds = [SHORT_FORM_FEED if short_feed else FORM_FEED] * (len(labels) - 1) + [FORM_FEED]
    return head + b''.join(label(rows, width) + feed for (rows, width), feed in zip(labels, feeds))


def errors(status: int, jam: bool = True) -> list[str]:
    """What the status byte tells is wrong, the error bit alone only where no
    other bit tells why. jam: whether the printer tells jams."""
    found = [text for bit, text in ((NO_LABELS, NO_LABELS_TEXT), (JAM, JAM_TEXT)) if status & bit and (jam or bit != JAM)]
    return found or ([ERROR_TEXT] if status & ERROR else [])


class Connection:
    """The printer through the printer interface of the kernel (usblp)."""

    def __init__(self, device: str):
        self.dev = os.open(device.removeprefix('file://'), os.O_RDWR)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.dev)

    def write(self, data: bytes):
        while data:
            data = data[os.write(self.dev, data):]

    def read(self, wait: float) -> bytes:
        """What the printer sent within wait s, empty if nothing."""
        return os.read(self.dev, 64) if select.select([self.dev], [], [], wait)[0] else b''


class UsbConnection(Connection):
    """The printer through PyUSB, usb://<vendor>:<product>[/<serial>]."""

    def __init__(self, device: str):
        self.printer = backends.open_printer(device)

    def __exit__(self, *args):
        self.printer.dispose()

    def write(self, data: bytes):
        for start in range(0, len(data), WRITE_CHUNK):
            self.printer.write(data[start:start + WRITE_CHUNK])

    def read(self, wait: float) -> bytes:
        return self.printer.read(64)


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


def status(c: Connection, key: str, head_bytes: int, roll: str | None = None) -> int:
    """The status byte of the printer, of the roll given of a Twin Turbo,
    see request(), NoAnswer if it did not answer. key, the printer's lock
    key: a request it leaves unanswered counts as outstanding for ASK_AGAIN
    s, in which no other is sent, an answer that came late is taken."""
    if key in _asked and time.monotonic() - _asked[key] < ASK_AGAIN:
        if late := c.read(0):
            del _asked[key]
            _answered[key] = True
            return late[-1]
        raise NoAnswer('no answer to ESC A')
    # An answer from before is not taken for this one
    backends.discard(lambda: c.read(0))
    sent = time.monotonic()
    c.write(request(head_bytes, roll))
    reply = _answer(c, sent + ANSWER_WAIT)
    if not reply:
        _asked[key] = sent
        _answered.setdefault(key, False)
        raise NoAnswer('no answer to ESC A')
    _asked.pop(key, None)
    _answered[key] = True
    return reply[-1]


def never_answered(key: str) -> bool:
    """Whether the printer of the key was asked and never answered."""
    return _answered.get(key) is False
