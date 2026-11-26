"""The Niimbot B1 on Bluetooth LE: the attribute protocol on L2CAP channel 4,
Niimbot's packets written to one characteristic and answered as its
notifications, against a fake B1 in place of the kernel's socket."""
import errno
import socket
import struct
import time

import pytest

from app.labeldesigner import bluetooth
from app.labeldesigner.drivers import devices, niimbot
from app.labeldesigner.drivers.niimbot import protocol
from tests.helpers import make_client, outcome
from tests.printing.test_niimbot import B1_CLOSED, B1_REPLIES, REPLIES, TEXT, FakePrinter

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'
STATUS = '/labeldesigner/api/printer_status'
# The characteristic Niimbot's packets go through, as the attribute protocol
# writes a 128-bit UUID
NIIMBOT = bytes.fromhex('bef8d6c99c214c9eb632bd58c1009f9f')[::-1]


def uuid16(value):
    return struct.pack('<H', value)


class LePrinter:
    """A B1 as a raw ATT socket reaches it: it asks for the MTU first, as
    the real one did, lists its characteristics, and answers what is written
    to the Niimbot characteristic with notifications once they are on."""

    def __init__(self, printer, value=0x0019, mtu=240):
        self.printer, self.value, self.mtu = printer, value, mtu
        # Declarations as the real one lists them: 16-bit UUIDs first
        self.declarations = [(0x0002, 0x20, 0x0003, uuid16(0x2a05)), (0x0006, 0x02, 0x0007, uuid16(0x2a00)),
                             (0x0008, 0x02, 0x0009, uuid16(0x2a01)),
                             (0x000d, 0x12, 0x000e, bytes(range(16))),
                             (value - 1, 0x1e, value, NIIMBOT)]
        self.made, self.addresses, self.sent = [], [], []
        self.fail, self.closed, self.dropped, self.tries = None, 0, False, 0

    def __call__(self, family, kind, proto):
        # In place of socket.socket
        self.made.append((family, kind, proto))
        return self

    def bind(self, address):
        self.bound = address

    def connect(self, address):
        self.tries += 1
        if self.fail == 'timeout':
            raise socket.timeout('timed out')
        if self.fail:
            raise OSError(self.fail, 'Host is down')
        self.addresses.append(address)
        self.dropped = False
        self.incoming = [bytes([0x02]) + struct.pack('<H', self.mtu)]
        self.notifying, self.stream = False, b''

    def settimeout(self, timeout):
        pass

    def close(self):
        self.closed += 1

    def send(self, pdu):
        if self.dropped:
            raise OSError(errno.ENOTCONN, 'Transport endpoint is not connected')
        assert len(pdu) <= self.mtu
        self.sent.append(pdu)
        op = pdu[0]
        if op == 0x02:
            self.incoming.append(bytes([0x03]) + struct.pack('<H', self.mtu))
        elif op == 0x08:
            start, end, kind = struct.unpack_from('<HHH', pdu, 1)
            found = [d for d in self.declarations if start <= d[0] <= end] if kind == 0x2803 else []
            if not found:
                self.incoming.append(bytes([0x01, 0x08]) + struct.pack('<H', start) + b'\x0a')
            else:
                # Entries of one length per answer
                size = len(found[0][3])
                entries = [struct.pack('<HBH', h, p, v) + u for h, p, v, u in found if len(u) == size]
                n = len(entries[0])
                entries = entries[:(self.mtu - 2) // n]
                self.incoming.append(bytes([0x09, n]) + b''.join(entries))
        elif op == 0x04:
            start, end = struct.unpack_from('<HH', pdu, 1)
            if start <= self.value + 1 <= end:
                self.incoming.append(bytes([0x05, 0x01]) + struct.pack('<H', self.value + 1) + uuid16(0x2902))
            else:
                self.incoming.append(bytes([0x01, 0x04]) + struct.pack('<H', start) + b'\x0a')
        elif op == 0x12:
            handle, on = struct.unpack_from('<HH', pdu, 1)
            assert handle == self.value + 1
            self.notifying = on == 1
            self.incoming.append(bytes([0x13]))
        elif op == 0x52:
            handle = struct.unpack_from('<H', pdu, 1)[0]
            assert handle == self.value
            self.stream += pdu[3:]
            found, self.stream = protocol.parse(self.stream)
            for command, data in found:
                self.printer._take(protocol.packet(command, data))
            answer, self.printer.pending = self.printer.pending, b''
            while self.notifying and answer:
                piece, answer = answer[:self.mtu - 3], answer[self.mtu - 3:]
                self.incoming.append(bytes([0x1b]) + struct.pack('<H', self.value) + piece)
        return len(pdu)

    def recv(self, size):
        if self.dropped:
            return b''
        if not self.incoming:
            raise socket.timeout('timed out')
        return self.incoming.pop(0)


@pytest.fixture(autouse=True)
def answered(monkeypatch):
    monkeypatch.setattr(protocol, '_kept', {})
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(niimbot, '_asked', {})
    monkeypatch.setattr(niimbot, '_no_serial', set())
    monkeypatch.setattr(niimbot, '_owed', {})
    monkeypatch.setattr(devices, 'looked', 0.0)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)


@pytest.fixture
def le(monkeypatch):
    peer = LePrinter(FakePrinter())
    monkeypatch.setattr(protocol.socket, 'socket', peer)
    return peer


@pytest.fixture
def b1(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Niimbot/B1'
    for command, reply in B1_REPLIES.items():
        monkeypatch.setitem(REPLIES, command, reply)
    return client


def test_b1_on_bluetooth_answers_over_bluetooth_le(b1, le):
    printer_ = b1.get(STATUS).get_json()['printers'][0]
    assert (printer_['errors'], printer_['roll'], printer_['battery']) == ([], 'PZ1G221322004205', 100)
    assert le.made == [(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)]
    assert le.addresses == [(MAC, 0, 4, socket.BDADDR_LE_PUBLIC)]
    # Asked without a beep, the label designer follows it closely
    assert printer_['follow'] is True


@pytest.mark.parametrize('heard_by, adapter', [({}, socket.BDADDR_ANY), ({MAC: '00:00:5E:00:53:F0'}, '00:00:5E:00:53:F0')])
def test_b1_is_reached_through_the_adapter_that_heard_it(b1, le, monkeypatch, heard_by, adapter):
    # Any adapter where no search heard it, e.g. set up by its address
    monkeypatch.setattr(bluetooth, 'heard_by', heard_by)
    b1.get(STATUS)
    assert le.bound == (adapter, 0, 4, socket.BDADDR_LE_PUBLIC)


def test_b1_on_bluetooth_le_tells_it_needs_python_3_14(b1, le, monkeypatch):
    # Before Python 3.14 an L2CAP address has no address type
    monkeypatch.delattr(protocol.socket, 'BDADDR_LE_PUBLIC')
    needs = 'Printers on Bluetooth LE need Python 3.14 or newer'
    assert b1.get(STATUS).get_json()['printers'][0]['errors'] == [needs]
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('failed', f'Could not send the label to the printer: {needs}')
    assert le.made == []


def test_the_printers_mtu_request_is_answered(b1, le):
    b1.get(STATUS)
    assert any(pdu[0] == 0x03 for pdu in le.sent)


def test_the_niimbot_characteristic_is_found_where_the_printer_has_it(b1, monkeypatch):
    peer = LePrinter(FakePrinter(), value=0x0031)
    monkeypatch.setattr(protocol.socket, 'socket', peer)
    assert b1.get(STATUS).get_json()['printers'][0]['errors'] == []


@pytest.mark.parametrize('mtu', [23, 240])
def test_a_label_is_written_in_pieces_the_mtu_takes(b1, monkeypatch, mtu):
    peer = LePrinter(FakePrinter(), mtu=mtu)
    monkeypatch.setattr(protocol.socket, 'socket', peer)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    # Each write fits the MTU, the fake checks, and the page was printed
    assert 0xf3 in peer.printer.commands


def heartbeats(peer):
    return sum(pdu[0] == 0x52 and protocol.parse(pdu[3:])[0][:1] == [(0xdc, b'\x01')] for pdu in peer.sent)


def test_b1_on_bluetooth_keeps_its_connection_for_the_designer_following_it(b1, le):
    # It beeps when a connection opens and closes, not at what goes over it:
    # the label designer showing it asks it on its own every 2 s
    followed = STATUS + '?printer=' + DEVICE
    b1.get(followed)
    b1.get(followed)
    assert len(le.addresses) == 1 and heartbeats(le) == 2
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '') and len(le.addresses) == 1


def test_status_of_all_printers_tells_the_last_state_of_a_b1_on_bluetooth(b1, le, monkeypatch):
    # A label designer left open in the background asks every 20 s; that
    # keeps the B1 neither connected nor beeping
    monkeypatch.setattr(protocol, 'KEEP_LE', 0.05)
    b1.get(STATUS)
    deadline = time.monotonic() + 2
    while not le.closed and time.monotonic() < deadline:
        b1.get(STATUS)
        time.sleep(0.01)
    assert le.closed == 1 and len(le.addresses) == 1 and heartbeats(le) == 1


def test_kept_connection_is_closed_unused(b1, le, monkeypatch):
    # Then the phone app can connect to the printer
    monkeypatch.setattr(protocol, 'KEEP_LE', 0.05)
    b1.get(STATUS)
    deadline = time.monotonic() + 2
    while not le.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert le.closed == 1
    # The label designer following it connects again
    b1.get(STATUS + '?printer=' + DEVICE)
    assert len(le.addresses) == 2


def test_dropped_connection_is_opened_again(b1, le):
    b1.get(STATUS)
    le.dropped = True
    # The query that finds it dropped tells so, the next one connects again
    b1.get(STATUS + '?look=1')
    printer_ = b1.get(STATUS + '?look=1').get_json()['printers'][0]
    assert printer_['errors'] == [] and len(le.addresses) == 2


def test_d110_m_stays_on_classic_bluetooth(tmp_path, le):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Niimbot/D110_M'
    le.fail = errno.EHOSTDOWN
    client.get(STATUS)
    assert le.made == [(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)]


def test_b1_on_bluetooth_switched_off_is_told_as_a_d110_m(tmp_path, le):
    said = {}
    for model in ('Niimbot/D110_M', 'Niimbot/B1'):
        client = make_client(tmp_path / model.replace('/', '-'))
        client.application.config['PRINTER_PRINTER'] = DEVICE
        client.application.config['PRINTER_MODEL'] = model
        le.fail = errno.EHOSTDOWN
        devices._unanswered.clear()
        said[model] = client.get(STATUS).get_json()['printers'][0]['errors']
    assert said['Niimbot/B1'] == said['Niimbot/D110_M'] != []


@pytest.mark.parametrize('model, device, follow', [
    ('Niimbot/B1', DEVICE, True), ('Niimbot/D110_M', DEVICE, False), ('Niimbot/B1', 'usbip://192.0.2.7/1-4.1.3', True),
    ('QL-800', 'file:///dev/usb/lp0', True), ('QL-800', 'usb://0x04f9:0x209b', True), ('QL-800', '/dev/usb/lp1', True),
    ('QL-800', 'tcp://192.0.2.9', False), ('PUQU/Q00', 'bt://00:00:5E:00:53:02/2', False), ('QL-800', 'simulation', False)])
def test_printers_that_are_asked_without_cost_are_followed(model, device, follow):
    # A status query of one on Bluetooth that beeps or on the network costs,
    # one on USB or kept on Bluetooth LE does not
    from app.labeldesigner import drivers
    assert drivers.for_model(model).followed(device, model) is follow


def test_b1_on_bluetooth_le_with_a_status_question_unanswered_is_not_asked_again(b1, le, monkeypatch):
    # The kept connection holds the late answer, the next poll reads it
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    followed = STATUS + '?printer=' + DEVICE
    assert b1.get(followed).get_json()['printers'][0]['errors'] == []
    take, missed = le.printer._take, []

    def once(data):
        if data[2] == 0xdc and not missed:
            return missed.append(data)
        take(data)
    monkeypatch.setattr(le.printer, '_take', once)
    for _ in range(2):
        assert b1.get(followed).get_json()['printers'][0]['errors'] == []
    assert missed and heartbeats(le) == 2
    le.incoming.append(bytes([0x1b]) + struct.pack('<H', le.value) + protocol.packet(0xdd, B1_CLOSED))
    assert b1.get(followed).get_json()['printers'][0]['errors'] == []
    assert heartbeats(le) == 2 and len(le.addresses) == 1


def test_b1_out_of_reach_on_bluetooth_le_is_left_alone_until_looked_for(b1, le):
    # Switched off or out of reach it does not advertise, the connection times
    # out as one of classic Bluetooth fails at once, both are left alone so
    le.fail = 'timeout'
    errors = b1.get(STATUS).get_json()['printers'][0]['errors']
    b1.get(STATUS)
    assert le.tries == 1 and errors == [devices.unanswered_reason(OSError(errno.EHOSTDOWN, 'Host is down'))]
    b1.get(STATUS + '?look=1')
    assert le.tries == 2
