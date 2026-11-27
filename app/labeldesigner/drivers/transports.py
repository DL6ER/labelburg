"""Connections to printers that several drivers share: classic Bluetooth
(RFCOMM), Bluetooth LE, USB and serial ports, read and written like sockets
by drivers that exchange packets with the printer, and RfcommPrinter, a
printer on classic Bluetooth as backends.Printer for the drivers that send
it a stream of bytes."""

import errno
import os
import select
import socket
import struct
import threading
import time

from .. import bluetooth
from . import backends, devices

# Connecting took about 4 s for a PUQU Q00, also with the printer close by
CONNECT_WAIT = 10
# The channel of an RfcommPrinter whose address names none
RFCOMM_CHANNEL = 1


def bt_address(device: str, channel: int) -> tuple[str, int]:
    """MAC address and RFCOMM channel of bt://<MAC>[/<channel>], channel
    where it names none."""
    mac, _, named = device[len('bt://'):].partition('/')
    return mac.upper(), int(named or channel)


def rfcomm(device: str, channel: int) -> socket.socket:
    """A connection over classic Bluetooth to bt://<MAC>[/<channel>], on
    channel where it names none."""
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(CONNECT_WAIT)
    try:
        with devices.bluetooth_turn:
            s.connect(bt_address(device, channel))
    except OSError:
        s.close()
        raise
    return s


class RfcommPrinter(backends.Printer):
    """A printer on classic Bluetooth, bt://<MAC>[/<channel>], for the
    drivers that write a job and read its answers as on USB."""

    # In seconds
    read_timeout = 0.01
    write_timeout = 15.

    def __init__(self, device_specifier: str, channel: int = RFCOMM_CHANNEL):
        self.s = rfcomm(device_specifier, channel)

    def _write(self, data: bytes):
        self.s.settimeout(self.write_timeout)
        self.s.sendall(data)

    def _read(self, length: int) -> bytes:
        self.s.settimeout(self.read_timeout)
        try:
            return self.s.recv(length)
        except TimeoutError:
            return b''

    def _dispose(self):
        self.s.close()


class UsbLink:
    """A printer on USB or shared over USB/IP, read and written like a
    socket, e.g. a Niimbot B1, which takes the same packets there as on
    Bluetooth."""

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


class SerialLink:
    """A printer on a serial port, serial:///dev/ttyACM<N> as Linux names
    one on USB, read and written like a socket: raw at 115200 baud 8N1,
    with DTR and RTS on."""

    # Seconds a write may take, whatever an answer may take
    write_timeout = 15.

    def __init__(self, device: str):
        import fcntl
        import termios
        import tty
        self.fd = os.open(device.removeprefix('serial://'), os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        self.timeout = CONNECT_WAIT
        try:
            mode = termios.tcgetattr(self.fd)
            tty.cfmakeraw(mode)
            mode[tty.CFLAG] &= ~(termios.CSTOPB | getattr(termios, 'CRTSCTS', 0))
            mode[tty.CFLAG] |= termios.CREAD | termios.CLOCAL
            mode[tty.ISPEED] = mode[tty.OSPEED] = termios.B115200
            termios.tcsetattr(self.fd, termios.TCSANOW, mode)
            try:
                fcntl.ioctl(self.fd, termios.TIOCMBIS, struct.pack('i', termios.TIOCM_DTR | termios.TIOCM_RTS))
            except OSError as e:
                # A port without these lines, e.g. a pseudo terminal
                if e.errno not in (errno.ENOTTY, errno.EINVAL):
                    raise
        except BaseException:
            os.close(self.fd)
            raise

    def settimeout(self, timeout: float):
        self.timeout = timeout

    def sendall(self, data: bytes):
        deadline = time.monotonic() + self.write_timeout
        while data:
            if not select.select([], [self.fd], [], max(0.0, deadline - time.monotonic()))[1]:
                raise socket.timeout('not sent')
            try:
                data = data[os.write(self.fd, data):]
            except BlockingIOError:
                pass

    def recv(self, size: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        while select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))[0]:
            try:
                return os.read(self.fd, size)
            except BlockingIOError:
                pass
        raise socket.timeout('no answer')

    def close(self):
        os.close(self.fd)


# Bluetooth LE: the attribute protocol on its own L2CAP channel. Packets are
# written to a characteristic and answered with notifications, as
# niimbluelib talks to a Niimbot B1. No pairing is needed.
ATT_CID = 4
ATT_MTU = 247
CLIENT_CONFIGURATION = 0x2902
# The connection is kept: the B1 beeps when one opens and when it closes, not
# at what goes over it. Closed after KEEP_LE seconds unused, so that the phone
# app can connect to the printer.
KEEP_LE = 60


class LeLink:
    """A printer on Bluetooth LE, read and written like a socket: what is
    sent is written to the characteristic, what is received are the
    notifications of notify, the same characteristic if None. Raises error
    where the printer has no such characteristic, no_answer where it closes
    the connection."""

    def __init__(self, device: str, characteristic: bytes, notify: bytes | None = None, *, error, no_answer):
        mac, _ = bt_address(device, 0)
        self.error, self.no_answer = error, no_answer
        if not hasattr(socket, 'BDADDR_LE_PUBLIC'):
            raise devices.NoBluetoothLe('Python before 3.14 opens no L2CAP connection of Bluetooth LE')
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        self.s.settimeout(CONNECT_WAIT)
        self.mtu, self.unread = 23, b''
        # Dropped, e.g. by a printer switched off; in use; last used; when
        # it is closed unused
        self.broken, self.busy, self.used, self.timer = False, False, 0.0, None
        try:
            self.s.bind((bluetooth.adapter_for(mac), 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
            with devices.bluetooth_turn:
                try:
                    self.s.connect((mac, 0, ATT_CID, socket.BDADDR_LE_PUBLIC))
                except TimeoutError as e:
                    # Not advertising, e.g. switched off: as classic
                    # Bluetooth tells of a printer it cannot page
                    raise OSError(errno.EHOSTDOWN, 'Host is down') from e
            told = self._ask(0x02, struct.pack('<H', ATT_MTU))
            self.mtu = min(ATT_MTU, struct.unpack('<H', told[:2])[0])
            self.value, end = self._characteristic(characteristic)
            self.notified = self.value
            if notify is not None and notify != characteristic:
                self.notified, end = self._characteristic(notify)
            self._ask(0x12, struct.pack('<HH', self._configuration(self.notified, end), 1))
        except (OSError, error, no_answer):
            self.s.close()
            raise

    def _ask(self, op: int, data: bytes) -> bytes | None:
        """The answer to an attribute request, None if the printer has no
        such attribute. Its own request for the MTU is answered meanwhile."""
        self._send(bytes([op]) + data)
        while True:
            pdu = self._recv()
            if not pdu:
                raise self.no_answer('the printer closed the connection')
            if pdu[0] == op + 1:
                return pdu[1:]
            if pdu[0] == 0x01:
                if pdu[4:5] == b'\x0a':
                    return None
                raise self.error(f'attribute error 0x{pdu[4]:02x} on 0x{op:02x}')
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
            raise self.error(f'no characteristic {uuid[::-1].hex()} on Bluetooth LE')
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
        raise self.error('no notifications of the characteristic')

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
            if pdu[0] in (0x1b, 0x1d) and struct.unpack_from('<H', pdu, 1)[0] == self.notified:
                self.unread = pdu[3:]
            self._other(pdu)
        data, self.unread = self.unread[:size], self.unread[size:]
        return data

    def close(self):
        self.s.close()


# The connections kept, by device
_kept: dict[str, LeLink] = {}
_kept_lock = threading.Lock()


def kept_le(device: str, characteristic: bytes, notify: bytes | None = None, *, error, no_answer) -> LeLink:
    """The kept connection to a printer on Bluetooth LE, opened as LeLink if
    there is none or it dropped. The driver lets one user at a time have it
    and gives it back with release_le()."""
    key = devices.lock_key(device)
    with _kept_lock:
        s = _kept.pop(key, None)
        if s is not None and not s.broken:
            _kept[key], s.busy = s, True
            return s
    if s is not None:
        s.close()
    s = LeLink(device, characteristic, notify, error=error, no_answer=no_answer)
    with _kept_lock:
        _kept[key], s.busy = s, True
    return s


def release_le(device: str, s: LeLink):
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


def _closing(key: str, s: LeLink, after: float) -> threading.Timer:
    timer = threading.Timer(after, _close_unused, (key, s))
    timer.daemon = True
    timer.start()
    return timer


def _close_unused(key: str, s: LeLink):
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
