"""Cat printers, e.g. the GB01 or the MX06, on Bluetooth LE: the packets of a
label against those of Cat-Printer (printer_lib/commander.py, printer.py), the
state they answer as kitty-printer and TiMini-Print read it, and printing
through the label designer against a fake printer in place of the kernel's
socket."""
import errno
import json
import socket
import struct
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner.drivers import cat, devices, transports
from app.labeldesigner.drivers.cat import protocol
from app.labeldesigner.enums import LabelContent, LabelOrientation
from tests.helpers import make_client, outcome

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'
STATUS = '/labeldesigner/api/printer_status'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])

# A row with its first dot printed and one all black, packed MSB first
FIRST_DOT, BLACK = b'\x80' + bytes(47), b'\xff' * 48
# The label of these rows as Cat-Printer sends an image (printer.py:448-487),
# each packet as commander.py makes it
PACKETS = [
    '5178a30001000000ff',  # get_device_state, commander.py:101-103
    '5178a30001000000ff',  # start_printing, commander.py:88-90
    '5178a4000100329eff',  # set_dpi_as_200, commander.py:113-115
    '5178bd00010020e0ff',  # set_speed(32), printer.py:291 and 455-456
    '5178af0002000040c7ff',  # set_energy(0x4000), printer.py:708
    '5178be0001000107ff',  # apply_energy, commander.py:96-99
    '5178a90001000000ff',  # update_device, commander.py:109-111
    '5178a6000b00aa551738445f5f5f44382ca1ff',  # start_lattice, commander.py:117-121
    # draw_bitmap, commander.py:151-154: the bits of each byte reversed
    '5178a2003000' + '01' + '00' * 47 + '08ff',
    '5178a2003000' + 'ff' * 48 + 'e8ff',
    '5178a6000b00aa5517000000000000001711ff',  # end_lattice, commander.py:123-127
    '5178bd0001000838ff',  # set_speed(8), printer.py:466
    '5178a10002008000b6ff',  # feed_paper(128), printer.py:471
    '5178a30001000000ff',  # get_device_state, printer.py:472
]
EMPTY_ROW = '5178a2003000' + '00' * 48 + '00ff'


def answer(state):
    """The state the printer notifies, 51 78 a3 01 01 00 <state> <CRC> ff as
    TiMini-Print tests/test_tiny_flow_control.py:50 shows it."""
    return protocol.packet(0xa3, bytes([state]), 1)


def test_packets_are_framed_as_the_sources_write_them():
    assert protocol.STATE.hex() == '5178a30001000000ff'
    # commander.py:75-76
    assert protocol.packet(0xae, b'\x10', 1).hex() == '5178ae0101001070ff'
    assert protocol.packet(0xae, b'\x00', 1).hex() == '5178ae0101000000ff'
    # TiMini-Print tests/fixtures/protocol_golden.json:2-4, there with other values
    assert protocol.packet(0xa4, b'\x33').hex() == '5178a40001003399ff'
    assert protocol.energy(0x1388).hex() == '5178af000200881367ff'
    assert protocol.packet(0xa2, b'\xcc').hex() == '5178a2000100cc6aff'


def test_label_is_sent_as_cat_printer_prints_an_image():
    assert [p.hex() for p in protocol.label_packets([FIRST_DOT, BLACK])] == PACKETS


def test_a_gb03_starts_in_the_newer_form():
    # start_printing_new, commander.py:92-94
    assert [p.hex() for p in protocol.label_packets([FIRST_DOT, BLACK], new_start=True)] == \
        PACKETS[:1] + ['125178a30001000000ff'] + PACKETS[2:]


def test_an_mx06_gets_the_paper_after_a_label_as_empty_rows():
    # printer.py:467-469 for the models of models.py:57-58
    assert [p.hex() for p in protocol.label_packets([FIRST_DOT, BLACK], feed_rows=True)] == \
        PACKETS[:-2] + [EMPTY_ROW] * 128 + PACKETS[-1:]


@pytest.mark.parametrize('state, told', [
    (0x00, []),
    (0x01, ['The printer has no paper']),
    (0x02, ['The cover of the printer is open']),
    (0x04, ['The printer is too hot, let it cool down']),
    # A low battery is only told, also with no paper, as TiMini-Print v5g.py:38
    # names 09 and kitty-printer reads its bits
    (0x08, []), (0x09, ['The printer has no paper']),
    # Paused and busy keep it from nothing
    (0x10, []), (0x80, []),
])
def test_state_bits_as_kitty_printer_reads_them(state, told):
    assert protocol.errors(state) == told
    assert protocol.battery_low(state) is bool(state & 0x08)


def test_packets_are_read_split_and_joined_and_damaged_ones_left_out():
    pause, state = protocol.packet(0xae, b'\x10', 1), answer(0x02)
    found, rest = protocol.parse(b'\x00' + pause[:4])
    assert (found, rest) == ([], pause[:4])
    damaged = state[:-2] + b'\x00\xff'
    found, rest = protocol.parse(rest + pause[4:] + damaged + state + b'\x51')
    assert (found, rest) == ([(0xae, b'\x10'), (0xa3, b'\x02')], b'\x51')


class _Label:
    label_content = LabelContent.TEXT_ONLY

    def __init__(self, image, orientation=LabelOrientation.STANDARD):
        self.image, self.label_orientation = image, orientation

    def generate(self):
        return self.image.copy()


def test_label_rows_lie_across_the_printhead():
    image = Image.new('L', (384, 20), 'white')
    image.putpixel((0, 0), 0)
    _, rows = cat.label_rows(_Label(image))
    assert (len(rows), rows[0][:1], set(len(row) for row in rows)) == (20, b'\x80', {48})
    # Drawn lying, turned across the paper
    _, rows = cat.label_rows(_Label(Image.new('L', (100, 384), 'white'), LabelOrientation.ROTATED))
    assert (len(rows), set(len(row) for row in rows)) == (100, {48})


@pytest.mark.parametrize('name, model', [
    ('GB01', 'Cat/GB01'), ('GB02', 'Cat/GB02'), ('GB03', 'Cat/GB03'), ('GT01', 'Cat/GT01'),
    ('MX05', 'Cat/MX05'), ('MX06', 'Cat/MX06'), ('MX08', 'Cat/MX08'), ('MX09', 'Cat/MX09'),
    ('MX10', 'Cat/MX10'), ('MX11', 'Cat/MX11'), ('PD01', 'Cat/PD01'), ('YT01', 'Cat/YT01'),
    # Cat-Printer and TiMini-Print take the start of these names
    ('GB01-1234', 'Cat/GB01'), ('GB03XY', 'Cat/GB03'),
    # TiMini-Print reaches these over classic Bluetooth, the MX ones by the
    # whole name
    ('GB02SH', None), ('GB03SL', None), ('GB03PH', None), ('GB03PL', None), ('MX06-1', None), ('gb01', None),
])
def test_a_cat_printer_is_known_by_its_bluetooth_name(name, model):
    found = cat.DRIVER.found_on_bluetooth({'address': MAC, 'name': name, 'le': True, 'services': [], 'manufacturer': []})
    assert found == ((DEVICE, model) if model else None)


@pytest.mark.parametrize('data, model', [
    # Of 5 or 7 bytes ending in 59, as TiMini-Print printer_models.json
    # names those of model v5x
    (bytes.fromhex('0102030459'), None), (bytes.fromhex('01020304050659'), None),
    (bytes.fromhex('010203040506'), 'Cat/MX06'), (bytes.fromhex('010259'), 'Cat/MX06'),
])
def test_an_mx06_of_another_protocol_is_left_alone(data, model):
    found = cat.DRIVER.found_on_bluetooth({'address': MAC, 'name': 'MX06', 'le': True, 'services': [], 'manufacturer': [data]})
    assert found == ((DEVICE, model) if model else None)


def test_a_gb01_with_such_data_is_one_all_the_same():
    device = {'address': MAC, 'name': 'GB01', 'le': True, 'services': [], 'manufacturer': [bytes.fromhex('0102030459')]}
    assert cat.DRIVER.found_on_bluetooth(device) == (DEVICE, 'Cat/GB01')


def uuid16(value):
    return struct.pack('<H', value)


class CatPeer:
    """A cat printer as a raw ATT socket reaches it: it lists AE01 and AE02
    among other characteristics, takes packets written to AE01 and answers
    each state query with its state, notified on AE02 once notifications are
    on. pause_after: bytes after which it asks to pause, for paused_for
    seconds, as flow control or, by_state, as a state; a state answered with
    the pause bit pauses it so too."""

    WRITE, NOTIFY = 0x000e, 0x0010

    def __init__(self, states=(0,), mtu=240, pause_after=None, paused_for=0.02, by_state=False):
        self.states, self.mtu = list(states), mtu
        self.pause_after, self.paused_for, self.by_state = pause_after, paused_for, by_state
        self.declarations = [(0x0002, 0x02, 0x0003, uuid16(0x2a00)), (0x000d, 0x0c, self.WRITE, uuid16(0xae01)),
                             (0x000f, 0x10, self.NOTIFY, uuid16(0xae02)), (0x0012, 0x02, 0x0013, uuid16(0xae10))]
        self.addresses, self.writes, self.stream = [], [], b''
        self.packets, self.incoming, self.notifying = [], [], False
        self.paused, self.written_paused, self.silent = False, 0, False

    def __call__(self, family, kind, proto):
        return self

    def bind(self, address):
        pass

    def connect(self, address):
        self.addresses.append(address)
        self.incoming, self.stream = [bytes([0x02]) + struct.pack('<H', self.mtu)], b''

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def notify(self, data):
        if self.notifying:
            self.incoming.append(bytes([0x1b]) + struct.pack('<H', self.NOTIFY) + data)

    def send(self, pdu):
        assert len(pdu) <= self.mtu
        op = pdu[0]
        if op == 0x02:
            self.incoming.append(bytes([0x03]) + struct.pack('<H', self.mtu))
        elif op == 0x08:
            start, end, kind = struct.unpack_from('<HHH', pdu, 1)
            found = [struct.pack('<HBH', h, p, v) + u for h, p, v, u in self.declarations if start <= h <= end]
            if not found:
                self.incoming.append(bytes([0x01, 0x08]) + struct.pack('<H', start) + b'\x0a')
            else:
                # As many as the MTU takes
                self.incoming.append(bytes([0x09, len(found[0])]) + b''.join(found[:(self.mtu - 2) // len(found[0])]))
        elif op == 0x04:
            start, end = struct.unpack_from('<HH', pdu, 1)
            if start <= self.NOTIFY + 1 <= end:
                self.incoming.append(bytes([0x05, 0x01]) + struct.pack('<H', self.NOTIFY + 1) + uuid16(0x2902))
            else:
                self.incoming.append(bytes([0x01, 0x04]) + struct.pack('<H', start) + b'\x0a')
        elif op == 0x12:
            assert struct.unpack_from('<HH', pdu, 1) == (self.NOTIFY + 1, 1)
            self.notifying = True
            self.incoming.append(bytes([0x13]))
        elif op == 0x52:
            assert struct.unpack_from('<H', pdu, 1)[0] == self.WRITE
            self.written_paused += self.paused
            self.writes.append(pdu[3:])
            self.stream += pdu[3:]
            found, self.stream = protocol.parse(self.stream)
            for command, data in found:
                self.packets.append(protocol.packet(command, data))
                if command == 0xa3 and not self.silent:
                    state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
                    self.notify(answer(state))
                    if state & protocol.PAUSED:
                        self.pause(by_state=True, told=True)
            if self.pause_after is not None and sum(map(len, self.writes)) >= self.pause_after:
                self.pause_after = None
                self.pause(self.by_state)
        return len(pdu)

    def pause(self, by_state, told=False):
        self.paused, self.until = True, time.monotonic() + self.paused_for
        self.resume = answer(0) if by_state else protocol.packet(0xae, b'\x00', 1)
        if not told:
            self.notify(answer(protocol.PAUSED) if by_state else protocol.packet(0xae, b'\x10', 1))

    def recv(self, size):
        if self.paused and time.monotonic() >= self.until:
            self.paused = False
            self.notify(self.resume)
        if not self.incoming:
            raise socket.timeout('timed out')
        return self.incoming.pop(0)

    def sent(self):
        return b''.join(self.writes)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(transports, '_kept', {})
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, 'looked', 0.0)
    monkeypatch.setattr(cat, '_asked', {})
    monkeypatch.setattr(protocol, 'PIECE_PAUSE', 0)
    monkeypatch.setattr(protocol, 'PAUSED_POLL', 0.001)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.2)


@pytest.fixture
def peer(monkeypatch):
    def install(**kwargs):
        fake = CatPeer(**kwargs)
        monkeypatch.setattr(transports.socket, 'socket', fake)
        return fake
    return install


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, model='Cat/GB01', printer=DEVICE)


def send(model, job):
    return cat.DRIVER.send(SimpleNamespace(device_specifier=DEVICE, model=model), job)


def test_a_label_reaches_the_printer_as_cat_printer_sends_it(peer):
    fake = peer()
    assert send('Cat/GB01', [[FIRST_DOT, BLACK]]) == ''
    assert fake.sent().hex() == ''.join(PACKETS)
    assert fake.addresses == [(MAC, 0, 4, socket.BDADDR_LE_PUBLIC)]


def test_each_label_is_printed_as_one_image(peer):
    fake = peer()
    assert send('Cat/MX06', [[BLACK], [FIRST_DOT]]) == ''
    assert fake.sent() == b''.join(protocol.label_packets([BLACK], feed_rows=True) + protocol.label_packets([FIRST_DOT], feed_rows=True))


@pytest.mark.parametrize('mtu, piece', [(240, 200), (23, 20)])
def test_a_label_is_written_in_pieces_of_200_bytes_the_mtu_takes(peer, mtu, piece):
    fake = peer(mtu=mtu)
    assert send('Cat/GB01', [[BLACK] * 40]) == ''
    assert max(map(len, fake.writes)) == piece
    assert fake.sent() == b''.join(protocol.label_packets([BLACK] * 40))


@pytest.mark.parametrize('by_state', [False, True])
def test_nothing_is_written_while_the_printer_asks_to_pause(peer, by_state):
    # By flow control as Cat-Printer reads it, or by its state as kitty-printer
    fake = peer(pause_after=600, by_state=by_state)
    assert send('Cat/GB01', [[BLACK] * 40]) == ''
    assert fake.written_paused == 0 and fake.sent() == b''.join(protocol.label_packets([BLACK] * 40))


def test_a_state_answered_with_the_pause_bit_holds_the_label_until_one_without(peer):
    fake = peer(states=[0, protocol.PAUSED, 0])
    assert send('Cat/GB01', [[BLACK] * 40]) == ''
    assert fake.written_paused == 0 and fake.sent() == b''.join(protocol.label_packets([BLACK] * 40))


@pytest.mark.parametrize('by_state', [False, True])
def test_a_pause_after_a_job_holds_for_the_next_one(peer, monkeypatch, by_state):
    # It comes with the answer to the last state query, the kept link keeps it
    monkeypatch.setattr(protocol, 'PAUSED_POLL', 0.05)
    one = b''.join(protocol.label_packets([BLACK]))
    fake = peer(pause_after=len(one), paused_for=0.2, by_state=by_state)
    assert send('Cat/GB01', [[BLACK]]) == ''
    assert fake.paused
    assert send('Cat/GB01', [[BLACK] * 40]) == ''
    assert fake.written_paused == 0 and fake.sent() == one + b''.join(protocol.label_packets([BLACK] * 40))
    assert len(fake.addresses) == 1


def test_a_printer_that_does_not_go_on_is_given_up(peer, monkeypatch):
    monkeypatch.setattr(protocol, 'RESUME_WAIT', 0.05)
    fake = peer(pause_after=600, paused_for=3600)
    assert send('Cat/GB01', [[BLACK] * 40]) == 'The printer does not answer'
    # The next job opens a new link, without the pause
    fake.paused = False
    assert send('Cat/GB01', [[BLACK]]) == ''
    assert len(fake.addresses) == 2


def test_a_low_battery_stops_no_label(peer):
    fake = peer(states=[protocol.LOW_POWER])
    assert send('Cat/GB01', [[BLACK] * 40]) == ''
    assert fake.sent() == b''.join(protocol.label_packets([BLACK] * 40))


def test_a_printer_without_paper_is_sent_nothing_more(peer):
    fake = peer(states=[protocol.NO_PAPER])
    assert send('Cat/GB01', [[BLACK]]) == 'The printer has no paper'
    assert fake.sent() == protocol.STATE


def test_a_printer_that_runs_out_of_paper_is_sent_no_more(peer):
    # Its answer to the start of the label tells it
    fake = peer(states=[0, protocol.NO_PAPER])
    assert send('Cat/GB01', [[BLACK] * 200, [BLACK]]) == 'The printer has no paper'
    assert len(fake.sent()) < len(b''.join(protocol.label_packets([BLACK] * 200)))


def test_its_last_state_after_a_label_is_told(peer):
    fake = peer(states=[0, 0, protocol.OVERHEATED])
    assert send('Cat/GB01', [[BLACK], [BLACK]]) == 'The printer is too hot, let it cool down'
    assert fake.sent() == b''.join(protocol.label_packets([BLACK]))


def test_a_printer_that_does_not_answer(peer):
    fake = peer()
    fake.silent = True
    assert send('Cat/GB01', [[BLACK]]) == 'The printer does not answer'
    assert fake.sent() == protocol.STATE


def test_a_printer_that_is_off(monkeypatch):
    def off(*args, **kwargs):
        raise OSError(errno.EHOSTDOWN, 'Host is down')
    monkeypatch.setattr(protocol.transports, 'kept_le', off)
    assert send('Cat/GB01', [[BLACK]]) == 'Could not send the label to the printer: The printer cannot be reached'
    assert devices.unanswered_before(DEVICE) is not None


def test_printed_through_the_label_designer(client, peer):
    fake = peer()
    response = client.post('/labeldesigner/api/print', data={'label_size': 'cat48', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    labels = [i for i, p in enumerate(fake.packets) if p == protocol.LATTICE_START]
    assert len(labels) == 2
    rows = fake.packets[labels[0] + 1:fake.packets.index(protocol.LATTICE_END)]
    assert rows and all(p[2] == 0xa2 and len(p) == 56 for p in rows) and any(any(p[6:54]) for p in rows)
    # Asked once, connected once
    assert len(fake.addresses) == 1


def test_status_through_the_label_designer(client, peer):
    peer(states=[protocol.COVER_OPEN])
    printer_ = client.get(STATUS).get_json()['printers'][0]
    assert (printer_['model'], printer_['path'], printer_['errors']) == ('Cat/GB01', DEVICE, ['The cover of the printer is open'])
    assert printer_['status_type'] == 'Error occurred'


def test_ready_through_the_label_designer(client, peer):
    peer()
    printer_ = client.get(STATUS).get_json()['printers'][0]
    assert (printer_['errors'], printer_['status_type'], printer_['battery_low']) == ([], 'Reply to status request', False)


def test_a_low_battery_is_told_as_a_hint(client, peer):
    peer(states=[protocol.LOW_POWER | protocol.NO_PAPER])
    printer_ = client.get(STATUS).get_json()['printers'][0]
    assert (printer_['errors'], printer_['battery_low']) == (['The printer has no paper'], True)


def queries(fake):
    return fake.packets.count(protocol.STATE)


def test_a_ready_printer_is_asked_again_only_when_looking_for_printers(client, peer):
    # Each query keeps it connected and awake
    fake = peer()
    for _ in range(3):
        assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    assert queries(fake) == 1
    client.get(STATUS + '?look=1')
    assert queries(fake) == 2
    # And after a print
    response = client.post('/labeldesigner/api/print', data={'label_size': 'cat48', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    asked = queries(fake)
    client.get(STATUS)
    assert queries(fake) == asked + 1


def test_a_printer_that_cannot_print_is_asked_every_time(client, peer):
    # So that the error goes once paper is in
    fake = peer(states=[protocol.NO_PAPER, protocol.NO_PAPER, 0])
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer has no paper']
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == ['The printer has no paper']
    assert client.get(STATUS).get_json()['printers'][0]['errors'] == []
    assert queries(fake) == 3


def test_600_dpi_is_refused(client):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'cat48', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    assert response.get_json() == {'success': False, 'message': 'The Cat printer GB01 cannot print at 600 dpi, turn off 600 dpi printing'}


def test_only_cat_printers_print_its_paper(client):
    response = client.post('/labeldesigner/api/print', data={'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False


def test_paper_is_torn_off_with_the_top_of_the_label_first():
    caps = cat.DRIVER.capabilities('Cat/MX10')
    assert (caps['cutter'], caps['manual_cutter'], caps['leaves_turned'], caps['vendor']) == (False, False, True, 'Cat printer')
    assert caps['qualities'] == ['quality'] and not caps['high_res']

