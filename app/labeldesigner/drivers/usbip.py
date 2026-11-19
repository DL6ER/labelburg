"""Printers plugged into another device that shares them over USB/IP, e.g., a
Raspberry Pi or an ESP32 next to the printer, as
usbip://<host>[:<port>][/<bus ID>]. The label designer imports the printer
itself, by the protocol Linux describes in
Documentation/usb/usbip_protocol.rst, and talks to its printer interface: no
kernel module, root or device passed to a container. Standard library only."""

import errno
import logging
import re
import socket
import struct
import threading
import time

logger = logging.getLogger(__name__)

PORT = 3240
VERSION = 0x0111
OP_REQ_DEVLIST, OP_REP_DEVLIST = 0x8005, 0x0005
OP_REQ_IMPORT, OP_REP_IMPORT = 0x8003, 0x0003
CMD_SUBMIT, CMD_UNLINK, RET_SUBMIT, RET_UNLINK = 1, 2, 3, 4
OUT, IN = 0, 1
# The flag Linux sets in the transfer_flags of a request that reads
URB_DIR_IN = 0x0200
# Answers to an import: the device is used by another client, or not shared
ST_NA, ST_DEV_BUSY, ST_NODEV = 0x01, 0x02, 0x04
# Status of a transfer of a device that is gone (Linux error numbers)
GONE = (-19, -108)  # ENODEV, ESHUTDOWN

# Path, bus ID, bus and device number, speed, vendor, product, release,
# class, subclass, protocol, configuration, configurations, interfaces
DEVICE = struct.Struct('>256s32sIIIHHHBBBBBB')
INTERFACE = struct.Struct('>BBBx')
OP = struct.Struct('>HHI')
# Command, sequence number, device, direction, endpoint and the 28 bytes
# that depend on the command
HEADER = struct.Struct('>IIIII28s')
# transfer_flags, length, start_frame, number_of_packets, interval, setup
SUBMIT = struct.Struct('>IiiiI8s')

PRINTER_CLASS = 7
GET_DEVICE_ID = 0
# Printer protocols in the order they are taken: bidirectional, then
# unidirectional and IEEE 1284.4. IPP over USB (4) is left to others.
PROTOCOLS = (2, 1, 3)

CONNECT_TIMEOUT = 5
CONTROL_TIMEOUT = 5
# A printer that could not be reached is asked for its device ID again after
# so many seconds, the drivers each ask for it to find the model
UNREACHABLE_FOR = 5
# A read asks for this many bytes, a multiple of the packet size of every
# USB speed. A write sends up to WRITE_SIZE bytes at a time.
READ_SIZE = 4096
WRITE_SIZE = 16384
# A connection that hears nothing is checked after KEEPALIVE seconds
KEEPALIVE = 30
# A host that acknowledges no data for USER_TIMEOUT seconds is gone, e.g.,
# switched off; a printer taking data slowly does not delay acknowledgments
USER_TIMEOUT = 5

ADDRESS = re.compile(r'usbip://(?P<host>\[[0-9a-f:.]+\]|[a-z0-9.-]+)(:(?P<port>\d{1,5}))?(/(?P<busid>[0-9.-]+))?/?', re.IGNORECASE)


def is_address(device) -> bool:
    return isinstance(device, str) and device.strip().lower().startswith('usbip://')


def parse(address: str) -> tuple[str, int, str]:
    """Host, port and bus ID of a shared printer, the bus ID empty for the
    printer the host shares."""
    match = ADDRESS.fullmatch(address.strip())
    if not match:
        raise ValueError(f'Not a USB/IP address: {address}')
    return match['host'].strip('[]').lower(), int(match['port'] or PORT), match['busid'] or ''


def key(address: str) -> str:
    """The same name for every spelling of the address."""
    host, port, busid = parse(address)
    return f"usbip://{f'[{host}]' if ':' in host else host}:{port}" + (f'/{busid}' if busid else '')


def _connect(host: str, port: int, timeout: float) -> socket.socket:
    connection = socket.create_connection((host, port), timeout=timeout)
    connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return connection


def _receive(connection: socket.socket, size: int, until=None) -> bytes:
    """Exactly size bytes. Timeouts are waited out until until() is true."""
    data = b''
    while len(data) < size:
        try:
            chunk = connection.recv(size - len(data))
        except TimeoutError as e:
            # Only a timeout of the socket has no errno, ETIMEDOUT of the
            # kernel ends the connection
            if e.errno is not None or until is None or until():
                raise
            continue
        if not chunk:
            raise OSError(errno.ENODEV, 'The host closed the connection to the printer')
        data += chunk
    return data


def _text(field: bytes) -> str:
    return field.split(b'\0', 1)[0].decode('ascii', 'replace')


def shared_devices(host: str, port: int = PORT, timeout: float = CONNECT_TIMEOUT) -> list[dict]:
    """The devices a host shares that no client uses: their bus ID, vendor,
    product, class and the classes of their interfaces."""
    with _connect(host, port, timeout) as connection:
        connection.sendall(OP.pack(VERSION, OP_REQ_DEVLIST, 0))
        _, code, status = OP.unpack(_receive(connection, OP.size))
        if code != OP_REP_DEVLIST or status:
            raise OSError(errno.EPROTO, f'{host} did not list its devices')
        count, = struct.unpack('>I', _receive(connection, 4))
        found = []
        for _ in range(min(count, 256)):
            record = DEVICE.unpack(_receive(connection, DEVICE.size))
            classes = [INTERFACE.unpack(_receive(connection, INTERFACE.size))[0] for _ in range(record[13])]
            found.append({'busid': _text(record[1]), 'vendor': record[5], 'product': record[6],
                          'device_class': record[8], 'interface_classes': classes})
        return found


def _printer_busid(host: str, port: int, timeout: float) -> str:
    shared = shared_devices(host, port, timeout)
    printers = [device for device in shared if PRINTER_CLASS in (device['device_class'], *device['interface_classes'])]
    if not printers:
        # Linux lists a device it has not configured without interfaces,
        # whether it is a printer shows once it is imported
        printers = [device for device in shared if not device['device_class'] and not device['interface_classes']]
    if not printers:
        raise OSError(errno.ENODEV, f'{host} shares no printer')
    if len(printers) > 1:
        logger.info('%s shares %d printers, using the one at %s', host, len(printers), printers[0]['busid'])
    return printers[0]['busid']


def _printer_interface(descriptor: bytes) -> tuple[int, int, int, int | None]:
    """Number and alternate setting of the printer interface of a
    configuration descriptor, and its bulk endpoints out and in (None for a
    printer that only takes data)."""
    interfaces, current, at = [], None, 0
    while at + 2 <= len(descriptor):
        length, kind = descriptor[at], descriptor[at + 1]
        if length < 2:
            break
        part = descriptor[at:at + length]
        if kind == 4 and length >= 9:
            current = {'number': part[2], 'alt': part[3], 'class': part[5], 'protocol': part[7], 'out': None, 'in': None}
            interfaces.append(current)
        elif kind == 5 and length >= 7 and current is not None and part[3] & 3 == 2:
            current['in' if part[2] & 0x80 else 'out'] = part[2] & 0x0F
        at += length
    printers = [i for i in interfaces if i['class'] == PRINTER_CLASS and i['protocol'] in PROTOCOLS and i['out'] is not None]
    if not printers:
        raise OSError(errno.ENODEV, 'The shared device is no printer')
    chosen = min(printers, key=lambda i: PROTOCOLS.index(i['protocol']))
    return chosen['number'], chosen['alt'], chosen['out'], chosen['in']


class SharedPrinter:
    """The printer interface of an imported printer. A job is read while it
    is written, so reads and writes may come from two threads; an own thread
    takes the answers of the host."""

    def __init__(self, address: str, timeout: float = CONNECT_TIMEOUT):
        host, port, busid = parse(address)
        busid = busid or _printer_busid(host, port, timeout)
        self._socket = _connect(host, port, timeout)
        self._lock = threading.Condition()
        self._send_lock, self._read_lock, self._write_lock = threading.Lock(), threading.Lock(), threading.Lock()
        # Direction and length of the transfers not answered yet, answers not taken yet
        self._pending, self._answers = {}, {}
        self._seqnum = 0
        self._reading = None
        self._unread = b''
        self._error = None
        self._closed = False
        try:
            self._import(host, busid)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for name, value in (('TCP_KEEPIDLE', KEEPALIVE), ('TCP_KEEPINTVL', 10), ('TCP_KEEPCNT', 3),
                                ('TCP_USER_TIMEOUT', USER_TIMEOUT * 1000)):
                if hasattr(socket, name):
                    self._socket.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
            threading.Thread(target=self._take_answers, name=f'usbip-{host}', daemon=True).start()
            self._set_up()
        except BaseException:
            self.close()
            raise

    def _import(self, host: str, busid: str):
        self._socket.sendall(OP.pack(VERSION, OP_REQ_IMPORT, 0) + busid.encode('ascii').ljust(32, b'\0')[:32])
        _, code, status = OP.unpack(_receive(self._socket, OP.size))
        if code != OP_REP_IMPORT:
            raise OSError(errno.EPROTO, f'{host} answered the import with {code:#x}')
        if status in (ST_NA, ST_DEV_BUSY):
            raise OSError(errno.EBUSY, f'The printer at {busid} of {host} is used by another client')
        if status:
            raise OSError(errno.ENODEV, f'{host} shares no device at {busid}')
        record = DEVICE.unpack(_receive(self._socket, DEVICE.size))
        self._device = record[2] << 16 | record[3]
        self._configuration = record[11]

    def _set_up(self):
        """Find the printer interface, set it up where the host has not, and
        read its IEEE 1284 device ID, which names the model."""
        head = self._control(0x80, 6, 0x0200, 0, 9)
        if len(head) < 9:
            raise OSError(errno.EPROTO, 'The printer sent no configuration')
        descriptor = self._control(0x80, 6, 0x0200, 0, struct.unpack_from('<H', head, 2)[0])
        number, alt, self._out, self._in = _printer_interface(descriptor)
        if not self._configuration:
            self._control(0x00, 9, descriptor[5], 0, 0)
        if alt:
            self._control(0x01, 11, alt, number, 0)
        answer = self._control(0xA1, GET_DEVICE_ID, 0, number << 8 | alt, 1023)
        length = int.from_bytes(answer[:2], 'big')
        self.device_id = answer[2:max(2, min(length, len(answer)))].decode('ascii', 'replace')

    @property
    def broken(self) -> bool:
        return self._error is not None or self._closed

    def _submit(self, endpoint: int, direction: int, length: int, data: bytes = b'', setup: bytes = bytes(8)) -> int:
        with self._lock:
            self._check()
            self._seqnum += 1
            seqnum = self._seqnum
            self._pending[seqnum] = (direction, length)
        command = SUBMIT.pack(URB_DIR_IN if direction == IN else 0, length, 0, 0, 0, setup)
        try:
            with self._send_lock:
                self._socket.sendall(HEADER.pack(CMD_SUBMIT, seqnum, self._device, direction, endpoint, command) + data)
        except OSError as e:
            self._fail(e)
            raise
        return seqnum

    def _check(self):
        # A new error each time, a shared one would collect the tracebacks
        # of every thread that raised it
        if self._error is not None:
            raise OSError(self._error.errno, self._error.strerror)
        if self._closed:
            raise OSError(errno.ENODEV, 'The connection to the printer is closed')

    def _wait(self, seqnum: int, timeout: float) -> tuple[int, bytes] | None:
        """Status and data of a transfer, None if it took longer than timeout."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while seqnum not in self._answers:
                self._check()
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self._lock.wait(left)
            return self._answers.pop(seqnum)

    def _take_answers(self):
        try:
            while True:
                command, seqnum, _, _, _, rest = HEADER.unpack(_receive(self._socket, HEADER.size, lambda: self._closed))
                status, length = struct.unpack_from('>ii', rest)
                if command not in (RET_SUBMIT, RET_UNLINK):
                    raise OSError(errno.EPROTO, f'Unknown answer {command:#x} of the host')
                with self._lock:
                    direction, asked = self._pending.get(seqnum, (None, 0))
                data = b''
                if command == RET_SUBMIT and direction == IN and length > 0:
                    if length > asked:
                        raise OSError(errno.EPROTO, f'The host sent {length} bytes for {asked}')
                    data = _receive(self._socket, length, lambda: self._closed)
                with self._lock:
                    self._pending.pop(seqnum, None)
                    self._answers[seqnum] = (status, data)
                    self._lock.notify_all()
        except OSError as e:
            self._fail(e)

    def _fail(self, error: OSError):
        if not error.errno:
            # e.g., a socket timeout
            error = OSError(errno.ETIMEDOUT if isinstance(error, TimeoutError) else errno.ENODEV,
                            str(error) or 'The connection to the printer failed')
        with self._lock:
            if self._error is None and not self._closed:
                self._error = error
                logger.info('The connection to the shared printer failed: %s', error)
            self._lock.notify_all()
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _control(self, request_type: int, request: int, value: int, index: int, length: int) -> bytes:
        setup = struct.pack('<BBHHH', request_type, request, value, index, length)
        answer = self._wait(self._submit(0, IN if request_type & 0x80 else OUT, length, setup=setup), CONTROL_TIMEOUT)
        if answer is None:
            self._fail(TimeoutError(errno.ETIMEDOUT, 'The printer did not answer a control request'))
            self._check()
        status, data = answer
        if status:
            raise OSError(errno.EIO, f'The printer refused control request {request} ({status})')
        return data

    def read(self, length: int, timeout: float) -> bytes:
        """Up to length bytes the printer sent, b'' if nothing came within
        timeout seconds. As with usblp, one read stays submitted until the
        printer answers it, also between reads: a canceled read loses its
        answer with some hosts."""
        if self._in is None:
            return b''
        with self._read_lock:
            if not self._unread:
                if self._reading is None:
                    self._reading = self._submit(self._in, IN, READ_SIZE)
                answer = self._wait(self._reading, timeout)
                if answer is None:
                    return b''
                self._reading = None
                status, self._unread = answer
                if status:
                    self._unread = b''
                    raise self._transfer_error(status, 'Reading from the printer')
            data, self._unread = self._unread[:length], self._unread[length:]
            return data

    def write(self, data: bytes, timeout: float):
        """Write data, raising TimeoutError if the printer did not take it
        within timeout seconds."""
        deadline = time.monotonic() + timeout
        with self._write_lock:
            for start in range(0, len(data), WRITE_SIZE):
                piece = data[start:start + WRITE_SIZE]
                answer = self._wait(self._submit(self._out, OUT, len(piece), piece), max(0.0, deadline - time.monotonic()))
                if answer is None:
                    # The host still has the data, the connection cannot be used further
                    self._fail(TimeoutError(errno.ETIMEDOUT, 'The printer did not take the data in time'))
                    raise TimeoutError('The printer did not take the data in time')
                if answer[0]:
                    raise self._transfer_error(answer[0], 'Writing to the printer')

    def _transfer_error(self, status: int, what: str) -> OSError:
        if status in GONE:
            # The host closes the connection, the next one imports the printer again
            error = OSError(errno.ENODEV, f'{what} failed, the printer is gone ({status})')
            self._fail(error)
            return error
        return OSError(errno.EIO, f'{what} failed ({status})')

    def close(self):
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        try:
            self._socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._socket.close()


# The imported printers, by key(). A printer stays imported while the label
# designer runs: the host resets it when the connection closes (Linux's
# usbip-host does), and another client could take it meanwhile.
_printers: dict[str, SharedPrinter] = {}
_locks: dict[str, threading.RLock] = {}
_locks_lock = threading.Lock()
# Until when a printer is not asked for its device ID, by key()
_unreachable: dict[str, float] = {}
# The device ID of each printer at its last import, by key(): one that
# cannot be reached is still the model it was
_device_ids: dict[str, str] = {}


def _lock(name: str) -> threading.RLock:
    with _locks_lock:
        return _locks.setdefault(name, threading.RLock())


def connect(address: str) -> SharedPrinter:
    """The printer at address, imported anew if it is not yet or no longer."""
    name = key(address)
    with _lock(name):
        printer = _printers.get(name)
        if printer is None or printer.broken:
            if printer is not None:
                printer.close()
            printer = _printers[name] = SharedPrinter(address)
            _device_ids[name] = printer.device_id
        return printer


def release(printer: SharedPrinter):
    """Close the connection if it failed, the next connect() imports the
    printer again."""
    if printer.broken:
        printer.close()


def unreachable(address: str) -> bool:
    """Whether the printer could not be reached a moment ago and is not
    imported meanwhile, see UNREACHABLE_FOR."""
    name = key(address)
    printer = _printers.get(name)
    return (printer is None or printer.broken) and time.monotonic() < _unreachable.get(name, 0)


def device_id(address: str) -> str:
    """The IEEE 1284 device ID of the printer, while it cannot be reached the
    one it told at its last import, empty if it was never reached."""
    name = key(address)
    # Held while it is tried, so that others wait for the answer instead of trying too
    with _lock(name):
        if unreachable(address):
            return _device_ids.get(name, '')
        try:
            return connect(address).device_id
        except (OSError, ValueError) as e:
            logger.debug('No device ID of %s: %s', address, e)
            _unreachable[name] = time.monotonic() + UNREACHABLE_FOR
            return _device_ids.get(name, '')


class Socket:
    """A shared printer with the interface of a socket, for drivers that talk
    to printers on the network and on USB alike."""

    def __init__(self, address: str, timeout: float):
        self._printer, self._timeout = connect(address), timeout

    def settimeout(self, timeout: float):
        self._timeout = timeout

    def recv(self, size: int) -> bytes:
        # A read may end with nothing, e.g., on a zero-length packet
        deadline = time.monotonic() + self._timeout
        while True:
            data = self._printer.read(size, max(0.0, deadline - time.monotonic()))
            if data:
                return data
            if time.monotonic() >= deadline:
                raise TimeoutError('The printer did not answer in time')

    def sendall(self, data: bytes):
        self._printer.write(data, self._timeout)

    def close(self):
        release(self._printer)
