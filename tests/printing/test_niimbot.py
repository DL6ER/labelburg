"""The Niimbot D110_M and B1: their protocol against a fake printer in place
of the RFCOMM socket or the USB printer, and printing and status through the
label designer."""
import errno
import json
import os
import struct
import time
from types import SimpleNamespace

import pytest

from app.labeldesigner import printer, printer_settings
from app.labeldesigner.drivers import devices, niimbot, progress, transports, usbip
from app.labeldesigner.drivers.niimbot import calibration, protocol
from tests.helpers import make_client, outcome, without_status

DEVICE = 'bt://00:F4:BA:17:12:06'
# A B1 on USB, shared over USB/IP
B1_DEVICE = 'usbip://192.0.2.7/1-4.1.3'
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
    monkeypatch.setattr(niimbot, '_owed', {})
    monkeypatch.setattr(devices, 'looked', 0.0)


@pytest.fixture
def fake(monkeypatch):
    printer = FakePrinter()

    def connect(device, le=False):
        # The real connection, its socket the fake printer
        c = protocol.Connection.__new__(protocol.Connection)
        c.s, c.received = printer, b''
        return c
    monkeypatch.setattr(protocol, 'connect', connect)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    return printer


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, model='Niimbot/D110_M', printer=DEVICE)


@pytest.fixture
def b1(client, monkeypatch):
    """The client with a B1 as its printer, which answers as one."""
    client.application.config['PRINTER_MODEL'] = 'Niimbot/B1'
    client.application.config['PRINTER_PRINTER'] = B1_DEVICE
    # Not asked over the network for a device ID, nor imported for it
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: '')
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: '')
    monkeypatch.setattr(usbip, 'connect', lambda device: SimpleNamespace(device_id=''))
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


def test_label_is_printed_in_the_order_of_the_script(client, fake, monkeypatch):
    without_status(monkeypatch)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    rows = [c for c in fake.commands if c in (0x84, 0x85)]
    sent = [c for c in fake.commands if c not in (0x84, 0x85)]
    assert rows and sent == [0x40, 0xdc, 0x1a, 0x21, 0x23, 0x01, 0xdc, 0x13, 0xe3, 0xa3, 0xf3]


def test_b1_label_is_printed_as_niimbluelib_prints_one(b1, fake, monkeypatch):
    without_status(monkeypatch)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
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


def test_b1_starts_the_next_label_once_it_takes_it(b1, fake, monkeypatch):
    without_status(monkeypatch)
    # Right after a label ended, the B1 on USB refuses the start of the next one once
    take = fake._take

    def refusing(data):
        if data[2] == 0x21 and fake.commands[-1:] == [0xf3]:
            fake.commands.append(0x21)
            fake.pending += protocol.packet(0x31, b'\x00')
        else:
            take(data)
    monkeypatch.setattr(fake, '_take', refusing)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(b1, response) == ('printed', '')
    assert fake.commands.count(0xf3) == 2 and fake.commands.count(0x21) == 3


def test_b1_refusing_a_label_for_longer_is_not_waited_for(b1, fake, monkeypatch):
    without_status(monkeypatch)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    monkeypatch.setitem(REPLIES, 0x21, (0x31, b'\x00'))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert (response.status_code, outcome(b1, response)[0]) == (200, 'failed')
    assert fake.commands.count(0x21) > 1 and 0x23 not in fake.commands


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


def test_b1_prints_with_the_packets_of_bluetooth(b1, monkeypatch):
    without_status(monkeypatch)
    printer, opened = FakePrinter(), []

    def open_printer(device):
        opened.append(UsbPrinter(printer))
        return opened[-1]
    monkeypatch.setattr(transports.backends, 'open_printer', open_printer)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    # Not asked over the network for a device ID
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: '')
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: '')
    b1.application.config['PRINTER_PRINTER'] = B1_DEVICE
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    sent = [c for c in printer.commands if c not in (0x84, 0x85)]
    assert sent == [0x40, 0xdc, 0x1a, 0x40, 0x21, 0x23, 0x01, 0x03, 0x13, 0xe3, 0xa3, 0xf3]
    assert opened and all(p.disposed for p in opened)


def test_b1_that_does_not_answer(monkeypatch):
    printer = UsbPrinter(FakePrinter())
    printer.read = lambda length=32: b''
    monkeypatch.setattr(transports.backends, 'open_printer', lambda device: printer)
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
    assert rows[:2] == [bytes(384)] * 2
    assert rows[2] == rows[-1] == bytes(30) + b'\x01' * 320 + bytes(34)


def test_b1_rows_follow_the_offset_of_the_printer():
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30', (0.5, -0.25))
    assert len(rows) == 240 and rows[0] == bytes(34) + b'\x01' * 320 + bytes(30)


def test_b1_moved_up_keeps_the_length_of_the_label():
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30', (0, -0.5))
    assert len(rows) == 240 and rows[-2:] == [bytes(384)] * 2 and rows[0][40] == 1


def test_b1_moved_far_is_cut_at_the_printhead():
    _, rows = niimbot.label_rows(_Black(384, 240), 'niimbot50x30', (5.0, 0))
    assert rows[2] == bytes(38) + b'\x01' * 346


def test_d110_m_ignores_an_offset():
    # Its start black, which leaves the printer last
    assert niimbot.label_rows(_Black(320, 96, start=10), 'niimbot12x40', (1, 1))[1] == niimbot.label_rows(_Black(320, 96, start=10), 'niimbot12x40')[1]


def test_b1_rows_beyond_the_printhead_are_left_out():
    # 50 mm across are printed on the middle 48 mm, moved left by the shift
    assert niimbot.centered(b'\x01' * 8 + bytes(384) + b'\x01' * 8, 384) == bytes(384)
    assert niimbot.centered(b'\x01' * 10 + bytes(380) + b'\x01' * 10, 384, 2) == bytes(380) + b'\x01' * 4
    assert niimbot.centered(b'\x01' * 400, 384) == b'\x01' * 384


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
    assert outcome(b1, response) == ('failed', 'This printer is a Niimbot D110_M, choose that model for it')
    assert 0x01 not in fake.commands


def test_printer_that_does_not_tell_its_model_prints(b1, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0x40)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.1)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')


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
    assert outcome(client, response) == ('printed', '')
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
    assert outcome(client, response) == ('failed', error)
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
    assert outcome(b1, response) == ('failed', 'The cover of the printer is open')
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
    assert outcome(client, response) == ('failed', 'The printer does not answer')
    # Asked again at the next status query
    sent = len(fake.commands)
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands[sent:sent + 1] == [0xdc]


def test_printer_that_does_not_tell_it_printed(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xa3, (0xb3, struct.pack('>H', 0)))
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer did not tell that it printed the label')


def test_roll_without_its_rfid_tag_is_refused(client, fake, monkeypatch):
    # As a D110_M answered with the tag peeled off the roll
    monkeypatch.setitem(REPLIES, 0x01, (protocol.ERROR, b'\x14'))
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer prints only on Niimbot rolls with their RFID tag')


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

    def unreachable(device, le=False):
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    client.post('/labeldesigner/api/print', data={'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    # No longer Ready
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']


def test_printer_that_is_off_is_not_looked_for_at_every_status_query(client, monkeypatch):
    calls = []

    def unreachable(device, le=False):
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
                                              ('niimbot50x30', lambda: calibration.wide(50, 30)),
                                              ('niimbotd50', lambda: calibration.circle(50))])
def test_calibration_label_is_placed_as_the_printer_is_calibrated(label_size, page):
    from types import SimpleNamespace
    job = niimbot.DRIVER.calibration_job(SimpleNamespace(label_size=label_size, offset=(-0.5, 0.25)))
    assert job == [niimbot.placed(_calibration_page(page()), 384, 2 + 4, 2 + 2)]


def test_b1_prints_its_calibration_label(b1, fake, tmp_path):
    b1.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)
    r = b1.post('/labeldesigner/api/calibration/print', data={'printer': B1_DEVICE, 'model': 'Niimbot/B1', 'label_size': 'niimbot40x30'})
    assert outcome(b1, r) == ('printed', '')
    packets = {packet[2]: data_of(packet) for packet in fake.packets}
    assert packets[0x13] == struct.pack('>HHH', 240, 384, 1)


def test_d110_m_prints_no_calibration_label(client, fake):
    r = client.post('/labeldesigner/api/calibration/print', data={'printer': DEVICE, 'label_size': 'niimbot12x40'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.NO_CALIBRATION
    assert fake.commands == []


@pytest.mark.parametrize('kind, status', [('narrow', 200), ('wide', 200), ('round', 200), ('other', 400)])
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
    assert outcome(b1, response) == ('printed', '')


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
    assert outcome(b1, response) == ('printed', '')


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
    monkeypatch.setattr(transports.backends, 'open_printer', lambda device: printer)
    usb = transports.UsbLink('/dev/usb/lp0')
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
    assert (response.status_code, outcome(b1, response)) == (200, ('failed', message))


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


def test_b1_tells_another_roll_at_the_next_status_query(b1, fake, monkeypatch):
    # It does not beep when asked, the label designer asks every 20 s
    status = '/labeldesigner/api/printer_status'
    assert b1.get(status).get_json()['printers'][0]['roll'] == 'PZ1G221322004205'
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, B1_TAG.replace(b'PZ1G221322004205', b'PZ1G221322004206')))
    assert b1.get(status).get_json()['printers'][0]['roll'] == 'PZ1G221322004206'


def test_b1_gone_is_told_at_the_next_status_query(b1, fake, monkeypatch):
    status = '/labeldesigner/api/printer_status'
    assert b1.get(status).get_json()['printers'][0]['errors'] == []

    # As a host that stopped sharing the B1 over USB/IP
    def gone(device, le=False):
        raise OSError(errno.ENODEV, '192.0.2.7 shares no device at 1-4.1.3')
    monkeypatch.setattr(protocol, 'connect', gone)
    errors = b1.get(status).get_json()['printers'][0]['errors']
    assert errors == ['The printer is switched off or not connected']


@pytest.mark.parametrize('listed', ['configured', 'found', 'only'])
def test_status_of_one_printer_asks_only_that_one(b1, fake, monkeypatch, listed):
    # The label designer follows a printer on USB closely, the others less
    usb = b1.application.config['PRINTER_PRINTER']
    if listed == 'configured':
        b1.application.config['PRINTERS'] = [('Niimbot/D110_M', DEVICE), ('Niimbot/B1', usb)]
    elif listed == 'found':
        b1.application.config['PRINTER_PRINTER'] = '?'
        monkeypatch.setattr(printer, '_cached_printers', [{'path': DEVICE, 'model': 'Niimbot/D110_M'},
                                                          {'path': usb, 'model': 'Niimbot/B1'}])
    response = b1.get('/labeldesigner/api/printer_status', query_string={'printer': usb})
    printers = response.get_json()['printers']
    assert [(p['path'], p['roll'], p['label_size']) for p in printers] == [(usb, 'PZ1G221322004205', 'niimbot50x30')]
    assert fake.commands == [0xdc, 0x1a, 0x40]


def test_status_of_a_printer_not_listed_is_refused(b1, fake):
    response = b1.get('/labeldesigner/api/printer_status', query_string={'printer': 'usbip://192.0.2.8/1-1'})
    assert response.status_code == 404 and fake.commands == []


def _roll(barcode: bytes, kind: int = 1) -> bytes:
    """The RFID tag of a roll by its barcode."""
    return bytes.fromhex('881d137bdf181080') + bytes([len(barcode)]) + barcode + b'\x10PZ0G000000000001' + struct.pack('>HHB', 552, 5, kind)


@pytest.mark.parametrize('barcode, label_size', [(b'052325253', 'niimbot15x8-2x3'), (b'052325254', 'niimbot13x35-2x1'),
                                                 (b'052325255', 'niimbot30x15-1x2')])
def test_b1_tells_the_page_of_a_roll_with_several_labels_to_a_page(b1, fake, monkeypatch, barcode, label_size):
    # Offered as the whole page, the label designer shows where it is cut
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, _roll(barcode)))
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['label_size'], printer_['label_size_assumed']) == (label_size, False)


def test_pages_of_several_labels_know_their_labels():
    # Lanes across, rows along, the gap between lanes in mm
    pages = {label.identifier: (label.tape_size, label.pieces) for label in niimbot.LABELS if label.pieces != (1, 1, 0)}
    assert pages == {'niimbot30x15-1x2': ((30, 30), (1, 2, 0)), 'niimbot15x8-2x3': ((30, 24), (2, 3, 0)),
                     'niimbot13x35-2x1': ((28, 35), (2, 1, 2))}
    assert niimbot.NAMES['niimbot13x35-2x1'] == '28 x 35 mm, 2 labels of 13 x 35 (Niimbot)'


def test_page_of_two_labels_prints_whole_on_a_b1(b1, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, _roll(b'052325255', kind=5)))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot30x15-1x2', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    # 30 mm along, across the printhead, on transparent labels
    size = struct.unpack('>HHH', data_of(next(p for p in fake.packets if p[2] == 0x13))[:6])
    assert size[:2] == (240, 384) and data_of(next(p for p in fake.packets if p[2] == 0x23)) == b'\x05'


@pytest.mark.parametrize('barcode, label_size', [(b'10262260', 'niimbot50x30'), (b'080924001', 'niimbot50x80')])
def test_b1_tells_the_labels_of_its_roll_by_the_barcode(b1, fake, monkeypatch, barcode, label_size):
    tag = bytes.fromhex('881d137bdf181080') + bytes([len(barcode)]) + barcode + b'\x10PZ0G000000000001' + struct.pack('>HHB', 114, 0, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['label_size'], printer_['label_size_assumed']) == (label_size, False)


def test_b1_tells_a_round_roll(b1, fake, monkeypatch):
    # T50*50ROUND, round labels 50 mm across
    tag = bytes.fromhex('881d137bdf181080') + b'\x09080924002' + b'\x10PZ0G000000000001' + struct.pack('>HHB', 180, 0, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['label_size'], printer_['label_size_assumed']) == ('niimbotd50', False)


def test_round_label_prints_on_a_b1(b1, fake, monkeypatch):
    # With a T50*50ROUND roll in it
    tag = bytes.fromhex('881d137bdf181080') + b'\x09080924002' + b'\x10PZ0G000000000001' + struct.pack('>HHB', 180, 0, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbotd50', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    # 50 mm across, as many rows along
    size = struct.unpack('>HHH', data_of(next(p for p in fake.packets if p[2] == 0x13))[:6])
    assert size[:2] == (400, 384)


def test_b1_gets_the_rows_of_a_label_in_one_write(b1, fake):
    # Over USB/IP each write waits for the host, row by row a label took seconds
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    rows = [w for w in fake.writes if w[2] in (0x84, 0x85)]
    assert len(rows) == 1 and len(protocol.parse(rows[0])[0]) > 1


def test_d110_m_gets_each_row_on_its_own(client, fake):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    rows = [w for w in fake.writes if w[2] in (0x84, 0x85)]
    assert len(rows) > 1 and all(len(protocol.parse(w)[0]) == 1 for w in rows)


def _silent_once(fake, monkeypatch, command):
    """Leave the next question of command unanswered, as a B1 did now and
    then. The questions left so."""
    take, missed = fake._take, []

    def once(data):
        if data[2] == command and not missed:
            return missed.append(data)
        take(data)
    monkeypatch.setattr(fake, '_take', once)
    return missed


def test_b1_with_a_status_question_unanswered_is_not_asked_again(b1, fake, monkeypatch):
    # One status question per printer is outstanding at most: the polls after
    # one went unanswered read whether its answer came, telling the last state
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    status = '/labeldesigner/api/printer_status'
    assert b1.get(status).get_json()['printers'][0]['errors'] == []
    missed = _silent_once(fake, monkeypatch, 0xdc)
    for _ in range(3):
        printer_ = b1.get(status).get_json()['printers'][0]
        assert (printer_['errors'], printer_['roll']) == ([], 'PZ1G221322004205')
    assert len(missed) == 1 and fake.commands.count(0xdc) == 1
    # Its answer comes late: it is read and the status asked on from there
    fake.pending += protocol.packet(0xdd, B1_CLOSED)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, B1_TAG.replace(b'PZ1G221322004205', b'PZ1G221322004206')))
    printer_ = b1.get(status).get_json()['printers'][0]
    assert printer_['roll'] == 'PZ1G221322004206' and fake.commands.count(0xdc) == 1


def test_b1_poll_reading_an_error_answer_asks_no_question(b1, fake, monkeypatch):
    # An error answer left from a stopped job comes before the one owed: the
    # poll that only reads goes on reading
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    status = '/labeldesigner/api/printer_status'
    b1.get(status)
    _silent_once(fake, monkeypatch, 0xdc)
    b1.get(status)
    fake.pending += protocol.packet(protocol.ERROR, b'\x06')
    asked = fake.commands.count(0xdc)
    assert b1.get(status).get_json()['printers'][0]['errors'] == []
    assert fake.commands.count(0xdc) == asked


def test_b1_status_question_unanswered_for_20_s_is_lost(b1, fake, monkeypatch):
    # Then it does not answer, and the next poll asks anew
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    monkeypatch.setattr(niimbot, 'STATUS_LOST', 0.2)
    status = '/labeldesigner/api/printer_status'
    assert b1.get(status).get_json()['printers'][0]['errors'] == []
    # Switched off, it stays on USB
    monkeypatch.delitem(REPLIES, 0xdc)
    assert b1.get(status).get_json()['printers'][0]['errors'] == []
    time.sleep(0.25)
    asked = fake.commands.count(0xdc)
    assert b1.get(status).get_json()['printers'][0]['errors'] == ['The printer does not answer']
    assert fake.commands.count(0xdc) == asked + 1


def test_b1_print_clears_an_unanswered_status_question(b1, fake, monkeypatch):
    # The print asks the printer itself, and may read that answer
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    status = '/labeldesigner/api/printer_status'
    b1.get(status)
    _silent_once(fake, monkeypatch, 0xdc)
    b1.get(status)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('printed', '')
    asked = fake.commands.count(0xdc)
    assert b1.get(status).get_json()['printers'][0]['errors'] == []
    assert fake.commands.count(0xdc) == asked + 1


def test_b1_that_stays_silent_does_not_answer(b1, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0xdc)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer does not answer']



def test_pages_are_counted_while_printing(b1, fake, monkeypatch):
    without_status(monkeypatch)
    seen = []
    send = fake.sendall
    fake.sendall = lambda data: (data[2] == 0x01 and seen.append(progress.state(B1_DEVICE))) or send(data)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot15x8-2x3', 'print_type': 'text', 'text': TEXT, 'print_count': '8'})
    assert outcome(b1, response) == ('printed', '')
    assert [(state['label'], state['labels'], state['unit']) for state in seen] == [(1, 2, 'page'), (2, 2, 'page')]


def test_page_that_is_not_told_printed(b1, fake, monkeypatch):
    without_status(monkeypatch)
    monkeypatch.setitem(REPLIES, 0xa3, (0xb3, struct.pack('>H', 0)))
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0)
    response = b1.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot15x8-2x3', 'print_type': 'text', 'text': TEXT})
    assert outcome(b1, response) == ('failed', 'The printer did not tell that it printed the page')


def test_device_id_names_the_b1():
    from app.labeldesigner import drivers
    assert drivers.told_model(B1_DEVICE_ID) == 'Niimbot/B1'
    assert niimbot.DRIVER.model_in('MFG:NIIMBOT;CMD:ESC;MDL: K9;') is None
    assert niimbot.DRIVER.model_in('MFG:Brother;CMD:PT-CBP;MDL:QL-800;') is None
