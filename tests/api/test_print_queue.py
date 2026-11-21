"""The print routes answer once the job is in the queue of its printer, which
prints one job after the other."""
import base64
import io
import json
import threading

import pytest
from PIL import Image

from app.labeldesigner import label, paperless, print_queue, printer, routes
from tests.helpers import EXAMPLE_FORMDATA, verify_image
from tests.printing.test_printer import DEVICE, ExclusiveUsbPrinter, client  # noqa: F401

API = '/labeldesigner/api'
PRINT = f'{API}/print'
STATUS_REQUEST = b'\x1b\x69\x53'
# The label of tests/images/simple.png, as tests/api/test_preview.py previews it
SIMPLE = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Left', 'size': '60', 'align': 'left'},
                     {'font': 'Droid Sans,Mono', 'text': '-- LONG MONO TEXT --', 'size': '50', 'align': 'center'}])
COUNTER = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{counter}}', 'size': '30', 'align': 'center'}])


def _held(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """started is set once the printer gets the data of a job, which it
    prints once release is set."""
    started, release = threading.Event(), threading.Event()
    write = ExclusiveUsbPrinter.write

    def held(self, data):
        if data != STATUS_REQUEST:
            started.set()
            release.wait(10)
        write(self, data)
    monkeypatch.setattr(ExclusiveUsbPrinter, 'write', held)
    return started, release


def _job(response) -> print_queue.Job:
    return print_queue.get(response.get_json()['job'])


def _ended(*responses) -> list[tuple[str, str]]:
    """The state and message of the jobs of the responses once they ended."""
    jobs = [_job(response) for response in responses]
    assert all(job.done.wait(10) for job in jobs)
    return [(job.state, job.message) for job in jobs]


def test_a_print_on_a_busy_printer_waits_and_prints(client, queued, monkeypatch):
    started, release = _held(monkeypatch)
    first = client.post(PRINT, data=EXAMPLE_FORMDATA)
    assert started.wait(10)
    second = client.post(PRINT, data=EXAMPLE_FORMDATA)
    release.set()
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.get_json()['queued'] is second.get_json()['queued'] is True
    assert second.get_json()['state'] == 'waiting'
    assert _ended(first, second) == [('printed', ''), ('printed', '')]
    assert _job(first).ended < _job(second).ended


def test_every_print_route_answers_with_its_job(client, monkeypatch):
    monkeypatch.setattr(ExclusiveUsbPrinter, 'print_seconds', 0.0)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    # Homebox prints on the printer of the label designer, as its status tells it
    state = {'errors': [], 'path': DEVICE, 'model': 'QL-800', 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [state], 'selected': DEVICE})
    image = io.BytesIO()
    Image.new('L', (200, 100), 0).save(image, 'PNG')
    webhook = client.post(f'{API}/webhook/print', json={
        'password': 'secret', 'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    calibration = client.post(f'{API}/calibration/print', data={'printer': 'simulation', 'model': 'Niimbot/B1'})
    answers = [('api', client.post(PRINT, data=EXAMPLE_FORMDATA)),
               ('api', client.post(f'{API}/repository/print', json={'name': 'QR-Example.json'})),
               ('api', client.post(f'{API}/demos/print', json={'id': 'storage/013-homebox-62.json'})),
               ('webhook', webhook),
               ('homebox', client.post(f'{API}/homebox', data=image.getvalue(), headers={'Content-Type': 'image/png'})),
               ('calibration', calibration)]
    named_from = {'api': 'the API', 'webhook': 'the webhook', 'homebox': 'Homebox', 'calibration': ''}
    for source, response in answers:
        answer = response.get_json()
        assert response.status_code == 200, (source, answer)
        assert {key: answer[key] for key in ('success', 'queued', 'start')} == {
            'success': True, 'queued': True, 'start': print_queue.START}, source
        job = _job(response)
        assert answer['job'] == job.id
        assert (job.source, job.told()['from'], job.state) == (source, named_from[source], 'printed'), source
    assert webhook.get_json()['count'] == 1
    assert (_job(calibration).title, _job(calibration).preview) == ('Calibration label', None)
    # Only the label designer and MCP are taken from a request
    for given, source in (('designer', 'designer'), ('mcp', 'mcp'), ('homebox', 'api'), ('', 'api')):
        assert _job(client.post(PRINT, data=dict(EXAMPLE_FORMDATA, source=given))).source == source


def test_bad_input_is_still_refused_at_once(client):
    for text, font, message in (('x' * (label.MAX_TEXT_LENGTH + 1), 'DejaVu Sans,Book', 'Text is too long'),
                                ('x', 'DejaVu Sans', 'Font must be given as "<family>,<style>"')):
        lines = json.dumps([{'font': font, 'text': text, 'size': '40'}])
        response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, text=lines))
        assert (response.status_code, response.get_json()) == (400, {'success': False, 'message': message})
    assert print_queue.line(DEVICE) == {'printing': None, 'waiting': [], 'held': None}


def test_a_later_copy_that_fails_while_drawing_fails_alone(client, queued, monkeypatch):
    started, release = _held(monkeypatch)
    first = client.post(PRINT, data=EXAMPLE_FORMDATA)
    assert started.wait(10)
    # Box 8 and Box 9 fit, Box 10 is a character too long
    monkeypatch.setattr(label, 'MAX_TEXT_LENGTH', 5)
    counted = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Box {{counter:8}}', 'size': '40'}])
    failing = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, text=counted, print_count='3'))
    behind = client.post(PRINT, data=EXAMPLE_FORMDATA)
    release.set()
    assert (failing.status_code, behind.status_code) == (200, 200)
    assert failing.get_json()['state'] == behind.get_json()['state'] == 'waiting'
    assert _ended(first, failing, behind) == [('printed', ''), ('failed', 'Text is too long'), ('printed', '')]
    assert print_queue.line(DEVICE)['held'] is None


def test_the_returned_image_is_the_first_label(client):
    response = client.post(f'{PRINT}?return_format=png', data=dict(EXAMPLE_FORMDATA, text=SIMPLE))
    answer = response.get_json()
    assert answer['job'] and answer['state'] == 'printed'
    returned = base64.b64decode(answer['image'])
    verify_image(returned, 'simple.png')
    # The preview of the queue is the same, at most 240 px on its long side
    with Image.open(io.BytesIO(returned)) as full, Image.open(io.BytesIO(_job(response).preview)) as small:
        assert max(small.size) == 240
        assert abs(small.width / small.height - full.width / full.height) < 0.05


def test_a_full_line_is_busy(client, queued, monkeypatch):
    monkeypatch.setattr(print_queue, 'MAX_WAITING', 1)
    started, release = _held(monkeypatch)
    first = client.post(PRINT, data=EXAMPLE_FORMDATA)
    assert started.wait(10)
    second = client.post(PRINT, data=EXAMPLE_FORMDATA)
    third = client.post(PRINT, data=EXAMPLE_FORMDATA)
    release.set()
    assert (first.status_code, second.status_code, second.get_json()['state']) == (200, 200, 'waiting')
    assert (third.status_code, third.get_json()) == (503, {'success': False, 'message': printer.BUSY})
    assert _ended(first, second) == [('printed', ''), ('printed', '')]


def test_the_title_of_a_job(client, tmp_path, monkeypatch):
    monkeypatch.setattr(ExclusiveUsbPrinter, 'print_seconds', 0.0)
    client.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path / 'repo')
    (tmp_path / 'repo').mkdir()

    def title(path, **kwargs):
        response = client.post(f'{API}/{path}', **kwargs)
        assert response.status_code == 200, response.get_json()
        return _job(response).title
    lines = json.dumps([{'text': ' ', 'size': '40'}, {'text': ' Spices ', 'size': '40'}])
    assert title('print', data=dict(EXAMPLE_FORMDATA, text=lines, title='  Kitchen shelf ')) == 'Kitchen shelf'
    assert title('print', data=dict(EXAMPLE_FORMDATA, text=lines, title='x' * 300)) == 'x' * 200
    assert title('print', data=dict(EXAMPLE_FORMDATA, text=lines)) == 'Spices'
    assert title('print', data=EXAMPLE_FORMDATA) == 'Label'
    # A saved label without text by the name it is saved under
    for saved, name in (({'name': 'Rack', 'title': 'Gewürzregal'}, 'Gewürzregal'), ({'name': 'Shelf'}, 'Shelf')):
        assert client.post(f'{API}/repository/save', json={
            **saved, 'label_size': '62', 'print_type': 'qrcode', 'code_text': 'x', 'text': []}).status_code == 200
        assert title('repository/print', json={'name': f'{saved["name"]}.json'}) == name
    assert title('repository/print', json={'name': 'Shelf.json', 'title': 'Pantry'}) == 'Pantry'


def _gray(png: bytes) -> tuple:
    with Image.open(io.BytesIO(png)) as image:
        return image.size, image.convert('L').tobytes()


def _small(png: bytes) -> tuple:
    """png made a preview of the queue."""
    with Image.open(io.BytesIO(png)) as image:
        small = image.convert('RGB')
    small.thumbnail((routes.PREVIEW_SIZE, routes.PREVIEW_SIZE))
    return small.size, small.convert('L').tobytes()


@pytest.mark.parametrize('data', [
    dict(EXAMPLE_FORMDATA, text=SIMPLE),
    # Six labels of a B1 to a page
    {'printer': 'simulation', 'model': 'Niimbot/B1', 'label_size': 'niimbot15x8-2x3', 'print_count': '8', 'text': COUNTER},
], ids=['label', 'page'])
def test_the_preview_of_a_job_is_shown_as_the_label_designer_shows_it(client, data):
    # Printed with the bottom first, turned
    data = dict(data, leaves_first='bottom')
    shown = client.post(f'{API}/preview', data=data)
    assert shown.status_code == 200
    response = client.post(f'{PRINT}?return_format=png', data=data)
    assert _gray(_job(response).preview) == _small(shown.data)
    # The image returned is as the printer gets it
    returned = base64.b64decode(response.get_json()['image'])
    assert _gray(returned) != _gray(shown.data)
    if 'model' not in data:
        with Image.open(io.BytesIO(shown.data)) as upright:
            assert _gray(returned) == (upright.size, upright.rotate(180).convert('L').tobytes())


def test_the_title_is_the_first_line_of_the_first_label(client, tmp_path):
    client.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)

    def title(text, path='print', **values):
        lines = json.dumps([{'font': 'DejaVu Sans,Book', 'text': line, 'size': '40'} for line in text])
        if path == 'print':
            response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, text=lines, printer='simulation', **values))
        else:
            response = client.post(f'{API}/repository/print', json=dict(values, name='Jar.json', printer='simulation'))
        assert response.status_code == 200, response.get_json()
        return _job(response).title
    # With its blanks filled in, the counter and the archive serial number of the first copy
    assert client.post(f'{API}/repository/save', json={
        'name': 'Jar', 'label_size': '62', 'text': [{'text': '{{blank:Content}}', 'size': '40'}]}).status_code == 200
    assert title([], path='repository', blanks={'Content': 'Chili'}) == 'Chili'
    assert title(['Box {{counter:8}}'], print_count='3') == 'Box 8'
    assert title(['ASN{{asn:5}}']) == 'ASN00001'
    # Without its icons, the first of its lines, spaces as one
    assert title(['{{mdi:lightbulb}}  Lamp  ']) == 'Lamp'
    assert title(['Spices\nShelf 2']) == 'Spices'
    assert title(['Spices'], title='Kitchen\nshelf  2') == 'Kitchen shelf 2'


def test_a_full_line_takes_no_archive_serial_number(client, tmp_path, queued, monkeypatch):
    client.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)
    monkeypatch.setattr(print_queue, 'MAX_WAITING', 1)
    asn = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'ASN{{asn:5}}', 'size': '40'}]))
    started, release = _held(monkeypatch)
    first = client.post(PRINT, data=EXAMPLE_FORMDATA)
    assert started.wait(10)
    second = client.post(PRINT, data=EXAMPLE_FORMDATA)
    refused = client.post(PRINT, data=asn)
    release.set()
    assert (refused.status_code, refused.get_json()['message']) == (503, printer.BUSY)
    assert _ended(first, second) == [('printed', ''), ('printed', '')]
    assert paperless.next_asn(client.application.config, str(tmp_path)) == 1
    # Taken once it is queued
    assert _ended(client.post(PRINT, data=asn)) == [('printed', '')]
    assert paperless.next_asn(client.application.config, str(tmp_path)) == 2


def test_a_job_explains_its_failure_with_the_app(client, monkeypatch):
    # The limits of the app are known when the job fails
    def fail(self):
        raise ValueError('A print job must not have more than 100 labels, 120 pages are selected')
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', fail)
    response = client.post(PRINT, data=EXAMPLE_FORMDATA)
    assert _ended(response) == [('failed', 'A print job must not have more than 100 labels')]
