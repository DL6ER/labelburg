"""Printers another device shares over USB/IP, against a fake host that
answers like Linux's usbip-host."""

import errno
import functools
import json
import socket
import threading
import time

import pytest

from app.labeldesigner import drivers, printer, printer_settings, routes
from app.labeldesigner.drivers import BROTHER, backends, devices, usbip
from app.labeldesigner.drivers.brother import usb
from app.labeldesigner.drivers.escpos import protocol as escpos
from app.labeldesigner.drivers.vc500w import DRIVER as VC500W
from app.labeldesigner.drivers.vc500w import protocol as vc500w
from tests.helpers import make_client, outcome, within
from tests.printing.test_vc500w import FakeVC500W
from tests.printing.test_usb_jobs import COMPLETED, PHASE_CHANGE, PRINTING, REPLY, WAITING, status
from tests.printing.usbip_host import QL1100_ID, VC500W_ID, FakeHost, SharedDevice, TricklingHost

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Shared', 'size': '40'}])


class FakeQL:
    """The printer behind a shared device: answers a status request, and a
    job, which ends with "print with feed", with the messages of a page."""

    def __init__(self, device):
        self.received = b''
        self._printer = device.printer
        threading.Thread(target=self._answer, daemon=True).start()

    def _answer(self):
        while True:
            try:
                data = self._printer.recv(65536)
                if not data:
                    return
                self.received += data
                if data.endswith(b'\x1biS'):
                    self._reply()
                elif data.endswith(b'\x1a'):
                    self._printer.sendall(status(PHASE_CHANGE, PRINTING) + status(COMPLETED) + status(PHASE_CHANGE, WAITING))
            except OSError:
                return

    def _reply(self):
        self._printer.sendall(status(REPLY))


class HoldingQL(FakeQL):
    """A FakeQL holding an error, as a QL-1100 does: it answers no status
    request until the error is cleared, then every one it was asked. With
    cleared_at, the error is cleared while that request waits."""

    def __init__(self, device, width):
        self.width, self.held, self.holding, self.cleared_at = width, 0, True, None
        self._lock = threading.Lock()
        super().__init__(device)

    def _reply(self):
        with self._lock:
            replies = 1
            if self.holding:
                self.held += 1
                if self.held != self.cleared_at:
                    return
                self.holding, replies = False, self.held
            self._printer.sendall(status(REPLY, width=self.width) * replies)

    def clear(self):
        with self._lock:
            self.holding = False
            self._printer.sendall(status(REPLY, width=self.width) * self.held)


class FakeTM:
    """An Epson receipt printer behind a shared device: answers the status
    requests with the bytes in 'replies' and its model name as a TM-T20II
    does, keeps the job."""

    def __init__(self, device):
        self.received = b''
        self.replies = {escpos.OFFLINE: bytes([0x12]), escpos.PAPER: bytes([0x12]), escpos.MODEL_NAME: b'\x5fTM-T20II\x00'}
        self._printer = device.printer
        threading.Thread(target=self._answer, daemon=True).start()

    def _answer(self):
        while True:
            try:
                data = self._printer.recv(65536)
                if not data:
                    return
                if data in self.replies:
                    self._printer.sendall(self.replies[data])
                else:
                    self.received += data
            except OSError:
                return

    def wait_for(self, end: bytes):
        deadline = time.monotonic() + 5
        while not self.received.endswith(end) and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.received


@pytest.fixture
def host():
    shared = FakeHost(SharedDevice())
    yield shared
    shared.close()


def test_status_of_a_shared_printer(host):
    ql = FakeQL(host.devices[0])
    state = usb.query_status(host.address())
    assert (state['model'], state['media_width'], state['errors']) == ('QL-800', 62, [])
    assert ql.received == b'\x1biS'
    # Asked again on the same connection: the host resets a printer it lets go
    usb.query_status(host.address())
    assert (host.devices[0].imports, host.devices[0].resets) == (1, 0)


def test_job_on_a_shared_printer(host):
    ql = FakeQL(host.devices[0])
    job = b'\x00' * 40000 + b'job\x1a'
    info = usb.send_job(job, host.address(), [30])
    assert info['did_print'] and info['ready_for_next_job']
    assert ql.received == job


def test_printer_found_among_the_shared_devices():
    keyboard, printer_ = SharedDevice('1-1', interfaces=((3, 1),)), SharedDevice('1-2')
    host = FakeHost(keyboard, printer_)
    try:
        FakeQL(printer_)
        usb.query_status(host.address())
        assert (keyboard.imports, printer_.imports) == (0, 1)
    finally:
        host.close()


def test_shared_devices_with_their_classes():
    host = FakeHost(SharedDevice('1-1'), SharedDevice('1-2', interfaces=((3, 1),), device_class=0xEF))
    try:
        assert [(device['busid'], device['device_class'], device['interface_classes'])
                for device in usbip.shared_devices('127.0.0.1', host.port)] == [('1-1', 0, [7]), ('1-2', 0xEF, [3])]
    finally:
        host.close()


def test_printer_named_by_its_bus_id():
    first, second = SharedDevice('1-1'), SharedDevice('1-2')
    host = FakeHost(first, second)
    try:
        FakeQL(second)
        usb.query_status(host.address('1-2'))
        assert (first.imports, second.imports) == (0, 1)
    finally:
        host.close()


@pytest.mark.parametrize('interfaces', [
    # The VC-500W and others also offer IPP over USB on a printer interface
    ((7, 4), (7, 2)),
    # The bidirectional interface tells the status
    ((7, 1), (7, 2)),
])
def test_bidirectional_printer_interface_is_taken(interfaces):
    device = SharedDevice(interfaces=interfaces)
    host = FakeHost(device)
    try:
        FakeQL(device)
        assert usb.query_status(host.address())['errors'] == []
        assert (0xA1, 0, 0, 1 << 8) in device.requests
    finally:
        host.close()


def test_printer_not_configured_by_the_host_is_configured():
    device = SharedDevice(configuration=0)
    host = FakeHost(device)
    try:
        FakeQL(device)
        usb.query_status(host.address())
        assert (0x00, 9, 1, 0) in device.requests
    finally:
        host.close()


def test_printer_linux_has_not_configured_is_found():
    # Linux lists a shared device without interfaces until a client
    # configures it
    keyboard, printer_ = SharedDevice('1-1', interfaces=((3, 1),)), SharedDevice('3-2', configuration=0)
    host = FakeHost(keyboard, printer_)
    try:
        FakeQL(printer_)
        assert usb.query_status(host.address())['errors'] == []
        assert (keyboard.imports, printer_.imports) == (0, 1)
    finally:
        host.close()


def test_device_id_names_the_model():
    vc, ql = SharedDevice('1-1', device_id=VC500W_ID), SharedDevice('1-2')
    host = FakeHost(vc, ql)
    try:
        assert devices.usb_device_id(host.address('1-1')) == VC500W_ID
        assert VC500W.model_of(host.address('1-1')) == 'VC-500W'
        assert devices.usb_device_id(host.address('1-2')) == QL1100_ID
        assert VC500W.model_of(host.address('1-2')) is None
        assert (BROTHER.model_of(host.address('1-1')), BROTHER.model_of(host.address('1-2'))) == (None, 'QL-1100')
    finally:
        host.close()


def test_printer_that_cannot_be_reached_has_no_device_id():
    with socket.create_server(('127.0.0.1', 0)) as closed:
        port = closed.getsockname()[1]
    assert devices.usb_device_id(f'usbip://127.0.0.1:{port}') == ''


def test_printer_that_cannot_be_reached_is_tried_once_for_its_model(tmp_path, monkeypatch):
    attempts = []

    def unreachable(host, port, timeout, deadline=None):
        attempts.append(host)
        raise OSError(errno.EHOSTUNREACH, 'No route to host')
    monkeypatch.setattr(usbip, '_connect', unreachable)
    client = make_client(tmp_path, empty_repo=True)
    client.application.config.update(PRINTER_PRINTER='usbip://192.0.2.1', PRINTER_MODEL='QL-800')
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]), 'label_size': '62'}
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert len(attempts) == 1
    # Printing tries it once more
    usbip._unreachable.clear()
    attempts.clear()
    assert outcome(client, client.post('/labeldesigner/api/print', data=data))[0] == 'failed'
    assert len(attempts) == 2
    # The model is asked again after a while
    usbip._unreachable[usbip.key('usbip://192.0.2.1')] -= usbip.UNREACHABLE_FOR
    attempts.clear()
    assert usbip.device_id('usbip://192.0.2.1') == '' and len(attempts) == 1


def test_printer_imported_meanwhile_is_asked_for_its_model():
    host = FakeHost(SharedDevice('1-2'))
    try:
        usbip._unreachable[usbip.key(host.address('1-2'))] = time.monotonic() + 60
        assert usbip.device_id(host.address('1-2')) == ''
        usbip.connect(host.address('1-2'))
        assert usbip.device_id(host.address('1-2')) == QL1100_ID
    finally:
        host.close()


def test_printer_switched_off_and_on_again(host):
    device = host.devices[0]
    FakeQL(device)
    usb.query_status(host.address())
    device.unplug()
    with pytest.raises(OSError) as error:
        usb.query_status(host.address())
    assert devices.unanswered_reason(error.value) == 'The printer is switched off or not connected'
    device.plug_in()
    FakeQL(device)
    assert usb.query_status(host.address())['errors'] == []
    assert device.imports == 2


def test_printer_used_by_another_client(host):
    FakeQL(host.devices[0])
    usb.query_status(host.address())
    with pytest.raises(OSError) as error:
        usbip.SharedPrinter(host.address('1-1'))
    assert devices.unanswered_reason(error.value) == 'The printer is in use by another program'


@pytest.mark.parametrize('shared, address', [((SharedDevice('1-1', interfaces=((3, 1),)),), ''), ((SharedDevice(),), '9-9')])
def test_no_printer_shared(shared, address):
    host = FakeHost(*shared)
    try:
        with pytest.raises(OSError) as error:
            usb.query_status(host.address(address))
        assert devices.unanswered_reason(error.value) == 'The printer is switched off or not connected'
    finally:
        host.close()


def test_printer_that_takes_no_data(host, monkeypatch):
    monkeypatch.setattr(backends.UsbipPrinter, 'write_timeout', 0.2)
    FakeQL(host.devices[0])
    usb.query_status(host.address())
    host.silent = True
    with pytest.raises(OSError) as error:
        usb.query_status(host.address())
    assert devices.unanswered_reason(error.value) == 'The printer does not answer'
    # The connection is given up, the next query imports the printer again
    host.silent = False
    # The host lets the printer go only once it noticed the closed connection
    deadline = time.monotonic() + 5
    while host.devices[0].used and time.monotonic() < deadline:
        time.sleep(0.01)
    assert usb.query_status(host.address())['errors'] == []
    assert host.devices[0].imports == 2


def test_answer_between_queries_is_kept(host):
    # One read stays submitted: what the printer sends meanwhile is not lost
    shared = usbip.connect(host.address())
    assert shared.read(32, 0.05) == b''
    host.devices[0].printer.sendall(b'unasked')
    assert shared.read(3, 1) == b'una'
    assert shared.read(32, 1) == b'sked'


@pytest.mark.parametrize('latency, read_latency, reply_size, after_import', [
    pytest.param(0, 0, None, False, id='fast'),
    # A slow link, a read ending with each reply as a printer's transfer does
    pytest.param(0.02, 0, 32, False, id='20 ms'),
    pytest.param(0.06, 0, 32, False, id='60 ms'),
    # Only reads slow, e.g. a host slow on its USB side
    pytest.param(0, 0.02, 32, False, id='reads 20 ms'),
    pytest.param(0, 0.06, 32, False, id='reads 60 ms'),
    # A link that slows down after the import, e.g. Wi-Fi saving power
    pytest.param(0.06, 0, 32, True, id='60 ms after the import'),
])
def test_status_query_reads_the_reply_to_its_own_request(host, monkeypatch, latency, read_latency, reply_size,
                                                         after_import):
    if not after_import:
        host.latency, host.read_latency = latency, read_latency
    host.reply_size = reply_size
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.1 + 5 * (latency + read_latency)))
    ql = HoldingQL(host.devices[0], width=62)
    with pytest.raises(TimeoutError):
        usb.query_status(host.address())
    host.latency, host.read_latency = latency, read_latency
    # Cleared at the printer, it answers the request and its retry, which
    # reach the server before the label designer asks again: that poll
    # takes them without asking
    ql.clear()
    shared = usbip.connect(host.address())
    deadline = time.monotonic() + 5
    while not shared._answers and time.monotonic() < deadline:
        time.sleep(0.01)
    asked = len(ql.received)
    assert usb.query_status(host.address())['errors'] == [] and len(ql.received) == asked
    # Then the roll is swapped, and swapped again
    for width in (29, 12, 38):
        ql.width = width
        assert usb.query_status(host.address())['media_width'] == width


@pytest.fixture
def lost_at_once(monkeypatch):
    """Each query asks again, as one does once the request before counts as
    lost."""
    monkeypatch.setattr(usb, 'ASK_AGAIN', 0)


@pytest.mark.parametrize('cleared_at', [5, 6], ids=['request', 'retry'])
def test_error_cleared_while_a_poll_waits(host, monkeypatch, lost_at_once, cleared_at):
    # Held through two requests and their retries, cleared while the third
    # request or its retry waits: the reply that comes first is the oldest
    host.latency, host.reply_size = 0.06, 32
    # Waits shortened to several times the delay of the host
    monkeypatch.setattr(usb, 'REPLY_WAIT', 0.25)
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.25))
    ql = HoldingQL(host.devices[0], width=62)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            usb.query_status(host.address())
    ql.cleared_at = cleared_at
    usb.query_status(host.address())
    for width in (29, 12, 38, 17):
        ql.width = width
        assert usb.query_status(host.address())['media_width'] == width


def test_replies_held_over_several_polls_are_waited_for(host, monkeypatch, lost_at_once):
    host.latency, host.reply_size = 0.06, 32
    monkeypatch.setattr(usb, 'REPLY_WAIT', 0.25)
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.25))
    ql = HoldingQL(host.devices[0], width=62)
    for _ in range(3):
        with pytest.raises(TimeoutError):
            usb.query_status(host.address())
    assert ql.held == 6
    ql.clear()
    ql.width = 29
    assert usb.query_status(host.address())['media_width'] == 29


def test_two_pollers_share_the_one_request(host, monkeypatch):
    # E.g. two tabs of the label designer, each asking every 2 s
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.1))
    ql = HoldingQL(host.devices[0], width=62)
    outcomes = []

    def poll():
        for _ in range(5):
            try:
                outcomes.append(usb.query_status(host.address()))
            except TimeoutError as e:
                outcomes.append(e)
    pollers = [threading.Thread(target=poll) for _ in range(2)]
    for poller in pollers:
        poller.start()
    for poller in pollers:
        poller.join(10)
    assert len(outcomes) == 10 and all(isinstance(outcome, TimeoutError) for outcome in outcomes)
    # The request and its retry
    assert ql.held == 2


def test_poll_while_a_request_is_outstanding_returns_at_once(host, monkeypatch):
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.5))
    ql = HoldingQL(host.devices[0], width=62)
    with pytest.raises(TimeoutError):
        usb.query_status(host.address())
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        usb.query_status(host.address())
    assert time.monotonic() - start < 0.5 and ql.held == 2


def test_printer_switched_off_with_a_request_outstanding(host, monkeypatch):
    # Switched on again, it holds no request: it is asked at once
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.1))
    device = host.devices[0]
    HoldingQL(device, width=62)
    with pytest.raises(TimeoutError):
        usb.query_status(host.address())
    device.unplug()
    with pytest.raises(OSError) as error:
        usb.query_status(host.address())
    assert devices.unanswered_reason(error.value) == 'The printer is switched off or not connected'
    device.plug_in()
    FakeQL(device)
    assert usb.query_status(host.address())['errors'] == []


def test_answer_longer_than_asked_ends_the_connection(host):
    shared = usbip.connect(host.address())
    host.extra = bytes(usbip.READ_SIZE)
    host.devices[0].printer.sendall(b'status')
    with pytest.raises(OSError):
        shared.read(32, 1)
    assert shared.broken


@pytest.mark.skipif(not hasattr(socket, 'TCP_USER_TIMEOUT'), reason='Linux only')
def test_host_that_acknowledges_nothing_is_given_up(host):
    shared = usbip.connect(host.address())
    assert shared._socket.getsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT) == usbip.USER_TIMEOUT * 1000


class Answers:
    """A connection that receives the bytes or raises the errors given."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def recv(self, size):
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_quiet_host_is_waited_for():
    assert usbip._receive(Answers(TimeoutError('timed out'), b'data'), 4, lambda: False) == b'data'


def test_connection_the_kernel_gave_up_ends():
    with pytest.raises(TimeoutError) as error:
        usbip._receive(Answers(TimeoutError(errno.ETIMEDOUT, 'Connection timed out'), b'data'), 4, lambda: False)
    assert error.value.errno == errno.ETIMEDOUT


@pytest.mark.parametrize('one, other, same', [
    ('USBIP://Printer-Pi', 'usbip://printer-pi:3240', True),
    ('usbip://printer-pi/1-1', 'usbip://printer-pi:3240/1-1', True),
    ('usbip://printer-pi/1-1', 'usbip://printer-pi/1-2', False),
    ('usbip://[2001:DB8::1]', 'usbip://[2001:db8::1]:3240', True),
    ('usbip://printer-pi', 'tcp://printer-pi', False),
])
def test_one_lock_for_each_shared_printer(one, other, same):
    assert (devices.lock_key(one) == devices.lock_key(other)) is same


def test_shared_printer_in_the_label_designer(host, tmp_path):
    ql = FakeQL(host.devices[0])
    client = make_client(tmp_path)
    client.application.config.update(PRINTER_PRINTER=host.address(), PRINTER_MODEL='QL-800')
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (shown['path'], shown['media_width'], shown['errors']) == (host.address(), 62, [])
    response = client.post('/labeldesigner/api/print', data={'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert ql.received.endswith(b'\x1a') and len(ql.received) > 1000


def test_shared_brother_printer_is_the_model_its_device_id_names(host, tmp_path):
    # Not the model PRINTER_MODEL names, also for requests that name none
    ql = FakeQL(host.devices[0])
    client = make_client(tmp_path)
    client.application.config.update(PRINTER_PRINTER=host.address(), PRINTER_MODEL='QL-800')
    with client.application.test_request_context():
        assert routes._printer(None, None) == (host.address(), 'QL-1100')
        assert routes._printer(None, 'QL-800') == (host.address(), 'QL-800')
    # Only the QL-1100 series takes it
    response = client.post('/labeldesigner/api/print', data={'label_size': '103', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert ql.received.endswith(b'\x1a')


def test_shared_printer_switched_off_is_the_model_its_device_id_named(host, tmp_path):
    device = host.devices[0]
    FakeQL(device)
    client = make_client(tmp_path)
    client.application.config.update(PRINTER_PRINTER=host.address(), PRINTER_MODEL='QL-800')
    data = {'label_size': '103', 'print_type': 'text', 'text': TEXT}
    assert outcome(client, client.post('/labeldesigner/api/print', data=data)) == ('printed', '')
    device.unplug()
    # Seen switched off, e.g. by the label designer, the host let it go
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert shown['errors'] == ['The printer is switched off or not connected']
    off, told = outcome(client, client.post('/labeldesigner/api/print', data=data))
    assert off == 'failed' and told.endswith('The printer is switched off or not connected')
    # Switched on again right away, the job is made for it
    device.plug_in()
    with client.application.test_request_context():
        assert routes._printer(None, None) == (host.address(), 'QL-1100')
    ql = FakeQL(device)
    assert outcome(client, client.post('/labeldesigner/api/print', data=data)) == ('printed', '')
    assert ql.received.endswith(b'\x1a')


def test_shared_printer_switched_off_keeps_its_settings_key(tmp_path):
    device = SharedDevice(device_id=QL1100_ID + 'SN:A1Z123456;')
    host = FakeHost(device)
    try:
        FakeQL(device)
        usb.query_status(host.address())
        device.unplug()
        with pytest.raises(OSError):
            usb.query_status(host.address())
        assert printer_settings.key(host.address()) == 'usb:QL-1100/A1Z123456'
    finally:
        host.close()


def test_shared_vc500w_is_known_by_its_device_id(tmp_path, monkeypatch):
    for name, value in (('POLL_INTERVAL', 0.01), ('DATA_DELAY', 0.05), ('FILL_WAIT', 0.05), ('START_TIMEOUT', 0.05)):
        monkeypatch.setattr(vc500w, name, value)
    vc = FakeVC500W(usbip=True)
    try:
        client = make_client(tmp_path)
        client.application.config.update(PRINTER_PRINTER=vc.target, PRINTER_MODEL='QL-800')

        def ql_commands(*args):
            raise AssertionError('A VC-500W must not get the commands of a QL printer')
        monkeypatch.setattr(usb, 'query_status', ql_commands)
        monkeypatch.setattr(usb, 'send_job', ql_commands)
        shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
        assert (shown['model'], shown['media_width']) == ('VC-500W', 25)
        response = client.post('/labeldesigner/api/print', data={'label_size': 'vc25', 'print_type': 'text', 'text': TEXT})
        assert outcome(client, response) == ('printed', '')
        assert len(vc.images) == 1
    finally:
        vc.close()


def test_shared_receipt_printer(tmp_path):
    device = SharedDevice(product=0x0E15, device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;')
    host = FakeHost(device)
    try:
        tm = FakeTM(device)
        client = make_client(tmp_path)
        client.application.config.update(PRINTER_PRINTER=host.address(), PRINTER_MODEL='Epson/ESC-POS')
        tm.replies[escpos.PAPER] = bytes([0x12 | 0x60])
        shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
        assert (shown['model'], shown['errors']) == ('Epson/ESC-POS', ['The printer has no paper'])
        tm.replies[escpos.PAPER] = bytes([0x12])
        response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
        assert outcome(client, response) == ('printed', '')
        assert tm.wait_for(escpos.CUT).startswith(escpos.INIT + b'\x1dv0')
        assert (device.imports, device.resets) == (1, 0)
    finally:
        host.close()


def test_shared_printer_that_cannot_be_reached(tmp_path):
    with socket.create_server(('127.0.0.1', 0)) as closed:
        address = f'usbip://127.0.0.1:{closed.getsockname()[1]}'
    client = make_client(tmp_path)
    client.application.config.update(PRINTER_PRINTER=address, PRINTER_MODEL='QL-800')
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (shown['path'], shown['errors']) == (address, ['The printer refuses the connection'])


def test_requests_name_only_configured_shared_printers(tmp_path):
    # The server connects only to hosts the configuration names
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'usbip://192.0.2.1', 'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert 'Unsupported printer' in response.get_json()['message']


@pytest.mark.parametrize('stage', ['devlist', 'import'])
def test_host_that_trickles_is_given_up(monkeypatch, stage):
    # Its device list and the import have one deadline together
    monkeypatch.setattr(usbip, 'HANDSHAKE_TIMEOUT', 1)
    host = TricklingHost(stage)
    try:
        started = time.monotonic()
        with pytest.raises(OSError):
            within(5, lambda: usbip.connect(host.address()))
        assert time.monotonic() - started < 2
        assert not usbip.connected(host.address())
    finally:
        host.close()


def test_devices_a_host_lists_are_few():
    host = FakeHost(*(SharedDevice(busid=f'1-{i}') for i in range(1, 41)))
    try:
        assert [d['busid'] for d in usbip.shared_devices('127.0.0.1', host.port)] == [f'1-{i}' for i in range(1, usbip.MAX_DEVICES + 1)]
    finally:
        host.close()


def test_device_id_is_not_waited_for_while_the_printer_is_imported(host):
    FakeQL(host.devices[0])
    told = usbip.connect(host.address()).device_id
    usbip.disconnect(host.address())
    taken, done = threading.Event(), threading.Event()

    def importing():
        with usbip._lock(usbip.key(host.address())):
            taken.set()
            done.wait(10)
    threading.Thread(target=importing, daemon=True).start()
    taken.wait()
    try:
        assert within(2, lambda: usbip.device_id(host.address())) == told
        assert within(2, lambda: devices.usb_device_id(host.address(), connect=False)) == told
        # Let go later, once the import is done
        within(2, lambda: usbip.disconnect(host.address()))
    finally:
        done.set()


def test_known_device_id_imports_nothing(host):
    assert devices.usb_device_id(host.address(), connect=False) == ''
    assert host.devices[0].imports == 0
    usbip.connect(host.address())
    assert devices.usb_device_id(host.address(), connect=False) == QL1100_ID


def test_model_waits_for_the_first_import_of_the_printer():
    # No device ID told yet: the model is not taken from the configuration
    # while another thread imports the printer over a slow link
    device = SharedDevice(product=0x0E15, device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;')
    host = FakeHost(device)
    taken = threading.Event()

    def first_import():
        with usbip._lock(usbip.key(host.address())):
            taken.set()
            time.sleep(3)
            usbip.connect(host.address())
    threading.Thread(target=first_import, daemon=True).start()
    taken.wait()
    try:
        assert within(8, lambda: drivers.resolve(host.address(), 'QL-800'))[1] == 'Epson/ESC-POS'
    finally:
        host.close()


def test_model_waits_for_one_import_at_most(monkeypatch):
    # Another thread holds the printer, e.g. in an import that fails slowly
    monkeypatch.setattr(usbip, 'HANDSHAKE_TIMEOUT', 1)
    monkeypatch.setattr(usbip, 'CONNECT_TIMEOUT', 0.25)
    address = 'usbip://127.0.0.1:3299'
    taken, done = threading.Event(), threading.Event()

    def importing():
        with usbip._lock(usbip.key(address)):
            taken.set()
            done.wait(20)
    threading.Thread(target=importing, daemon=True).start()
    taken.wait()
    try:
        started = time.monotonic()
        assert within(10, lambda: drivers.resolve(address, 'QL-800'))[1] == 'QL-800'
        assert time.monotonic() - started < 2.5
    finally:
        done.set()


def test_a_failed_import_is_not_tried_again_at_once(monkeypatch):
    with socket.create_server(('127.0.0.1', 0)) as closed:
        address = f'usbip://127.0.0.1:{closed.getsockname()[1]}'
    with pytest.raises(OSError):
        usbip.connect(address)
    tried = []

    def refused(*args, **kwargs):
        tried.append(args)
        raise ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused')
    monkeypatch.setattr(usbip, '_connect', refused)
    assert usbip.device_id(address) == ''
    assert tried == []


TM_T20II = 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;'


@pytest.fixture
def unknown_epson(tmp_path):
    """An Epson receipt printer shared over USB/IP that never told its device
    ID: its first import failed a moment ago, e.g. in a status query. The
    label designer has the default model."""
    device = SharedDevice(product=0x0E15, device_id=TM_T20II)
    host = FakeHost(device)
    tm = FakeTM(device)
    usbip._unreachable[usbip.key(host.address())] = time.monotonic() + 60
    client = make_client(tmp_path, empty_repo=True)
    client.application.config.update(PRINTER_PRINTER=host.address(), PRINTER_MODEL='QL-800')
    yield client, tm
    host.close()


def test_a_job_made_for_another_model_is_not_sent(unknown_epson):
    client, tm = unknown_epson
    answer = client.post('/labeldesigner/api/print', data={'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert (answer.status_code, outcome(client, answer)) == (200, ('failed', printer.WRONG_MODEL))
    assert tm.received == b''
    # Imported meanwhile, it is the model its device ID names
    with client.application.test_request_context():
        assert routes._printer(None, None)[1] == 'Epson/ESC-POS'


@pytest.mark.parametrize('path, values', [('feed', {}), ('calibration/print', {'model': 'Niimbot/B1', 'label_size': 'niimbot40x30'})])
def test_a_job_of_its_own_made_for_another_model_is_not_sent(unknown_epson, path, values):
    client, tm = unknown_epson
    answer = client.post(f'/labeldesigner/api/{path}', data=values)
    assert (answer.status_code, outcome(client, answer)) == (200, ('failed', printer.WRONG_MODEL))
    assert tm.received == b''


def test_a_printer_that_never_told_its_model_is_asked_in_its_own_commands(unknown_epson):
    client, tm = unknown_epson
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert shown['model'] == 'Epson/ESC-POS'
    assert tm.received == b''
