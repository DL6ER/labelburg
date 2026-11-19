"""The protocol of the Niimbot D110_M (firmware 4.x) over classic Bluetooth (RFCOMM),
and of the B1 over Bluetooth LE and its USB printer interface, the print sequence of the D110_M of https://github.com/eigger/hass-niimbot
(custom_components/niimbot/niimprint/printer.py), that of the B1 of
niimbluelib's B1PrintTask (https://github.com/MultiMote/niimbluelib, MIT).
Packets are 55 55 <command> <length> <data> <checksum> aa aa, the checksum
the XOR of command, length and data."""

import os
import select
import socket
import struct
import threading
import time

from .. import backends, bitmap, devices

# 8 dots per mm, the printhead of the D110_M is 15 mm wide, that of the B1 48 mm
DPI = 203
HEAD = 120
B1_HEAD = 384
# The print sequences, see print_label()
V4, B1 = 'v4', 'b1'
CHANNEL = 1
# Connecting may take a few seconds, an answer comes within ANSWER_WAIT
CONNECT_WAIT = 10
ANSWER_WAIT = 5
# How long a label may take to print, and how often the printer is asked
# meanwhile how many pages it printed, as the script this came from does
PRINT_WAIT = 30
PAGE_POLL = 0.5
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


def address(device: str) -> tuple[str, int]:
    """MAC address and RFCOMM channel of bt://<MAC>[/<channel>]."""
    mac, _, channel = device[len('bt://'):].partition('/')
    return mac.upper(), int(channel or CHANNEL)


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


def row_packets(rows: list[bytes], head: int = HEAD) -> list[bytes]:
    """Rows of dots, 1 printed, as packets: equal rows that follow each
    other once with their count, bitmap rows (85) with the dots in each
    third of the printhead of head dots, empty rows (84) without them."""
    out, y, third = [], 0, head // 8 // 3
    while y < len(rows):
        n = 1
        while y + n < len(rows) and rows[y + n] == rows[y] and n < 255:
            n += 1
        packed = bitmap.pack(rows[y])
        if any(packed):
            counts = [int.from_bytes(packed[k * third:(k + 1) * third]).bit_count() for k in range(3)]
            out.append(packet(0x85, struct.pack('>H3BB', y, *counts, n) + packed))
        else:
            out.append(packet(0x84, struct.pack('>HB', y, n)))
        y += n
    return out


class UsbSocket:
    """A printer on USB or shared over USB/IP, read and written like a
    socket. The B1 takes the same packets there as on Bluetooth."""

    def __init__(self, device: str):
        self.printer = backends.open_printer(device)
        self.timeout = CONNECT_WAIT

    def settimeout(self, timeout: float):
        self.timeout = timeout

    def sendall(self, data: bytes):
        self.printer.write(data)

    def recv(self, size: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        while not (data := self._read(size, deadline)):
            if time.monotonic() >= deadline:
                raise socket.timeout('no answer')
        return data

    def _read(self, size: int, deadline: float) -> bytes:
        # usblp blocks a read until the printer answers: read once it has
        if isinstance(self.printer, backends.KernelPrinter):
            ready, _, _ = select.select([self.printer.dev], [], [], max(0.0, deadline - time.monotonic()))
            return os.read(self.printer.dev, size) if ready else b''
        return self.printer.read(size)

    def close(self):
        self.printer.dispose()


# Bluetooth LE: the attribute protocol on its own L2CAP channel. The B1 takes
# Niimbot's packets written to one characteristic and answers with its
# notifications, as niimbluelib talks to it. It needs no pairing.
ATT_CID = 4
ATT_MTU = 247
NIIMBOT_CHARACTERISTIC = bytes.fromhex('bef8d6c99c214c9eb632bd58c1009f9f')[::-1]
CLIENT_CONFIGURATION = 0x2902
# The connection is kept: the B1 beeps when one opens and when it closes, not
# at what goes over it. Closed after KEEP_LE seconds unused, so that the phone
# app can connect to the printer.
KEEP_LE = 60


class LeSocket:
    """A printer on Bluetooth LE, read and written like a socket."""

    def __init__(self, device: str):
        mac, _ = address(device)
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        self.s.settimeout(CONNECT_WAIT)
        self.mtu, self.unread = 23, b''
        # Dropped, e.g. by a printer switched off; in use; last used; when
        # it is closed unused
        self.broken, self.busy, self.used, self.timer = False, False, 0.0, None
        try:
            self.s.bind((socket.BDADDR_ANY, 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
            with devices.bluetooth_turn:
                self.s.connect((mac, 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
            told = self._ask(0x02, struct.pack('<H', ATT_MTU))
            self.mtu = min(ATT_MTU, struct.unpack('<H', told[:2])[0])
            self.value, end = self._characteristic(NIIMBOT_CHARACTERISTIC)
            self._ask(0x12, struct.pack('<HH', self._configuration(self.value, end), 1))
        except (OSError, NiimbotError):
            self.s.close()
            raise

    def _ask(self, op: int, data: bytes) -> bytes | None:
        """The answer to an attribute request, None if the printer has no
        such attribute. Its own request for the MTU is answered meanwhile."""
        self._send(bytes([op]) + data)
        while True:
            pdu = self._recv()
            if not pdu:
                raise NoAnswer('the printer closed the connection')
            if pdu[0] == op + 1:
                return pdu[1:]
            if pdu[0] == 0x01:
                if pdu[4:5] == b'\x0a':
                    return None
                raise NiimbotError(f'attribute error 0x{pdu[4]:02x} on 0x{op:02x}')
            self._other(pdu)

    def _other(self, pdu: bytes):
        if pdu[0] == 0x02:
            self._send(bytes([0x03]) + struct.pack('<H', ATT_MTU))
        elif pdu[0] == 0x1d:
            self._send(b'\x1e')

    def _send(self, pdu: bytes):
        try:
            self.s.send(pdu)
        except TimeoutError:
            raise
        except OSError:
            self.broken = True
            raise

    def _recv(self) -> bytes:
        """An attribute PDU, b'' once the connection dropped."""
        try:
            pdu = self.s.recv(ATT_MTU)
        except TimeoutError:
            raise
        except OSError:
            self.broken = True
            raise
        if not pdu:
            self.broken = True
        return pdu

    def _characteristic(self, uuid: bytes) -> tuple[int, int]:
        """The value handle of a characteristic and the last handle of its
        descriptors."""
        value, start = None, 1
        while start <= 0xffff:
            answer = self._ask(0x08, struct.pack('<HHH', start, 0xffff, 0x2803))
            if answer is None:
                break
            n = answer[0]
            for i in range(1, len(answer), n):
                handle, _, at = struct.unpack_from('<HBH', answer, i)
                if value is not None:
                    return value, handle - 1
                if answer[i + 5:i + n] == uuid:
                    value = at
                start = handle + 1
        if value is None:
            raise NiimbotError('no Niimbot characteristic on Bluetooth LE')
        return value, 0xffff

    def _configuration(self, value: int, end: int) -> int:
        """The handle that turns the notifications of a characteristic on."""
        start = value + 1
        while start <= end:
            answer = self._ask(0x04, struct.pack('<HH', start, end))
            if answer is None:
                break
            size = 4 if answer[0] == 1 else 18
            for i in range(1, len(answer), size):
                handle = struct.unpack_from('<H', answer, i)[0]
                if size == 4 and struct.unpack_from('<H', answer, i + 2)[0] == CLIENT_CONFIGURATION:
                    return handle
                start = handle + 1
        raise NiimbotError('no notifications of the Niimbot characteristic')

    def settimeout(self, timeout: float):
        self.s.settimeout(timeout)

    def sendall(self, data: bytes):
        # Written without an answer, in pieces the MTU takes
        piece = self.mtu - 3
        for i in range(0, len(data), piece):
            self._send(bytes([0x52]) + struct.pack('<H', self.value) + data[i:i + piece])

    def recv(self, size: int) -> bytes:
        while not self.unread:
            pdu = self._recv()
            if not pdu:
                return b''
            if pdu[0] in (0x1b, 0x1d) and struct.unpack_from('<H', pdu, 1)[0] == self.value:
                self.unread = pdu[3:]
            self._other(pdu)
        data, self.unread = self.unread[:size], self.unread[size:]
        return data

    def close(self):
        self.s.close()


# The connections kept, by device
_kept: dict[str, LeSocket] = {}
_kept_lock = threading.Lock()


def _kept_le(device: str) -> LeSocket:
    """The kept connection to a printer on Bluetooth LE, opened if there is
    none or it dropped. The driver lets one user at a time have it."""
    key = devices.lock_key(device)
    with _kept_lock:
        s = _kept.pop(key, None)
        if s is not None and not s.broken:
            _kept[key], s.busy = s, True
            return s
    if s is not None:
        s.close()
    s = LeSocket(device)
    with _kept_lock:
        _kept[key], s.busy = s, True
    return s


def _release_le(device: str, s: LeSocket):
    key = devices.lock_key(device)
    with _kept_lock:
        s.busy, s.used = False, time.monotonic()
        if not s.broken:
            if s.timer is None:
                s.timer = _closing(key, s, KEEP_LE)
            return
        if _kept.get(key) is s:
            del _kept[key]
    s.close()


def _closing(key: str, s: LeSocket, after: float) -> threading.Timer:
    timer = threading.Timer(after, _close_unused, (key, s))
    timer.daemon = True
    timer.start()
    return timer


def _close_unused(key: str, s: LeSocket):
    with _kept_lock:
        s.timer = None
        if _kept.get(key) is not s:
            return
        unused = time.monotonic() - s.used
        if s.busy or unused < KEEP_LE:
            s.timer = _closing(key, s, KEEP_LE if s.busy else KEEP_LE - unused)
            return
        del _kept[key]
    s.close()


class Connection:
    def __init__(self, device: str, le: bool = False):
        self.received = b''
        if not device.startswith('bt://'):
            self.s = UsbSocket(device)
            return
        if le:
            self.s, self.device = _kept_le(device), device
            return
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.s.settimeout(CONNECT_WAIT)
        try:
            with devices.bluetooth_turn:
                self.s.connect(address(device))
        except OSError:
            self.s.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if isinstance(self.s, LeSocket):
            _release_le(self.device, self.s)
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


def print_label(c: Connection, rows: list[bytes], label_type: int, sequence: str = V4):
    """Print one label, rows of dots across the printhead, as the script
    this came from prints one: a job of one page, then the printer is asked
    every PAGE_POLL seconds how many pages it printed. The B1 starts the
    page and is told its size and number in a shorter form."""
    height, width = len(rows), len(rows[0])
    c.ok(0x21, bytes([DENSITY]), 0x31)
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
        c.send(b''.join(row_packets(rows, B1_HEAD)))
    else:
        for data in row_packets(rows, HEAD):
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
