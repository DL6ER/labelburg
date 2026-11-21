"""Reading and changing the lines of the print queue over the API."""
import io
import threading

from PIL import Image

from app.labeldesigner import print_queue, printer, routes
from tests.api.test_print_queue import API, PRINT, SIMPLE, _held, _job
from tests.helpers import EXAMPLE_FORMDATA
from tests.printing.test_printer import DEVICE, client  # noqa: F401

QUEUE = f'{API}/queue'


def _two_jobs(client, monkeypatch):
    """A job the printer holds and a second one waiting for it."""
    started, release = _held(monkeypatch)
    first = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, text=SIMPLE, title='First', print_count=2))
    assert started.wait(10)
    second = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, text=SIMPLE))
    return first, second, release


def test_the_queue_of_a_printer(client, queued, monkeypatch):
    monkeypatch.setattr(routes.progress, 'state', lambda path: {'labels': 2, 'label': 1} if path == DEVICE else None)
    first, second, release = _two_jobs(client, monkeypatch)
    third = client.post(PRINT, data=EXAMPLE_FORMDATA)
    answer = client.get(QUEUE, query_string={'printer': DEVICE})
    release.set()
    assert answer.status_code == 200
    told = answer.get_json()
    assert told['start'] == print_queue.START
    assert told['held'] is None
    assert told['printing']['id'] == first.get_json()['job']
    assert (told['printing']['state'], told['printing']['progress']) == ('printing', {'labels': 2, 'label': 1})
    assert [job['id'] for job in told['waiting']] == [second.get_json()['job'], third.get_json()['job']]
    assert all('progress' not in job for job in told['waiting'])
    assert told['waiting'][0]['preview'] == f'{QUEUE}/{told["waiting"][0]["id"]}/preview.png'
    assert client.get(QUEUE, query_string={'printer': 'tcp://192.0.2.9'}).get_json() == {
        'printing': None, 'waiting': [], 'held': None, 'start': print_queue.START}


def test_every_printer_with_something_in_its_line(client, queued, monkeypatch):
    _, second, release = _two_jobs(client, monkeypatch)
    other = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, printer='simulation', model='QL-800'))
    assert _job(other).done.wait(10)
    answer = client.get(QUEUE)
    release.set()
    assert answer.status_code == 200
    told = answer.get_json()
    assert told['start'] == print_queue.START
    # The idle line of the simulator is left out
    assert [line['printer'] for line in told['printers']] == [DEVICE]
    line = told['printers'][0]
    assert (line['printing']['state'], [job['id'] for job in line['waiting']], line['held']) == (
        'printing', [second.get_json()['job']], None)


def test_reading_the_queue_never_asks_the_printer(client, queued, monkeypatch):
    _, _, release = _two_jobs(client, monkeypatch)

    def asked(*args, **kwargs):
        raise AssertionError('The queue asked the printer')
    for name in ('device_state', 'status_of_printer', 'get_ptr_status'):
        monkeypatch.setattr(printer, name, asked)
    for name in ('get_ptr_status', 'status_of_printer'):
        monkeypatch.setattr(routes, name, asked)
    for _ in range(10):
        assert client.get(QUEUE, query_string={'printer': DEVICE}).status_code == 200
        assert client.get(QUEUE).status_code == 200
    release.set()


def test_a_job_and_its_preview(client, queued, monkeypatch):
    first, second, release = _two_jobs(client, monkeypatch)
    job_id = second.get_json()['job']
    answer = client.get(f'{QUEUE}/{job_id}')
    assert answer.status_code == 200
    told = answer.get_json()
    assert (told['id'], told['state'], told['start']) == (job_id, 'waiting', print_queue.START)
    png = client.get(told['preview'])
    release.set()
    assert (png.status_code, png.mimetype) == (200, 'image/png')
    assert max(Image.open(io.BytesIO(png.data)).size) <= 240
    unknown = client.get(f'{QUEUE}/nothing')
    assert (unknown.status_code, unknown.get_json()) == (404, {
        'success': False, 'message': 'No such print job', 'start': print_queue.START})
    missing = client.get(f'{QUEUE}/nothing/preview.png')
    assert (missing.status_code, missing.get_json()['message']) == (404, 'No such print job')
    # A job whose preview is dropped
    print_queue.get(job_id).preview = None
    gone = client.get(told['preview'])
    assert (gone.status_code, gone.get_json()['message']) == (404, 'The preview of this print job is no longer kept')


def test_an_empty_printer_lists_every_line(client, queued, monkeypatch):
    _, _, release = _two_jobs(client, monkeypatch)
    answer = client.get(QUEUE, query_string={'printer': ''}).get_json()
    release.set()
    assert [line['printer'] for line in answer['printers']] == [DEVICE]


def test_continue_needs_a_printer(client):
    answer = client.post(f'{QUEUE}/continue', data={})
    assert (answer.status_code, answer.get_json()) == (400, {'success': False, 'message': 'printer is required'})


def test_a_job_without_a_preview_has_none(client, queued):
    answer = client.post(f'{API}/calibration/print', data={'printer': 'simulation', 'model': 'Niimbot/B1'})
    job_id = answer.get_json()['job']
    assert client.get(f'{QUEUE}/{job_id}').get_json()['preview'] is None
    assert client.get(f'{QUEUE}/{job_id}/preview.png').status_code == 404


def test_remove_over_the_api(client, queued, monkeypatch):
    first, second, release = _two_jobs(client, monkeypatch)
    waiting, printing = second.get_json()['job'], first.get_json()['job']
    removed = client.post(f'{QUEUE}/{waiting}/remove')
    assert (removed.status_code, removed.get_json()) == (200, {'success': True})
    assert client.get(f'{QUEUE}/{waiting}').get_json()['state'] == 'removed'
    again = client.post(f'{QUEUE}/{waiting}/remove')
    assert (again.status_code, again.get_json()) == (409, {'success': False, 'message': 'It is not waiting in the queue anymore'})
    refused = client.post(f'{QUEUE}/{printing}/remove')
    assert (refused.status_code, refused.get_json()) == (409, {'success': False, 'message': 'It is printing already'})
    unknown = client.post(f'{QUEUE}/nothing/remove')
    assert (unknown.status_code, unknown.get_json()) == (404, {
        'success': False, 'message': 'No such print job', 'start': print_queue.START})
    release.set()


def test_continue_over_the_api(client, queued):
    release = threading.Event()
    first = print_queue.add(DEVICE, lambda: release.wait(10) and 'The cover is open', title='A', labels=1, source='api',
                            explain=str)
    behind = print_queue.add(DEVICE, lambda: '', title='B', labels=1, source='api', explain=str)
    release.set()
    assert first.done.wait(10)
    assert client.get(QUEUE, query_string={'printer': DEVICE}).get_json()['held'] == 'The cover is open'
    assert behind.state == 'waiting'
    answer = client.post(f'{QUEUE}/continue', data={'printer': DEVICE})
    assert (answer.status_code, answer.get_json()) == (200, {'success': True})
    assert behind.done.wait(10)
    assert (behind.state, client.get(QUEUE, query_string={'printer': DEVICE}).get_json()['held']) == ('printed', None)


def test_printer_status_tells_the_queue(client, queued, monkeypatch):
    first, second, release = _two_jobs(client, monkeypatch)
    state = {'errors': [], 'path': DEVICE, 'model': 'QL-800', 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': DEVICE})
    status = client.get(f'{API}/printer_status').get_json()
    release.set()
    assert status['printers'][0]['queue'] == {'printing': True, 'waiting': 1, 'held': False}
