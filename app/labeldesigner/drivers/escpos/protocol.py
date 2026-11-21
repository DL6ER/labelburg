"""ESC/POS of Epson receipt printers, as in the ESC/POS command reference
of Epson: the label as a raster image (GS v 0), a cut after it (GS V 65),
and the status the printer tells at once (DLE EOT)."""

import os
import select
import time

from .. import backends, devices, reach, usbip
from ..backends import discard as _discard

# Rows per raster image, the printer takes them in pieces
BAND = 256
# How long the printer may take to answer a status request, in s
ANSWER_WAIT = 2
# A job goes to a printer on PyUSB in pieces: PyUSB gives up on a single
# write after 15 s, a long receipt takes longer to print
WRITE_CHUNK = 16384
# Port of raw printing on the network, and how long connecting may take
PORT = 9100
CONNECT_WAIT = 10
# How long a piece of a job may take to reach a printer on the network
WRITE_WAIT = 15
# How long a status request left unanswered counts as outstanding: queries
# meanwhile send none and wait for none, they only read whether it came
ASK_AGAIN = 20

# When each printer was asked the request it left unanswered, by the key
# get_errors() is given
_asked: dict[str, float] = {}

INIT = b'\x1b@'
# Feed to the cutter and cut. A TM-T20II leaves one corner uncut, as its
# memory switches say, whatever the command.
CUT = b'\x1dVA\x00'
# Offline cause, and the roll paper sensor
OFFLINE = b'\x10\x04\x02'
PAPER = b'\x10\x04\x04'


class EscposError(Exception):
    pass


def raster(rows: list[bytes], width: int) -> bytes:
    """Rows of packed dots, width bytes each, MSB left, as GS v 0 images of
    BAND rows each."""
    bands = (rows[y:y + BAND] for y in range(0, len(rows), BAND))
    return b''.join(b'\x1dv0\x00' + bytes([width & 0xff, width >> 8, len(band) & 0xff, len(band) >> 8]) + b''.join(band)
                    for band in bands)


def job(labels: list[tuple]) -> bytes:
    """(cut after it, rows, bytes per row, ...) of each label as one job."""
    return INIT + b''.join(raster(rows, width) + (CUT if cut else b'') for cut, rows, width, *_ in labels)


def errors(offline: int, paper: int) -> list[str]:
    found = []
    if offline & 0x04:
        found.append('The cover of the printer is open')
    if offline & 0x20 or paper & 0x60:
        found.append('The printer has no paper')
    if offline & 0x40:
        found.append('The printer reports an error, remove paper stuck at its cutter or switch it off and on')
    return found


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

    def came(self) -> bool:
        """Whether the printer sent anything, read without waiting."""
        return bool(select.select([self.dev], [], [], 0)[0] and os.read(self.dev, 64))

    def ask(self, request: bytes) -> int:
        """The status byte the printer answers a DLE EOT request with."""
        # A TM-T20II kept an answer from before, which was taken for this one
        _discard(lambda: select.select([self.dev], [], [], 0)[0] and os.read(self.dev, 64))
        self.write(request)
        # usblp also tells empty USB packets, read until an answer comes
        deadline = time.monotonic() + ANSWER_WAIT
        while (left := deadline - time.monotonic()) > 0:
            if select.select([self.dev], [], [], left)[0] and (reply := os.read(self.dev, 64)):
                return reply[-1]
        raise EscposError('no answer to the status request')


class UsbConnection(Connection):
    """The printer through PyUSB, usb://<vendor>:<product>[/<serial>], or
    shared by another device, usbip://<host>."""

    def __init__(self, device: str):
        self.printer = backends.open_printer(device)

    def __exit__(self, *args):
        self.printer.dispose()

    def write(self, data: bytes):
        for start in range(0, len(data), WRITE_CHUNK):
            self.printer.write(data[start:start + WRITE_CHUNK])

    def came(self) -> bool:
        return bool(self.printer.read(64))

    def ask(self, request: bytes) -> int:
        """The status byte the printer answers a DLE EOT request with."""
        _discard(lambda: self.printer.read(64))
        self.write(request)
        deadline = time.monotonic() + ANSWER_WAIT
        while time.monotonic() < deadline:
            if reply := self.printer.read(64):
                return reply[-1]
        raise EscposError('no answer to the status request')


class TcpConnection(Connection):
    """The printer on the network, tcp://<host>[:<port>], port 9100 if left
    out, an IPv6 address in brackets."""

    def __init__(self, device: str):
        self.socket = reach.create_connection(devices.tcp_address(device, PORT), timeout=CONNECT_WAIT)

    def __exit__(self, *args):
        self.socket.close()

    def write(self, data: bytes):
        # In pieces as on USB, the timeout counts for each
        self.socket.settimeout(WRITE_WAIT)
        for start in range(0, len(data), WRITE_CHUNK):
            self.socket.sendall(data[start:start + WRITE_CHUNK])

    def came(self) -> bool:
        # A late answer went to the connection it was asked on
        return False

    def ask(self, request: bytes) -> int:
        """The status byte the printer answers a DLE EOT request with."""
        self.socket.setblocking(False)
        try:
            _discard(lambda: self.socket.recv(64))
        except BlockingIOError:
            pass
        self.write(request)
        self.socket.settimeout(ANSWER_WAIT)
        try:
            reply = self.socket.recv(64)
        except TimeoutError:
            reply = b''
        if not reply:
            raise EscposError('no answer to the status request')
        return reply[-1]


def connect(device: str) -> Connection:
    if device.startswith('usb://') or usbip.is_address(device):
        return UsbConnection(device)
    if device.startswith('tcp://'):
        return TcpConnection(device)
    return Connection(device)


def get_errors(c: Connection, key: str | None = None) -> list[str]:
    """What the printer tells is wrong. With key, the printer's lock key, a
    request it leaves unanswered counts as outstanding for ASK_AGAIN s, in
    which it is asked again only once anything came from it."""
    if key in _asked and time.monotonic() - _asked[key] < ASK_AGAIN and not c.came():
        raise EscposError('no answer to the status request')
    sent = time.monotonic()
    try:
        found = errors(c.ask(OFFLINE), c.ask(PAPER))
    except EscposError:
        if key is not None:
            _asked[key] = sent
        raise
    _asked.pop(key, None)
    return found
