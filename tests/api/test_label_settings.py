"""The settings of labels kept on the server for every browser: how dates
are written and the language of their names, the order of copies of
several files and the font of new labels."""

import base64
import datetime
import io
import json
import os
import re

import pytest
from PIL import Image

from app.labeldesigner import dates, printer_settings, refusals
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _pdf, EXAMPLE_FORMDATA, make_client, outcome, pixels

URL = '/labeldesigner/api/settings/labels'
NONE_SET = {'date_format': '', 'date_language': '', 'collate': '', 'font_family': '', 'history': ''}


def test_label_settings_are_kept_for_every_browser(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.get(URL).get_json() == NONE_SET
    r = client.post(URL, data={'date_format': '%d.%m.%Y', 'font_family': 'DejaVu Sans'})
    assert r.status_code == 200
    assert r.get_json() == {'success': True, 'settings': {**NONE_SET, 'date_format': '%d.%m.%Y', 'font_family': 'DejaVu Sans'}}
    # Only what is named changes, another browser's change stays
    assert client.post(URL, json={'collate': 0}).get_json()['settings'] == {
        **NONE_SET, 'date_format': '%d.%m.%Y', 'collate': '0', 'font_family': 'DejaVu Sans'}
    # Kept in the folder of the labels, also for the next start of the server
    again = make_client(tmp_path, empty_repo=True)
    assert again.get(URL).get_json() == {**NONE_SET, 'date_format': '%d.%m.%Y', 'collate': '0', 'font_family': 'DejaVu Sans'}
    # An empty value removes one
    assert client.post(URL, data={'font_family': '', 'collate': ''}).get_json()['settings'] == {**NONE_SET, 'date_format': '%d.%m.%Y'}


def test_label_settings_are_kept_with_the_printer_settings(tmp_path, monkeypatch):
    # In the same file, a change of the one keeps the other; tcp://a is
    # known as if the search found it
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://a'})
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'collate': '0'})
    client.post('/labeldesigner/api/settings', data={'printer': 'tcp://a', 'cut': 'last', 'quality': 'fast'})
    client.post(URL, data={'date_format': '%m/%d/%Y'})
    assert json.loads((tmp_path / 'label_repo' / printer_settings.FILE).read_text()) == {
        'printers': {'tcp://a:9100': {'cut': 'last'}}, 'quality': 'fast', 'labels': {'collate': '0', 'date_format': '%m/%d/%Y'}}
    assert client.get('/labeldesigner/api/settings', query_string={'printer': 'tcp://a'}).get_json() == {'cut': 'last', 'quality': 'fast'}
    # All of them removed, nothing of them is left
    client.post(URL, data={'date_format': '', 'collate': ''})
    assert 'labels' not in json.loads((tmp_path / 'label_repo' / printer_settings.FILE).read_text())


def test_label_settings_are_checked(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    for wrong in ({'date_format': '%A'}, {'date_format': '%Y'}, {'collate': '2'}, {'collate': 'yes'}, {'history': 'off'},
                  {'date_language': 'xx'}, {'date_language': 'en'}, {'date_language': 'DE'}, {'date_language': 'de_DE'},
                  {'font_family': 'No Such Font'}, {'font_family': 'DejaVu Sans,Book'}, {'quality': 'fast'},
                  {'date_format': '%d.%m.%Y', 'colour': 'red'}, {}):
        r = client.post(URL, data=wrong)
        assert r.status_code == 400, wrong
        assert r.get_json() == {'success': False, 'message': printer_settings.LABEL_SETTINGS_MESSAGE}
    for wrong in ({'collate': [0]}, {'date_format': {'x': 1}}, {'font_family': None}, {'date_language': ['de']}):
        assert client.post(URL, json=wrong).status_code == 400, wrong
    assert client.get(URL).get_json() == NONE_SET
    assert not (tmp_path / 'label_repo' / printer_settings.FILE).exists()


def test_label_settings_take_json_numbers_and_flags(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(URL, json={'collate': True}).get_json()['settings']['collate'] == '1'
    assert client.post(URL, json={'collate': 0}).get_json()['settings']['collate'] == '0'
    assert client.post(URL, json={'collate': 2}).status_code == 400


def test_label_settings_written_by_hand_that_are_wrong_are_not_set(tmp_path):
    # The file may be edited by hand, a font may be gone since
    client = make_client(tmp_path, empty_repo=True)
    path = tmp_path / 'label_repo' / printer_settings.FILE
    path.write_text(json.dumps({'printers': {}, 'labels': {'date_format': '%A', 'date_language': 'xx', 'collate': 0, 'font_family': 'Gone Sans', 'history': False}}))
    assert client.get(URL).get_json() == NONE_SET
    path.write_text(json.dumps({'printers': {}, 'labels': ['collate', '0']}))
    assert client.get(URL).get_json() == NONE_SET
    assert client.post(URL, data={'collate': '0'}).get_json()['settings'] == {**NONE_SET, 'collate': '0'}
    path.write_text('{"printers": [')
    assert client.get(URL).get_json() == NONE_SET


def test_label_settings_that_cannot_be_saved_are_told(tmp_path, monkeypatch):
    # E.g. a file written by another user: it is kept as it is
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    client.post(URL, data={'collate': '0'})
    path = tmp_path / 'label_repo' / printer_settings.FILE
    before = path.read_text()

    def unreadable(file, *args, **kwargs):
        if str(file) == str(path):
            raise PermissionError(13, 'Permission denied')
        return open(file, *args, **kwargs)
    monkeypatch.setattr(routes, 'open', unreadable, raising=False)
    r = client.post(URL, data={'date_format': '%d.%m.%Y'})
    assert r.status_code == 500 and r.get_json() == {'success': False, 'message': refusals.LABEL_SETTINGS_NOT_SAVED}
    assert path.read_text() == before


def test_the_refusal_of_label_settings_is_sent_as_it_is():
    text = printer_settings.LABEL_SETTINGS_MESSAGE
    assert refusals.message(ValueError(text), '-') == text


def test_the_page_is_given_the_label_settings(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.post(URL, data={'collate': '0', 'font_family': 'DejaVu Sans'})

    def config(page):
        html = client.get(page).get_data(as_text=True)
        return json.loads(re.search(r'DESIGNER_CONFIG = (\{.*\});</script>', html).group(1))
    assert config('/labeldesigner/')['label_settings'] == {**NONE_SET, 'collate': '0', 'font_family': 'DejaVu Sans'}
    # The date formats it offers are those the server takes
    assert config('/labeldesigner/')['date_formats'] == ['%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y']
    # The font menu lists the families, the weight, width and slant of each style
    assert config('/labeldesigner/')['font_families'] == client.application.extensions['fonts'].families()


def _orders(client, tmp_path, **request):
    """The pages of a PDF of two pages printed twice, 0 for the first page
    and 1 for the second, in the order they were printed."""
    from app.labeldesigner import printer
    folder = tmp_path / 'simulated_labels'
    for name in os.listdir(folder) if folder.exists() else []:
        os.remove(folder / name)
    r = client.post(**request)
    assert r.status_code == 200, r.get_json()
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in names]
    return [0 if h == heights[0] else 1 for h in heights]


def test_copies_are_ordered_as_set_where_the_request_leaves_it_out(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]',
            'printer': 'simulation', 'print_count': '2', 'pdf_pages': 'all'}

    def printed(**given):
        files = [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf')]
        return _orders(client, tmp_path, path='/labeldesigner/api/print', data={**data, **given, 'image': files})
    # As sets, as before, where nothing is set
    assert printed() == [0, 1, 0, 1]
    client.post(URL, data={'collate': '0'})
    assert printed() == printed(collate='') == [0, 0, 1, 1]
    assert printed(collate='1') == [0, 1, 0, 1]
    client.post(URL, data={'collate': '1'})
    assert printed() == [0, 1, 0, 1]
    assert printed(collate='0') == [0, 0, 1, 1]


@pytest.mark.parametrize('saved, requested, order', [
    (None, None, [0, 0, 1, 1]), (None, '1', [0, 1, 0, 1]), ('1', None, [0, 1, 0, 1]), ('1', '0', [0, 0, 1, 1]),
])
def test_saved_labels_are_ordered_as_set_where_they_and_the_request_leave_it_out(tmp_path, saved, requested, order):
    # The order saved with a label comes first, as its cutting does
    client = make_client(tmp_path, empty_repo=True)
    client.post(URL, data={'collate': '0'})
    label = {'name': 'pages', 'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '1', 'text': [],
             'pdf_pages': 'all', 'image_mime': 'application/pdf', 'image_data': base64.b64encode(_pdf((300, 200), (200, 300))).decode('ascii')}
    if saved is not None:
        label['collate'] = saved
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    body = {'name': 'pages.json', 'printer': 'simulation', 'print_count': 2}
    if requested is not None:
        body['collate'] = requested
    assert _orders(client, tmp_path, path='/labeldesigner/api/repository/print', json=body) == order


def _made(monkeypatch):
    """The labels the requests make, as they are made."""
    from app.labeldesigner import routes
    made = []
    make = routes.create_label_from_request
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: made.append(make(*args, **kwargs)) or made[-1])
    return made


def test_lines_without_a_font_are_in_the_font_of_new_labels(tmp_path, monkeypatch):
    # E.g. those of API requests and of the MCP tools. Lines with a font keep
    # it, one that is not installed is replaced by the default font as before.
    made = _made(monkeypatch)
    client = make_client(tmp_path, empty_repo=True)
    default = ','.join(client.application.extensions['fonts'].get_default_font())
    text = json.dumps([{'text': 'Flour', 'size': '40'}, {'text': 'Sugar', 'size': '40', 'font': 'DejaVu Sans,Bold'},
                       {'text': 'Salt', 'size': '40', 'font': 'Gone Sans,Regular'}])

    def fonts():
        assert client.post('/labeldesigner/api/preview', data={'printer': 'simulation', 'text': text}).status_code == 200
        return [line['font'] for line in made[-1].text]
    assert fonts() == [default, 'DejaVu Sans,Bold', default]
    # A family of the font menu, e.g. the Mono of DejaVu Sans, as well
    for family, font in (('Droid Sans', 'Droid Sans,Regular'), ('DejaVu Sans', 'DejaVu Sans,Book'),
                         ('DejaVu Sans Mono', 'DejaVu Sans,Mono / Book')):
        assert client.post(URL, data={'font_family': family}).status_code == 200
        assert fonts() == [font, 'DejaVu Sans,Bold', default]
    client.post(URL, data={'font_family': ''})
    assert fonts() == [default, 'DejaVu Sans,Bold', default]


def test_text_printed_by_the_mcp_tools_is_in_the_font_of_new_labels(tmp_path, monkeypatch):
    made = _made(monkeypatch)
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    client.post(URL, data={'font_family': 'Droid Sans'})
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
        'name': 'print_text', 'arguments': {'printer': 'Kitchen', 'label_size': '62', 'count': 1, 'lines': ['Flour']}}},
        headers={'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'})
    assert not response.get_json()['result'].get('isError')
    assert made and all(label.text[0]['font'] == 'Droid Sans,Regular' for label in made)


def test_date_blanks_without_a_format_are_written_as_set(tmp_path):
    # A format of the blank comes first, e.g. one saved with the label
    client = make_client(tmp_path, empty_repo=True)
    fields = {'Due': {'type': 'date'}, 'Made on': {'type': 'date', 'format': '%d/%m/%Y'}}
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Due}}|{{blank:Made on}}'}]),
            'fields': json.dumps(fields), 'blanks': json.dumps({'Due': '2026-11-01', 'Made on': '2026-11-02'})}

    def written():
        with client.application.test_request_context():
            label = create_label_from_request(data)
            label.process_templates()
            return label.text[0]['text']
    assert written() == '2026-11-01|02/11/2026'
    client.post(URL, data={'date_format': '%d.%m.%Y'})
    assert written() == '01.11.2026|02/11/2026'
    client.post(URL, data={'date_format': '%m/%d/%Y'})
    assert written() == '11/01/2026|02/11/2026'
    client.post(URL, data={'date_format': ''})
    assert written() == '2026-11-01|02/11/2026'


def test_saved_labels_write_their_date_blanks_as_set(tmp_path, monkeypatch):
    # E.g. printed by Grocy or the MCP tools: saved without a format, as the
    # label designer saves them where the server keeps one
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    client.post(URL, data={'date_format': '%d.%m.%Y'})
    label = {'name': 'Jar', 'label_size': '62', 'text': [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Filled {{blank:Filled on}}'}],
             'fields': {'Filled on': {'type': 'date'}}}
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    r = client.post('/labeldesigner/api/repository/print', json={'name': 'Jar.json', 'blanks': {'Filled on': '2026-11-03'}})
    assert outcome(client, r) == ('printed', '')
    queued[0].process_templates()
    assert queued[0].text[0]['text'] == 'Filled 03.11.2026'


def test_the_language_of_dates_is_kept(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    for language in dates.LANGUAGES:
        assert client.post(URL, data={'date_language': language}).get_json()['settings'] == {**NONE_SET, 'date_language': language}
    assert client.post(URL, json={'date_language': 'de'}).get_json()['settings'] == {**NONE_SET, 'date_language': 'de'}
    assert make_client(tmp_path, empty_repo=True).get(URL).get_json() == {**NONE_SET, 'date_language': 'de'}
    # Empty is English
    assert client.post(URL, data={'date_language': ''}).get_json()['settings'] == NONE_SET
    # The refusal names them
    assert 'date_language (cs, da, de, es, fi, fr, it, nb, nl, pl, pt or sv' in printer_settings.LABEL_SETTINGS_MESSAGE


def test_the_page_is_given_the_languages_of_dates(tmp_path):
    html = make_client(tmp_path, empty_repo=True).get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'DESIGNER_CONFIG = (\{.*\});</script>', html).group(1))
    # Each in itself, English the empty one and first
    assert config['date_languages'] == [['', 'English'], ['cs', 'Čeština'], ['da', 'Dansk'], ['de', 'Deutsch'], ['es', 'Español'],
                                        ['fr', 'Français'], ['it', 'Italiano'], ['nl', 'Nederlands'], ['nb', 'Norsk bokmål'],
                                        ['pl', 'Polski'], ['pt', 'Português'], ['fi', 'Suomi'], ['sv', 'Svenska']]
    assert config['datetime_codes']['languages']['fr'] == {'%a': 'sam.', '%A': 'samedi', '%b': 'mars', '%B': 'mars'}


def test_requests_name_months_and_weekdays_in_the_language_of_dates(tmp_path, monkeypatch):
    # Without the label designer: previews and prints of the API, saved
    # labels printed by name, e.g. by Grocy or the MCP tools
    made = _made(monkeypatch)
    client = make_client(tmp_path, empty_repo=True)
    when = str(int(datetime.datetime(2026, 3, 7, 14, 5).timestamp()))
    text = json.dumps([{'text': '{{datetime:%A, %-d. %B %Y}}', 'size': '40'}])
    assert client.post('/labeldesigner/api/preview', data={'text': text, 'timestamp': when}).status_code == 200
    assert made[-1].text[0]['text'] == 'Saturday, 7. March 2026'
    client.post(URL, data={'date_language': 'de'})
    assert client.post('/labeldesigner/api/preview', data={'text': text, 'timestamp': when}).status_code == 200
    assert made[-1].text[0]['text'] == 'Samstag, 7. März 2026'
    r = client.post('/labeldesigner/api/print', data={'text': text, 'timestamp': when, 'printer': 'simulation'})
    assert outcome(client, r) == ('printed', '')
    assert made[-1].text[0]['text'] == 'Samstag, 7. März 2026'
    label = {'name': 'Jar', 'label_size': '62', 'text': [{'size': '40', 'text': 'Filled {{blank:Filled on}}'}],
             'fields': {'Filled on': {'type': 'date', 'format': '%a %-d. %b'}}}
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    r = client.post('/labeldesigner/api/repository/print', json={'name': 'Jar.json', 'printer': 'simulation', 'blanks': {'Filled on': '2026-11-22'}})
    assert outcome(client, r) == ('printed', '')
    assert made[-1].text[0]['text'] == 'Filled So. 22. Nov.'


def test_the_name_of_the_month_is_drawn_in_the_language_of_dates(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.post(URL, data={'date_language': 'de'})

    def drawn(text, **more):
        r = client.post('/labeldesigner/api/preview', data={
            **EXAMPLE_FORMDATA, 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': text}]), **more})
        assert r.status_code == 200
        return pixels(r.data)
    assert drawn('{{datetime:%-d. %B %Y}}', timestamp=str(int(datetime.datetime(2026, 3, 7, 12).timestamp()))) == drawn('7. März 2026')
