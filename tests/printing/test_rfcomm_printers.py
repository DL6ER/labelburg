"""TSPL label printers and ESC/POS receipt printers on classic Bluetooth:
their jobs and status over RFCOMM against fake printers on socket pairs,
the same bytes as on USB, printers that are off or not paired, and the
classic search finding them by their names."""
import errno
import json
import os
import socket
import threading
import time
import types

import pytest

from app.labeldesigner import bluetooth, drivers, printer, refusals
from app.labeldesigner.drivers import base, devices, escpos, progress, transports, tspl
from app.labeldesigner.drivers.escpos import protocol as escpos_protocol
from app.labeldesigner.drivers.tspl import protocol as tspl_protocol
from tests.helpers import make_client, outcome
from tests.printing.test_bluetooth import Management, found
from tests.printing.test_escpos import QUIET, Usblp as EscposUsblp
from tests.printing.test_transports import PRINTER_CLASS, class_of_device
from tests.printing.test_tspl import FakeTspl, FakeUsblp

MAC = '00:00:5E:00:53:21'
DEVICE = f'bt://{MAC}'
STATUS = '/labeldesigner/api/printer_status'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
TSPL_LABEL = {'label_size': 'tspl57x32', 'print_type': 'text', 'text': TEXT, 'print_count': '2'}
# The end of each label of TSPL
PRINTED = b'PRINT 1,1\r\n'
ESCPOS_LABEL = {'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT, 'print_count': '2', 'cut': 'last'}


class FakeEscpos:
    """What a receipt printer answers: a status byte to each DLE EOT
    request, nothing where it does not answer them, e.g. a clone."""

    def __init__(self, answering=True, offline=QUIET):
        self.answering, self.offline = answering, offline

    def heard(self, data: bytes) -> bytes:
        if not self.answering:
            return b''
        return {escpos_protocol.OFFLINE: bytes([self.offline]), escpos_protocol.PAPER: bytes([QUIET])}.get(data, b'')


class Connection:
    """An RFCOMM socket: one end of a socket pair, the printer at the other."""

    def __init__(self, fake):
        self.fake = fake
        self.ours, self.theirs = socket.socketpair()

    def connect(self, address):
        self.fake.addresses.append(address)
        error = self.fake.error
        if self.fake.during_page:
            self.fake.during_page()
        if error:
            self.theirs.close()
            raise OSError(error, os.strerror(error))
        received = bytearray()
        self.fake.received.append(received)
        thread = threading.Thread(target=self._serve, args=(received,), daemon=True)
        self.fake.threads.append(thread)
        thread.start()

    def _serve(self, received):
        with self.theirs:
            while True:
                try:
                    data = self.theirs.recv(65536)
                    if not data:
                        return
                    received += data
                    if answer := self.fake.printer.heard(data):
                        self.theirs.sendall(answer)
                except OSError:
                    return

    def close(self):
        self.ours.close()
        self.fake.closed += 1

    def __getattr__(self, name):
        return getattr(self.ours, name)


class OnBluetooth:
    """A printer on classic Bluetooth in place of the RFCOMM sockets: each
    connection one of its own, what each received in received. With error,
    connecting fails as the kernel tells it, e.g. EHOSTDOWN for one that is
    switched off. during_page() is called while it is paged."""

    def __init__(self, printer_):
        self.printer, self.error, self.during_page = printer_, 0, None
        self.made, self.addresses, self.received, self.threads, self.closed = [], [], [], [], 0

    def __call__(self, family, kind, proto):
        self.made.append((family, kind, proto))
        return Connection(self)

    def all_received(self) -> list[bytes]:
        """What each connection received, once it was closed."""
        for thread in self.threads:
            thread.join(5)
        return [bytes(received) for received in self.received]


class Sockets:
    """The socket module as transports.py uses it, with the fake RFCOMM sockets."""

    def __init__(self, fake):
        self.socket = fake

    def __getattr__(self, name):
        return getattr(socket, name)


@pytest.fixture(autouse=True)
def afresh(monkeypatch):
    monkeypatch.setattr(tspl, '_ask_after', {})
    monkeypatch.setattr(tspl, '_names', {})
    monkeypatch.setattr(tspl_protocol, 'ANSWER_WAIT', 0.3)
    monkeypatch.setattr(escpos_protocol, 'ANSWER_WAIT', 0.3)
    monkeypatch.setattr(escpos_protocol, '_asked', {})
    monkeypatch.setattr(escpos_protocol, '_answered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, '_unanswered', {})
    # The speed of a job here is not that of a later printer on the same terminal
    monkeypatch.setattr(progress, '_factors', {})


@pytest.fixture
def on_bluetooth(monkeypatch):
    def install(printer_):
        fake = OnBluetooth(printer_)
        monkeypatch.setattr(transports, 'socket', Sockets(fake))
        return fake
    return install


@pytest.fixture
def clock(monkeypatch):
    """The clock of the status queries on Bluetooth, moved on by hand from
    now, before the label designer looks for printers."""
    now = [time.monotonic()]
    monkeypatch.setattr(base, 'time', types.SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(devices, 'looked', 0.0)
    return now


def tspl_on_usb(tmp_path, label: dict) -> bytes:
    """The job of a label printed on a TSPL printer behind usblp."""
    usblp = FakeUsblp()
    try:
        client = make_client(tmp_path, model='TSC/TSPL', printer=f'file://{usblp.path}')
        response = client.post('/labeldesigner/api/print', data=label)
        assert outcome(client, response) == ('printed', '')
        deadline = time.monotonic() + 5
        while usblp.received.count(PRINTED) < int(label['print_count']) and time.monotonic() < deadline:
            time.sleep(0.01)
        received = usblp.received
    finally:
        usblp.close()
    # After the status requests of the print
    return received[received.index(b'\r\nSIZE'):]


def escpos_on_usb(tmp_path, monkeypatch, label: dict) -> bytes:
    """What a receipt printer behind usblp received for a label: the
    status requests before it and the job."""
    usblp = EscposUsblp(answering=True)
    with monkeypatch.context() as patched:
        patched.setattr(escpos_protocol, 'connect', usblp.connect)
        client = make_client(tmp_path, model='Epson/ESC-POS', printer='file:///dev/usb/lp3')
        response = client.post('/labeldesigner/api/print', data=label)
        assert outcome(client, response) == ('printed', '')
        received = usblp.got(escpos_protocol.CUT)
    usblp.close()
    return received


def test_tspl_label_on_bluetooth_is_the_label_on_usb(tmp_path, on_bluetooth):
    fake = on_bluetooth(FakeTspl())
    client = make_client(tmp_path, model='TSC/TSPL', printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data=TSPL_LABEL)
    assert outcome(client, response) == ('printed', '')
    # The status asked for the print, then the job on a connection of its own
    *asked, job = fake.all_received()
    assert all(received.startswith(tspl_protocol.STATUS) for received in asked)
    assert job == tspl_on_usb(tmp_path, TSPL_LABEL) and job.count(PRINTED) == 2
    # Classic Bluetooth on channel 1, each connection closed again
    assert set(fake.made) == {(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)}
    assert set(fake.addresses) == {(MAC, 1)} and fake.closed == len(fake.made)


def test_escpos_label_on_bluetooth_is_the_label_on_usb(tmp_path, monkeypatch, on_bluetooth):
    fake = on_bluetooth(FakeEscpos())
    client = make_client(tmp_path, model='Epson/ESC-POS', printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data=ESCPOS_LABEL)
    assert outcome(client, response) == ('printed', '')
    job = fake.all_received()[-1]
    # Asked for its status before the job, on the same connection, as on USB
    on_usb = escpos_on_usb(tmp_path, monkeypatch, ESCPOS_LABEL)
    assert job == on_usb[-len(job):] and job.startswith(escpos_protocol.OFFLINE + escpos_protocol.PAPER + escpos_protocol.INIT)
    assert job.count(b'\x1dv0\x00') == 2 and job.endswith(escpos_protocol.CUT)
    assert set(fake.addresses) == {(MAC, 1)} and fake.closed == len(fake.made)


def test_printer_on_the_rfcomm_channel_it_names(tmp_path, on_bluetooth):
    fake = on_bluetooth(FakeEscpos())
    client = make_client(tmp_path, model='Epson/ESC-POS', printer=f'{DEVICE}/3')
    response = client.post('/labeldesigner/api/print', data=ESCPOS_LABEL)
    assert outcome(client, response) == ('printed', '')
    assert set(fake.addresses) == {(MAC, 3)}


def test_tspl_status_over_bluetooth(on_bluetooth, clock):
    fake = on_bluetooth(FakeTspl(status=0x04))
    state = tspl.DRIVER.status(DEVICE, 'TSC/TSPL')
    assert (state['errors'], state['status_type']) == (['No labels are in the printer'], 'Error occurred')
    fake.printer.status = 0
    clock[0] += base.BLUETOOTH_STATUS_EVERY
    state = tspl.DRIVER.status(DEVICE, 'TSC/TSPL')
    # Its model as it answers ~!T once it is ready
    assert (state['errors'], state['device_name'], state['path']) == ([], 'TE200', DEVICE)
    assert fake.all_received() == [tspl_protocol.STATUS, tspl_protocol.STATUS + tspl_protocol.MODEL_NAME]


def test_escpos_status_over_bluetooth(on_bluetooth):
    on_bluetooth(FakeEscpos(offline=QUIET | 0x04))
    state = escpos.DRIVER.status(DEVICE, 'Epson/ESC-POS')
    assert (state['errors'], state['path']) == (['The cover of the printer is open'], DEVICE)


@pytest.mark.parametrize('model', ['TSC/TSPL', 'Epson/ESC-POS'])
def test_printer_on_bluetooth_that_does_not_answer_its_status_is_printed_on(tmp_path, on_bluetooth, model):
    # Connected, as one that never answered on USB: told as ready
    fake = on_bluetooth(FakeTspl(status=None) if model == 'TSC/TSPL' else FakeEscpos(answering=False))
    client = make_client(tmp_path, model=model, printer=DEVICE)
    state = client.get(STATUS).get_json()['printers'][0]
    assert (state['errors'], state['model'], state['path']) == ([], model, DEVICE)
    label = TSPL_LABEL if model == 'TSC/TSPL' else ESCPOS_LABEL
    response = client.post('/labeldesigner/api/print', data=label)
    assert outcome(client, response) == ('printed', '')
    assert fake.all_received()[-1].endswith(PRINTED if model == 'TSC/TSPL' else escpos_protocol.CUT)


@pytest.mark.parametrize('model', ['TSC/TSPL', 'Epson/ESC-POS'])
def test_printer_on_bluetooth_that_is_off_is_not_paged_again(tmp_path, on_bluetooth, model):
    # Paging it takes the radio for seconds
    fake = on_bluetooth(FakeTspl() if model == 'TSC/TSPL' else FakeEscpos())
    fake.error = errno.EHOSTDOWN
    client = make_client(tmp_path, model=model, printer=DEVICE)
    for _ in range(3):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert len(fake.addresses) == 1
    # Looked for again when the label designer looks for printers
    fake.error = 0
    assert client.get(STATUS + '?look=1').get_json()['printers'][0]['errors'] == []
    assert len(fake.addresses) == 2


@pytest.mark.parametrize('model', ['TSC/TSPL', 'Epson/ESC-POS'])
def test_printer_on_bluetooth_is_asked_again_once_it_printed(tmp_path, on_bluetooth, model):
    fake = on_bluetooth(FakeTspl() if model == 'TSC/TSPL' else FakeEscpos())
    devices.note_answer(DEVICE, OSError(errno.EHOSTDOWN, 'Host is down'))
    client = make_client(tmp_path, model=model, printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data=TSPL_LABEL if model == 'TSC/TSPL' else ESCPOS_LABEL)
    assert outcome(client, response) == ('printed', '')
    asked = len(fake.addresses)
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    assert len(fake.addresses) == asked + 1


@pytest.mark.parametrize('model, label', [('TSC/TSPL', TSPL_LABEL), ('Epson/ESC-POS', ESCPOS_LABEL)])
@pytest.mark.parametrize('error', [errno.EACCES, errno.EBADE])
def test_printer_on_bluetooth_that_is_not_paired(tmp_path, on_bluetooth, model, label, error):
    fake = on_bluetooth(FakeEscpos())
    fake.error = error
    client = make_client(tmp_path, model=model, printer=DEVICE)
    for _ in range(2):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['Pair the printer with this computer first']
    # Paged once, as a printer that answers
    assert len(fake.addresses) == 1
    response = client.post('/labeldesigner/api/print', data=label)
    message = 'Could not send the label to the printer: Pair the printer with this computer first'
    assert outcome(client, response) == ('failed', message) and message in refusals.MESSAGES


@pytest.mark.parametrize('model, label', [('TSC/TSPL', TSPL_LABEL), ('Epson/ESC-POS', ESCPOS_LABEL)])
def test_status_on_bluetooth_is_asked_at_most_every_30_s(tmp_path, on_bluetooth, clock, model, label):
    fake = on_bluetooth(FakeTspl() if model == 'TSC/TSPL' else FakeEscpos(answering=False))
    client = make_client(tmp_path, model=model, printer=DEVICE)
    start = clock[0]

    def asked(at, look=False):
        clock[0] = start + at
        assert client.get(STATUS + ('?look=1' if look else '')).get_json()['printers'][0]['errors'] == []
        return len(fake.addresses)
    # Also one that does not answer its status requests, which the status
    # tells as ready
    assert [asked(0), asked(14), asked(29)] == [1, 1, 1]
    # Looking for printers asks it at once
    assert [asked(29, look=True), asked(58), asked(60)] == [2, 2, 3]
    # As does the first status query after printing, which took a connection
    response = client.post('/labeldesigner/api/print', data=label)
    assert outcome(client, response) == ('printed', '')
    assert len(fake.addresses) == 4 and asked(60) == 5


@pytest.mark.parametrize('model', ['TSC/TSPL', 'Epson/ESC-POS'])
def test_printer_switched_on_while_it_was_paged_is_paged_again(tmp_path, on_bluetooth, model):
    fake = on_bluetooth(FakeTspl() if model == 'TSC/TSPL' else FakeEscpos())
    fake.error = errno.EHOSTDOWN

    def switched_on_and_looked_for():
        # "Look for printers" before the page of the printer that was off ended
        fake.error, fake.during_page = 0, None
        devices.look_again()
    fake.during_page = switched_on_and_looked_for
    client = make_client(tmp_path, model=model, printer=DEVICE)
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    assert len(fake.addresses) == 2


@pytest.mark.parametrize('model, label', [('TSC/TSPL', TSPL_LABEL), ('Epson/ESC-POS', ESCPOS_LABEL)])
def test_printer_on_bluetooth_that_is_off_when_printing(tmp_path, on_bluetooth, model, label):
    fake = on_bluetooth(FakeEscpos())
    fake.error = errno.EHOSTDOWN
    client = make_client(tmp_path, model=model, printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data=label)
    message = 'Could not send the label to the printer: The printer cannot be reached'
    assert outcome(client, response) == ('failed', message) and message in refusals.MESSAGES


@pytest.mark.parametrize('name', [
    'TM-P20_123456', 'TM-P60II_123456', 'TM-P80_123456', 'TM-m10_123456', 'TM-m30_123456', 'TM-m30II_123456',
    'TM-m50_123456', 'TM-P20II_123456', 'TM-P80II_123456', 'TM-m30III_123456',
    # As the tables of the TM-P20II and TM-P80II spell it, and the name a
    # printer of both kinds of Bluetooth has on LE
    'TM-P20II-123456', 'TM-P80II-123456', 'TM-P20II-123456-L', 'TM-P80II-123456-L', 'TM-m30III_123456-L',
])
def test_epson_printers_on_classic_bluetooth_are_known_by_their_names(name):
    device = {'address': MAC, 'name': name, 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}
    assert escpos.DRIVER.found_on_bluetooth(device) == (DEVICE, 'Epson/ESC-POS')
    # Reached on classic Bluetooth only
    assert escpos.DRIVER.found_on_bluetooth(dict(device, le=True, classic=False)) is None


@pytest.mark.parametrize('name', ['TM-P20', 'TM-P20_12345', 'TM-m30-123456', 'TM-P20_123456-L', 'TM-T20II_123456',
                                  'Printer001', 'Phone'])
def test_other_names_on_classic_bluetooth_are_no_epson_printers(name):
    device = {'address': MAC, 'name': name, 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}
    assert all(driver.found_on_bluetooth(device) is None for driver in drivers.DRIVERS)


@pytest.fixture
def nearby(tmp_path, monkeypatch):
    """The automatic detection with the kernel's management channel as a
    fake that finds the devices given."""
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', True)
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    monkeypatch.setattr(printer, '_bluetooth', {})
    monkeypatch.setattr(printer, '_bluetooth_searched', None)
    monkeypatch.setattr(printer, '_bluetooth_thread', None)
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: ([], []))
    monkeypatch.setattr(devices, 'looked', 0.0)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'

    def install(*found_devices):
        fake = Management(found_devices)
        monkeypatch.setattr(bluetooth, 'socket', Sockets(fake))
        monkeypatch.setattr(bluetooth, 'select', types.SimpleNamespace(
            select=lambda r, w, x, t=None: (r if fake.incoming else [], [], [])))
        return client, fake
    return install


def listed(client, look=False):
    """The printers the detection lists once its search on Bluetooth ended."""
    client.get(STATUS + ('?look=1' if look else ''))
    printer._bluetooth_thread.join(5)
    printer._last_scan_ts = 0
    return [(p['path'], p['model'], p['errors']) for p in client.get(STATUS).get_json()['printers'] if p['path'] != 'simulation']


def test_epson_printer_found_on_classic_bluetooth_prints_as_on_usb(tmp_path, monkeypatch, nearby, on_bluetooth):
    client, management = nearby(found(MAC, kind=0, name='TM-P20_123456', raw=class_of_device(PRINTER_CLASS)))
    fake = on_bluetooth(FakeEscpos())
    # Classic Bluetooth is searched only when the label designer looks for printers
    assert listed(client) == []
    assert listed(client, look=True) == [(DEVICE, 'Epson/ESC-POS', [])]
    assert [params for op, _, params in management.sent if op == 0x0023] == [b'\x06', b'\x07']
    response = client.post('/labeldesigner/api/print', data=dict(ESCPOS_LABEL, printer=DEVICE, model='Epson/ESC-POS'))
    assert outcome(client, response) == ('printed', '')
    job = fake.all_received()[-1]
    assert escpos_on_usb(tmp_path, monkeypatch, ESCPOS_LABEL).endswith(job)
    assert job.startswith(escpos_protocol.OFFLINE + escpos_protocol.PAPER + escpos_protocol.INIT)


class HangsUp:
    """A printer that closes the connection at the first request."""

    def heard(self, data: bytes) -> bytes:
        raise ConnectionAbortedError


def test_connection_the_printer_closed_is_not_read_as_one_without_an_answer(on_bluetooth):
    on_bluetooth(HangsUp())
    printer_ = transports.RfcommPrinter(DEVICE)
    # Nothing came yet
    assert printer_.read(64) == b''
    printer_.write(b'?')
    with pytest.raises(ConnectionResetError):
        for _ in range(500):
            printer_.read(64)
    printer_.dispose()


@pytest.mark.parametrize('model', ['TSC/TSPL', 'Epson/ESC-POS'])
def test_status_of_a_printer_on_bluetooth_that_closes_the_connection_is_not_waited_for(on_bluetooth, monkeypatch, model):
    monkeypatch.setattr(tspl_protocol, 'ANSWER_WAIT', 5)
    monkeypatch.setattr(escpos_protocol, 'ANSWER_WAIT', 5)
    on_bluetooth(HangsUp())
    started = time.monotonic()
    if model == 'TSC/TSPL':
        # Told as ready, as one that never answered
        assert tspl.DRIVER.status(DEVICE, model)['errors'] == []
    else:
        with pytest.raises(ConnectionResetError):
            escpos.DRIVER.status(DEVICE, model)
    assert time.monotonic() - started < 1


@pytest.fixture
def searches(tmp_path, monkeypatch):
    """The automatic detection with an Epson nearby on classic Bluetooth,
    whose search goes on until released is set; with the state of
    devices.classic_search at each search."""
    searched, release = [], threading.Event()
    release.set()

    def discover(timeout, classic=False):
        searched.append(devices.classic_search.is_set())
        release.wait(5)
        epson = {'address': MAC, 'name': 'TM-P20_123456', 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}
        return [epson] if classic else []
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
    return client, release, searched


def looked_for_while_searching(client, release) -> list:
    """The errors of the printers listed when the label designer looks for
    printers, while classic Bluetooth is still searched."""
    release.clear()
    printer._last_scan_ts = 0
    data = client.get(STATUS + '?look=1').get_json()
    assert data['searching']
    return [(p['path'], p['errors']) for p in data['printers'] if p['path'] != 'simulation']


def errors_once_searched(client, release) -> list:
    release.set()
    printer._bluetooth_thread.join(5)
    return [p['errors'] for p in client.get(STATUS).get_json()['printers'] if p['path'] == DEVICE]


def test_printers_on_bluetooth_are_not_paged_while_classic_bluetooth_is_searched(on_bluetooth, searches):
    fake = on_bluetooth(FakeEscpos())
    client, release, searched = searches
    assert listed(client) == []
    assert listed(client, look=True) == [(DEVICE, 'Epson/ESC-POS', [])] and len(fake.addresses) == 1
    # Its last answer while classic Bluetooth is searched again
    fake.printer.offline = QUIET | 0x04
    assert looked_for_while_searching(client, release) == [(DEVICE, [])] and len(fake.addresses) == 1
    # Paged once the search ended
    assert errors_once_searched(client, release) == [['The cover of the printer is open']]
    assert len(fake.addresses) == 2 and searched == [False, True, True] and not devices.classic_search.is_set()


def test_printer_on_bluetooth_that_was_off_is_paged_once_the_search_ended(on_bluetooth, searches):
    fake = on_bluetooth(FakeEscpos())
    fake.error = errno.EHOSTDOWN
    client, release, _ = searches
    listed(client)
    assert listed(client, look=True) == [(DEVICE, 'Epson/ESC-POS', ['The printer cannot be reached'])]
    # Switched on, it is paged once the next search ended, not during it
    fake.error = 0
    assert looked_for_while_searching(client, release) == [(DEVICE, ['The printer cannot be reached'])]
    assert len(fake.addresses) == 1
    assert errors_once_searched(client, release) == [[]] and len(fake.addresses) == 2


def test_last_answer_of_a_printer_that_got_a_job_is_kept_for_a_search(on_bluetooth):
    fake = on_bluetooth(FakeTspl())
    assert tspl.DRIVER.status(DEVICE, 'TSC/TSPL')['errors'] == []
    tspl.DRIVER.ask_bluetooth_again(DEVICE)
    fake.printer.status = 0x04
    devices.classic_search.set()
    assert tspl.DRIVER.status(DEVICE, 'TSC/TSPL')['errors'] == [] and len(fake.addresses) == 1
    devices.classic_search.clear()
    assert tspl.DRIVER.status(DEVICE, 'TSC/TSPL')['errors'] == ['No labels are in the printer'] and len(fake.addresses) == 2
