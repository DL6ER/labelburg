"""The Niimbot D110_M and B1: their protocol against a fake printer in place
of the RFCOMM socket or the USB printer, and printing and status through the
label designer."""
import errno
import json
import os
import struct

import pytest

from app.labeldesigner import printer, printer_settings
from app.labeldesigner.drivers import devices, niimbot, progress
from app.labeldesigner.drivers.niimbot import calibration, protocol
from tests.helpers import make_client

DEVICE = 'bt://00:F4:BA:17:12:06'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '30'}])
# The RFID tag of a 12 x 40 mm roll
TAG = bytes(8) + b'\x0801222281' + b'\x10PZ1G910304007921' + struct.pack('>HHB', 192, 87, 1)
# The replies of a D110_M as the script this driver came from got them
REPLIES = {0x21: (0x31, b'\x01'), 0x23: (0x33, b'\x01'), 0x01: (0x02, b'\x01'), 0x13: (0x14, b'\x01'),
           0xe3: (0xe4, b'\x01'), 0xa3: (0xb3, struct.pack('>H', 1)), 0xf3: (0xf4, b'\x01'),
           0x1a: (0x1b, TAG), 0xdc: (0xdd, bytes.fromhex('101f0069006932460104')), 0x40: (0x48, struct.pack('>H', 2320))}
# The replies of a B1 where they differ, after the packets niimbluelib logged
# of one: its model ID, a page started and the RFID tag of its roll. Its
# heartbeats of 13 bytes with the lid closed, open and without a roll.
B1_TAG = bytes.fromhex('881d7e4fd9970000') + b'\x0810262260' + b'\x10PZ1G221322004205' + struct.pack('>HHB', 276, 153, 1)
B1_CLOSED = bytes.fromhex('1ff7006100610000' '4f00040001')
B1_OPEN = bytes.fromhex('2017005400540000' '4f01040100')
B1_NO_ROLL = bytes.fromhex('2017008500850000' '4f00040100')
B1_REPLIES = {0x40: (0x48, struct.pack('>H', 4096)), (0x40, 0x0b): (0x4b, b'TEST000001'), 0xdc: (0xdd, B1_CLOSED),
              0x03: (0x04, b'\x01'), 0x1a: (0x1b, B1_TAG)}


class FakePrinter:
    def __init__(self):
        self.commands, self.packets, self.writes, self.pending = [], [], [], b''

    def close(self):
        pass

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        # Several packets may come in one write
        self.writes.append(data)
        while data:
            self._take(data[:data[3] + 7])
            data = data[data[3] + 7:]

    def _take(self, data):
        command = data[2]
        self.commands.append(command)
        self.packets.append(data)
        # Some commands answer by their first byte, e.g. 0x40 by what it asks
        key = (command, data[4]) if (command, data[4]) in REPLIES else command
        # The heartbeat while printing is not waited for
        if key in REPLIES and not (command == 0xdc and 0x01 in self.commands):
            self.pending += protocol.packet(*REPLIES[key])

    def recv(self, size):
        data, self.pending = self.pending, b''
        return data


@pytest.fixture(autouse=True)
def answered(monkeypatch):
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(niimbot, '_asked', {})
    monkeypatch.setattr(niimbot, '_no_serial', set())
    monkeypatch.setattr(devices, 'looked', 0.0)


@pytest.fixture
def fake(monkeypatch):
    printer = FakePrinter()

    def connect(device):
        # The real connection, its socket the fake printer
        c = protocol.Connection.__new__(protocol.Connection)
        c.s, c.received = printer, b''
        return c
    monkeypatch.setattr(protocol, 'connect', connect)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    return printer


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Niimbot/D110_M'
    return client


@pytest.fixture
def b1(client, monkeypatch):
    """The client with a B1 as its printer, which answers as one."""
    client.application.config['PRINTER_MODEL'] = 'Niimbot/B1'
    for command, reply in B1_REPLIES.items():
        monkeypatch.setitem(REPLIES, command, reply)
    return client


def data_of(packet: bytes) -> bytes:
    return packet[4:4 + packet[3]]


def test_packets_as_the_script_sent_them():
    assert protocol.packet(0x21, b'\x03') == bytes.fromhex('555521010323aaaa')
    assert protocol.parse(protocol.packet(0x31, b'\x01') + b'\x55')[0] == [(0x31, b'\x01')]
    assert protocol.row_packets([[0] * 96] * 3) == [protocol.packet(0x84, b'\x00\x00\x03')]
    row = [1] + [0] * 95
    assert protocol.row_packets([row, row]) == [protocol.packet(0x85, bytes([0, 0, 1, 0, 0, 2, 0x80]) + bytes(11))]


def test_label_is_printed_in_the_order_of_the_script(client, fake):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    rows = [c for c in fake.commands if c in (0x84, 0x85)]
    sent = [c for c in fake.commands if c not in (0x84, 0x85)]
    assert rows and sent == [0x40, 0xdc, 0x1a, 0x21, 0x23, 0x01, 0xdc, 0x13, 0xe3, 0xa3, 0xf3]


def test_b1_label_is_printed_as_niimbluelib_prints_one(b1, fake):
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    rows = [c for c in fake.commands if c in (0x84, 0x85)]
    sent = [c for c in fake.commands if c not in (0x84, 0x85)]
    # Its serial number asked after its roll
    assert rows and sent == [0x40, 0xdc, 0x1a, 0x40, 0x21, 0x23, 0x01, 0x03, 0x13, 0xe3, 0xa3, 0xf3]
    packets = {packet[2]: data_of(packet) for packet in fake.packets}
    # One page of 30 mm, rows across the whole printhead
    assert packets[0x01] == struct.pack('>H5B', 1, 0, 0, 0, 0, 0)
    assert packets[0x13] == struct.pack('>HHH', 240, 384, 1)
    # The label type of the roll
    assert packets[0x23] == b'\x01'


class UsbPrinter:
    """The fake printer behind the printer backend of USB and USB/IP."""

    def __init__(self, printer):
        self.printer, self.disposed = printer, False

    def write(self, data):
        self.printer.sendall(data)

    def read(self, length=32):
        return self.printer.recv(length)

    def dispose(self):
        self.disposed = True


def test_b1_on_usb_prints_with_the_packets_of_bluetooth(b1, monkeypatch):
    printer, opened = FakePrinter(), []

    def open_printer(device):
        opened.append(UsbPrinter(printer))
        return opened[-1]
    monkeypatch.setattr(protocol.backends, 'open_printer', open_printer)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    # Not asked over the network for a device ID
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device: '')
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: '')
    b1.application.config['PRINTER_PRINTER'] = 'usbip://192.0.2.7/1-4.1.3'
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    sent = [c for c in printer.commands if c not in (0x84, 0x85)]
    assert sent == [0x40, 0xdc, 0x1a, 0x40, 0x21, 0x23, 0x01, 0x03, 0x13, 0xe3, 0xa3, 0xf3]
    assert opened and all(p.disposed for p in opened)


def test_b1_on_usb_that_does_not_answer(monkeypatch):
    printer = UsbPrinter(FakePrinter())
    printer.read = lambda length=32: b''
    monkeypatch.setattr(protocol.backends, 'open_printer', lambda device: printer)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    with protocol.connect('usb://0x3513:0x0002') as c:
        with pytest.raises(protocol.NoAnswer):
            protocol.heartbeat(c)
    assert printer.disposed


def test_b1_takes_its_own_rolls():
    driver = niimbot.DRIVER
    sizes = {label.identifier: label for label in driver.labels}
    assert driver.fits(sizes['niimbot50x30'], 'Niimbot/B1') and not driver.fits(sizes['niimbot50x30'], 'Niimbot/D110_M')
    assert driver.fits(sizes['niimbot12x40'], 'Niimbot/D110_M') and not driver.fits(sizes['niimbot12x40'], 'Niimbot/B1')
    # 50 mm wide, 48 mm of it printed
    assert (sizes['niimbot50x30'].dots_total, sizes['niimbot50x30'].dots_printable) == ((400, 240), (384, 240))
    assert driver.label_names['niimbot40x30'] == '40 x 30 mm die-cut (Niimbot)'


class _Black:
    """A label all black, w x h dots as the label designer draws it."""
    label_content = None

    def __init__(self, w, h, start=None):
        self.size, self.start = (w, h), start

    def generate(self):
        from PIL import Image
        img = Image.new('RGB', self.size, 'black')
        if self.start:
            # Only its first columns black
            img.paste((255, 255, 255), (self.start, 0) + self.size)
        return img


def test_b1_rows_span_the_printhead_with_the_label_in_the_middle():
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30')
    assert (len(rows), len(rows[0])) == (240, 384)
    # Moved left and down by the shifts of the B1, as long as the label
    assert rows[:2] == [[0] * 384] * 2
    assert rows[2] == rows[-1] == [0] * 30 + [1] * 320 + [0] * 34


def test_b1_rows_follow_the_offset_of_the_printer():
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30', (0.5, -0.25))
    assert len(rows) == 240 and rows[0] == [0] * 34 + [1] * 320 + [0] * 30


def test_b1_moved_up_keeps_the_length_of_the_label():
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30', (0, -0.5))
    assert len(rows) == 240 and rows[-2:] == [[0] * 384] * 2 and rows[0][40] == 1


def test_b1_moved_far_is_cut_at_the_printhead():
    _, rows = niimbot.label_rows(_Black(384, 240), 'niimbot50x30', (5.0, 0))
    assert rows[2] == [0] * 38 + [1] * 346


def test_d110_m_ignores_an_offset():
    # Its start black, which leaves the printer last
    assert niimbot.label_rows(_Black(320, 96, start=10), 'niimbot12x40', (1, 1))[1] == niimbot.label_rows(_Black(320, 96, start=10), 'niimbot12x40')[1]


def test_b1_rows_beyond_the_printhead_are_left_out():
    # 50 mm across are printed on the middle 48 mm, moved left by the shift
    assert niimbot.centered([1] * 8 + [0] * 384 + [1] * 8, 384) == [0] * 384
    assert niimbot.centered([1] * 10 + [0] * 380 + [1] * 10, 384, 2) == [0] * 380 + [1] * 4
    assert niimbot.centered([1] * 400, 384) == [1] * 384


def test_rows_tell_the_dots_in_each_third_of_a_wide_printhead():
    row = [1] * 128 + [0] * 128 + [1] * 64 + [0] * 64
    packet = protocol.row_packets([row], 384)[0]
    assert packet[2] == 0x85
    # Row 0, the dots in each 16 bytes, once, then the 48 bytes
    assert data_of(packet)[:6] == struct.pack('>H3BB', 0, 128, 0, 64, 1)
    assert len(data_of(packet)) == 6 + 48


def test_printer_of_another_model_is_refused(b1, fake, monkeypatch):
    # Its print sequence and printhead would not fit
    monkeypatch.setitem(REPLIES, 0x40, (0x48, struct.pack('>H', 2320)))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'This printer is a Niimbot D110_M, choose that model for it'
    assert 0x01 not in fake.commands


def test_printer_that_does_not_tell_its_model_prints(b1, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0x40)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.1)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}


def test_b1_tells_its_battery(b1, fake, monkeypatch):
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['errors'], printer_['battery_low']) == ('Niimbot/B1', [], False)
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, B1_CLOSED[:10] + bytes([1]) + B1_CLOSED[11:]))
    printer_ = b1.get('/labeldesigner/api/printer_status?look=1').get_json()['printers'][0]
    assert printer_['battery_low'] is True


def test_labels_are_counted_while_printing(client, fake):
    # Noted at the start of each label
    seen = []
    send = fake.sendall
    fake.sendall = lambda data: (data[2] == 0x01 and seen.append(progress.state(DEVICE))) or send(data)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT, 'print_count': '3'})
    assert response.get_json() == {'success': True}
    assert [(state['label'], state['labels']) for state in seen] == [(1, 3), (2, 3), (3, 3)]
    assert all(state['seconds_left'] >= 0 for state in seen)
    assert progress.state(DEVICE) is None


def test_rows_are_as_wide_as_the_label_across_the_printhead():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # Its start black
            img = Image.new('RGB', (320, 96), 'white')
            img.paste((0, 0, 0), (0, 0, 10, 96))
            return img
    _, rows = niimbot.label_rows(_Label(), 'niimbot12x40')
    assert (len(rows), len(rows[0])) == (320, 96)
    # Its end leaves the printer first, as on the other printers
    assert not any(rows[0]) and all(rows[-1])


def test_status_through_the_label_designer(client, fake):
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['path'], printer_['errors']) == ('Niimbot/D110_M', DEVICE, [])
    assert fake.commands == [0xdc, 0x1a]


def test_roll_without_its_rfid_tag_is_an_error(client, fake, monkeypatch):
    # As a D110_M answered with the tag peeled off the roll, or no roll
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, b'\x00'))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['No Niimbot roll with its RFID tag is in the printer']
    assert printer_['media_width'] is None


def test_status_tells_the_size_of_a_known_roll(client, fake, monkeypatch):
    tag = bytes(8) + b'\x0801222281' + b'\x10PZ1G910304007921' + struct.pack('>HHB', 192, 87, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['media_width'], printer_['media_length']) == (12, 40)


def test_b1_status_tells_the_size_of_its_roll(b1, fake):
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['media_width'], printer_['media_length']) == (50, 30)


def test_printer_is_asked_only_when_looking_for_printers(client, fake):
    # It beeps at each connection, the designer asks for the status every 20 s
    client.get('/labeldesigner/api/printer_status')
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands == [0xdc, 0x1a]
    assert client.get('/labeldesigner/api/printer_status?look=1').get_json()['printers'][0]['errors'] == []
    assert fake.commands == [0xdc, 0x1a] * 2
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands == [0xdc, 0x1a] * 2


def beat(lid=1, battery=3):
    # As a D110_M answers, 1 when its lid is closed
    return (0xdd, bytes.fromhex('101f0069006932460000')[:8] + bytes([lid, battery]))


@pytest.mark.parametrize('battery, low, percent', [(0, True, 0), (1, True, 25), (2, False, 50), (4, False, 100)])
def test_status_tells_the_battery_in_percent(client, fake, monkeypatch, battery, low, percent):
    monkeypatch.setitem(REPLIES, 0xdc, beat(battery=battery))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['battery_low'], printer_['battery']) == (low, percent)


def test_open_lid_is_an_error(client, fake, monkeypatch):
    error = 'The cover of the printer is open'
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10220054005432460004')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == [error]
    # Not printed then
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == error
    assert 0x21 not in fake.commands


def test_lid_closed_again_is_told_at_the_next_status_query(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10220054005432460004')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The cover of the printer is open']
    # Closed, without "Look for printers"
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('101f0069006932460104')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []
    # Then the kept state is told, the printer beeps at each connection
    sent = len(fake.commands)
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands[sent:] == []


def test_no_roll_is_not_an_open_lid(client, fake, monkeypatch):
    # As a D110_M answered with the lid closed and no roll inside
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10200097009732460104')))
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, b'\x00'))
    errors = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors']
    assert errors == ['No Niimbot roll with its RFID tag is in the printer']


def test_b1_tells_its_serial_number_and_its_roll(b1, fake):
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['serial'], printer_['roll']) == ('TEST000001', 'PZ1G221322004205')


def test_d110_m_is_not_asked_for_its_serial_number(client, fake):
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['serial'] is None and printer_['roll'] == 'PZ1G910304007921'
    assert not any(packet[2] == 0x40 and packet[4] == 0x0b for packet in fake.packets)


def test_b1_with_its_lid_open(b1, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, B1_OPEN))
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, b'\x00'))
    assert b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The cover of the printer is open']
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The cover of the printer is open'
    assert 0x21 not in fake.commands


def test_b1_without_a_roll(b1, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, B1_NO_ROLL))
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, b'\x00'))
    errors = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors']
    assert errors == ['No Niimbot roll with its RFID tag is in the printer']


def test_printer_that_stops_answering(client, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0xe3)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The printer does not answer'
    # Asked again at the next status query
    sent = len(fake.commands)
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands[sent:sent + 1] == [0xdc]


def test_printer_that_does_not_tell_it_printed(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xa3, (0xb3, struct.pack('>H', 0)))
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The printer did not tell that it printed the label'


def test_roll_without_its_rfid_tag_is_refused(client, fake, monkeypatch):
    # As a D110_M answered with the tag peeled off the roll
    monkeypatch.setitem(REPLIES, 0x01, (protocol.ERROR, b'\x14'))
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The printer prints only on Niimbot rolls with their RFID tag'


def test_tag_laid_out_otherwise_is_an_unknown_roll(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, bytes(8) + b'\x0801'))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['errors'], printer_['media_width']) == ([], None)


def test_print_updates_the_status(client, fake, monkeypatch):
    client.get('/labeldesigner/api/printer_status')
    # Another roll was loaded since
    tag = bytes(8) + b'\x0801222281' + b'\x10PZ1G910304007921' + struct.pack('>HHB', 192, 87, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    client.post('/labeldesigner/api/print', data={'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['media_width'], printer_['media_length']) == (12, 40)


def test_print_that_could_not_reach_the_printer_asks_it_again(client, fake, monkeypatch):
    client.get('/labeldesigner/api/printer_status')

    def unreachable(device):
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    client.post('/labeldesigner/api/print', data={'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    # No longer Ready
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']


def test_printer_that_is_off_is_not_looked_for_at_every_status_query(client, monkeypatch):
    calls = []

    def unreachable(device):
        calls.append(device)
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    for _ in range(2):
        assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert calls == [DEVICE]


def test_bluetooth_connections_are_set_up_one_at_a_time(monkeypatch):
    # The radio pages one device after another, a printer that was off made
    # the others give up waiting
    import threading
    import time
    from app.labeldesigner.drivers.puqu import protocol as puqu
    paging, most = [], []

    class Socket:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            paging.append(address)
            most.append(len(paging))
            time.sleep(0.05)
            paging.remove(address)

        def close(self):
            pass
    monkeypatch.setattr(protocol.socket, 'socket', Socket)
    threads = [threading.Thread(target=protocol.Connection, args=(DEVICE,)),
               threading.Thread(target=puqu.connect, args=('bt://CD:AC:BB:E0:46:4C/2',))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(most) == 1 and len(most) == 2


def _calibration_page(img):
    return img.point(lambda v: 0 if v < 128 else 255)


@pytest.mark.parametrize('label_size, page', [('niimbot40x30', lambda: calibration.narrow(40, 30)),
                                              ('niimbot50x30', lambda: calibration.wide(50, 30))])
def test_calibration_label_is_placed_as_the_printer_is_calibrated(label_size, page):
    from types import SimpleNamespace
    job = niimbot.DRIVER.calibration_job(SimpleNamespace(label_size=label_size, offset=(-0.5, 0.25)))
    assert job == [niimbot.placed(_calibration_page(page()), 384, 2 + 4, 2 + 2)]


def test_b1_prints_its_calibration_label(b1, fake, tmp_path):
    b1.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)
    r = b1.post('/labeldesigner/api/calibration/print', data={'printer': DEVICE, 'model': 'Niimbot/B1', 'label_size': 'niimbot40x30'})
    assert r.get_json() == {'success': True}
    packets = {packet[2]: data_of(packet) for packet in fake.packets}
    assert packets[0x13] == struct.pack('>HHH', 240, 384, 1)


def test_d110_m_prints_no_calibration_label(client, fake):
    r = client.post('/labeldesigner/api/calibration/print', data={'printer': DEVICE, 'label_size': 'niimbot12x40'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.NO_CALIBRATION
    assert fake.commands == []


@pytest.mark.parametrize('kind, status', [('narrow', 200), ('wide', 200), ('other', 400)])
def test_example_of_the_calibration_label(b1, kind, status):
    r = b1.get('/labeldesigner/api/calibration/example.png', query_string={'kind': kind})
    assert r.status_code == status
    assert (r.mimetype == 'image/png') is (status == 200)


def test_b1_refusing_its_serial_number_still_prints(b1, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, (0x40, 0x0b), (protocol.ERROR, b'\x01'))
    printer_ = b1.get('/labeldesigner/api/printer_status?look=1').get_json()['printers'][0]
    assert (printer_['errors'], printer_['serial']) == ([], None)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}


def test_b1_not_telling_its_serial_number_is_not_asked_again(b1, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, (0x40, 0x0b))
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    b1.get('/labeldesigner/api/printer_status?look=1')
    b1.get('/labeldesigner/api/printer_status?look=1')
    assert sum(packet[2] == 0x40 and packet[4] == 0x0b for packet in fake.packets) == 1


def test_roll_serial_number_other_than_letters_and_digits_is_left_out(b1, fake, monkeypatch):
    tag = B1_TAG.replace(b'PZ1G221322004205', b'PZ1-TEST00000001')
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    assert b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['roll'] is None


@pytest.mark.parametrize('across, column', [(-1.0, range(0, 6)), (1.0, range(372, 384))])
def test_wide_frame_stays_on_the_paper_when_moved_1_mm(across, column):
    from types import SimpleNamespace
    [rows] = niimbot.DRIVER.calibration_job(SimpleNamespace(label_size='niimbot50x30', offset=(across, 0)))
    assert any(rows[120][x] for x in column)


B1_DEVICE_ID = 'MFG:NIIMBOT;CMD:ESC;MDL: B1;'


@pytest.mark.parametrize('device', ['file:///dev/usb/lp0', '/dev/usb/lp1', 'usb://0x3513:0x0002'])
def test_b1_plugged_in_is_known_by_its_device_id(monkeypatch, device):
    from app.labeldesigner import drivers
    monkeypatch.setattr(devices, 'usb_device_id', lambda d: B1_DEVICE_ID)
    assert drivers.resolve(device, 'Unknown') == (niimbot.DRIVER, 'Niimbot/B1')
    # Listed when found, a request may name it
    assert any(pattern.fullmatch(device) for pattern in niimbot.DRIVER.device_patterns)
    monkeypatch.setattr(devices, 'usb_device_id', lambda d: 'MFG:Brother;CMD:PT-CBP;MDL:QL-800;')
    assert niimbot.DRIVER.model_of(device) is None


def test_printer_refusing_its_model_id_still_prints(b1, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, (0x40, 0x08), (protocol.ERROR, b'\x01'))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}


def test_roll_serial_number_of_other_letters_is_left_out(b1, fake, monkeypatch):
    # 16 bytes as the tag tells them, 15 letters with one outside A-Z
    tag = B1_TAG.replace(b'PZ1G221322004205', 'PZ1\u00c4TEST0000001'.encode())
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    assert b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['roll'] is None


def test_usb_read_waits_no_longer_than_its_time(monkeypatch):
    # usblp blocks a read until the printer answers, one that never does
    # must not hold the printer
    import threading
    from app.labeldesigner.drivers import backends
    read_end, write_end = os.pipe()
    printer = backends.KernelPrinter.__new__(backends.KernelPrinter)
    printer.dev = read_end
    monkeypatch.setattr(protocol.backends, 'open_printer', lambda device: printer)
    usb = protocol.UsbSocket('/dev/usb/lp0')
    usb.settimeout(0.1)
    outcome = []

    def read():
        try:
            outcome.append(usb.recv(64))
        except OSError as e:
            outcome.append(e)
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(2)
    try:
        assert not thread.is_alive() and isinstance(outcome[0], TimeoutError)
    finally:
        os.close(write_end)
        thread.join(2)
        usb.close()


@pytest.mark.parametrize('code, message', [(0x01, 'The cover of the printer is open'),
                                           (0x02, 'The printer has no labels'),
                                           (0x03, 'The battery of the printer is low'),
                                           (0x07, 'The printhead is too hot')])
def test_error_answer_while_printing_tells_why(b1, fake, monkeypatch, code, message):
    # As a B1 answered at the end of the page with its cover opened
    monkeypatch.setitem(REPLIES, 0xe3, (protocol.ERROR, bytes([code])))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.status_code == 503 and response.get_json()['message'] == message


def test_error_answer_left_from_a_stopped_job_is_not_the_status(b1, fake):
    # A B1 answered the first heartbeat after a job stopped by its opened
    # cover with error 6, the next one as usual
    fake.pending = protocol.packet(protocol.ERROR, b'\x06')
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == [] and printer_['battery'] == 100


def test_printer_that_stays_silent_does_not_answer(b1, fake, monkeypatch):
    # A B1 switched off on USB stays on the bus, charged by the cable
    monkeypatch.delitem(REPLIES, 0xdc)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer does not answer']


@pytest.fixture
def b1_on_usb(b1, monkeypatch):
    b1.application.config['PRINTER_PRINTER'] = 'usbip://192.0.2.7/1-4.1.3'
    # Not asked over the network for a device ID
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device: '')
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: '')
    return b1


def test_b1_on_usb_tells_another_roll_at_the_next_status_query(b1_on_usb, fake, monkeypatch):
    # It does not beep when asked, the label designer asks every 20 s
    status = '/labeldesigner/api/printer_status'
    assert b1_on_usb.get(status).get_json()['printers'][0]['roll'] == 'PZ1G221322004205'
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, B1_TAG.replace(b'PZ1G221322004205', b'PZ1G221322004206')))
    assert b1_on_usb.get(status).get_json()['printers'][0]['roll'] == 'PZ1G221322004206'


def test_b1_on_usb_gone_is_told_at_the_next_status_query(b1_on_usb, fake, monkeypatch):
    status = '/labeldesigner/api/printer_status'
    assert b1_on_usb.get(status).get_json()['printers'][0]['errors'] == []

    # As a host that stopped sharing the B1 over USB/IP
    def gone(device):
        raise OSError(errno.ENODEV, '192.0.2.7 shares no device at 1-4.1.3')
    monkeypatch.setattr(protocol, 'connect', gone)
    errors = b1_on_usb.get(status).get_json()['printers'][0]['errors']
    assert errors == ['The printer is switched off or not connected']


@pytest.mark.parametrize('listed', ['configured', 'found', 'only'])
def test_status_of_one_printer_asks_only_that_one(b1_on_usb, fake, monkeypatch, listed):
    # The label designer follows a printer on USB closely, the others less
    usb = b1_on_usb.application.config['PRINTER_PRINTER']
    if listed == 'configured':
        b1_on_usb.application.config['PRINTERS'] = [('Niimbot/D110_M', DEVICE), ('Niimbot/B1', usb)]
    elif listed == 'found':
        b1_on_usb.application.config['PRINTER_PRINTER'] = '?'
        monkeypatch.setattr(printer, '_cached_printers', [{'path': DEVICE, 'model': 'Niimbot/D110_M'},
                                                          {'path': usb, 'model': 'Niimbot/B1'}])
    response = b1_on_usb.get('/labeldesigner/api/printer_status', query_string={'printer': usb})
    printers = response.get_json()['printers']
    assert [(p['path'], p['roll'], p['label_size']) for p in printers] == [(usb, 'PZ1G221322004205', 'niimbot50x30')]
    assert fake.commands == [0xdc, 0x1a, 0x40]


def test_status_of_a_printer_not_listed_is_refused(b1_on_usb, fake):
    response = b1_on_usb.get('/labeldesigner/api/printer_status', query_string={'printer': 'usbip://192.0.2.8/1-1'})
    assert response.status_code == 404 and fake.commands == []


def test_b1_gets_the_rows_of_a_label_in_one_write(b1, fake):
    # Over USB/IP each write waits for the host, row by row a label took seconds
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    rows = [w for w in fake.writes if w[2] in (0x84, 0x85)]
    assert len(rows) == 1 and len(protocol.parse(rows[0])[0]) > 1


def test_d110_m_gets_each_row_on_its_own(client, fake):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    rows = [w for w in fake.writes if w[2] in (0x84, 0x85)]
    assert len(rows) > 1 and all(len(protocol.parse(w)[0]) == 1 for w in rows)


def test_b1_on_usb_leaving_one_question_unanswered_is_asked_again(b1_on_usb, fake, monkeypatch):
    # Every few minutes a B1 answered a status question not, the next one it did
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    take, missed = fake._take, []

    def once(data):
        if data[2] == 0xdc and not missed:
            return missed.append(data)
        take(data)
    monkeypatch.setattr(fake, '_take', once)
    printer_ = b1_on_usb.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert missed and printer_['errors'] == []


def test_b1_on_usb_that_stays_silent_does_not_answer(b1_on_usb, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0xdc)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    printer_ = b1_on_usb.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer does not answer']
