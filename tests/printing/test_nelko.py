"""The Nelko P21 on classic Bluetooth against a fake P21 on socket pairs:
its status, roll and battery, one connection kept for all of it, its job,
and the search finding it by its name."""
import errno
import socket
import time

import pytest

from app.labeldesigner import tape
from app.labeldesigner.drivers import base, devices, nelko, progress, transports
from app.labeldesigner.drivers.nelko import protocol
from app.labeldesigner.drivers.tspl import protocol as tspl
from tests.helpers import make_client, outcome
from tests.printing.test_rfcomm_printers import DEVICE, MAC, STATUS, TEXT, OnBluetooth, Sockets

MODEL = 'Nelko/P21'
LABEL = {'label_size': 'nelko14x40', 'print_type': 'text', 'text': TEXT, 'print_count': '2'}
# The tag of a roll of 14 x 40 mm white labels with gaps, as a P21 told it
TAG = bytes.fromhex('000c011203000301121215280f0eed03')


class FakeP21:
    """What a P21 answers its requests with; a status byte, its tag and its
    battery level as two BCD digits."""

    def __init__(self, status=0, tag=TAG, battery=b'\x75'):
        self.status, self.tag, self.battery = status, tag, battery

    def heard(self, data: bytes) -> bytes:
        return {protocol.STATUS: bytes([self.status]), protocol.ROLL: self.tag,
                protocol.BATTERY: b'BATTERY ' + self.battery + b'\x00\r\n'}.get(data, b'')


@pytest.fixture(autouse=True)
def afresh(monkeypatch):
    monkeypatch.setattr(transports, '_kept', {})
    monkeypatch.setattr(base, '_bluetooth_asked', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.3)
    monkeypatch.setattr(progress, '_factors', {})
    yield
    for link in transports._kept.values():
        link.close()


@pytest.fixture
def p21(monkeypatch):
    fake = OnBluetooth(FakeP21())
    monkeypatch.setattr(transports, 'socket', Sockets(fake))
    return fake


def received(fake, ending: bytes) -> bytes:
    """What the kept connection received, once it ends with ending."""
    deadline = time.monotonic() + 5
    while not (fake.received and bytes(fake.received[-1]).endswith(ending)) and time.monotonic() < deadline:
        time.sleep(0.01)
    return bytes(fake.received[-1])


def test_p21_tells_its_roll_and_battery(p21):
    state = nelko.DRIVER.status(DEVICE, MODEL)
    assert (state['errors'], state['status_type'], state['path']) == ([], 'Reply to status request', DEVICE)
    assert (state['media_width'], state['media_length'], state['media_type']) == (14, 40, 'Die-cut labels')
    assert (state['battery'], state['battery_low']) == (75, False)
    # Over classic Bluetooth on channel 1
    assert set(p21.made) == {(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)}
    assert p21.addresses == [(MAC, 1)]


def test_p21_with_little_charge_left(p21):
    p21.printer.battery = b'\x15'
    assert (nelko.DRIVER.status(DEVICE, MODEL)['battery'], nelko.DRIVER.status(DEVICE, MODEL)['battery_low']) == (15, True)


@pytest.mark.parametrize('tag', [TAG[:-1] + b'\x00', bytes(16)])
def test_p21_roll_without_a_tag_that_holds_together_tells_no_size(p21, tag):
    p21.printer.tag = tag
    state = nelko.DRIVER.status(DEVICE, MODEL)
    assert (state['errors'], state['media_width'], state['media_length']) == ([], None, 0)


def test_p21_errors_as_tspl_tells_them(p21):
    p21.printer.status = 0x01
    state = nelko.DRIVER.status(DEVICE, MODEL)
    assert (state['errors'], state['status_type']) == (['The printhead is open'], 'Error occurred')


def test_p21_keeps_one_connection_for_status_and_jobs(tmp_path, p21):
    # It beeps when a connection opens and when it closes
    client = make_client(tmp_path, model=MODEL, printer=DEVICE)
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    base._bluetooth_asked.clear()
    assert client.get(STATUS).get_json()['printers'][0]['media_width'] == 14
    response = client.post('/labeldesigner/api/print', data=LABEL)
    assert outcome(client, response) == ('printed', '')
    job = received(p21, b'PRINT 1,1\r\n')
    assert p21.addresses == [(MAC, 1)] and p21.closed == 0
    assert job.count(b'PRINT 1,1\r\n') == 2


def test_p21_job_is_tspl_on_its_label(tmp_path, p21):
    client = make_client(tmp_path, model=MODEL, printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data=dict(LABEL, print_count='1'))
    assert outcome(client, response) == ('printed', '')
    job = received(p21, b'PRINT 1,1\r\n')
    label = job[job.index(b'\r\nSIZE'):]
    # 14 x 40 mm with gaps of 5 mm, printed on 96 x 284 dots
    assert label.startswith(b'\r\nSIZE 14 mm,40 mm\r\nGAP 5 mm,0 mm\r\nDIRECTION 0,0\r\n')
    head = b'BITMAP 0,0,12,284,0,'
    assert head in label
    assert len(label) == label.index(head) + len(head) + 12 * 284 + len(b'\r\nPRINT 1,1\r\n')


def test_p21_with_an_error_is_sent_no_job(tmp_path, p21):
    client = make_client(tmp_path, model=MODEL, printer=DEVICE)
    p21.printer.status = 0x04
    response = client.post('/labeldesigner/api/print', data=LABEL)
    assert outcome(client, response) == ('failed', 'No labels are in the printer')
    assert b'SIZE' not in received(p21, protocol.STATUS)


def test_p21_is_connected_again_once_it_dropped_the_connection(p21):
    nelko.DRIVER.status(DEVICE, MODEL)
    # Switched itself off and on again
    next(iter(transports._kept.values())).s.theirs.shutdown(socket.SHUT_RDWR)
    base._bluetooth_asked.clear()
    assert nelko.DRIVER.status(DEVICE, MODEL)['errors'] == []
    assert p21.addresses == [(MAC, 1), (MAC, 1)]


def test_p21_that_is_off_is_not_paged_again(tmp_path, p21):
    p21.error = errno.EHOSTDOWN
    client = make_client(tmp_path, model=MODEL, printer=DEVICE)
    for _ in range(3):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert len(p21.addresses) == 1


def test_p21_that_does_not_answer(p21):
    p21.printer.heard = lambda data: b''
    with pytest.raises(protocol.NoAnswer):
        nelko.DRIVER.status(DEVICE, MODEL)


def test_kept_rfcomm_link_stays_open_until_it_drops(p21):
    link = transports.kept_rfcomm(DEVICE, 1)
    transports.release(DEVICE, link, None)
    assert transports.kept_rfcomm(DEVICE, 1) is link and link.timer is None
    link.broken = True
    transports.release(DEVICE, link, None)
    assert transports._kept == {} and p21.closed == 1


@pytest.mark.parametrize('name, found', [('P21', True), ('P21_1234', True), ('P210', False), ('B1-123', False)])
def test_p21_is_found_on_bluetooth_le_by_its_name(name, found):
    device = {'address': MAC, 'name': name, 'le': True}
    assert nelko.DRIVER.found_on_bluetooth(device) == ((DEVICE, MODEL) if found else None)


def test_p21_job_that_dropped_midway_is_not_sent_again(tmp_path, p21, monkeypatch):
    sendall = transports.RfcommLink.sendall

    def dropping(self, data):
        if b'SIZE' in data:
            self.broken = True
            raise ConnectionResetError(errno.ECONNRESET, 'Connection reset by peer')
        sendall(self, data)
    monkeypatch.setattr(transports.RfcommLink, 'sendall', dropping)
    client = make_client(tmp_path, model=MODEL, printer=DEVICE)
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    response = client.post('/labeldesigner/api/print', data=LABEL)
    state, message = outcome(client, response)
    # It may have printed a part
    assert state == 'failed' and message.startswith(devices.NOT_SENT)
    assert len(p21.addresses) == 1


def test_p21_roll_is_the_label_size_in_the_printer(p21):
    state = nelko.DRIVER.status(DEVICE, MODEL)
    t = tape.in_printer(state, MODEL, '', '62')
    assert (t.id, t.assumed) == ('nelko14x40', False)
