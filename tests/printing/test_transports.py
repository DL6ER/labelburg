"""The connections several drivers share: a serial port against a pseudo
terminal, the serial interface of a USB printer against a fake PyUSB, a
printer on classic Bluetooth over a socket pair, Bluetooth LE against a fake
printer, and the search on classic Bluetooth."""
import errno
import os
import socket
import struct
import sys
import termios
import threading
import time
import types
from dataclasses import replace

import pytest

from app.labeldesigner import bluetooth, drivers, printer, refusals
from app.labeldesigner.drivers import backends, devices, niimbot, transports
from app.labeldesigner.drivers.base import Driver
from app.labeldesigner.drivers.brother import models as brother_models, usb as brother_usb
from tests.helpers import EXAMPLE_FORMDATA, make_client, outcome
from tests.printing.test_bluetooth import Management, event, found
from tests.printing.test_niimbot import FakePrinter
from tests.printing.test_niimbot_ble import NIIMBOT, LePrinter
from tests.printing.test_printer import _status_packet
from tests.printing.test_usb_backends import Endpoint, USBError, USBTimeoutError

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'
STATUS = '/labeldesigner/api/printer_status'


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


def test_serial_port_set_up_otherwise_before_is_set_back_to_8n1(pty):
    # e.g. by another program: two stop bits and flow control
    device, _ = pty
    fd = os.open(device.removeprefix('serial://'), os.O_RDWR | os.O_NOCTTY)
    try:
        mode = termios.tcgetattr(fd)
        mode[0] |= termios.IXOFF | termios.IXANY
        mode[2] |= termios.CSTOPB | termios.CRTSCTS
        termios.tcsetattr(fd, termios.TCSANOW, mode)
        before = termios.tcgetattr(fd)
        assert before[0] & termios.IXOFF and before[0] & termios.IXANY and before[2] & termios.CSTOPB and before[2] & termios.CRTSCTS
    finally:
        os.close(fd)
    link = transports.SerialLink(device)
    try:
        iflag, _, cflag = termios.tcgetattr(link.fd)[:3]
        assert not cflag & (termios.CSTOPB | termios.CRTSCTS) and not iflag & (termios.IXOFF | termios.IXANY)
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
    """A printer that is a serial port on USB: a communications interface and
    a data interface with its endpoints, numbered 0 and 1 unless numbers says
    otherwise. Drivers of the kernel hold the interfaces of bound."""

    def __init__(self, product, reads=(), vendor=0x3513, refuses_setup=False, numbers=(0, 1), bound=(0, 1)):
        self.idVendor, self.idProduct, self.bDeviceClass, self.iSerialNumber = vendor, product, 0xef, 3
        self.control = types.SimpleNamespace(bInterfaceClass=0x02, bInterfaceNumber=numbers[0], endpoints=[Endpoint(0x83)])
        self.data = types.SimpleNamespace(bInterfaceClass=0x0a, bInterfaceNumber=numbers[1],
                                          endpoints=[Endpoint(0x81, reads), Endpoint(0x02)])
        self.refuses_setup, self.calls, self.requests, self.bound = refuses_setup, [], [], set(bound)

    def __iter__(self):
        return iter([self])

    def interfaces(self):
        return [self.control, self.data]

    def is_kernel_driver_active(self, interface):
        return interface in self.bound

    def detach_kernel_driver(self, interface):
        self.bound.discard(interface)
        self.calls.append(('detach', interface))

    def attach_kernel_driver(self, interface):
        self.bound.add(interface)
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
    # The kernel's serial driver holds both interfaces
    assert device.calls == [('detach', 0), ('detach', 1), 'configure', 'dispose', ('attach', 0), ('attach', 1)]


def test_serial_port_on_usb_next_to_other_interfaces(pyusb, monkeypatch):
    # Interface 0 of another function, e.g. a keyboard, is left to its driver
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    device = SerialDevice(0x0001, numbers=(1, 2), bound=(0, 1, 2))
    pyusb.append(device)
    printer_ = backends.open_printer('usb://0x3513:0x0001')
    assert device.requests[0][3] == 1
    printer_.dispose()
    assert device.calls == [('detach', 1), ('detach', 2), 'configure', 'dispose', ('attach', 1), ('attach', 2)]
    assert device.bound == {0, 1, 2}


def test_serial_port_on_usb_that_refuses_its_setup_is_used_as_it_is(pyusb, monkeypatch):
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    pyusb.append(SerialDevice(0x0001, reads=[b'ok'], refuses_setup=True))
    printer_ = backends.open_printer('usb://0x3513:0x0001')
    assert printer_.read() == b'ok'
    printer_.dispose()


def test_serial_port_on_usb_gives_back_the_interfaces_the_kernel_did_not_take_yet(pyusb, monkeypatch):
    # cdc_acm may take one back together with the other
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    device = SerialDevice(0x0001)
    attach = device.attach_kernel_driver

    def taken(interface):
        if interface == 0:
            raise USBError('Resource busy')
        attach(interface)
    device.attach_kernel_driver = taken
    pyusb.append(device)
    backends.open_printer('usb://0x3513:0x0001').dispose()
    assert device.calls[-1] == ('attach', 1)


class Unconfigured(SerialDevice):
    """A serial port on USB no configuration is set on yet, so no driver of
    the kernel holds it and its interfaces cannot be asked."""

    configured = False

    def is_kernel_driver_active(self, interface):
        if not self.configured:
            raise USBError('Entity not found')
        return super().is_kernel_driver_active(interface)

    def set_configuration(self):
        self.configured = True
        super().set_configuration()

    def get_active_configuration(self):
        if not self.configured:
            raise USBError('Entity not found')
        return self


def test_serial_port_on_usb_not_configured_yet_is_set_up(pyusb, monkeypatch):
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    device = Unconfigured(0x0001, reads=[b'ok'], bound=())
    pyusb.append(device)
    printer_ = backends.open_printer('usb://0x3513:0x0001')
    assert printer_.read() == b'ok'
    printer_.dispose()
    assert device.calls == ['configure', 'dispose']


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
    started = time.monotonic()
    assert printer_.read(32) == b''
    assert time.monotonic() - started < 0.5
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


class Off:
    """A printer on classic Bluetooth that is switched off, in place of the
    RFCOMM socket: paging it fails, with error from the kernel."""

    def __init__(self):
        self.tries, self.closed, self.error = 0, 0, errno.EHOSTDOWN

    def __call__(self, *args):
        return self

    def settimeout(self, timeout):
        pass

    def connect(self, address):
        self.tries += 1
        raise OSError(self.error, os.strerror(self.error))

    def close(self):
        self.closed += 1


@pytest.fixture
def off(monkeypatch):
    printer_ = Off()
    monkeypatch.setattr(transports.socket, 'socket', printer_)
    monkeypatch.setattr(devices, '_unanswered', {})
    return printer_


def test_printer_on_classic_bluetooth_that_is_off(off):
    with pytest.raises(OSError) as raised:
        backends.open_printer(DEVICE)
    assert raised.value.errno == errno.EHOSTDOWN and off.closed == 1
    assert not isinstance(raised.value, backends.NotPaired)
    assert devices.unanswered(raised.value) == 'The printer cannot be reached'


@pytest.mark.parametrize('error', [errno.EACCES, errno.EBADE])
def test_printer_on_classic_bluetooth_that_is_not_paired(off, error):
    # The kernel's errors of Authentication Failure and Pairing Not Allowed,
    # and of PIN or Key Missing
    off.error = error
    with pytest.raises(backends.NotPaired) as raised:
        backends.open_printer(DEVICE)
    assert raised.value.errno == error and off.closed == 1
    assert devices.unanswered(raised.value) == 'Pair the printer with this computer first'
    assert devices.not_sent(raised.value) in refusals.MESSAGES
    assert devices.not_sent(raised.value) == 'Could not send the label to the printer: Pair the printer with this computer first'


def test_printer_on_bluetooth_of_unknown_model_is_not_asked(tmp_path, off):
    # No driver of a printer on Bluetooth would be asked, Brother's commands
    # would go to whatever printer it is
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, printer=DEVICE, model='Unknown'))
    assert (response.status_code, response.get_json()['message']) == (400, refusals.UNKNOWN_MODEL)
    assert off.tries == 0


@pytest.mark.parametrize('device', ['usbip://192.0.2.7/1-1', '/dev/usb/lp_brother'])
def test_printer_of_unknown_model_elsewhere_is_asked_for_its_model(tmp_path, monkeypatch, device):
    # On USB/IP or a port named by udev, the fallback driver may well be the
    # one of the printer: it is asked as before
    asked = []
    # Its device ID names no model, e.g. it did not answer
    monkeypatch.setattr(devices, 'usb_device_id', lambda device_: '')
    driver = drivers.resolve(device, 'Unknown')[0]

    def status(self, device_, model):
        asked.append(device_)
        raise OSError(errno.EHOSTUNREACH, 'No route to host')
    monkeypatch.setattr(type(driver), 'status', status)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = device
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, printer=device, model='Unknown'))
    assert response.get_json()['message'] != refusals.UNKNOWN_MODEL
    assert asked == [device]


@pytest.fixture
def brother_bluetooth(monkeypatch):
    """The QL-820NWB as a model with Bluetooth, which no row tells yet."""
    monkeypatch.setitem(brother_models.MODELS, 'QL-820NWB', replace(brother_models.MODELS['QL-820NWB'], bluetooth=True))


def test_brother_model_without_bluetooth_is_not_reached_on_bluetooth(tmp_path, off):
    # Told at once as before Bluetooth was taken, e.g. with the default model
    client = make_client(tmp_path, printer=DEVICE)
    for _ in range(2):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer is switched off or not connected']
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size='62'))
    assert outcome(client, response) == ('failed', 'Could not send the label to the printer: The printer is switched off or not connected')
    assert off.tries == 0


def test_brother_model_with_bluetooth_is_reached_over_rfcomm(tmp_path, rfcomm, brother_bluetooth):
    peer, theirs = rfcomm
    told = bytearray(_status_packet(0x00))
    told[3], told[4] = 0x34, 0x41

    def answer():
        theirs.settimeout(5)
        if theirs.recv(64) == b'\x1b\x69\x53':
            theirs.sendall(bytes(told))
    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    client = make_client(tmp_path, model='QL-820NWB', printer=DEVICE)
    state = client.get(STATUS).get_json()['printers'][0]
    thread.join(5)
    assert (state['errors'], state['model']) == ([], 'QL-820NWB') and peer.addresses == [(MAC, 1)]


def test_brother_printer_on_bluetooth_that_is_off_is_not_paged_again(tmp_path, off, brother_bluetooth):
    # Paging it takes the radio for seconds, as for the other printers on Bluetooth
    client = make_client(tmp_path, model='QL-820NWB', printer=DEVICE)
    for _ in range(2):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert off.tries == 1
    client.get(STATUS + '?look=1')
    assert off.tries == 2


def test_brother_printer_on_bluetooth_is_asked_again_once_it_printed(tmp_path, off, brother_bluetooth, monkeypatch):
    client = make_client(tmp_path, model='QL-820NWB', printer=DEVICE)
    client.get(STATUS)
    monkeypatch.setattr(brother_usb, 'send_job', lambda data, device, lengths: {'did_print': True, 'ready_for_next_job': True})
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size='62'))
    assert outcome(client, response) == ('printed', '')
    client.get(STATUS)
    assert off.tries == 2


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


# A class of device as a printer tells it: imaging, printer
PRINTER_CLASS = 0x040680


def class_of_device(value):
    return bytes([4, 0x0d]) + value.to_bytes(3, 'little')


@pytest.fixture
def management(monkeypatch):
    def install(*args, **kwargs):
        fake = Management(*args, **kwargs)
        monkeypatch.setattr(bluetooth.socket, 'socket', fake)
        monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: (r if fake.incoming else [], [], []))
        return fake
    return install


def test_classic_search_finds_devices_with_their_class(management):
    classic, le_only, both = '00:00:5E:00:53:0A', '00:00:5E:00:53:0B', '00:00:5E:00:53:0C'
    fake = management([found(classic, kind=0, name='PT-P710BT0001', raw=class_of_device(PRINTER_CLASS)),
                       found(le_only, name='B1-TEST0001'),
                       found(both, kind=0, raw=class_of_device(0x5a020c)), found(both, kind=1, name='Phone')])
    devices_found = {device['address']: device for device in bluetooth.discover(1, classic=True)}
    assert devices_found[classic] == {'address': classic, 'name': 'PT-P710BT0001', 'le': False, 'services': [],
                                      'classic': True, 'device_class': PRINTER_CLASS}
    assert devices_found[le_only] == {'address': le_only, 'name': 'B1-TEST0001', 'le': True, 'services': [],
                                      'classic': False, 'device_class': None}
    assert devices_found[both] == {'address': both, 'name': 'Phone', 'le': True, 'services': [],
                                   'classic': True, 'device_class': 0x5a020c}
    # Classic Bluetooth and LE, and stopped again
    assert [(op, params) for op, _, params in fake.sent] == [(0x0003, b''), (0x0004, b''), (0x0023, b'\x07'), (0x0024, b'\x07')]


class LeOnly(Management):
    """An adapter without classic Bluetooth, which refuses to search it."""

    def send(self, data):
        op, index, _ = struct.unpack_from('<HHH', data)
        if op == 0x0023 and data[6] != 0x06:
            self.sent.append((op, index, data[6:]))
            self.incoming.append(event(0x0002, index, struct.pack('<HB', op, 0x0c)))
            return len(data)
        return super().send(data)


def test_search_on_an_adapter_without_classic_bluetooth_still_finds_le(monkeypatch):
    fake = LeOnly([found(MAC, name='B1-TEST0001')])
    waits = []
    monkeypatch.setattr(bluetooth.socket, 'socket', fake)
    monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: waits.append(t) or (r if fake.incoming else [], [], []))
    assert [(d['address'], d['name']) for d in bluetooth.discover(5, classic=True)] == [(MAC, 'B1-TEST0001')]
    assert [(op, params) for op, _, params in fake.sent] == [(0x0003, b''), (0x0004, b''), (0x0023, b'\x07'), (0x0023, b'\x06'), (0x0024, b'\x06')]
    # As long as a search of LE alone
    assert waits[-1] <= 5


class OneLeOnly(Management):
    """Two adapters, of which the first listed has no classic Bluetooth."""

    def send(self, data):
        op, index, _ = struct.unpack_from('<HHH', data)
        if op == 0x0023 and index == 1 and data[6] != 0x06:
            self.sent.append((op, index, data[6:]))
            self.incoming.append(event(0x0002, index, struct.pack('<HB', op, 0x0c)))
            return len(data)
        return super().send(data)


def test_classic_search_goes_on_on_the_adapter_that_has_it(monkeypatch):
    classic = '00:00:5E:00:53:0A'
    fake = OneLeOnly([found(MAC, name='B1-TEST0001', index=1), found(classic, kind=0, name='PT-P710BT0001', index=0)],
                     controllers=(1, 0))
    waits = []
    monkeypatch.setattr(bluetooth.socket, 'socket', fake)
    monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: waits.append(t) or (r if fake.incoming else [], [], []))
    assert {d['address'] for d in bluetooth.discover(5, classic=True)} == {MAC, classic}
    started = [(index, params) for op, index, params in fake.sent if op == 0x0023]
    assert sorted(started) == [(0, b'\x07'), (1, b'\x06'), (1, b'\x07')]
    assert sorted((index, params) for op, index, params in fake.sent if op == 0x0024) == [(0, b'\x07'), (1, b'\x06')]
    # As long as the kernel searches both on the other one
    assert min(waits) > 5


@pytest.mark.parametrize('classic, longest', [(True, 11), (False, 5)])
def test_classic_search_lasts_until_the_kernel_searched_both(monkeypatch, classic, longest):
    # LE for 5.12 s, then classic Bluetooth for as long
    fake = Management([found(MAC, name='B1-TEST0001')])
    waits = []
    monkeypatch.setattr(bluetooth.socket, 'socket', fake)
    monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: waits.append(t) or (r if fake.incoming else [], [], []))
    bluetooth.discover(5, classic=classic)
    assert longest - 1 < waits[0] <= longest


class ClassicDriver(Driver):
    models = ('Maker/BT1',)
    bluetooth_kind = 'classic'
    bluetooth_names = {r'BT1-\d+': 'Maker/BT1'}


@pytest.mark.parametrize('device, printer_', [
    ({'address': MAC, 'name': 'BT1-0001', 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS},
     (DEVICE, 'Maker/BT1')),
    # Found by its name on the way the driver reaches it only
    ({'address': MAC, 'name': 'BT1-0001', 'le': True, 'services': [], 'classic': False, 'device_class': None}, None),
    ({'address': MAC, 'name': 'BT1-0001', 'le': True, 'services': []}, None),
    ({'address': MAC, 'name': 'BT2-0001', 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}, None),
])
def test_printers_on_classic_bluetooth_are_known_by_their_names(device, printer_):
    assert ClassicDriver().found_on_bluetooth(device) == printer_


def test_printers_on_bluetooth_le_are_known_by_their_services():
    driver = Driver()
    driver.bluetooth_services = {NIIMBOT: 'Maker/LE1'}
    assert driver.found_on_bluetooth({'address': MAC, 'name': 'LE1', 'le': True, 'services': [NIIMBOT]}) == (DEVICE, 'Maker/LE1')
    assert driver.found_on_bluetooth({'address': MAC, 'name': 'LE1', 'le': True, 'services': []}) is None


@pytest.fixture
def searches(tmp_path, monkeypatch):
    """The automatic detection with the kind of each Bluetooth search."""
    kinds = []

    def discover(timeout, classic=False):
        kinds.append(classic)
        return []
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', True)
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    monkeypatch.setattr(printer.bluetooth, 'discover', discover)
    monkeypatch.setattr(printer, '_bluetooth', {})
    monkeypatch.setattr(printer, '_bluetooth_searched', None)
    monkeypatch.setattr(printer, '_bluetooth_thread', None)
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'looked', 0.0)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'

    def search(look=False):
        client.get('/labeldesigner/api/printer_status' + ('?look=1' if look else ''))
        printer._bluetooth_thread.join(5)
        return kinds[-1]
    return search, kinds


def test_classic_bluetooth_is_not_searched_while_no_driver_names_printers_there(searches, monkeypatch):
    monkeypatch.setattr(drivers.BROTHER, 'bluetooth_names', {})
    search, kinds = searches
    assert search() is False
    assert search(look=True) is False and kinds == [False, False]


def test_classic_bluetooth_is_searched_only_when_looking_for_printers(searches):
    # Brother names its P-touch printers on Bluetooth there
    search, kinds = searches
    # Not at the first detection, which runs without being asked for
    assert search() is False
    assert search(look=True) is True and kinds == [False, True]


class KeptLink:
    """What release_le() takes of a link it keeps."""

    def __init__(self):
        self.busy, self.used, self.broken, self.timer, self.closed = True, 0.0, False, None, 0

    def close(self):
        self.closed += 1


def test_a_kept_bluetooth_link_is_closed_after_each_use_where_the_printer_saves_energy(monkeypatch):
    saving, other = 'bt://00:00:5E:00:53:01', 'bt://00:00:5E:00:53:02'
    monkeypatch.setattr(devices, 'saving_energy', frozenset({devices.lock_key(saving)}))
    monkeypatch.setattr(transports, '_kept', {})
    links = {device: KeptLink() for device in (saving, other)}
    for device, link in links.items():
        transports._kept[devices.lock_key(device)] = link
        transports.release_le(device, link)
    # The other one is kept until it was unused for KEEP_LE seconds
    links[other].timer.cancel()
    assert (links[saving].closed, links[other].closed) == (1, 0)
    assert list(transports._kept) == [devices.lock_key(other)]
