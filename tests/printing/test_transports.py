"""The connections several drivers share: a serial port against a pseudo
terminal, the serial interface of a USB printer against a fake PyUSB, a
printer on classic Bluetooth over a socket pair and Bluetooth LE against a
fake printer."""
import errno
import os
import socket
import struct
import sys
import termios
import time
import types

import pytest

from app.labeldesigner import drivers
from app.labeldesigner.drivers import backends, niimbot, transports
from tests.printing.test_niimbot import FakePrinter
from tests.printing.test_niimbot_ble import NIIMBOT, LePrinter
from tests.printing.test_usb_backends import Endpoint, USBError, USBTimeoutError

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'


@pytest.fixture
def pty():
    """A pseudo terminal: the serial port's end, by its path, and the printer's end."""
    printer_end, port = os.openpty()
    yield f'serial://{os.ttyname(port)}', printer_end
    os.close(printer_end)
    os.close(port)


def test_serial_port_is_raw_at_115200_baud_8n1(pty):
    device, _ = pty
    link = transports.SerialLink(device)
    try:
        iflag, oflag, cflag, lflag, ispeed, ospeed, cc = termios.tcgetattr(link.fd)
        assert (ispeed, ospeed) == (termios.B115200, termios.B115200)
        assert cflag & termios.CSIZE == termios.CS8 and not cflag & (termios.PARENB | termios.CSTOPB | termios.CRTSCTS)
        assert cflag & termios.CLOCAL and cflag & termios.CREAD
        assert not lflag & (termios.ICANON | termios.ECHO | termios.ISIG) and not oflag & termios.OPOST
        assert not iflag & (termios.IXON | termios.IXOFF | termios.ICRNL)
    finally:
        link.close()


def test_serial_port_sends_and_receives_bytes_as_they_are(pty):
    device, printer_end = pty
    link = transports.SerialLink(device)
    try:
        # Line ends and control characters stay what they are
        link.sendall(b'\x55\x55\r\n\x03\x11\xaa\xaa')
        assert os.read(printer_end, 64) == b'\x55\x55\r\n\x03\x11\xaa\xaa'
        os.write(printer_end, b'\x55\x55\x03\x11\x13\r\n')
        link.settimeout(1)
        assert link.recv(64) == b'\x55\x55\x03\x11\x13\r\n'
    finally:
        link.close()


def test_serial_port_that_does_not_answer_times_out(pty):
    device, _ = pty
    link = transports.SerialLink(device)
    try:
        link.settimeout(0.05)
        started = time.monotonic()
        with pytest.raises(socket.timeout):
            link.recv(64)
        assert time.monotonic() - started < 1
    finally:
        link.close()


def test_serial_port_turns_dtr_and_rts_on(pty, monkeypatch):
    import fcntl
    asked, ioctl = [], fcntl.ioctl

    def recorded(fd, request, arg=0):
        asked.append((request, arg))
        return ioctl(fd, request, arg)
    monkeypatch.setattr(fcntl, 'ioctl', recorded)
    # A pseudo terminal has no such lines, it is used all the same
    link = transports.SerialLink(pty[0])
    link.close()
    assert asked == [(termios.TIOCMBIS, struct.pack('i', termios.TIOCM_DTR | termios.TIOCM_RTS))]


def test_serial_port_that_is_not_there():
    with pytest.raises(FileNotFoundError):
        transports.SerialLink('serial:///dev/no-such-ttyACM')


class SerialDevice:
    """A printer that is a serial port on USB: a communications interface (0)
    and a data interface (1) with its endpoints."""

    def __init__(self, product, reads=(), vendor=0x3513, refuses_setup=False):
        self.idVendor, self.idProduct, self.bDeviceClass, self.iSerialNumber = vendor, product, 0xef, 3
        self.control = types.SimpleNamespace(bInterfaceClass=0x02, bInterfaceNumber=0, endpoints=[Endpoint(0x83)])
        self.data = types.SimpleNamespace(bInterfaceClass=0x0a, bInterfaceNumber=1,
                                          endpoints=[Endpoint(0x81, reads), Endpoint(0x02)])
        self.refuses_setup, self.calls, self.requests = refuses_setup, [], []

    def __iter__(self):
        return iter([self])

    def interfaces(self):
        return [self.control, self.data]

    def is_kernel_driver_active(self, interface):
        return interface == 0

    def detach_kernel_driver(self, interface):
        self.calls.append(('detach', interface))

    def attach_kernel_driver(self, interface):
        self.calls.append(('attach', interface))

    def set_configuration(self):
        self.calls.append('configure')

    def get_active_configuration(self):
        return self

    def ctrl_transfer(self, request_type, request, value, index, data, timeout):
        if self.refuses_setup:
            raise USBError('Pipe error')
        self.requests.append((request_type, request, value, index, data))


@pytest.fixture
def pyusb(monkeypatch):
    """A fake PyUSB with the devices of its list, found by the classes of
    their interfaces."""
    found_devices = []
    libusb = object()

    def find(find_all, custom_match, backend, idVendor=None):
        assert backend is libusb
        return [d for d in found_devices if idVendor in (None, d.idVendor) and custom_match(d)]
    usb = types.ModuleType('usb')
    usb.core = types.SimpleNamespace(find=find, USBError=USBError, USBTimeoutError=USBTimeoutError)

    def find_descriptor(parent, bInterfaceClass=None, custom_match=None):
        if bInterfaceClass is not None:
            return next((i for i in parent.interfaces() if i.bInterfaceClass == bInterfaceClass), None)
        return next((e for e in parent.endpoints if custom_match(e)), None)
    usb.util = types.SimpleNamespace(
        find_descriptor=find_descriptor, endpoint_direction=lambda address: address & 0x80,
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        dispose_resources=lambda device: device.calls.append('dispose'),
        get_string=lambda device, index: 'TEST0001')
    monkeypatch.setitem(sys.modules, 'usb', usb)
    monkeypatch.setitem(sys.modules, 'usb.core', usb.core)
    monkeypatch.setitem(sys.modules, 'usb.util', usb.util)
    monkeypatch.setattr(backends.UsbPrinter, 'read_timeout', 0.)
    monkeypatch.setattr(backends, '_libusb', lambda: libusb)
    return found_devices


def test_printer_a_driver_lists_is_reached_through_its_serial_interface(pyusb, monkeypatch):
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    device = SerialDevice(0x0001, reads=[None, b'\x55\x55'])
    pyusb.append(device)
    printer_ = backends.open_printer('usb://0x3513:0x0001')
    assert isinstance(printer_, backends.UsbSerialPrinter) and printer_.dev is device
    # Set to 115200 baud 8N1 with DTR and RTS on, as the kernel sets the port
    assert device.requests == [(0x21, 0x20, 0, 0, struct.pack('<IBBB', 115200, 0, 0, 8)), (0x21, 0x22, 0x03, 0, None)]
    printer_.write(b'job')
    assert device.data.endpoints[1].written == [(b'job', 15000)]
    # A read that times out is tried once more
    assert printer_.read() == b'\x55\x55'
    printer_.dispose()
    assert device.calls == [('detach', 0), 'configure', 'dispose', ('attach', 0)]


def test_serial_port_on_usb_that_refuses_its_setup_is_used_as_it_is(pyusb, monkeypatch):
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    pyusb.append(SerialDevice(0x0001, reads=[b'ok'], refuses_setup=True))
    printer_ = backends.open_printer('usb://0x3513:0x0001')
    assert printer_.read() == b'ok'
    printer_.dispose()


def test_serial_port_on_usb_no_driver_lists_is_no_printer(pyusb):
    # As before: only devices with a printer interface are printed on
    assert all(driver.usb_serial_ids == () for driver in drivers.DRIVERS)
    pyusb.append(SerialDevice(0x0001))
    with pytest.raises(FileNotFoundError):
        backends.open_printer('usb://0x3513:0x0001')


class Peer:
    """One end of a socket pair in place of an RFCOMM socket."""

    def __init__(self, end):
        self.end, self.made, self.addresses = end, [], []

    def __call__(self, family, kind, proto):
        self.made.append((family, kind, proto))
        return self

    def connect(self, address):
        self.addresses.append(address)

    def __getattr__(self, name):
        return getattr(self.end, name)


@pytest.fixture
def rfcomm(monkeypatch):
    """The printer's end of a socket pair, and the end of the label designer
    as the socket it connects."""
    ours, theirs = socket.socketpair()
    peer = Peer(ours)
    monkeypatch.setattr(transports.socket, 'socket', peer)
    yield peer, theirs
    ours.close()
    theirs.close()


def test_printer_on_classic_bluetooth_takes_a_stream_of_bytes(rfcomm):
    peer, theirs = rfcomm
    printer_ = backends.open_printer(DEVICE)
    assert isinstance(printer_, transports.RfcommPrinter)
    assert peer.made == [(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)]
    assert peer.addresses == [(MAC, 1)]
    printer_.write(b'\x1b@job')
    assert theirs.recv(64) == b'\x1b@job'
    # Nothing to read is told at once, an answer as it came
    assert printer_.read(32) == b''
    theirs.sendall(b'status')
    deadline = time.monotonic() + 2
    while not (answer := printer_.read(32)) and time.monotonic() < deadline:
        pass
    assert answer == b'status'
    printer_.dispose()
    theirs.settimeout(1)
    assert theirs.recv(64) == b''


def test_printer_on_classic_bluetooth_on_the_channel_it_names(rfcomm):
    peer, _ = rfcomm
    backends.open_printer(f'bt://{MAC.lower()}/3').dispose()
    assert peer.addresses == [(MAC, 3)]


def test_printer_on_classic_bluetooth_that_is_off(monkeypatch):
    closed = []

    class Off:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            raise OSError(errno.EHOSTDOWN, 'Host is down')

        def close(self):
            closed.append(True)
    monkeypatch.setattr(transports.socket, 'socket', Off)
    with pytest.raises(OSError) as raised:
        backends.open_printer(DEVICE)
    assert raised.value.errno == errno.EHOSTDOWN and closed == [True]


class Refused(Exception):
    pass


class Gone(Refused):
    pass


def le_link(device=DEVICE, characteristic=NIIMBOT, notify=None):
    return transports.LeLink(device, characteristic, notify, error=Refused, no_answer=Gone)


@pytest.fixture
def le(monkeypatch):
    peer = LePrinter(FakePrinter())
    monkeypatch.setattr(transports.socket, 'socket', peer)
    monkeypatch.setattr(transports, '_kept', {})
    return peer


def test_bluetooth_le_raises_the_errors_of_the_driver(le, monkeypatch):
    with pytest.raises(Refused, match='no characteristic'):
        le_link(characteristic=bytes(16))
    assert le.closed == 1
    monkeypatch.setattr(le, 'recv', lambda size: b'')
    with pytest.raises(Gone):
        le_link()
    assert le.closed == 2


class SplitPrinter:
    """A printer on Bluetooth LE that takes what is written to one
    characteristic and answers it, reversed, with notifications of another."""

    WRITE, NOTIFY = bytes(range(16)), bytes(range(16, 32))

    def __init__(self):
        self.declarations = [(0x0010, 0x04, 0x0011, self.WRITE), (0x0013, 0x10, 0x0014, self.NOTIFY)]
        self.incoming, self.written, self.notifying = [], [], set()

    def __call__(self, family, kind, proto):
        return self

    def bind(self, address):
        pass

    def connect(self, address):
        pass

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def send(self, pdu):
        op = pdu[0]
        if op == 0x02:
            self.incoming.append(bytes([0x03]) + struct.pack('<H', 100))
        elif op == 0x08:
            start = struct.unpack_from('<H', pdu, 1)[0]
            entries = [struct.pack('<HBH', h, p, v) + u for h, p, v, u in self.declarations if h >= start]
            self.incoming.append(bytes([0x09, 21]) + b''.join(entries) if entries else bytes([0x01, 0x08, 0, 0, 0x0a]))
        elif op == 0x04:
            start, end = struct.unpack_from('<HH', pdu, 1)
            told = [v + 1 for _, _, v, _ in self.declarations if start <= v + 1 <= end]
            self.incoming.append(bytes([0x05, 0x01]) + struct.pack('<HH', told[0], 0x2902) if told else bytes([0x01, 0x04, 0, 0, 0x0a]))
        elif op == 0x12:
            self.notifying.add(struct.unpack_from('<H', pdu, 1)[0] - 1)
            self.incoming.append(bytes([0x13]))
        elif op == 0x52:
            handle = struct.unpack_from('<H', pdu, 1)[0]
            self.written.append((handle, pdu[3:]))
            # Its notifications, the one of the written characteristic first
            self.incoming.append(bytes([0x1b]) + struct.pack('<H', 0x0011) + b'ignored')
            if 0x0014 in self.notifying:
                self.incoming.append(bytes([0x1b]) + struct.pack('<H', 0x0014) + pdu[3:][::-1])
        return len(pdu)

    def recv(self, size):
        if not self.incoming:
            raise socket.timeout('timed out')
        return self.incoming.pop(0)


def test_bluetooth_le_answers_on_another_characteristic(monkeypatch):
    peer = SplitPrinter()
    monkeypatch.setattr(transports.socket, 'socket', peer)
    link = le_link(characteristic=SplitPrinter.WRITE, notify=SplitPrinter.NOTIFY)
    assert peer.notifying == {0x0014}
    link.sendall(b'ping')
    assert peer.written == [(0x0011, b'ping')] and link.recv(64) == b'gnip'


def test_kept_bluetooth_le_link_is_taken_again(le):
    first = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    transports.release_le(DEVICE, first)
    # Also by another spelling of the device
    again = transports.kept_le(f'bt://{MAC.lower()}', NIIMBOT, error=Refused, no_answer=Gone)
    assert again is first and len(le.addresses) == 1
    transports.release_le(DEVICE, again)
    assert le.closed == 0
    again.timer.cancel()


def test_kept_bluetooth_le_link_is_closed_unused_only(le, monkeypatch):
    monkeypatch.setattr(transports, 'KEEP_LE', 0.1)
    link = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    transports.release_le(DEVICE, link)
    # Taken again before its time is up: kept while in use, its time
    # counted again once given back
    assert transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone) is link
    time.sleep(0.2)
    with transports._kept_lock:
        assert le.closed == 0 and link.timer is not None
    transports.release_le(DEVICE, link)
    deadline = time.monotonic() + 2
    while not le.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert le.closed == 1 and transports._kept == {}
    # Opened anew next time
    assert transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone) is not link
    assert len(le.addresses) == 2


def test_dropped_bluetooth_le_link_is_closed_when_given_back(le):
    link = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    le.dropped = True
    with pytest.raises(OSError):
        link.sendall(b'x')
    transports.release_le(DEVICE, link)
    assert le.closed == 1 and transports._kept == {} and link.timer is None
