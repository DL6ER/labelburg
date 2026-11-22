"""The protocol of the small thermal printers on Bluetooth LE called cat
printers, e.g. the GB01 or the MX06: a label as Cat-Printer prints an image
(https://github.com/NaitLee/Cat-Printer, GPL-3.0, its printer_lib/commander.py
CC0-1.0, cited as commander.py and printer.py), the state the printer
answers as kitty-printer (https://github.com/NaitLee/kitty-printer,
common/cat-protocol.ts, CC0-1.0) and TiMini-Print
(https://github.com/Dejniel/TiMini-Print, Apache-2.0) read it. Packets are
51 78 <command> <0, 1 in answers> <length, 2 bytes LE> <data> <CRC-8> ff."""

import struct
import time

from .. import transports

# 384 dots across the printhead (Cat-Printer printer_lib/models.py:15), on
# 4.8 cm, 203.2 dpi (kitty-printer common/constants.ts:11)
HEAD = 384
DPI = 203
# Written to AE01, answered as notifications of AE02 (printer.py:295-296,
# kitty-printer common/constants.ts:7-8), as the attribute protocol writes
# 16-bit UUIDs
WRITE = struct.pack('<H', 0xae01)
NOTIFY = struct.pack('<H', 0xae02)

# As Cat-Printer prints an image: speed 32 (printer.py:291), energy 0x4000
# (printer.py:708, server.py:82 and 205), speed 8 and 128 dots of paper after
# it (printer.py:466-471)
SPEED = 32
ENERGY = 0x4000
FINISH_SPEED = 8
FEED = 128

# Written in pieces of 200 bytes, 0.02 s apart; while the printer asks to
# pause, writing waits and looks again every 0.2 s (printer.py:293 and 420-428)
PIECE = 200
PIECE_PAUSE = 0.02
PAUSED_POLL = 0.2
# Seconds the printer may take to answer, and to ask to go on after a pause
ANSWER_WAIT = 3
RESUME_WAIT = 30
# Seconds a write may take
WRITE_WAIT = 10

# The flow control the printer notifies: 51 78 ae 01 01 00 10 70 ff pauses,
# the same with 00 goes on (commander.py:75-76, TiMini-Print
# timiniprint/printing/runtime/tiny.py:26-31)
FLOW = 0xae
# The bits of the state it answers a state query with (kitty-printer
# common/cat-protocol.ts:89-96 and 122, the byte after the length), the same in
# TiMini-Print timiniprint/protocol/families/v5g.py:36-41
NO_PAPER, COVER_OPEN, OVERHEATED, LOW_POWER = 0x01, 0x02, 0x04, 0x08
ERRORS = {
    NO_PAPER: 'The printer has no paper',
    COVER_OPEN: 'The cover of the printer is open',
    OVERHEATED: 'The printer is too hot, let it cool down',
    LOW_POWER: 'The battery of the printer is low, charge it',
}


class CatError(Exception):
    pass


class NoAnswer(CatError, TimeoutError):
    pass


def crc8(data: bytes) -> int:
    """CRC-8 with the polynomial 0x07, the table of commander.py:10-40."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xff if crc & 0x80 else crc << 1
    return crc


def packet(command: int, data: bytes, answer: int = 0) -> bytes:
    """commander.py:78-86, the length in 2 bytes as TiMini-Print
    timiniprint/protocol/packet.py:91-100 writes it."""
    return bytes([0x51, 0x78, command, answer]) + struct.pack('<H', len(data)) + data + bytes([crc8(data), 0xff])


# commander.py:88-127 and 133-154
STATE = packet(0xa3, b'\x00')
# The start of a GB03, which takes the newer form (models.py:52-53)
START_NEW = b'\x12' + STATE
# Cat-Printer names it setting 200 dpi, TiMini-Print darkness 2 of 5
# (timiniprint/protocol/_prefixed_commands.py:7-10)
DPI_200 = packet(0xa4, bytes([50]))
APPLY_ENERGY = packet(0xbe, b'\x01')
UPDATE = packet(0xa9, b'\x00')
LATTICE_START = packet(0xa6, bytes.fromhex('aa551738445f5f5f44382c'))
LATTICE_END = packet(0xa6, bytes.fromhex('aa55170000000000000017'))
# Each byte of a row with its bits reversed: the first dot is the lowest bit
_REVERSED = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))


def speed(value: int) -> bytes:
    return packet(0xbd, bytes([value]))


def energy(value: int) -> bytes:
    return packet(0xaf, struct.pack('<H', value))


def feed(dots: int) -> bytes:
    return packet(0xa1, struct.pack('<H', dots))


def row(dots: bytes) -> bytes:
    """A row of packed dots, MSB first, 1 printed, as the printer takes it."""
    return packet(0xa2, dots.translate(_REVERSED))


def label_packets(rows: list[bytes], new_start: bool = False, feed_rows: bool = False) -> list[bytes]:
    """A label as Cat-Printer prints an image (printer.py:448-487): rows of
    HEAD // 8 bytes. new_start: a GB03. feed_rows: the paper after it as
    empty rows, as for the MX05 to MX10 (models.py:57-58)."""
    after = [row(bytes(HEAD // 8))] * FEED if feed_rows else [feed(FEED)]
    return [STATE, START_NEW if new_start else STATE, DPI_200, speed(SPEED), energy(ENERGY), APPLY_ENERGY, UPDATE,
            LATTICE_START, *(row(dots) for dots in rows), LATTICE_END, speed(FINISH_SPEED), *after, STATE]


def parse(received: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """The complete packets in what was received, as (command, data), and
    the rest. One whose checksum or end does not fit is skipped, as by
    TiMini-Print timiniprint/protocol/packet.py:19-81."""
    found = []
    while True:
        i = received.find(b'\x51\x78')
        if i < 0:
            return found, received[-1:] if received[-1:] == b'\x51' else b''
        received = received[i:]
        if len(received) < 6:
            return found, received
        n = struct.unpack_from('<H', received, 4)[0]
        if len(received) < n + 8:
            return found, received
        data = received[6:6 + n]
        if received[n + 7] != 0xff or received[n + 6] != crc8(data):
            received = received[1:]
            continue
        found.append((received[2], bytes(data)))
        received = received[n + 8:]


def errors(state: int) -> list[str]:
    return [message for bit, message in ERRORS.items() if state & bit]


class Connection:
    """The kept connection to a printer over Bluetooth LE, see
    transports.kept_le(), and what it notified."""

    def __init__(self, device: str):
        self.device = device
        self.s = transports.kept_le(device, WRITE, NOTIFY, error=CatError, no_answer=NoAnswer)
        self.received, self.paused = b'', False
        # The state queries answered and the last state
        self.answers, self.state = 0, 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        transports.release_le(self.device, self.s)

    def _read(self, wait: float) -> str:
        """Read what the printer notifies within wait seconds, at least
        once: its flow control and its state. The first error its state
        tells, else an empty string."""
        error, deadline = '', time.monotonic() + wait
        while True:
            # Never 0, a socket without a timeout would not wait at all
            self.s.settimeout(max(deadline - time.monotonic(), 0.001))
            try:
                data = self.s.recv(256)
            except TimeoutError:
                data = None
            finally:
                self.s.settimeout(WRITE_WAIT)
            if data == b'':
                raise NoAnswer('the printer closed the connection')
            found, self.received = parse(self.received + (data or b''))
            for command, content in found:
                if command == FLOW and content in (b'\x10', b'\x00'):
                    self.paused = content == b'\x10'
                elif command == STATE[2] and content:
                    self.answers, self.state = self.answers + 1, content[0]
                    error = error or next(iter(errors(self.state)), '')
            if error or time.monotonic() >= deadline:
                return error

    def ask_state(self) -> int:
        """The state the printer answers, see errors()."""
        asked = self.answers
        self.s.sendall(STATE)
        deadline = time.monotonic() + ANSWER_WAIT
        while self.answers == asked:
            if time.monotonic() >= deadline:
                raise NoAnswer('no answer to the state query')
            self._read(min(deadline - time.monotonic(), PAUSED_POLL))
        return self.state

    def write(self, data: bytes) -> str:
        """Write data in pieces, each once the printer lets it, PIECE_PAUSE
        apart. The first error its state tells meanwhile, else an empty
        string; the rest is then not written."""
        size = min(PIECE, self.s.mtu - 3)
        for i in range(0, len(data), size):
            deadline = time.monotonic() + RESUME_WAIT
            while self.paused:
                if time.monotonic() >= deadline:
                    raise NoAnswer('the printer did not go on')
                if error := self._read(PAUSED_POLL):
                    return error
            self.s.sendall(data[i:i + size])
            if error := self._read(PIECE_PAUSE):
                return error
        return ''

    def print_label(self, packets: list[bytes]) -> str:
        """Print a label of label_packets(): the printer is asked its state
        first, then sent the rest, and its last state is waited for, at most
        ANSWER_WAIT s, as the printer goes on printing what it was sent. The
        error its state tells, else an empty string."""
        error = next(iter(errors(self.ask_state())), '')
        if error:
            return error
        asked = self.answers + sum(p in (STATE, START_NEW) for p in packets[1:])
        error = self.write(b''.join(packets[1:]))
        deadline = time.monotonic() + ANSWER_WAIT
        while not error and self.answers < asked and time.monotonic() < deadline:
            error = self._read(min(deadline - time.monotonic(), PAUSED_POLL))
        return error
