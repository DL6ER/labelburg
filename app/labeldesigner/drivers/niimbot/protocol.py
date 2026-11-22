"""The protocol of the Niimbot printers: the D110_M (firmware 4.x) over classic Bluetooth (RFCOMM),
the others over Bluetooth LE and on USB, the print sequence of the D110_M of https://github.com/eigger/hass-niimbot
(custom_components/niimbot/niimprint/printer.py), that of the B1 of
niimbluelib's B1PrintTask (https://github.com/MultiMote/niimbluelib, MIT),
see models.py for the models of each.
Packets are 55 55 <command> <length> <data> <checksum> aa aa, the checksum
the XOR of command, length and data."""

import socket
import struct
import time

from .. import bitmap, transports

# 8 dots per mm, the printhead of the D110_M is 15 mm wide, that of the B1 48 mm
DPI = 203
HEAD = 120
B1_HEAD = 384
# The print sequences, see print_label()
V4, B1 = 'v4', 'b1'
CHANNEL = 1
# Seconds the printer may take to answer
ANSWER_WAIT = 5
# How long a label may take to print, and how often the printer is asked
# meanwhile how many pages it printed, as the script this came from does
PRINT_WAIT = 30
PAGE_POLL = 0.5
# A B1 refuses the next label for a moment after one ended, about 0.3 s on
# USB: it is asked again every START_POLL seconds within ANSWER_WAIT
START_POLL = 0.1
# Density 1 to 5
DENSITY = 3
# Label type when the roll has no RFID tag: labels with gaps
WITH_GAPS = 1

ERROR = 0xdb
# Byte 8 of the heartbeat of a D110_M: 1 with the lid closed, with a roll
# inside and without one (10 1f 00 69 00 69 32 46 01 04 and 10 20 00 97 00 97
# 32 46 01 04), 0 with the lid open (10 22 00 54 00 54 32 46 00 04). It tells
# no paper: the bytes that change without a roll change with an open lid too.
LID_OPEN = 0
# Bytes 9 to 12 of the 13 a B1 answers: lid (1 open), battery, paper (1 none)
# and whether it read the RFID tag of the roll, e.g. 1f f7 00 61 00 61 00 00
# 4f 00 04 00 01 closed with a roll, 20 17 00 54 00 54 00 00 4f 01 04 01 00
# open, 20 17 00 85 00 85 00 00 4f 00 04 01 00 closed without a roll.
B1_LID_OPEN = 1


class NiimbotError(Exception):
    pass


class NoAnswer(NiimbotError, TimeoutError):
    """Also of a B1 switched off on USB, which stays on the bus."""


class NoTag(NiimbotError):
    pass


class NotPrinted(NiimbotError):
    pass


def packet(command: int, data: bytes) -> bytes:
    check = command ^ len(data)
    for byte in data:
        check ^= byte
    return bytes([0x55, 0x55, command, len(data), *data, check, 0xaa, 0xaa])


def parse(received: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """The complete packets in what was received, as (command, data), and
    the rest."""
    found = []
    while True:
        i = received.find(b'\x55\x55')
        if i < 0:
            return found, b''
        if len(received) < i + 4 or len(received) < i + received[i + 3] + 7:
            return found, received[i:]
        n = received[i + 3]
        found.append((received[i + 2], bytes(received[i + 4:i + 4 + n])))
        received = received[i + n + 7:]


def counts(packed: bytes, head: int) -> bytes:
    """The dots of a packed row as niimbluelib counts them for a printhead of
    head dots: in each third of it where the row fits into them, else all
    of them, little-endian after a zero."""
    third = head // 8 // 3
    if len(packed) <= 3 * third:
        return bytes(int.from_bytes(packed[k * third:(k + 1) * third]).bit_count() for k in range(3))
    return b'\x00' + struct.pack('<H', int.from_bytes(packed).bit_count())


def row_packets(rows: list[bytes], head: int = HEAD) -> list[bytes]:
    """Rows of dots, 1 printed, as packets: equal rows that follow each
    other once with their count, bitmap rows (85) with their dots counted,
    see counts(), empty rows (84) without them."""
    out, y = [], 0
    while y < len(rows):
        n = 1
        while y + n < len(rows) and rows[y + n] == rows[y] and n < 255:
            n += 1
        packed = bitmap.pack(rows[y])
        if any(packed):
            out.append(packet(0x85, struct.pack('>H', y) + counts(packed, head) + bytes([n]) + packed))
        else:
            out.append(packet(0x84, struct.pack('>HB', y, n)))
        y += n
    return out


# On Bluetooth LE the B1 takes Niimbot's packets written to this
# characteristic and answers with its notifications, as niimbluelib talks to it
NIIMBOT_CHARACTERISTIC = bytes.fromhex('bef8d6c99c214c9eb632bd58c1009f9f')[::-1]
# The service of that characteristic, as a printer may advertise it
NIIMBOT_SERVICE = bytes.fromhex('e7810a7173ae499d8c15faa9aef0c3f2')[::-1]
UsbSocket = transports.UsbLink


class Connection:
    def __init__(self, device: str, le: bool = False):
        self.received = b''
        if not device.startswith('bt://'):
            self.s = transports.UsbLink(device)
            return
        if le:
            self.s = transports.kept_le(device, NIIMBOT_CHARACTERISTIC, error=NiimbotError, no_answer=NoAnswer)
            self.device = device
            return
        self.s = transports.rfcomm(device, CHANNEL)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if isinstance(self.s, transports.LeLink):
            transports.release_le(self.device, self.s)
        else:
            self.s.close()

    def send(self, data: bytes):
        self.s.sendall(data)

    def ask(self, command: int, data: bytes, replies, send: bool = True, wait: float | None = None) -> bytes:
        """Send a packet, the data of the first reply that is one of
        replies, waited for ANSWER_WAIT seconds or wait. Without send only
        read, e.g. the late answer to one sent before. Raises NiimbotError if
        the printer reports an error or does not answer."""
        replies = {replies} if isinstance(replies, int) else set(replies)
        if send:
            self.s.sendall(packet(command, data))
        deadline = time.monotonic() + (ANSWER_WAIT if wait is None else wait)
        while time.monotonic() < deadline:
            self.s.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                data = self.s.recv(256)
            except socket.timeout:
                break
            if not data:
                break
            found, self.received = parse(self.received + data)
            for reply, content in found:
                if reply == ERROR:
                    raise NiimbotError(f'error 0x{content[0]:02x} on 0x{command:02x}', content[0])
                if reply in replies:
                    return content
        raise NoAnswer(f'no answer to 0x{command:02x}')

    def ok(self, command: int, data: bytes, reply: int):
        if not self.ask(command, data, reply)[:1] == b'\x01':
            raise NiimbotError(f'refused 0x{command:02x}')


def connect(device: str, le: bool = False) -> Connection:
    """le: a printer on bt:// over Bluetooth LE, else classic Bluetooth."""
    return Connection(device, le)


def heartbeat(c: Connection, send: bool = True, wait: float | None = None) -> dict:
    """Whether the lid is open and the battery (0 to 4), from the 10 bytes
    a D110_M answers, e.g. 10 1f 00 69 00 69 32 46 01 04, or the 13 a B1
    answers; the raw data if the length is another. send and wait as for
    Connection.ask()."""
    try:
        data = c.ask(0xdc, b'\x01', {0xd9, 0xdd, 0xde, 0xdf}, send, wait)
    except NoAnswer:
        raise
    except NiimbotError:
        # An error answer left from a job the printer stopped, e.g. with its
        # cover opened, comes before the answer
        data = c.ask(0xdc, b'\x01', {0xd9, 0xdd, 0xde, 0xdf}, send, wait)
    if len(data) == 13:
        return {'open': data[9] == B1_LID_OPEN, 'battery': data[10]}
    if len(data) != 10:
        return {'raw': data.hex()}
    return {'open': data[8] == LID_OPEN, 'battery': data[9]}


def model_id(c: Connection) -> int | None:
    """The model ID the printer tells, e.g. 4096 for a B1, None if it does
    not tell."""
    try:
        data = c.ask(0x40, b'\x08', 0x48)
    except NiimbotError:
        return None
    return struct.unpack('>H', data[:2])[0] if len(data) >= 2 else None


def printer_serial(c: Connection) -> str | None:
    """The serial number the printer tells, None if it tells none."""
    try:
        data = c.ask(0x40, b'\x0b', 0x4b)
    except NiimbotError:
        return None
    text = data.decode('ascii', 'replace').strip('\x00 ')
    return text if text.isalnum() else None


def reason(error: NiimbotError) -> str | None:
    """Why the printer refused, by its error code as niimbluelib names them,
    e.g. 1 for a cover opened during a job; None for other codes."""
    code = error.args[1] if len(error.args) > 1 else None
    return {0x01: 'The cover of the printer is open', 0x02: 'The printer has no labels',
            0x03: 'The battery of the printer is low', 0x07: 'The printhead is too hot'}.get(code)


def errors(beat: dict) -> list[str]:
    """What keeps the printer from printing, by its heartbeat."""
    return ['The cover of the printer is open'] if beat.get('open') else []


def roll(c: Connection, send: bool = True, wait: float | None = None) -> dict | None:
    """The loaded roll from its RFID tag, None without a tag (the D110_M
    answers 00 so), {} for a tag laid out otherwise. send and wait as for
    Connection.ask()."""
    data = c.ask(0x1a, b'\x01', 0x1b, send, wait)
    if len(data) <= 1:
        return None
    try:
        i = 8
        barcode = data[i + 1:i + 1 + data[i]].decode()
        i += 1 + data[i]
        serial = data[i + 1:i + 1 + data[i]].decode()
        i += 1 + data[i]
        total, used, kind = struct.unpack('>HHB', data[i:i + 5])
    except (IndexError, UnicodeDecodeError, struct.error):
        return {}
    return {'barcode': barcode, 'serial': serial, 'total': total, 'used': used, 'type': kind}


def print_label(c: Connection, rows: list[bytes], label_type: int, sequence: str = V4, density: int = DENSITY,
                head: int | None = None, beat_at_end: bool = False):
    """Print one label, rows of dots across the printhead of head dots, as
    the script this came from prints one: a job of one page, then the
    printer is asked every PAGE_POLL seconds how many pages it printed. The
    B1 sequence starts the page and tells its size and number in a shorter
    form. beat_at_end: a heartbeat after the end, not waited for, as
    niimbluelib's D110M_V4 sends one."""
    height, width = len(rows), len(rows[0])
    if head is None:
        head = B1_HEAD if sequence == B1 else HEAD
    deadline = time.monotonic() + ANSWER_WAIT
    while c.ask(0x21, bytes([density]), 0x31)[:1] != b'\x01':
        if time.monotonic() > deadline:
            raise NiimbotError('refused 0x21')
        time.sleep(START_POLL)
    c.ok(0x23, bytes([label_type]), 0x33)
    try:
        if sequence == B1:
            c.ok(0x01, struct.pack('>H5B', 1, 0, 0, 0, 0, 0), 0x02)
        else:
            c.ok(0x01, struct.pack('>H7B', 1, 0, 0, 0, 0, 0, 0, 0), 0x02)
    except NiimbotError as e:
        # A D110_M answered so to a roll with its RFID tag peeled off
        if e.args[1:] == (0x14,):
            raise NoTag(e.args[0]) from e
        raise
    if sequence == B1:
        c.ok(0x03, b'\x01', 0x04)
        c.ok(0x13, struct.pack('>HHH', height, width, 1), 0x14)
    else:
        c.send(packet(0xdc, b'\x01'))
        c.ok(0x13, struct.pack('>HHHHB', height, width, 1, 0, 0), 0x14)
    if sequence == B1:
        # In one write, over USB/IP each write waits for the host
        c.send(b''.join(row_packets(rows, head)))
    else:
        for data in row_packets(rows, head):
            c.send(data)
    c.ok(0xe3, b'\x01', 0xe4)
    deadline = time.monotonic() + PRINT_WAIT
    while True:
        time.sleep(PAGE_POLL)
        if struct.unpack('>H', c.ask(0xa3, b'\x01', 0xb3)[:2])[0] >= 1:
            break
        if time.monotonic() > deadline:
            raise NotPrinted('the label was not printed')
    c.ok(0xf3, b'\x01', 0xf4)
    if beat_at_end:
        c.send(packet(0xdc, b'\x01'))
