"""The protocol of the Niimbot printers: the D110_M (firmware 4.x) over classic Bluetooth (RFCOMM),
the others over Bluetooth LE and on USB, the print sequence of the D110_M of https://github.com/eigger/hass-niimbot
(custom_components/niimbot/niimprint/printer.py), the others those of
niimbluelib's print tasks (https://github.com/MultiMote/niimbluelib, MIT,
src/print_tasks at commit 35110a71), see models.py for the models of each.
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
V4, B1, D110M_V4 = 'v4', 'b1', 'd110m_v4'
B21_V1, B21_L2B, D110, D11_V1, H1S = 'b21_v1', 'b21_l2b', 'd110', 'd11_v1', 'h1s'
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
# niimbluelib's In_NotSupported, an answer it takes as an error too
NOT_SUPPORTED = 0x00
# Byte 8 of the heartbeat of a D110_M: 1 with the lid closed, with a roll
# inside and without one (10 1f 00 69 00 69 32 46 01 04 and 10 20 00 97 00 97
# 32 46 01 04), 0 with the lid open (10 22 00 54 00 54 32 46 00 04). It tells
# no paper: the bytes that change without a roll change with an open lid too.
# niimbluelib reads the D110's 10 bytes so too, see models.INVERTED_LID.
LID_OPEN = 0
# Bytes 9 to 12 of the 13 a B1 answers: lid (1 open), battery, paper (1 none)
# and whether it read the RFID tag of the roll, e.g. 1f f7 00 61 00 61 00 00
# 4f 00 04 00 01 closed with a roll, 20 17 00 54 00 54 00 00 4f 01 04 01 00
# open, 20 17 00 85 00 85 00 00 4f 00 04 01 00 closed without a roll.
# niimbluelib's print tasks wait for each answer up to 10 s within a page,
# up to 1 s in Utils.doUntilTrue() and for a printEnd after a failed label,
# and 10 ms before each packet (pageTimeoutMs, DEFAULT_PACKET_TIMEOUT of
# src/packets/protocol.ts:37, packetIntervalMs)
PAGE_WAIT = 10
PACKET_WAIT = 1
PACKET_GAP = 0.01
# The answer to each packet of those tasks, by niimbluelib's commandsMap
ANSWERS = {0x01: 0x02, 0x03: 0x04, 0x13: 0x14, 0x15: 0x16, 0x20: 0x30, 0x21: 0x31, 0x23: 0x33, 0x86: 0xd3,
           0xa3: 0xb3, 0xa5: 0xb5, 0xe3: 0xe4, 0xf3: 0xf4}


class NiimbotError(Exception):
    pass


class NoAnswer(NiimbotError, TimeoutError):
    """Also of a B1 switched off on USB, which stays on the bus."""


class NoTag(NiimbotError):
    pass


class NotPrinted(NiimbotError):
    pass


class NotSupported(NiimbotError):
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


def counts(packed: bytes, head: int, total: bool = False) -> bytes:
    """The dots of a packed row as niimbluelib counts them for a printhead of
    head dots: in each third of it where the row fits into them, else or
    with total all of them, little-endian after a zero."""
    third = head // 8 // 3
    if len(packed) <= 3 * third and not total:
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


def image_packets(rows: list[bytes], head: int, total: bool = False, checks: bool = False) -> list[bytes]:
    """Rows of dots, 1 printed, as niimbluelib's ImageEncoder and
    writeImageDataSingleColor send them: equal rows once with their count,
    a count ending at every 200th row, empty rows (84), rows of up to 6 dots
    by the place of each (83), others as bitmap (85), counted for a
    printhead of head dots, see counts(). With checks the number of every
    200th row follows it (86)."""
    out, y = [], 0
    while y < len(rows):
        n = 1
        while y + n < len(rows) and rows[y + n] == rows[y] and (y + n) % 200:
            n += 1
        packed = bitmap.pack(rows[y])
        dots = int.from_bytes(packed).bit_count()
        if not dots:
            out.append(packet(0x84, struct.pack('>HB', y, n)))
        elif dots <= 6:
            places = b''.join(struct.pack('>H', x) for x, dot in enumerate(rows[y]) if dot)
            out.append(packet(0x83, struct.pack('>H', y) + counts(packed, head, total) + bytes([n]) + places))
        else:
            out.append(packet(0x85, struct.pack('>H', y) + counts(packed, head, total) + bytes([n]) + packed))
        y += n
        if checks and not y % 200:
            out.append(packet(0x86, struct.pack('>HB', y - 1, 1)))
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
            self.s = transports.usb_link(device)
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

    # Packets received after the answer to the last one sent, e.g. a page
    # index told along with the end of the page
    unread = ()

    def send(self, data: bytes):
        self.unread = []
        self.s.sendall(data)

    def ask(self, command: int, data: bytes, replies, send: bool = True, wait: float | None = None) -> bytes:
        """Send a packet, the data of the first reply that is one of
        replies, waited for ANSWER_WAIT seconds or wait. Without send only
        read, e.g. the late answer to one sent before. Raises NiimbotError if
        the printer reports an error or does not answer."""
        replies = {replies} if isinstance(replies, int) else set(replies)
        if send:
            self.send(packet(command, data))
        deadline = time.monotonic() + (ANSWER_WAIT if wait is None else wait)
        while True:
            while self.unread:
                reply, content = self.unread.pop(0)
                if reply == ERROR:
                    raise NiimbotError(f'error 0x{content[0]:02x} on 0x{command:02x}', content[0])
                if reply == NOT_SUPPORTED:
                    raise NotSupported(f'0x{command:02x} not supported')
                if reply in replies:
                    return content
            if time.monotonic() >= deadline:
                break
            self.s.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                data = self.s.recv(256)
            except socket.timeout:
                break
            if not data:
                break
            self.unread, self.received = parse(self.received + data)
        raise NoAnswer(f'no answer to 0x{command:02x}')

    def ok(self, command: int, data: bytes, reply: int):
        if not self.ask(command, data, reply)[:1] == b'\x01':
            raise NiimbotError(f'refused 0x{command:02x}')


def connect(device: str, le: bool = False) -> Connection:
    """le: a printer on bt:// over Bluetooth LE, else classic Bluetooth."""
    return Connection(device, le)


def heartbeat(c: Connection, send: bool = True, wait: float | None = None, inverted: bool = False) -> dict:
    """Whether the lid is open and the battery (0 to 4), from the 10 bytes
    a D110_M answers, e.g. 10 1f 00 69 00 69 32 46 01 04, or the 13 a B1
    answers or 19 as niimbluelib reads them, their lid the other way round
    where inverted; the raw data if the length is another. send and wait as
    for Connection.ask()."""
    try:
        data = c.ask(0xdc, b'\x01', {0xd9, 0xdd, 0xde, 0xdf}, send, wait)
    except NoAnswer:
        raise
    except NiimbotError:
        # An error answer left from a job the printer stopped, e.g. with its
        # cover opened, comes before the answer
        data = c.ask(0xdc, b'\x01', {0xd9, 0xdd, 0xde, 0xdf}, send, wait)
    if len(data) in (13, 19):
        lid = len(data) - 4
        return {'open': (data[lid] != 0) != inverted, 'battery': data[lid + 1]}
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


def protocol_version(c: Connection) -> int:
    """The protocol version as niimbluelib's connectNegotiate() asks for it:
    1 for a printer that connects as a new one (2), by its status data for
    one that connects as of version 3 (3), else 0. The packet to connect
    leads with 03."""
    time.sleep(PACKET_GAP)
    c.send(b'\x03' + packet(0xc1, b'\x01'))
    connected = c.ask(0xc1, b'', 0xc2, send=False)[:1]
    if connected == b'\x02':
        return 1
    if connected != b'\x03':
        return 0
    data = _request(c, 0xa5)
    if len(data) < 13:
        return 0
    n = data[11] * 100 + data[12]
    return 3 if 204 <= n < 300 else 4 if 300 <= n < 302 else 5 if n >= 302 else 0


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
    answers 00 so, a printer without RFID that it is not supported), {}
    for a tag laid out otherwise. send and wait as for Connection.ask()."""
    try:
        data = c.ask(0x1a, b'\x01', 0x1b, send, wait)
    except NotSupported:
        return None
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
                head: int | None = None, exact: bool = False):
    """Print one label, rows of dots across the printhead of head dots: a
    job of one page, then the printer is asked every PAGE_POLL seconds how
    many pages it printed. V4 as hass-niimbot prints one, the others as
    niimbluelib's print tasks of those names, see also _task(). exact:
    the rows as niimbluelib sends them, see image_packets()."""
    height, width = len(rows), len(rows[0])
    if head is None:
        head = B1_HEAD if sequence == B1 else HEAD
    if sequence in (B21_V1, B21_L2B, D110, D11_V1, H1S):
        return _task(c, rows, label_type, sequence, density, head)
    if sequence == D110M_V4:
        c.ok(0x23, bytes([label_type]), 0x33)
    _set_density(c, density)
    if sequence != D110M_V4:
        c.ok(0x23, bytes([label_type]), 0x33)
    try:
        if sequence == B1:
            c.ok(0x01, struct.pack('>H5B', 1, 0, 0, 0, 0, 0), 0x02)
        elif sequence == D110M_V4:
            # At speed 1, niimbluelib's default
            c.ok(0x01, struct.pack('>H7B', 1, 0, 0, 0, 0, 0, 1, 0), 0x02)
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
    elif sequence == D110M_V4:
        # Not waited for, as a B21_PRO leaves the first packet after the
        # start unanswered on Bluetooth
        c.send(packet(0xa3, b'\x01'))
        c.ok(0x13, struct.pack('>HHHHBBBH', height, width, 1, 0, 0, 0, 0, 0), 0x14)
    else:
        c.send(packet(0xdc, b'\x01'))
        c.ok(0x13, struct.pack('>HHHHB', height, width, 1, 0, 0), 0x14)
    packets = image_packets(rows, head) if exact else row_packets(rows, head)
    if sequence == B1:
        # In one write, over USB/IP each write waits for the host
        c.send(b''.join(packets))
    else:
        for data in packets:
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
    if sequence == D110M_V4:
        # Not waited for, a B21_PRO drops the packet after the end
        c.send(packet(0xdc, b'\x01'))


def _set_density(c: Connection, density: int):
    """Set the density, asked again while the printer refuses it."""
    deadline = time.monotonic() + ANSWER_WAIT
    while c.ask(0x21, bytes([density]), 0x31)[:1] != b'\x01':
        if time.monotonic() > deadline:
            raise NiimbotError('refused 0x21')
        time.sleep(START_POLL)


def _task(c: Connection, rows: list[bytes], label_type: int, sequence: str, density: int, head: int):
    """Print one label as niimbluelib's print task of the sequence does,
    e.g. B21V1PrintTask.ts for B21_V1: printInit(), printPage(),
    waitForPageFinished() and waitForFinished(), then printEnd() as its
    example of a task ends every one, also a failed one
    (AbstractPrintTask.ts:67-88). It judges no answer but those to the
    B21_L2B's start and end of the page and to the B21_V1's printEnd."""
    height, width = len(rows), len(rows[0])
    try:
        _request(c, 0x21, bytes([density]))
        _request(c, 0x23, bytes([label_type]))
        _request(c, 0x01, struct.pack('>H', 1) if sequence == H1S else b'\x01')
        if sequence in (D110, D11_V1):
            _request(c, 0x20, wait=PAGE_WAIT)
        if sequence == B21_L2B:
            _until_true(c, 0x03, 5, NiimbotError('refused 0x03'))
        else:
            _request(c, 0x03, wait=PAGE_WAIT)
        size = {D11_V1: struct.pack('>H', height), H1S: struct.pack('>HHHHB', height, width, 1, 0, 0)}
        _request(c, 0x13, size.get(sequence, struct.pack('>HH', height, width)), PAGE_WAIT)
        if sequence in (D110, D11_V1):
            _request(c, 0x15, struct.pack('>H', 1), PAGE_WAIT)
        b21 = sequence in (B21_V1, B21_L2B)
        for data in image_packets(rows, head, total=b21, checks=b21):
            if data[2] == 0x86:
                _request(c, 0x86, data[4:-3], PAGE_WAIT)
            else:
                time.sleep(PACKET_GAP)
                c.send(data)
        _request(c, 0xe3, wait=PAGE_WAIT)
        if sequence == B21_V1:
            # Its printEnd taken ends the print, no other follows
            _until_ended(c)
            return
        if sequence == B21_L2B:
            _until_true(c, 0xe3, 20, NotPrinted('the label was not printed'))
        elif sequence == D11_V1:
            _until_page_index(c)
        else:
            # The D110 waits for the page, then for all pages
            for _ in range(2 if sequence == D110 else 1):
                _until_printed(c)
    except NiimbotError:
        _end_quietly(c)
        raise
    _request(c, 0xf3)


def _request(c: Connection, command: int, data: bytes = b'\x01', wait: float | None = None) -> bytes:
    """A packet of niimbluelib's print tasks and the answer to it."""
    time.sleep(PACKET_GAP)
    return c.ask(command, data, ANSWERS[command], wait=wait)


def _until_true(c: Connection, command: int, attempts: int, error: NiimbotError):
    """Utils.doUntilTrue(): the packet until the printer answers 1, attempts
    times at most, PAGE_POLL seconds apart after an answer of 0, each answer
    waited for PACKET_WAIT. Raises the last error of an attempt, else error."""
    for _ in range(attempts):
        try:
            if _request(c, command, wait=PACKET_WAIT)[:1] == b'\x01':
                return
            time.sleep(PAGE_POLL)
        except NiimbotError as e:
            error = e
    raise error


def _until_ended(c: Connection):
    """waitUntilPrintFinishedByPrintEndPoll(): printEnd every PAGE_POLL
    seconds until the printer takes it. niimbluelib's printEnd() stops that
    poll at its first packet (protocol.ts:450), one refused is sent again
    here as the poll means to."""
    deadline = time.monotonic() + PRINT_WAIT
    while True:
        time.sleep(PAGE_POLL)
        if _request(c, 0xf3)[:1] == b'\x01':
            return
        if time.monotonic() > deadline:
            raise NotPrinted('the label was not printed')


def _until_printed(c: Connection):
    """waitUntilPrintFinishedByStatusPoll(): every PAGE_POLL seconds how
    many pages were printed, each asked twice at most (getPrintStatus(2)),
    until one was. An error in an answer of 10 bytes is raised."""
    deadline = time.monotonic() + PRINT_WAIT
    while True:
        time.sleep(PAGE_POLL)
        try:
            data = _request(c, 0xa3)
        except NiimbotError:
            data = _request(c, 0xa3)
        if len(data) < 4:
            raise NiimbotError(f'print status {data.hex()}')
        if len(data) == 10 and data[6]:
            raise NiimbotError(f'print error 0x{data[6]:02x}', data[6])
        if struct.unpack('>H', data[:2])[0] >= 1:
            return
        if time.monotonic() > deadline:
            raise NotPrinted('the label was not printed')


def _until_page_index(c: Connection):
    """waitUntilPrintFinishedByPageIndex(): the page index the printer tells
    by itself (e0) until a page was printed, each within ANSWER_WAIT of the
    one before, all within PRINT_WAIT."""
    deadline = time.monotonic() + PRINT_WAIT
    while True:
        try:
            data = c.ask(0xe0, b'', 0xe0, send=False)
        except NoAnswer as e:
            raise NotPrinted('the label was not printed') from e
        if len(data) == 2 and struct.unpack('>H', data)[0] >= 1:
            return
        if time.monotonic() > deadline:
            raise NotPrinted('the label was not printed')


def _end_quietly(c: Connection):
    """printEnd after a failed label, its answer not waited for long."""
    try:
        _request(c, 0xf3, wait=PACKET_WAIT)
    except (NiimbotError, OSError):
        pass
