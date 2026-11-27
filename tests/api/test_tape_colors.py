"""The colors of the tape in a printer: in its status, and the preview shown
in them where the label designer asks for it."""

import io
import json

import pytest
from PIL import Image, ImageOps

from app.labeldesigner import adding, refusals, routes
from app.labeldesigner.drivers import devices, usbip
from app.labeldesigner.drivers.brother import usb
from app.labeldesigner.drivers.brother.status import parse_status
from tests.helpers import make_client
from tests.printing.test_tape_colors import colors, ptouch_status

PRINTER = 'tcp://192.0.2.10'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Shelf', 'size': '40', 'align': 'center'}])
LABEL = {'label_size': 'pt24', 'orientation': 'standard', 'text': TEXT, 'high_res': '0', 'printer': PRINTER, 'model': 'PT-P750W'}


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printers these tests name are known, as if the search found them
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {PRINTER, 'tcp://192.0.2.11'})


class FakePtouch:
    """A P-touch printer on USB that answers a status request with status."""

    def __init__(self, status: bytes):
        self.status, self.unread = status, b''

    def write(self, data: bytes):
        if data.endswith(b'\x1b\x69\x53'):
            self.unread += self.status

    def read(self, length: int) -> bytes:
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def dispose(self):
        pass


def test_status_of_a_ptouch_printer_tells_the_colors_of_its_tape(tmp_path, monkeypatch):
    monkeypatch.setattr(usb, 'get_printer', lambda device: FakePtouch(ptouch_status(0x06, 0x08)))
    client = make_client(tmp_path, model='PT-P750W', printer='file:///dev/usb/lp0')
    status = client.get('/labeldesigner/api/printer_status').get_json()
    told = status['printers'][0]
    assert (told['path'], told['tape']['id'], told['tape']['known']) == ('file:///dev/usb/lp0', 'pt24', True)
    assert told['colors'] == colors('yellow', '#ffd400', 'black', '#000000') == status['colors']
    # And as the status told them before
    assert (told['tape_color'], told['text_color']) == ('Yellow', 'Black')


def test_printers_that_tell_no_colors_have_empty_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(usb, 'get_printer', lambda device: FakePtouch(ptouch_status(0x02, 0x08)))
    client = make_client(tmp_path, model='PT-P750W', printer='file:///dev/usb/lp0')
    status = client.get('/labeldesigner/api/printer_status').get_json()
    # The tape reported, its colors not, and the demo printer has none
    assert status['printers'][0]['tape']['id'] == 'pt24' and status['printers'][1]['path'] == 'simulation'
    for told in status['printers']:
        assert told['colors'] == {}
    assert [(p['tape_color'], p['text_color']) for p in status['printers']] == [('Other', 'Black'), ('', '')]


def test_a_printer_that_does_not_answer_keeps_the_colors_it_told(tmp_path, monkeypatch):
    devices.status_cache[devices.lock_key('/dev/usb/lp0')] = parse_status(ptouch_status(0x08, 0x01))

    def gone(device):
        raise FileNotFoundError(2, 'No such file or directory')
    monkeypatch.setattr(usb, 'get_printer', gone)
    client = make_client(tmp_path, model='PT-P750W', printer='file:///dev/usb/lp0')
    told = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert told['errors'] and told['tape']['id'] == 'pt24'
    assert told['colors'] == colors('black', '#000000', 'white', '#ffffff')


def reports(tape_code: int, ink_code: int, width: int = 24):
    """The last status of the printer, as the label designer follows it."""
    devices.status_cache[devices.lock_key(PRINTER)] = parse_status(ptouch_status(tape_code, ink_code, width))


def preview(client, **values):
    response = client.post('/labeldesigner/api/preview', data={**LABEL, **values})
    assert response.status_code == 200, response.get_data(as_text=True)
    return response, Image.open(io.BytesIO(response.data))


def test_preview_in_the_colors_of_the_tape(tmp_path):
    client = make_client(tmp_path)
    reports(0x08, 0x01)
    response, shown = preview(client, tape_colors='1')
    plain = preview(client)[1]
    # White print on black tape: the label as it is, the other way round,
    # stored in gray as the label on white is
    assert plain.mode == shown.mode == 'L' and shown.size == plain.size
    assert shown.tobytes() == ImageOps.invert(plain).tobytes()
    assert shown.getpixel((0, 0)) == 0 and 255 in {px for _, px in shown.getcolors(256)}
    assert response.headers['X-Label-Tape-Colors'] == '#000000,#ffffff'


def test_preview_on_white_tape_stays_as_it_was(tmp_path):
    client = make_client(tmp_path)
    reports(0x01, 0x08)
    response, shown = preview(client, tape_colors='1')
    plain = preview(client)[1]
    assert shown.mode == 'L' and shown.tobytes() == plain.tobytes()
    assert response.headers['X-Label-Tape-Colors'] == '#ffffff,#000000'


def test_preview_on_yellow_tape(tmp_path):
    client = make_client(tmp_path)
    reports(0x06, 0x08)
    shown = preview(client, tape_colors='1')[1]
    seen = {px for _, px in shown.getcolors(65536)}
    assert shown.mode == 'RGB'
    assert shown.getpixel((0, 0)) == (255, 212, 0) and (0, 0, 0) in seen
    # Only colors between the ink and the tape
    assert all(b == 0 and abs(g - r * 212 / 255) <= 1 for r, g, b in seen)


def test_the_printer_is_not_asked_for_the_colors(tmp_path, monkeypatch):
    # The configured printer shared over USB/IP named without its model,
    # which would be imported to read it
    shared = 'usbip://192.0.2.30/1-1'
    devices.status_cache[devices.lock_key(shared)] = parse_status(ptouch_status(0x08, 0x01))

    def asked(*args, **kwargs):
        raise AssertionError('The printer was asked')
    monkeypatch.setattr(routes.drivers, 'resolve', asked)
    monkeypatch.setattr(usbip, 'connect', asked)
    monkeypatch.setattr(devices, 'usb_device_id', asked)
    client = make_client(tmp_path, printer=shared)
    with client.application.test_request_context():
        assert routes._tape_colors({'printer': shared, 'label_size': 'pt24'}) == ('#000000', '#ffffff')
        assert routes._tape_colors({'label_size': 'pt24'}) == ('#000000', '#ffffff')
        assert routes._tape_colors({'printer': shared, 'label_size': 'pt12'}) is None
        assert routes._tape_colors({'printer': 'usbip://192.0.2.31/1-1', 'label_size': 'pt24'}) is None


def test_preview_on_clear_tape_is_see_through(tmp_path):
    client = make_client(tmp_path)
    reports(0x03, 0x08)
    response, shown = preview(client, tape_colors='1')
    plain = preview(client)[1].convert('L')
    assert shown.mode == 'RGBA'
    assert shown.getpixel((0, 0))[3] == 0
    # The print as dark as it was, the tape as clear as the paper was white
    assert [a for *_, a in shown.get_flattened_data()] == [255 - v for v in plain.get_flattened_data()]
    assert {tuple(rgb) for *rgb, a in shown.get_flattened_data() if a == 255} == {(0, 0, 0)}
    assert response.headers['X-Label-Tape-Colors'] == '#ffffff00,#000000'


def test_api_previews_stay_as_they_were(tmp_path):
    client = make_client(tmp_path)
    before = preview(client)
    reports(0x08, 0x01)
    for values in ({}, {'tape_colors': '0'}, {'tape_colors': ''}):
        response, shown = preview(client, **values)
        assert response.data == before[0].data and shown.mode == 'L'
        assert 'X-Label-Tape-Colors' not in response.headers


@pytest.mark.parametrize('case', ['no colors', 'no status', 'another tape', 'simulator', 'another printer'])
def test_preview_where_the_tape_does_not_tell_its_colors(tmp_path, case):
    client = make_client(tmp_path)
    plain = preview(client)[0].data
    values = {'tape_colors': '1'}
    if case == 'no colors':
        reports(0xF1, 0xF1)
    elif case == 'another tape':
        # 12 mm white on black tape, the label is made for 24 mm
        reports(0x08, 0x01, width=12)
    elif case == 'simulator':
        reports(0x08, 0x01)
        values['printer'] = 'simulation'
        plain = preview(client, printer='simulation')[0].data
    elif case == 'another printer':
        reports(0x08, 0x01)
        values['printer'] = 'tcp://192.0.2.11'
        plain = preview(client, printer='tcp://192.0.2.11')[0].data
    response = preview(client, **values)[0]
    assert response.data == plain and 'X-Label-Tape-Colors' not in response.headers


def test_tape_colors_is_a_flag(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data={**LABEL, 'tape_colors': 'yes'})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'tape_colors must be 0 or 1'
    assert 'tape_colors must be 0 or 1' in refusals.MESSAGES


@pytest.mark.parametrize('tape_code, ink_code, shown', [
    (0x08, 0x0A, colors('black', '#000000', 'gold', '#d4af37')),
    (0xF0, 0xF0, {}),
])
def test_a_printer_found_tells_the_colors_of_its_tape(tmp_path, monkeypatch, tape_code, ink_code, shown):
    found = {'address': PRINTER, 'kind': 'network', 'model': 'PT-P750W', 'device_id': {},
             'state': parse_status(ptouch_status(tape_code, ink_code))}
    monkeypatch.setattr(adding, 'identify', lambda text, mode, keep=False: dict(found))
    answer = make_client(tmp_path).post('/labeldesigner/api/printers/identify', data={'address': '192.0.2.10'}).get_json()
    assert answer['printer']['tape']['id'] == 'pt24'
    assert answer['printer']['colors'] == shown
