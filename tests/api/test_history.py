"""The labels printed, from every browser and program: listed, opened in
the label designer, printed again and cleared."""
import base64
import io
import json
import os
import threading

import pytest
from PIL import Image

from app.labeldesigner import paperless, print_queue, print_store, refusals, routes
from tests.helpers import EXAMPLE_FORMDATA, make_client

API = '/labeldesigner/api'


def lines(*texts):
    return json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '40'} for text in texts])


LABEL = dict(EXAMPLE_FORMDATA, printer='simulation', text=lines('Jar'))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(print_store, 'ENABLED', True)
    monkeypatch.setattr(print_store, 'FOLDER', None)
    yield make_client(tmp_path, empty_repo=True)
    print_store.close()


def printed(client, path='print', **kwargs) -> str:
    response = client.post(f'{API}/{path}', **kwargs)
    assert response.status_code == 200, response.get_json()
    return response.get_json()['job']


def history(client, **query) -> dict:
    response = client.get(f'{API}/history', query_string=query)
    assert response.status_code == 200
    return response.get_json()


def entry(client, job_id) -> dict:
    return next(told for told in history(client)['entries'] if told['id'] == job_id)


def png(width=200, height=100, color=0) -> bytes:
    image = io.BytesIO()
    Image.new('L', (width, height), color).save(image, 'PNG')
    return image.getvalue()


def test_the_labels_printed_newest_first(client):
    first = printed(client, data=dict(LABEL, text=lines('Flour'), title='Flour', source='designer'))
    second = printed(client, data=dict(LABEL, text=lines('Sugar'), print_count='2'))
    told = history(client)
    assert told['enabled'] is True
    assert [entry['id'] for entry in told['entries']] == [second, first]
    newest = told['entries'][0]
    assert {key: newest[key] for key in ('title', 'labels', 'from', 'from_detail', 'printer_name', 'state', 'message', 'again', 'open', 'again_asks')} == {
        'title': 'Sugar', 'labels': 2, 'from': 'another program', 'from_detail': 'Sent over the API', 'printer_name': 'Demo printer',
        'state': 'printed', 'message': '', 'again': True, 'open': True, 'again_asks': []}
    assert newest['time'].endswith('Z') and newest['preview'] == f'{API}/history/{second}/preview.png'
    assert told['entries'][1]['from'] == ''
    # A page at a time
    assert [entry['id'] for entry in history(client, limit=1)['entries']] == [second]
    assert [entry['id'] for entry in history(client, limit=1, before=second)['entries']] == [first]
    preview = client.get(newest['preview'])
    assert preview.status_code == 200 and preview.mimetype == 'image/png'
    with Image.open(io.BytesIO(preview.data)) as image:
        assert max(image.size) <= routes.PREVIEW_SIZE


@pytest.mark.parametrize('limit', ['0', 'x', '-1'])
def test_a_limit_is_a_whole_number(client, limit):
    response = client.get(f'{API}/history', query_string={'limit': limit})
    assert response.status_code == 400


def test_labels_of_other_programs_are_listed_and_printed_again_but_not_opened(client, monkeypatch):
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    state = {'errors': [], 'path': 'simulation', 'model': 'QL-800', 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [state], 'selected': 'simulation'})
    webhook = printed(client, 'webhook/print', json={'password': 'secret', 'printer': 'simulation', 'images': [base64.b64encode(png()).decode()]})
    homebox = printed(client, 'homebox', data=png(), headers={'Content-Type': 'image/png'})
    assert [(entry['from'], entry['open'], entry['again']) for entry in history(client)['entries']] == [
        ('Homebox', False, True), ('another program', False, True)]
    response = client.get(f'{API}/history/{webhook}/label')
    assert (response.status_code, response.get_json()['message']) == (409, print_store.NOT_MADE_HERE)
    again = printed(client, f'history/{homebox}/print', data={'printer': 'simulation'})
    assert print_queue.get(again).state == 'printed'
    assert entry(client, again)['title'] == entry(client, homebox)['title']


def test_a_label_printed_is_opened_as_it_was_printed(client):
    fields = {'Content': {'type': 'text'}}
    job = printed(client, data=dict(LABEL, text=lines('Jar {{blank:Content}}'), blanks=json.dumps({'Content': 'Chili'}),
                                    fields=json.dumps(fields), title='Chili'))
    label = client.get(f'{API}/history/{job}/label').get_json()
    assert json.loads(label['text'])[0]['text'] == 'Jar {{blank:Content}}'
    assert (label['blanks'], label['fields'], label['label_size'], label['title']) == ({'Content': 'Chili'}, fields, '62', 'Chili')
    assert 'image_data' not in label and 'printer' not in label
    # Of an image, its first one
    upload = dict(LABEL, print_type='image', image=(io.BytesIO(png(color=128)), 'photo.png'))
    photo = printed(client, data=upload, content_type='multipart/form-data')
    label = client.get(f'{API}/history/{photo}/label').get_json()
    with Image.open(io.BytesIO(base64.b64decode(label['image_data']))) as image:
        assert image.size == (200, 100) and image.convert('L').getpixel((5, 5)) == 128
    assert (label['image_mime'], label['image_name']) == ('image/png', 'image.png')


def test_printed_again_on_the_printer_chosen_with_the_same_number(client, monkeypatch):
    calls, create = [], routes.create_label_from_request

    def recorded(d={}, files={}, counter=0, *args, **kwargs):
        if not threading.current_thread().name.startswith(print_queue.WORKER_PREFIX):
            calls.append((kwargs.get('asn'), counter, d.get('timestamp')))
        return create(d, files, counter, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    job = printed(client, data=dict(LABEL, text=lines('ASN{{asn:5}}', '{{datetime:%H:%M}}'), print_count='2'))
    told = entry(client, job)
    assert told['again_asks'] == ['date', 'asn']
    started = print_store.entry(job)['started_at']
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    assert paperless.next_asn(client.application.config, repo) == 3
    del calls[:]
    # As before unless told otherwise: the same numbers, the date it printed
    same = printed(client, f'history/{job}/print', data={'printer': 'simulation'})
    assert calls == [(1, 0, int(started)), (1, 1, int(started))]
    assert paperless.next_asn(client.application.config, repo) == 3
    assert entry(client, same)['again_asks'] == ['asn']
    del calls[:]
    fresh = printed(client, f'history/{job}/print', json={'printer': 'simulation', 'as_before': 0, 'source': 'designer'})
    assert calls == [(3, 0, None), (3, 1, None)]
    assert paperless.next_asn(client.application.config, repo) == 5
    assert entry(client, fresh)['from'] == ''


def test_a_date_blank_left_to_the_day_asks_for_the_date(client):
    def asks(blanks, fields, text='Made {{blank:Day}}'):
        job = printed(client, data=dict(LABEL, text=lines(text), blanks=json.dumps(blanks), fields=json.dumps(fields)))
        return entry(client, job)['again_asks']
    assert asks({}, {'Day': {'type': 'date'}}) == ['date']
    assert asks({'Day': '+5d'}, {'Day': {'type': 'date'}}) == ['date']
    assert asks({}, {'Day': {'type': 'date', 'default': '+1w'}}) == ['date']
    assert asks({'Day': '2026-11-03'}, {'Day': {'type': 'date'}}) == []
    assert asks({'Day': ''}, {'Day': {'type': 'date'}}) == []
    assert asks({}, {'Day': {'type': 'text'}}) == []
    # A time of its own
    job = printed(client, data=dict(LABEL, text=lines('{{datetime:%Y}}'), timestamp='1700000000'))
    assert entry(client, job)['again_asks'] == []


def test_a_label_whose_image_is_gone_is_not_printed_again(client):
    upload = dict(LABEL, print_type='image', image=(io.BytesIO(png()), 'photo.png'))
    job = printed(client, data=upload, content_type='multipart/form-data')
    os.remove(print_store.image_path(job, 0))
    assert entry(client, job)['again'] is False
    response = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation'})
    assert (response.status_code, response.get_json()['message']) == (409, print_store.NO_IMAGE)


@pytest.mark.parametrize('path, method', [('preview.png', 'get'), ('label', 'get'), ('print', 'post')])
def test_an_unknown_label_is_told(client, path, method):
    response = getattr(client, method)(f'{API}/history/unknown/{path}')
    assert (response.status_code, response.get_json()['message']) == (404, print_store.NO_SUCH_ENTRY)


def test_labels_are_not_kept_while_switched_off(client):
    kept = printed(client, data=LABEL)
    response = client.post(f'{API}/settings/labels', data={'history': '0'})
    assert response.get_json()['settings']['history'] == '0'
    printed(client, data=LABEL)
    assert history(client) == {'enabled': False, 'entries': []}
    for path, method in (('preview.png', 'get'), ('label', 'get'), ('print', 'post')):
        response = getattr(client, method)(f'{API}/history/{kept}/{path}')
        assert (response.status_code, response.get_json()['message']) == (404, print_store.NOT_KEPT)
    assert client.post(f'{API}/settings/labels', data={'history': ''}).get_json()['settings']['history'] == ''
    assert [entry['id'] for entry in history(client)['entries']] == [kept]
    assert client.post(f'{API}/settings/labels', data={'history': 'no'}).status_code == 400


def test_clear_removes_them_all(client):
    printed(client, data=LABEL)
    assert client.post(f'{API}/history/clear').get_json() == {'success': True}
    assert history(client)['entries'] == []


def test_a_job_is_told_from_the_history_after_a_restart(client, tmp_path):
    job = printed(client, data=LABEL)
    print_queue.reset()
    print_store.close()
    client = make_client(tmp_path, empty_repo=True)
    told = client.get(f'{API}/queue/{job}').get_json()
    assert {key: told[key] for key in ('id', 'state', 'message', 'labels', 'from', 'start')} == {
        'id': job, 'state': 'printed', 'message': '', 'labels': 1, 'from': 'another program', 'start': print_queue.START}
    assert client.get(told['preview']).mimetype == 'image/png'
    assert client.get(f'{API}/queue/unknown').status_code == 404


def test_the_texts_of_the_history_reach_the_client():
    for text in (print_store.NO_SUCH_ENTRY, print_store.NO_IMAGE, print_store.NOT_MADE_HERE, print_store.NOT_KEPT,
                 print_store.NOT_CLEARED, print_store.PRINTING_AT_RESTART):
        assert refusals.message(ValueError(text), refusals.INVALID_INPUT) == text
