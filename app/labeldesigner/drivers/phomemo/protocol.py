"""The protocols of Phomemo's label printers, a dialect of ESC/POS: a header,
the label as one raster image (GS v 0) of rows of dots packed MSB first, 1
printed, and a footer, each family its own. Status queries 1f 11 <what> are
answered 1a <what> <value>.

Every byte is as the sources below send it, cited by their short names and
the file and line:

[PT] vivier/phomemo-tools, GPL-3.0, at 3020f43
[LB] Ladeburger/phomemo-tools, its fork, GPL-3.0, at cf99432
[PD] polskafan/phomemo_d30, MIT, at 1f90dbb
[TP] yaddran/thermal-print, GPL-3.0, at 73dd564
[PY] transcriptionstream/phomymo, MIT, at 1f58d3f
[OD] odensc/phomemo-d30-web-bluetooth, Apache-2.0, at 179a0da
[KN] Knightro63/phomemo, MIT, at 1c015d7"""

import socket
import struct
import time

from .. import transports

DOTS_PER_MM = 8

# Bluetooth LE: written to ff02 of service ff00, answered on ff03 ([PY]
# constants.js:113-115, [OD] index.js:250-254, [TP] Q30/config.js:18-22)
WRITE = struct.pack('<H', 0xff02)
NOTIFY = struct.pack('<H', 0xff03)
# Written in pieces of 128 bytes, 20 ms apart ([PY] constants.js:124-125)
LE_PIECE = 128
LE_PAUSE = 0.02
# Classic Bluetooth ([PT] cups/backend/phomemo.py:144-145)
CHANNEL = 1

# Seconds the answers to status queries are waited for, twice the 500 ms
# [PY] waits for one (ble.js:394)
ANSWER_WAIT = 1.0
# A job is over once the printer was quiet this long, or told that it
# printed: closed earlier, the print stops ([PT] cups/backend/phomemo.py:174-198)
DONE_WAIT = 8.0

# The raster image: GS v 0, normal size, bytes per row and rows, 16 bit
# little endian ([PT] README.md:434-447, rastertopm110.py:204-215)
RASTER = b'\x1d\x76\x30\x00'

# M110, M120, M220, M421: speed 5 and density 10 as [PT] sends them, the media
# type, the label in one raster image, the footer ([PT] rastertopm110.py:183-219,
# README.md:482-508; one image on the M220: [PT] issue 13)
M110_HEADER = bytes.fromhex('1b4e0d05' '1b4e040a')
MEDIA = b'\x1f\x11'
# Labels with gaps, continuous paper ([PT] cups/drv/phomemo-m110.drv:71-72)
GAPS, CONTINUOUS = 0x0a, 0x0b
M110_FOOTER = bytes.fromhex('1ff00500' '1ff00300')

# D30, D35: sent before the image as by the app ([PD] print_text.py:71-72,
# [PT] rastertopd30.py:64-75, also on a D35: [PT] pull request 34)
D30_START = bytes.fromhex('1f112400' '1b40')
# Q30: ESC @ and the image ([TP] Q30/ble.js:213-235)
Q30_START = b'\x1b\x40'

# M02, M02 Pro, T02: ESC @, centered, 1f 11 02 04; images of at most 255 rows
# each; fed twice by 2 lines, then its battery, timer, firmware and serial
# number asked as by the app ([PT] README.md:424-460, tools/phomemo-filter.py:13-34)
M02_HEADER = bytes.fromhex('1b40' '1b6101' '1f110204')
M02_ROWS = 255
M02_FOOTER = bytes.fromhex('1b6402' '1b6402' '1f1108' '1f110e' '1f1107' '1f1109')

# Status queries and their answers ([TP] Q30/ble.js:86-140 and 177-187,
# [PY] ble.js:14-24 and 507-601)
ASK_BATTERY = b'\x1f\x11\x08'
ASK_PAPER = b'\x1f\x11\x11'
ASK_COVER = b'\x1f\x11\x12'
ANSWER = 0x1a
BATTERY, COVER, PAPER, RESULT, PRINTED = 0x04, 0x05, 0x06, 0x0b, 0x0f
NO_PAPER, COVER_OPEN, PRINT_FAILED, DONE = 0x88, 0x98, 0xb8, 0x0c
# Instead of a percentage while the battery runs low
LOW_BATTERY = {0xa4: 0, 0xa3: 3, 0xa2: 5, 0xa1: 10}
# The answers of three bytes, the others are left out where they may be
# longer, e.g. the firmware 1a 07 01 00 00 ([PT] README.md:468-475)
SHORT = (0x03, BATTERY, COVER, PAPER, 0x09, RESULT, 0x0c, PRINTED, 0x17)

NO_LABELS = 'The printer has no labels'
OPEN = 'The cover of the printer is open'
FAILED = 'The printer reports a print error'


class PhomemoError(Exception):
    pass


class NoAnswer(PhomemoError, TimeoutError):
    pass


def raster(rows: list[bytes]) -> bytes:
    """Rows of the same width as one raster image."""
    return RASTER + struct.pack('<HH', len(rows[0]), len(rows)) + b''.join(rows)


def m110_job(rows: list[bytes], media: int) -> bytes:
    return M110_HEADER + MEDIA + bytes([media]) + raster(rows) + M110_FOOTER


def d30_job(rows: list[bytes]) -> bytes:
    return D30_START + raster(rows)


def q30_job(rows: list[bytes]) -> bytes:
    return Q30_START + raster(rows)


def m02_job(rows: list[bytes]) -> bytes:
    blocks = (raster(rows[i:i + M02_ROWS]) for i in range(0, len(rows), M02_ROWS))
    return M02_HEADER + b''.join(blocks) + M02_FOOTER


def replies(data: bytes) -> tuple[list[tuple[int, int]], bytes]:
    """The answers of three bytes in what the printer sent, as (what, value),
    and the start of one not complete yet."""
    found, i = [], 0
    while (i := data.find(ANSWER, i)) >= 0:
        if len(data) - i < 3:
            return found, data[i:]
        if data[i + 1] in SHORT:
            found.append((data[i + 1], data[i + 2]))
            i += 3
        else:
            i += 1
    return found, b''


def problem(what: int, value: int) -> str:
    """What keeps the printer from printing by one of its answers, or an
    empty string."""
    if what == PAPER and value == NO_PAPER:
        return NO_LABELS
    if what == COVER and value == COVER_OPEN:
        return OPEN
    if what == RESULT and value == PRINT_FAILED:
        return FAILED
    return ''


def errors(told: dict) -> list[str]:
    """What keeps the printer from printing, by its answers to state()."""
    return [message for what in (PAPER, COVER) if what in told and (message := problem(what, told[what]))]


def battery(value: int) -> tuple[int, bool] | None:
    """The battery in percent and whether it runs low, by its answer, None
    for a value that is none of them."""
    if value in LOW_BATTERY:
        return LOW_BATTERY[value], True
    return (value, False) if value <= 100 else None


class Connection:
    """A printer on Bluetooth LE or classic Bluetooth, on USB or on a serial
    port, written to and read from alike."""

    def __init__(self, device: str, le: bool = False):
        self.device, self.le, self.received = device, False, b''
        if device.startswith('bt://'):
            if le:
                self.s = transports.kept_le(device, WRITE, NOTIFY, error=PhomemoError, no_answer=NoAnswer)
                self.le = True
            else:
                self.s = transports.rfcomm(device, CHANNEL)
        elif device.startswith('serial://'):
            self.s = transports.SerialLink(device)
        else:
            self.s = transports.UsbLink(device)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.le:
            transports.release_le(self.device, self.s)
        else:
            self.s.close()

    def send(self, data: bytes):
        if not self.le:
            self.s.sendall(data)
            return
        for i in range(0, len(data), LE_PIECE):
            self.s.sendall(data[i:i + LE_PIECE])
            time.sleep(LE_PAUSE)

    def answers(self, wait: float) -> list[tuple[int, int]] | None:
        """The answers that came within wait seconds, as soon as some did,
        None once the printer closed the connection."""
        deadline = time.monotonic() + wait
        while True:
            self.s.settimeout(max(deadline - time.monotonic(), 0.01))
            try:
                data = self.s.recv(256)
            except socket.timeout:
                return []
            if not data:
                return None
            found, self.received = replies(self.received + data)
            if found or time.monotonic() >= deadline:
                return found


def connect(device: str, le: bool = False) -> Connection:
    return Connection(device, le)


def state(c: Connection) -> dict:
    """What the printer tells of its paper, cover and battery, by what it
    answered, empty where it answers nothing. Answers left from before are
    read first."""
    while c.answers(0.01):
        pass
    told, deadline = {}, time.monotonic() + ANSWER_WAIT
    for query in (ASK_PAPER, ASK_COVER, ASK_BATTERY):
        c.send(query)
    while len(told) < 3 and (left := deadline - time.monotonic()) > 0:
        found = c.answers(left)
        if found is None:
            break
        told.update((what, value) for what, value in found if what in (PAPER, COVER, BATTERY))
    return told


def _wait(c: Connection, wait: float) -> tuple[str, bool]:
    """Until the printer tells that it printed a label: ('', True), or an
    error: (it, False), or it is quiet for wait seconds: ('', False)."""
    while True:
        found = c.answers(wait)
        if not found:
            return '', False
        for what, value in found:
            if message := problem(what, value):
                return message, False
            if what == PRINTED and value == DONE:
                return '', True


def print_labels(c: Connection, jobs: list[bytes], printed) -> str:
    """Print the jobs one after the other, each once the printer told that
    it printed the one before. A printer that tells it of none gets them one
    after the other and is waited for at the end. An empty string when all
    were printed, else the error the printer reports. printed(n) is called
    with the labels printed."""
    tells, quiet = True, False
    for i, job in enumerate(jobs):
        c.send(job)
        error, told = _wait(c, DONE_WAIT if tells else 0.01)
        if error:
            return error
        # Waited for until it was quiet after this label
        quiet, tells = tells and not told, told
        printed(i + 1)
    if tells or quiet:
        return ''
    error, _ = _wait(c, DONE_WAIT)
    return error
