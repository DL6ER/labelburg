"""The Nelko P21 on classic Bluetooth (RFCOMM channel 1): TSPL for the label,
as tspl/protocol.py sends it, and the P21's own requests for its state, each
ending with CR LF, as nelko-p21-print found them
(https://github.com/merlinschumacher/nelko-p21-print, AGPL-3.0, facts only):
the status byte of TSPL, the tag of the roll and the battery. It answers no
other TSPL request, e.g. ~!T."""

import time

from .. import transports

CHANNEL = 1
# How long the printer may take to answer
ANSWER_WAIT = 2
STATUS = b'\x1b!?\r\n'
ROLL = b'\x1b!o\r\n'
BATTERY = b'BATTERY?\r\n'
# The tag of the roll: 16 bytes, the last two its CRC-16/MODBUS, big-endian
TAG_SIZE = 16
TAG_LENGTH, TAG_WIDTH = 11, 13


class NoAnswer(OSError):
    """The printer left a request unanswered."""


class Dropped(OSError):
    """The connection dropped while a job was sent."""


def _discard(link):
    """Drop a late answer to a request before, which is not taken for the next one."""
    link.settimeout(0.01)
    for _ in range(16):
        try:
            if not link.recv(256):
                raise ConnectionResetError('The printer closed the connection')
        except TimeoutError:
            return


def _answer(link, request: bytes, done) -> bytes:
    """What the printer answers request with, once done(answer)."""
    _discard(link)
    link.sendall(request)
    answer = b''
    deadline = time.monotonic() + ANSWER_WAIT
    while not done(answer):
        left = deadline - time.monotonic()
        if left <= 0:
            raise NoAnswer(f'no answer to {request!r}')
        link.settimeout(left)
        try:
            part = link.recv(256)
        except TimeoutError:
            continue
        if not part:
            raise ConnectionResetError('The printer closed the connection')
        answer += part
    return answer


def status(link) -> int:
    """The status byte of TSPL, see tspl.protocol.ERRORS."""
    return _answer(link, STATUS, bool)[0]


def crc16(data: bytes) -> int:
    crc = 0xffff
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xa001 if crc & 1 else crc >> 1
    return crc


def roll(link) -> tuple[int, int] | None:
    """Width and length in mm of the labels its tag tells, None without a
    tag that holds together."""
    tag = _answer(link, ROLL, lambda answer: len(answer) >= TAG_SIZE)[:TAG_SIZE]
    if crc16(tag[:-2]) != int.from_bytes(tag[-2:], 'big') or not tag[TAG_WIDTH] or not tag[TAG_LENGTH]:
        return None
    return tag[TAG_WIDTH], tag[TAG_LENGTH]


def battery(link) -> int | None:
    """The charge of its battery in percent, None if it tells none. It
    answers BATTERY <level as two BCD digits><charging> CR LF."""
    answer = _answer(link, BATTERY, lambda answer: answer.endswith(b'\r\n'))
    if not answer.startswith(b'BATTERY ') or len(answer) != len(b'BATTERY ') + 4:
        return None
    level = answer[len(b'BATTERY ')]
    tens, ones = level >> 4, level & 0x0f
    return tens * 10 + ones if tens < 10 and ones < 10 else None


def using(device: str, use):
    """What use(link) tells over the kept connection, which stays open: the
    P21 beeps each time one opens and closes. Over a new one where the kept
    one dropped meanwhile, e.g. as the printer switched itself off."""
    for _ in range(2):
        link = transports.kept_rfcomm(device, CHANNEL)
        # Given back before at least once
        kept = link.used > 0
        try:
            return use(link)
        except (NoAnswer, Dropped):
            raise
        except OSError:
            if not (kept and link.broken):
                raise
        finally:
            transports.release(device, link, None)
