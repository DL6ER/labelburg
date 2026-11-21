"""POST /api/scan: a saved label filled with a scanned code, printed or only previewed."""

import base64
import io

import pytest
from PIL import Image

from app.labeldesigner import scan
from tests.helpers import make_client
from tests.repository.helpers import SAVE_URL

SCAN_URL = '/labeldesigner/api/scan'
AJAX = {'X-Requested-With': 'XMLHttpRequest'}

BOX = {'name': 'Box', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': '{{blank:code}}',
       'text': [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:code}}'}]}


@pytest.fixture
def printed(tmp_path, monkeypatch):
    """A client with BOX saved, and the labels the printer got."""
    from app.labeldesigner import printer
    queued = []
    def add(self, label, cut=True, high_res=False, fast=False):
        label.generate()
        queued.append(label)
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', add)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BOX).status_code == 200
    client.queued = queued
    return client


def _save(client, **label):
    assert client.post(SAVE_URL, json={**BOX, **label}).status_code == 200


def _scan(client, **body):
    return client.post(SCAN_URL, json=body, headers=AJAX)


def _lines(label):
    label.process_templates()
    return [line['text'] for line in label.text]


def test_scan_prints_the_label_with_the_code(printed):
    response = _scan(printed, name='Box.json', code='A-17')
    assert response.status_code == 200
    result = response.get_json()
    assert result['success'] is True and result['printed'] is True
    assert result['label'] == 'Box.json' and result['filled'] == {'code': 'A-17'}
    assert len(printed.queued) == 1
    printed.queued[0].process_templates()
    assert printed.queued[0].code_text == 'A-17'
    assert Image.open(io.BytesIO(base64.b64decode(result['image']))).format == 'PNG'


def test_preview_only_prints_nothing(printed):
    response = _scan(printed, name='Box.json', code='A-17', preview_only='1')
    assert response.status_code == 200
    result = response.get_json()
    assert result['printed'] is False and result['filled'] == {'code': 'A-17'}
    assert Image.open(io.BytesIO(base64.b64decode(result['image']))).format == 'PNG'
    assert printed.queued == []


def test_vcard_fills_name_and_address_blanks(printed):
    _save(printed, name='Card.json', print_type='text', code_text='', text=[
        {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Name}}'},
        {'font': 'DejaVu Sans,Book', 'size': '30', 'text': '{{blank:Address}}'}])
    card = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Erika Muster\r\nADR:;;Hauptstr. 5;Bonn;;53111;Deutschland\r\nEND:VCARD\r\n'
    assert _scan(printed, name='Card.json', code=card).status_code == 200
    assert _lines(printed.queued[0]) == ['Erika Muster', 'Hauptstr. 5', '53111 Bonn', 'Deutschland']


ROOM = {'name': 'Room', 'print_type': 'text', 'code_text': '', 'blanks': {'Room': 'Stored'}, 'text': [
    {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:code}}'},
    {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Room}}'}]}


def test_request_fields_do_not_fill_blanks(printed):
    _save(printed, **{**ROOM, 'name': 'Room.json'})
    response = printed.post(SCAN_URL, json={'name': 'Room.json', 'code': 'A-17', 'Room': 'Kitchen', 'blanks': {'Room': 'X'}}, headers=AJAX)
    assert response.status_code == 200 and response.get_json()['filled'] == {'code': 'A-17'}
    assert _lines(printed.queued[0]) == ['A-17', 'Stored']


def test_request_fields_do_not_fill_blanks_of_the_preview(printed, monkeypatch):
    from app.labeldesigner import routes
    _save(printed, **{**ROOM, 'name': 'Room.json'})
    made = []
    create = routes.create_label_from_request
    monkeypatch.setattr(routes, 'create_label_from_request', lambda data, *args, **kwargs: made.append(data['blanks']) or create(data, *args, **kwargs))
    response = printed.post(SCAN_URL, json={'name': 'Room.json', 'code': 'A-17', 'Room': 'Kitchen', 'blanks': {'Room': 'X'}, 'preview_only': 1}, headers=AJAX)
    assert response.status_code == 200
    assert made == [{'Room': 'Stored', 'code': 'A-17'}]


def test_preview_only_is_read_like_other_flags(printed):
    for value in (0, '0', None):
        assert _scan(printed, name='Box.json', code='A-17', preview_only=value).get_json()['printed'] is True
    assert len(printed.queued) == 3
    printed.queued.clear()
    for value in (1, '1', 'true'):
        assert _scan(printed, name='Box.json', code='A-17', preview_only=value).get_json()['printed'] is False
    for value in ('on', 'yes', [1]):
        response = _scan(printed, name='Box.json', code='A-17', preview_only=value)
        assert response.status_code == 400 and response.get_json()['success'] is False
    assert printed.queued == []


def test_refused_preview_answers_like_a_refused_print(printed, monkeypatch):
    from app.labeldesigner import routes
    for error, status in ((routes.TextTooLong('too long'), 413), (ValueError('bad'), 400)):
        monkeypatch.setattr(routes, '_preview_label', lambda *args, error=error: (_ for _ in ()).throw(error))
        response = _scan(printed, name='Box.json', code='x', preview_only=1)
        assert response.status_code == status
        result = response.get_json()
        assert result['success'] is False and result['message']


def test_refusals_are_sent_as_they_are(printed):
    _save(printed, name='None.json', print_type='text', code_text='', text=[{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'x'}])
    _save(printed, name='Two.json', print_type='text', code_text='', text=[
        {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:a}}'}, {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:b}}'}])
    for body, message in (({'name': 'None.json', 'code': 'x'}, scan.NO_BLANKS), ({'name': 'Two.json', 'code': 'x'}, scan.SEVERAL_BLANKS),
                          ({'name': 'Box.json', 'code': 'https>&&example.org'}, scan.WRONG_LAYOUT), ({'name': 'Box.json', 'code': '  '}, scan.NOTHING_SCANNED),
                          ({'name': 'Box.json', 'code': ['x']}, scan.NOTHING_SCANNED), ({'name': 'Box.json'}, scan.NOTHING_SCANNED),
                          ({'name': 'Box.json', 'code': 'BEGIN:VCARD\nFN:Erika Muster'}, scan.INCOMPLETE),
                          ({'name': 'Box.json', 'code': 'TEL:+49 228 1234\nEND:VCARD'}, scan.INCOMPLETE)):
        response = _scan(printed, **body)
        assert (response.status_code, response.get_json()['message']) == (400, message)
    response = _scan(printed, name='Gone', code='x')
    assert (response.status_code, response.get_json()['message']) == (404, 'Not found')
    response = _scan(printed, code='x')
    assert (response.status_code, response.get_json()['message']) == (400, 'No name specified')
    assert printed.queued == []


def test_saved_copies_apply(printed):
    _save(printed, name='Three.json', print_count=3)
    assert _scan(printed, name='Three.json', code='A-17').status_code == 200
    assert len(printed.queued) == 3


def test_cross_origin_scan_is_refused(printed):
    response = printed.post(SCAN_URL, json={'name': 'Box.json', 'code': 'x'}, headers={'Origin': 'https://evil.example'})
    assert response.status_code == 403
    assert printed.queued == []


def test_get_never_prints(printed):
    assert printed.get(SCAN_URL + '?name=Box.json&code=x').status_code == 405
    assert printed.queued == []
