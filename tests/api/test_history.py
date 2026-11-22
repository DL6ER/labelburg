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
from tests.helpers import EXAMPLE_FORMDATA, make_client, printed_days_ago

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


@pytest.mark.parametrize('source, told', [('mcp', 'an AI assistant'), ('designer', ''), ('homebox', 'another program')])
def test_where_a_label_printed_comes_from(client, source, told):
    job = printed(client, data=dict(LABEL, source=source))
    assert entry(client, job)['from'] == told


@pytest.mark.parametrize('limit', ['0', 'x', '-1', '201'])
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
    # Printed half a day ago, its time comes out otherwise now
    printed_days_ago(job, 0.5)
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
    # Printed again, it still asks: the date it printed is kept apart from the label
    printed_days_ago(same, 0.5)
    assert entry(client, same)['again_asks'] == ['date', 'asn']
    del calls[:]
    printed(client, f'history/{same}/print', data={'printer': 'simulation'})
    assert calls == [(1, 0, int(started)), (1, 1, int(started))]
    del calls[:]
    fresh = printed(client, f'history/{same}/print', json={'printer': 'simulation', 'as_before': 0, 'source': 'designer'})
    assert calls == [(3, 0, None), (3, 1, None)]
    assert paperless.next_asn(client.application.config, repo) == 5
    assert entry(client, fresh)['from'] == ''
    printed_days_ago(fresh, 0.5)
    assert entry(client, fresh)['again_asks'] == ['date', 'asn']


def test_a_label_with_a_time_of_its_own_keeps_it(client, monkeypatch):
    times, create = [], routes.create_label_from_request

    def recorded(d={}, files={}, counter=0, *args, **kwargs):
        times.append(d.get('timestamp'))
        return create(d, files, counter, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    job = printed(client, data=dict(LABEL, text=lines('{{datetime:%Y}}'), timestamp='1700000000'))
    del times[:]
    printed(client, f'history/{job}/print', data={'printer': 'simulation', 'as_before': '0'})
    assert set(times) == {'1700000000'}


def test_a_date_blank_left_to_the_day_asks_for_the_date(client):
    def asks(blanks, fields, text='Made {{blank:Day}}'):
        job = printed(client, data=dict(LABEL, text=lines(text), blanks=json.dumps(blanks), fields=json.dumps(fields)))
        printed_days_ago(job, 1)
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


def test_switched_off_the_labels_kept_go_and_none_are_kept(client):
    upload = dict(LABEL, print_type='image', image=(io.BytesIO(png()), 'photo.png'))
    kept = printed(client, data=upload, content_type='multipart/form-data')
    response = client.post(f'{API}/settings/labels', data={'history': '0'})
    assert response.get_json()['settings']['history'] == '0'
    folder = os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], print_store.HISTORY)
    assert [name for name in os.listdir(folder) if name != print_store.LOCK_FILE] == []
    printed(client, data=LABEL)
    assert history(client) == {'enabled': False, 'entries': []}
    for path, method in (('preview.png', 'get'), ('label', 'get'), ('print', 'post')):
        response = getattr(client, method)(f'{API}/history/{kept}/{path}')
        assert (response.status_code, response.get_json()['message']) == (404, print_store.NOT_KEPT)
    assert client.post(f'{API}/settings/labels', data={'history': ''}).get_json()['settings']['history'] == ''
    assert history(client)['entries'] == []
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
                 print_store.NOT_CLEARED, print_store.PRINTING_AT_RESTART, print_store.LIMIT_REFUSED, refusals.OTHER_LABEL,
                 refusals.SAME_NUMBERS):
        assert refusals.message(ValueError(text), refusals.INVALID_INPUT) == text


def test_printed_again_it_is_cut_as_the_printer_is_set(client):
    job = printed(client, data=dict(LABEL, cut='each', half_cut='1', chain='0', print_count='2'))
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'cut': 'last'}).status_code == 200
    again = printed(client, f'history/{job}/print', data={'printer': 'simulation'})
    assert print_store.entry(again)['printer']['cut'] == 'last'


def webhook_job(client, monkeypatch, **values) -> str:
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    state = {'errors': [], 'path': 'simulation', 'model': 'QL-800', 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [state], 'selected': 'simulation'})
    return printed(client, 'webhook/print', json={'password': 'secret', 'printer': 'simulation', 'images': [base64.b64encode(png()).decode()], **values})


def test_an_image_printed_again_prints_at_the_quality_of_the_printer(client, monkeypatch):
    job = webhook_job(client, monkeypatch, high_res=1)
    assert print_store.entry(job)['high_res'] is True
    # The QL-1100 prints at 300 dpi only
    again = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'model': 'QL-1100'})
    assert print_queue.get(again).state == 'printed'
    kept = print_store.entry(again)
    assert kept['high_res'] is False and kept['shapes'][0]['width'] * 2 == print_store.entry(job)['shapes'][0]['width']


@pytest.mark.parametrize('model', ['PT-P750W', 'Niimbot/B1', 'VC-500W'])
def test_an_image_is_not_printed_again_on_a_printer_without_its_label(client, monkeypatch, model):
    job = webhook_job(client, monkeypatch)
    response = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'model': model})
    assert (response.status_code, response.get_json()['message']) == (400, refusals.OTHER_LABEL)


def test_printed_again_with_copies_and_values_of_its_own(client):
    job = printed(client, data=dict(LABEL, text=lines('Jar {{blank:What}}'), blanks=json.dumps({'What': 'Peas'}), print_count='10',
                                    title='Peas'))
    told = entry(client, job)
    assert (told['labels'], told['copies']) == (10, 10)
    # A torn one of them
    one = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '1'})
    assert (entry(client, one)['labels'], entry(client, one)['title']) == (1, 'Peas')
    # As many as before, unless told
    assert entry(client, printed(client, f'history/{job}/print', data={'printer': 'simulation'}))['labels'] == 10
    beans = printed(client, f'history/{job}/print', json={'printer': 'simulation', 'print_count': 2, 'blanks': {'What': 'Beans'}, 'title': 'Beans'})
    told = entry(client, beans)
    assert (told['labels'], told['copies'], told['title']) == (2, 2, 'Beans')
    assert client.get(f'{API}/history/{beans}/label').get_json()['blanks'] == {'What': 'Beans'}
    response = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'print_count': '0'})
    assert response.get_json()['message'] == 'print_count must be greater than 0'


def test_the_same_numbers_are_never_more_than_before(client):
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    job = printed(client, data=dict(LABEL, text=lines('ASN{{asn:5}}'), print_count='3'))
    one = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '1'})
    assert print_store.entry(one)['asn'] == 1
    response = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'print_count': '4'})
    assert (response.status_code, response.get_json()['message']) == (400, refusals.SAME_NUMBERS)
    assert paperless.next_asn(client.application.config, repo) == 4
    fresh = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '4', 'as_before': '0'})
    assert (print_store.entry(fresh)['asn'], paperless.next_asn(client.application.config, repo)) == (4, 8)


def test_the_date_blanks_that_follow_the_day_are_told(client):
    fields = {'Day': {'type': 'date'}, 'Best': {'type': 'date', 'default': '+5d'}, 'Made': {'type': 'date'}}
    job = printed(client, data=dict(LABEL, text=lines('{{blank:Day}} {{blank:Best}} {{blank:Made}}'), fields=json.dumps(fields),
                                    blanks=json.dumps({'Made': '2026-11-03'})))
    printed_days_ago(job, 1)
    told = entry(client, job)
    assert (told['again_asks'], told['date_blanks']) == (['date'], ['Day', 'Best'])
    # A date of its own for each of them, nothing follows the day
    fixed = printed(client, f'history/{job}/print', json={'printer': 'simulation', 'blanks': {'Day': '2026-11-01', 'Best': '2026-11-06'}})
    assert (entry(client, fixed)['again_asks'], entry(client, fixed)['date_blanks']) == ([], [])


def test_a_picture_printed_again_takes_copies(client, monkeypatch):
    job = webhook_job(client, monkeypatch)
    assert entry(client, job)['copies'] == 1
    again = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '3', 'blanks': '{"x": "y"}'})
    assert (entry(client, again)['labels'], entry(client, again)['copies']) == (3, 3)
    assert print_queue.get(again).state == 'printed'


def test_a_picture_printed_again_is_cut_as_the_request_or_the_printer_says(client, monkeypatch):
    job = webhook_job(client, monkeypatch)
    assert print_store.entry(printed(client, f'history/{job}/print', data={'printer': 'simulation', 'cut': 'last'}))['printer']['cut'] == 'last'
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'cut': 'none'}).status_code == 200
    assert print_store.entry(printed(client, f'history/{job}/print', data={'printer': 'simulation'}))['printer']['cut'] == 'none'


def test_a_picture_printed_again_and_again_keeps_its_copies(client, monkeypatch):
    job = webhook_job(client, monkeypatch)
    three = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '3'})
    again = printed(client, f'history/{three}/print', data={'printer': 'simulation'})
    assert [(entry(client, each)['labels'], entry(client, each)['copies']) for each in (three, again)] == [(3, 3), (3, 3)]
    assert len(print_store.entry(again)['shapes']) == 1


@pytest.mark.parametrize('collate, allowed', [('1', True), ('0', False)])
def test_the_same_numbers_go_only_on_the_labels_that_had_them(client, collate, allowed):
    # Two files, three copies: as sets A B A B A B, grouped A A A B B B
    upload = dict(LABEL, print_type='image_qrcode_text', code_text='{{asn}}', text=lines('ASN{{asn:5}}'), print_count='3', collate=collate,
                  image=[(io.BytesIO(png()), 'a.png'), (io.BytesIO(png(color=255)), 'b.png')])
    job = printed(client, data=upload, content_type='multipart/form-data')
    response = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'print_count': '1'})
    if allowed:
        assert response.status_code == 200 and print_store.entry(response.get_json()['job'])['order'] == [0, 1]
    else:
        assert (response.status_code, response.get_json()['message']) == (400, refusals.SAME_NUMBERS)
    fresh = client.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'print_count': '1', 'as_before': '0'})
    assert fresh.status_code == 200


def test_the_same_date_is_written_as_it_was(client, monkeypatch):
    made, label = [], routes.SimpleLabel

    def recorded(*args, **kwargs):
        made.append((kwargs['date_format'], kwargs['date_language']))
        return label(*args, **kwargs)
    monkeypatch.setattr(routes, 'SimpleLabel', recorded)
    assert client.post(f'{API}/settings/labels', data={'date_format': '%d.%m.%Y', 'date_language': 'de'}).status_code == 200
    job = printed(client, data=dict(LABEL, text=lines('{{datetime:%d. %B %Y}}', '{{blank:Day}}'), fields=json.dumps({'Day': {'type': 'date'}})))
    assert client.post(f'{API}/settings/labels', data={'date_format': '', 'date_language': ''}).status_code == 200
    del made[:]
    printed(client, f'history/{job}/print', data={'printer': 'simulation'})
    assert set(made) == {('%d.%m.%Y', 'de')}
    del made[:]
    printed(client, f'history/{job}/print', data={'printer': 'simulation', 'as_before': '0'})
    assert set(made) == {('%Y-%m-%d', '')}


def test_a_label_printed_again_as_it_was_counts_as_it_did(client, monkeypatch):
    totals, create = [], routes.create_label_from_request

    def recorded(d={}, files={}, counter=0, *args, **kwargs):
        totals.append((counter, kwargs.get('total')))
        return create(d, files, counter, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    job = printed(client, data=dict(LABEL, text=lines('Box {{counter}} of {{total}}', 'ASN{{asn:5}}'), print_count='3'))
    del totals[:]
    # Box 1 of 3 that tore, not Box 1 of 1
    again = printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '1'})
    assert set(totals) == {(0, 3)}
    del totals[:]
    printed(client, f'history/{again}/print', data={'printer': 'simulation'})
    assert set(totals) == {(0, 3)}
    del totals[:]
    printed(client, f'history/{job}/print', data={'printer': 'simulation', 'print_count': '1', 'as_before': '0'})
    assert set(totals) == {(0, 1)}


def test_the_same_numbers_refused_tell_what_to_do():
    assert refusals.SAME_NUMBERS == 'Print all its labels again, or choose the next number.'


def test_a_label_printed_is_named_by_its_first_line_as_printed_also_a_saved_one(client):
    saved = {'name': 'Pantry_jar.json', 'title': 'Pantry jar', 'label_size': '62', 'text': [{'text': '{{blank:What}}', 'size': '40'}]}
    assert client.post(f'{API}/repository/save', json=saved).status_code == 200
    # E.g. Grocy prints its saved label by name for each product
    by_name = printed(client, 'repository/print', json={'name': 'Pantry jar', 'printer': 'simulation', 'blanks': {'What': 'Rice'}})
    fields = {'Day': {'type': 'date', 'format': '%m/%d/%Y'}}
    filled = printed(client, data=dict(LABEL, text=lines('Opened {{blank:Day}}'), blanks=json.dumps({'Day': '2026-11-22'}), fields=json.dumps(fields)))
    assert [entry(client, job)['title'] for job in (by_name, filled)] == ['Rice', 'Opened 11/22/2026']
    # Printed again with other values it is named anew, twice it still knows the saved label
    again = printed(client, f'history/{by_name}/print', json={'printer': 'simulation', 'blanks': {'What': 'Pesto'}})
    twice = printed(client, f'history/{again}/print', json={'printer': 'simulation', 'blanks': {'What': 'Beans'}})
    assert [entry(client, job)['title'] for job in (again, twice)] == ['Pesto', 'Beans']
    assert print_store.entry(twice)['named'] == 'Pantry_jar.json'
    assert client.get(f'{API}/history/{twice}/label').get_json()['name'] == 'Pantry_jar.json'
    again = printed(client, f'history/{filled}/print', json={'printer': 'simulation', 'blanks': {'Day': '2026-12-24'}})
    assert entry(client, again)['title'] == 'Opened 12/24/2026'


def test_the_date_is_asked_for_only_where_it_comes_out_otherwise_today(client):
    def asks(text, days=0):
        job = printed(client, data=dict(LABEL, text=lines(text)))
        printed_days_ago(job, days)
        return entry(client, job)['again_asks']
    # Printed earlier today, the same date comes out
    assert asks('{{datetime:%d.%m.%Y}}') == []
    assert asks('{{datetime:%d.%m.%Y}}', days=1) == ['date']
    # A time comes out otherwise a minute later
    assert asks('{{datetime:%H:%M}}', days=1 / 1440) == ['date']
    # Neither a date nor a time
    assert asks('Jar', days=1) == []


def test_a_label_without_text_is_named_by_its_code_else_by_the_name_of_its_picture(client):
    from tests.helpers import _pdf
    code = printed(client, data=dict(LABEL, print_type='qrcode_text', text=lines(''), code_text='Shelf {{blank:No}}', blanks=json.dumps({'No': '7'})))
    picture = printed(client, data=dict(LABEL, print_type='image', text='[]', image=(io.BytesIO(png()), 'cat.jpg')), content_type='multipart/form-data')
    pdf = printed(client, data=dict(LABEL, print_type='image', text='[]', image=(io.BytesIO(_pdf((100, 50))), 'manual.pdf')), content_type='multipart/form-data')
    assert [entry(client, job)['title'] for job in (code, picture, pdf)] == ['Shelf 7', 'cat', 'manual']


def test_the_answer_of_a_print_names_it_as_the_list_does(client):
    saved = {'name': 'Spice.json', 'label_size': '62', 'text': [{'text': 'Spice {{blank:What}}', 'size': '40'}]}
    assert client.post(f'{API}/repository/save', json=saved).status_code == 200
    answers = [client.post(f'{API}/print', data=dict(LABEL, text=lines('Jar {{blank:What}}'), blanks=json.dumps({'What': 'Peas'}))).get_json(),
               client.post(f'{API}/repository/print', json={'name': 'Spice', 'printer': 'simulation', 'blanks': {'What': 'Chili'}}).get_json()]
    answers.append(client.post(f'{API}/history/{answers[0]["job"]}/print', json={'printer': 'simulation', 'blanks': {'What': 'Beans'}}).get_json())
    assert [answer['title'] for answer in answers] == ['Jar Peas', 'Spice Chili', 'Jar Beans']
    assert [entry(client, answer['job'])['title'] for answer in answers] == ['Jar Peas', 'Spice Chili', 'Jar Beans']


def test_a_label_printed_again_with_other_values_counts_anew(client, monkeypatch):
    totals, create = [], routes.create_label_from_request
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: (totals.append(kwargs.get('total')), create(*args, **kwargs))[1])
    job = printed(client, data=dict(LABEL, text=lines('Box {{counter}} of {{total}} {{blank:What}}'), blanks=json.dumps({'What': 'Peas'}), print_count='3'))
    del totals[:]
    # Box 1 of 1 Beans, a label of its own
    beans = printed(client, f'history/{job}/print', json={'printer': 'simulation', 'print_count': 1, 'blanks': {'What': 'Beans'}})
    assert set(totals) == {1} and entry(client, beans)['title'] == 'Box 1 of 1 Beans'
    del totals[:]
    # The same values, as the label designer sends them: one of the three
    printed(client, f'history/{job}/print', json={'printer': 'simulation', 'print_count': 1, 'blanks': {'What': 'Peas'}})
    assert set(totals) == {3}
