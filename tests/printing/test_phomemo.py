"""Phomemo's label printers: their jobs byte for byte as the sources send
them (the short names of protocol.py, e.g. [PT] for vivier/phomemo-tools),
their answers, how they are found and reached, and printing and status
through the label designer against a fake printer."""
import json
import os
import socket
import struct

import pytest
from PIL import Image, ImageOps

from app.labeldesigner import refusals, tape
from app.labeldesigner.drivers import backends, devices, phomemo, transports
from app.labeldesigner.drivers.phomemo import protocol
from app.labeldesigner.label import LabelOrientation
from tests.helpers import make_client, outcome

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])


class _Label:
    label_content = None
    label_orientation = LabelOrientation.STANDARD

    def __init__(self, image):
        self.image = image

    def generate(self):
        return self.image.copy()


def _marked(size):
    """A white image with black dots that tell its sides and ends apart."""
    image = Image.new('L', size, 255)
    for x, y in ((0, 0), (5, 1), (size[0] - 1, size[1] - 1), (size[0] // 2, 3)):
        image.putpixel((x, y), 0)
    return image


def _vivier(page):
    # A CUPS page as [PT] rastertopm110.py:229-232 and rastertopd30.py:90-93 make it
    return ImageOps.invert(page).convert('1')


def test_m110_job_is_that_of_the_cups_filter():
    # [PT] rastertopm110.py:183-219 and 236-240 for a page as wide as the
    # printhead, 48 bytes ([PY] printers.json:163)
    inner = _marked((304, 240))
    page = Image.new('L', (384, 240), 255)
    page.paste(inner, (40, 0))
    image = _vivier(page)
    expected = (b'\x1b\x4e\x0d' + bytes([5]) + b'\x1b\x4e\x04' + bytes([10]) + b'\x1f\x11' + bytes([10])
                + b'\x1dv0' + bytes([0]) + (48).to_bytes(2, 'little') + (240).to_bytes(2, 'little') + image.tobytes()
                + b'\x1f\xf0\x05\x00' + b'\x1f\xf0\x03\x00')
    size = phomemo._BY_ID['phomemo40x30']
    _, rows = phomemo.label_rows(_Label(inner), size, phomemo.M110)
    assert phomemo.label_job(rows, phomemo.M110, size) == expected
    # Continuous paper, media type 11 ([PT] cups/drv/phomemo-m110.drv:72)
    endless = phomemo.label_job(rows, phomemo.M110, phomemo._BY_ID['phomemo50'])
    assert endless[:11] == expected[:10] + bytes([11]) and endless[11:] == expected[11:]


def test_m110_family_places_labels_in_the_middle_of_the_printhead():
    # 1 mm blank on both sides of the label ([PT] cups/drv/phomemo-m110.drv:39)
    size = phomemo._BY_ID['phomemo40x30']
    assert size.dots_printable == (304, 240)
    image = Image.new('L', (304, 240), 255)
    image.putpixel((0, 0), 0)
    for model, width, left in ((phomemo.M110, 48, 40), (phomemo.M220, 72, 136), (phomemo.M421, 40, 8)):
        _, rows = phomemo.label_rows(_Label(image), size, model)
        assert (len(rows), len(rows[0])) == (240, width)
        assert int.from_bytes(rows[0]).bit_length() == width * 8 - left


def test_m02_job_is_that_of_the_app():
    # [PT] README.md:424-460, images of at most 255 rows (line 447,
    # rastertopm02_t02.py:113-118); 48 bytes a row (line 464)
    page = _marked((384, 300))
    image = _vivier(page)
    blocks = [image.crop((0, 0, 384, 255)), image.crop((0, 255, 384, 300))]
    expected = (bytes.fromhex('1b40 1b6101 1f110204')
                + b''.join(b'\x1dv0\x00' + struct.pack('<HH', 48, block.height) + block.tobytes() for block in blocks)
                + bytes.fromhex('1b6402 1b6402 1f1108 1f110e 1f1107 1f1109'))
    size = phomemo._BY_ID['phomemo-m02']
    _, rows = phomemo.label_rows(_Label(page), size, phomemo.M02)
    assert phomemo.label_job(rows, phomemo.M02, size) == expected
    # One image up to 255 rows, as [KN] lib/phomemo.dart:140-143, 194-200 and 231-240
    _, rows = phomemo.label_rows(_Label(page.crop((0, 0, 384, 200))), size, phomemo.T02)
    header = bytes([0x1b, 0x40, 0x1b, 0x61, 1, 0x1f, 0x11, 0x02, 0x04])
    footer = bytes([0x1b, 0x64, 0x02, 0x1b, 0x64, 0x02, 0x1f, 0x11, 0x08, 0x1f, 0x11, 0x0e, 0x1f, 0x11, 0x07, 0x1f, 0x11, 0x09])
    marker = bytes([0x1d, 0x76, 0x30, 0, 48, 0, 200, 0])
    assert phomemo.label_job(rows, phomemo.T02, size) == header + marker + image.crop((0, 0, 384, 200)).tobytes() + footer


def test_m02_pro_prints_300_dpi():
    # [PT] cups/drv/phomemo-m02pro.drv:38, 48 mm of its 50 mm paper
    size = phomemo._BY_ID['phomemo-m02pro']
    assert phomemo.DRIVER.label_dpi(size) == (300, 300) and size.dots_printable == (567, 0)
    _, rows = phomemo.label_rows(_Label(Image.new('L', (567, 10), 255)), size, phomemo.M02PRO)
    assert len(rows[0]) == 71
    assert phomemo.DRIVER.label_dpi(phomemo._BY_ID['phomemo-m02']) == (203, 203)


def test_d30_job_is_that_of_the_scripts():
    # A 12 x 40 mm label drawn lying, turned as [PT] rastertopd30.py:94 turns
    # it; sent as [PD] print_text.py:72-87 sends it
    page = _marked((320, 96))
    expected = bytes.fromhex('1f1124001b401d7630000c004001') + _vivier(page).transpose(Image.ROTATE_90).tobytes()
    size = phomemo._BY_ID['phomemo-d12x40']
    img, rows = phomemo.label_rows(_Label(page), size, phomemo.D30)
    assert img.size == (96, 320)
    assert phomemo.label_job(rows, phomemo.D30, size) == phomemo.label_job(rows, phomemo.D35, size) == expected


def _yaddran(page):
    # The dots of a label drawn lying as [TP] Q30/index.js:34-69 packs them
    dots = [1 if value < 128 else 0 for value in page.tobytes()]
    data, b, c = [], 0, 0
    for x in range(page.width):
        for y in range(page.height - 1, -1, -1):
            b, c = b << 1 | dots[x + y * page.width], c + 1
            if c == 8:
                data.append(b)
                b, c = 0, 0
    return bytes(data)


def test_q30_job_is_that_of_the_web_page():
    # [TP] Q30/ble.js:213-235: ESC @, then the image 12 bytes wide
    page = _marked((400, 96))
    data = _yaddran(page)
    expected = bytes([27, 64]) + bytes([29, 118, 48, 0, 12, 0, len(data) // 12 % 256, len(data) // 12 // 256]) + data
    size = phomemo._BY_ID['phomemo-d12x50']
    _, rows = phomemo.label_rows(_Label(page), size, phomemo.Q30)
    assert phomemo.label_job(rows, phomemo.Q30, size) == expected


def test_labels_drawn_standing_are_not_turned():
    size = phomemo._BY_ID['phomemo-d12x40']
    page = _marked((96, 320))
    img, rows = phomemo.label_rows(_Label(page), size, phomemo.D30)
    assert img.size == (96, 320) and b''.join(rows) == _vivier(page).tobytes()


@pytest.mark.parametrize('data, found, rest', [
    # What an M02 told after a job ([PT] README.md:468-475): the battery, the
    # timer, the firmware and the start of its serial number, left out
    (bytes.fromhex('1a045a 1a090c 1a0701 0000 1a08 513030314530'), [(0x04, 0x5a), (0x09, 0x0c)], b''),
    (bytes.fromhex('1a0688 1a0599 1a0f0c'), [(0x06, 0x88), (0x05, 0x99), (0x0f, 0x0c)], b''),
    # One not complete yet, also after other notifications ([TP] Q30/ble.js:81-85)
    (bytes.fromhex('0101 1a06'), [], b'\x1a\x06'),
])
def test_answers(data, found, rest):
    assert protocol.replies(data) == (found, rest)


def test_what_the_answers_mean():
    # [TP] Q30/ble.js:93-107 and 118-121
    assert protocol.errors({protocol.PAPER: 0x88, protocol.COVER: 0x98}) == ['The printer has no labels', 'The cover of the printer is open']
    assert protocol.errors({protocol.PAPER: 0x89, protocol.COVER: 0x99, protocol.BATTERY: 0xa4}) == []
    assert protocol.problem(protocol.RESULT, 0xb8) == 'The printer reports a print error'
    assert [protocol.battery(value) for value in (0x5a, 0xa1, 0xa2, 0xa3, 0xa4, 0xff)] == [
        (90, False), (10, True), (5, True), (3, True), (0, True), None]


def test_status_queries():
    # [TP] Q30/ble.js:177-187, [PT] README.md:449-460
    assert (protocol.ASK_BATTERY, protocol.ASK_PAPER, protocol.ASK_COVER) == (b'\x1f\x11\x08', b'\x1f\x11\x11', b'\x1f\x11\x12')


@pytest.mark.parametrize('name, model', [
    ('Mr.in_M02', 'Phomemo/M02'), ('M02', 'Phomemo/M02'), ('M02 Pro', 'Phomemo/M02Pro'), ('M02PRO', 'Phomemo/M02Pro'),
    ('T02', 'Phomemo/T02'), ('M110', 'Phomemo/M110'), ('M120', 'Phomemo/M120'), ('M220', 'Phomemo/M220'),
    ('D30', 'Phomemo/D30'), ('D35', 'Phomemo/D35'), ('Q30', 'Phomemo/Q30'),
    # Its serial number alone tells no model ([PT] issue 37), nor do others
    ('Q199E5797220037', None), ('M02S', None), ('B1-H123', None), ('', None),
])
def test_printers_on_bluetooth_by_their_names(name, model):
    found = phomemo.DRIVER.found_on_bluetooth({'address': MAC, 'name': name, 'le': True, 'services': []})
    assert found == ((DEVICE, model) if model else None)
    # Found on Bluetooth LE only
    assert phomemo.DRIVER.found_on_bluetooth({'address': MAC, 'name': name, 'le': False, 'classic': True, 'services': []}) is None


def test_printers_on_usb_by_their_ids(tmp_path, monkeypatch):
    # [PT] cups/backend/phomemo.py:81-84
    from app.labeldesigner import drivers
    assert drivers.resolve('usb://0x0493:0xb002', 'Unknown') == (phomemo.DRIVER, 'Phomemo/M02')
    assert drivers.resolve('usb://0x0493:0x8760/A02014090305', 'QL-800') == (phomemo.DRIVER, 'Phomemo/M110')
    # Through usblp by what the kernel tells of it
    folder = tmp_path / '3-3.7.2'
    (folder / '3-3.7.2:1.2').mkdir(parents=True)
    (folder / 'idVendor').write_text('0493\n')
    (folder / 'idProduct').write_text('b002\n')
    (tmp_path / 'lp0').symlink_to(folder / '3-3.7.2:1.2')
    monkeypatch.setattr(devices, 'USB_INTERFACE', str(tmp_path / '{name}'))
    assert drivers.resolve('file:///dev/usb/lp0', 'Unknown') == (phomemo.DRIVER, 'Phomemo/M02')
    # The IDs others share stay the configured model ([PT] issues 37 and 53)
    assert drivers.resolve('usb://0x0483:0x5740', 'Phomemo/M220') == (phomemo.DRIVER, 'Phomemo/M220')
    assert devices.usb_ids('file:///dev/usb/lp1') is None and devices.usb_ids('tcp://192.0.2.1') is None


def test_serial_port_on_usb_is_reached_as_one():
    # The M02, M02 Pro and T02 that are a serial port only ([PT] issues 41, 44 and 52)
    assert backends._over_serial('usb://0x4c4a:0x4155') and not backends._over_serial('usb://0x0493:0xb002')


@pytest.fixture
def links(monkeypatch):
    opened = []
    monkeypatch.setattr(transports, 'kept_le', lambda device, write, notify, **kw: opened.append(('le', device, write, notify)) or Fake())
    monkeypatch.setattr(transports, 'release_le', lambda device, s: None)
    monkeypatch.setattr(transports, 'rfcomm', lambda device, channel: opened.append(('rfcomm', device, channel)) or Fake())
    monkeypatch.setattr(transports, 'SerialLink', lambda device: opened.append(('serial', device)) or Fake())
    monkeypatch.setattr(transports, 'UsbLink', lambda device: opened.append(('usb', device)) or Fake())
    return opened


@pytest.mark.parametrize('model, device, link', [
    # ff02 written, ff03 notifies ([PY] constants.js:113-115, [OD] index.js:250-254)
    ('Phomemo/D30', DEVICE, ('le', DEVICE, b'\x02\xff', b'\x03\xff')),
    ('Phomemo/M110', DEVICE, ('le', DEVICE, b'\x02\xff', b'\x03\xff')),
    ('Phomemo/Q30', DEVICE + '/1', ('le', DEVICE + '/1', b'\x02\xff', b'\x03\xff')),
    # Channel 1 ([PT] cups/backend/phomemo.py:145, [PD] README.md:28-31)
    ('Phomemo/D30', DEVICE + '/1', ('rfcomm', DEVICE + '/1', 1)),
    ('Phomemo/M02', DEVICE, ('rfcomm', DEVICE, 1)),
    ('Phomemo/M421', DEVICE, ('rfcomm', DEVICE, 1)),
    ('Phomemo/T02', 'serial:///dev/ttyACM0', ('serial', 'serial:///dev/ttyACM0')),
    ('Phomemo/M110', 'file:///dev/usb/lp0', ('usb', 'file:///dev/usb/lp0')),
])
def test_each_model_is_reached_as_the_sources_reach_it(links, model, device, link):
    with protocol.connect(device, phomemo.over_le(device, model)):
        pass
    assert links == [link]


class Fake:
    """A printer answering its status queries, and telling after each job of
    job_length bytes the next of its reports, if it has one."""

    def __init__(self, paper=0x89, cover=0x99, battery=0x5a, reports=(), job_length=None):
        self.answers = {protocol.ASK_PAPER: paper, protocol.ASK_COVER: cover, protocol.ASK_BATTERY: battery}
        self.reports, self.job_length = list(reports), job_length
        self.sent, self.pending, self.job, self.closed = [], b'', b'', False

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.sent.append(data)
        if data in self.answers:
            value = self.answers[data]
            self.pending += b'' if value is None else bytes([0x1a, {protocol.ASK_PAPER: 6, protocol.ASK_COVER: 5, protocol.ASK_BATTERY: 4}[data], value])
            return
        self.job += data
        if self.job_length and len(self.job) % self.job_length == 0 and self.reports:
            self.pending += self.reports.pop(0)

    def recv(self, size):
        if not self.pending:
            raise socket.timeout('timed out')
        data, self.pending = self.pending, b''
        return data

    def close(self):
        self.closed = True

    def jobs(self):
        return [data for data in self.sent if data not in self.answers]


@pytest.fixture(autouse=True)
def answered(monkeypatch):
    # Printers that did not answer in another test are looked for again
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(protocol, 'LE_PAUSE', 0)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    monkeypatch.setattr(protocol, 'DONE_WAIT', 0.05)


@pytest.fixture
def fake(monkeypatch):
    def install(**kw):
        printer = Fake(**kw)
        for name in ('kept_le', 'rfcomm'):
            monkeypatch.setattr(transports, name, lambda *args, **kws: printer)
        monkeypatch.setattr(transports, 'release_le', lambda device, s: None)
        return printer
    return install


def _client(tmp_path, model):
    return make_client(tmp_path, model=model, printer=DEVICE)


D30_JOB = len(protocol.D30_START) + 8 + 12 * 320
DONE = bytes.fromhex('1a0f0c')


def test_printer_that_tells_nothing_is_waited_for_once_after_the_last_label(monkeypatch):
    waits = []
    monkeypatch.setattr(protocol, '_wait', lambda c, wait: waits.append(wait) or ('', False))

    class Silent:
        def send(self, data):
            pass
    assert protocol.print_labels(Silent(), [b'1'], lambda n: None) == '' and waits == [protocol.DONE_WAIT]
    waits.clear()
    assert protocol.print_labels(Silent(), [b'1', b'2', b'3'], lambda n: None) == ''
    assert waits == [protocol.DONE_WAIT, 0.01, 0.01, protocol.DONE_WAIT]


def test_labels_are_printed_one_after_the_other(tmp_path, fake):
    printer = fake(reports=[DONE, DONE], job_length=D30_JOB)
    client = _client(tmp_path, 'Phomemo/D30')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'phomemo-d12x40', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    jobs = b''.join(printer.jobs())
    assert len(jobs) == 2 * D30_JOB and jobs.count(protocol.D30_START + b'\x1dv0\x00\x0c\x00\x40\x01') == 2
    # In pieces of 128 bytes on Bluetooth LE ([PY] constants.js:124)
    assert [len(piece) for piece in printer.jobs()].count(128) == 2 * 30 and max(map(len, printer.jobs())) == 128


def test_printer_that_tells_nothing_gets_the_labels_and_is_waited_for(tmp_path, fake):
    printer = fake(job_length=D30_JOB)
    client = _client(tmp_path, 'Phomemo/D30')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'phomemo-d12x40', 'print_type': 'text', 'text': TEXT, 'print_count': '3'})
    assert outcome(client, response) == ('printed', '')
    assert len(b''.join(printer.jobs())) == 3 * D30_JOB


def test_printer_without_labels_is_not_sent_the_job(tmp_path, fake):
    printer = fake(paper=0x88)
    client = _client(tmp_path, 'Phomemo/M110')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo40x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer has no labels')
    assert printer.jobs() == []


def test_error_while_printing_stops_the_job(tmp_path, fake):
    job = len(protocol.M110_HEADER) + 3 + 8 + 48 * 240 + len(protocol.M110_FOOTER)
    printer = fake(reports=[bytes.fromhex('1a0598')], job_length=job)
    client = _client(tmp_path, 'Phomemo/M110')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'phomemo40x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('failed', 'The cover of the printer is open')
    assert len(b''.join(printer.jobs())) == job


def test_printer_reporting_a_print_error(tmp_path, fake):
    fake(reports=[bytes.fromhex('1a0bb8')], job_length=D30_JOB)
    client = _client(tmp_path, 'Phomemo/D30')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo-d12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer reports a print error')


def test_printer_that_does_not_answer_is_printed_on(tmp_path, fake):
    # Not every model answers the status queries: it is not known to fail
    printer = fake(paper=None, cover=None, battery=None)
    client = _client(tmp_path, 'Phomemo/M02')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo-m02', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert printer.jobs()[0].startswith(protocol.M02_HEADER) and printer.jobs()[0].endswith(protocol.M02_FOOTER)


def test_printer_that_is_off(tmp_path, monkeypatch):
    def unreachable(*args, **kw):
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(transports, 'kept_le', unreachable)
    client = _client(tmp_path, 'Phomemo/D30')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo-d12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response)[0] == 'failed'
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']


def test_status_through_the_label_designer(tmp_path, fake):
    fake(cover=0x98, battery=0xa2)
    client = _client(tmp_path, 'Phomemo/Q30')
    state = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (state['model'], state['path'], state['errors']) == ('Phomemo/Q30', DEVICE, ['The cover of the printer is open'])
    assert (state['battery'], state['battery_low']) == (5, True)


def test_600_dpi_is_refused(tmp_path, fake):
    client = _client(tmp_path, 'Phomemo/M02Pro')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'phomemo-m02pro', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    message = 'The Phomemo M02 Pro cannot print at 600 dpi, turn off 600 dpi printing'
    assert response.get_json() == {'success': False, 'message': message}
    assert message in refusals.MESSAGES


def test_only_its_printers_print_its_labels(tmp_path):
    client = _client(tmp_path, 'Phomemo/D30')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo40x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False


def test_serial_port_prints_only_where_configured(tmp_path, monkeypatch):
    printer = Fake()
    monkeypatch.setattr(transports, 'SerialLink', lambda device: printer)
    client = make_client(tmp_path, model='Phomemo/T02', printer='serial:///dev/ttyACM0')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo-m02', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert printer.jobs()[0].startswith(protocol.M02_HEADER)
    # A request cannot send its bytes to another one, e.g. of a 3D printer
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'phomemo-m02', 'print_type': 'text', 'text': TEXT, 'printer': 'serial:///dev/ttyACM1', 'model': 'Phomemo/T02'})
    assert response.get_json() == {'success': False, 'message': 'Unsupported printer'}


def test_same_paper_on_another_model():
    driver = phomemo.DRIVER
    assert driver.label_for('phomemo-m02', 'Phomemo/M02Pro') == 'phomemo-m02pro'
    assert driver.label_for('phomemo-m02pro', 'Phomemo/T02') == 'phomemo-m02'
    assert driver.label_for('phomemo40x30', 'Phomemo/M421') == 'phomemo40x30'
    assert driver.label_for('62', 'Phomemo/M110') == '62'
    assert tape.same_paper('phomemo-m02', 'Phomemo/M02Pro') == 'phomemo-m02pro'


def test_each_model_has_its_labels():
    takes = {model: tape.takes(model) for model in phomemo.MODELS}
    assert takes['Phomemo/M02'] == takes['Phomemo/T02'] == ['phomemo-m02']
    assert 'phomemo70x80' in takes['Phomemo/M220'] and 'phomemo70x80' not in takes['Phomemo/M110']
    assert 'phomemo102x152' in takes['Phomemo/M421'] and 'phomemo50' not in takes['Phomemo/M421']
    assert 'phomemo-d12x70' in takes['Phomemo/Q30'] and 'phomemo-d12x70' not in takes['Phomemo/D30']
    assert all(takes[model] for model in phomemo.MODELS)


@pytest.mark.parametrize('model, size', [('Phomemo/M110', 'phomemo40x30'), ('Phomemo/M220', 'phomemo40x30'),
                                         ('Phomemo/M421', 'phomemo102x152'), ('Phomemo/D30', 'phomemo-d12x40'),
                                         ('Phomemo/Q30', 'phomemo-d12x50'), ('Phomemo/M02Pro', 'phomemo-m02pro')])
def test_each_model_is_assumed_to_have_the_labels_its_source_starts_with(model, size):
    assert tape.assumed_size(model, '62') == size


def test_what_the_label_designer_offers():
    caps = phomemo.DRIVER.capabilities('Phomemo/M02Pro')
    assert (caps['qualities'], caps['cutter'], caps['model_shown'], caps['vendor']) == (['quality'], False, 'M02 Pro', 'Phomemo')
    # The end of a label drawn lying that leaves the printer first
    assert phomemo.DRIVER.capabilities('Phomemo/D30')['leaves_turned'] and not phomemo.DRIVER.capabilities('Phomemo/Q30')['leaves_turned']


def test_demo_printer_keeps_the_label_as_printed(tmp_path):
    client = make_client(tmp_path, model='Phomemo/D30', printer='simulation')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'phomemo-d12x40', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    from app.labeldesigner import printer as simulator
    kept = [entry.path for entry in os.scandir(simulator.SIMULATED_LABELS_DIR) if entry.name.endswith('_phomemo_0.png')]
    with Image.open(kept[0]) as image:
        assert len(kept) == 1 and image.size == (96, 320)
