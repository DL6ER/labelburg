"""Sheets of labels of office printers through the API, on the demo printer
as an "Office printer": one label previewed or the whole sheet, where a
print starts and how it fills the sheets, and the settings of the printer."""
import base64
import io
import json
import re
import struct

import pytest
from PIL import Image

from app.labeldesigner import ipp, printer, sheets
from app.labeldesigner.drivers.ipp import documents
from app.labeldesigner.label_names import SIZES
from tests.helpers import make_client, outcome

API = '/labeldesigner/api'
DEMO = {'printer': 'simulation', 'model': 'Other/Office'}
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{counter}}', 'size': '40', 'inverted': True}])


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'demo_model': 'Other/Office'}).status_code == 200
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'label_size': 'sheet-l7160'}).status_code == 200
    return client


@pytest.fixture
def shots(monkeypatch):
    """The pages the demo printer got."""
    kept = []
    monkeypatch.setattr(printer._Simulator, '__call__', lambda self, image, name: kept.append(image))
    return kept


def preview(client, **values):
    response = client.post(f'{API}/preview', data={'label_size': 'sheet-l7160', 'text': TEXT, **DEMO, **values})
    assert response.status_code == 200, response.get_json()
    header = response.headers.get('X-Label-Sheet')
    return Image.open(io.BytesIO(response.data)), json.loads(header) if header else None


def inked(page: Image.Image) -> list[bool]:
    return [page.convert('L').crop((x + 10, y + 10, x + 740, y + 440)).getextrema()[0] < 128
            for x, y in sheets.spots(SIZES['sheet-l7160'], False)]


def test_preview_of_a_sheet_is_one_label(client):
    image, state = preview(client, print_count=3)
    assert image.size == (750, 450)
    assert (state['size'], len(state['labels']), state['labels'][0], state['labels'][4]) == ([210, 297], 21, [7.2, 15.2, 63.5, 38.1], [73.2, 53.2, 63.5, 38.1])
    assert {key: state[key] for key in ('start', 'filled', 'sheets', 'fill')} == {'start': 1, 'filled': 3, 'sheets': 1, 'fill': 21}


def test_preview_tells_where_a_print_starts_and_how_many_sheets(client):
    _, state = preview(client, print_count=5, sheet_start=20)
    assert {key: state[key] for key in ('start', 'filled', 'sheets', 'fill')} == {'start': 20, 'filled': 3, 'sheets': 2, 'fill': 23}


def test_whole_sheet_on_request(client):
    image, _ = preview(client, print_count=2, sheet_start=20, whole_sheet=1)
    assert image.size == (2480, 3508)
    assert inked(image) == [False] * 19 + [True, True]


def test_whole_sheet_of_plain_paper_with_its_cut_lines(client):
    image, _ = preview(client, whole_sheet=1, cut_lines=1, text='[]')
    assert image.convert('L').histogram()[sheets.CUT_GRAY] > 0
    plain, _ = preview(client, whole_sheet=1, text='[]')
    assert plain.convert('L').getextrema() == (255, 255)


def test_preview_goes_on_where_the_sheet_was_left(client, shots):
    assert outcome(client, client.post(f'{API}/print', data={'label_size': 'sheet-l7160', 'text': TEXT, **DEMO, 'print_count': 4})) == ('printed', '')
    _, state = preview(client, sheet_start='next')
    assert state['start'] == 5


def test_whole_page_counts_its_sheets(client):
    image, state = preview(client, label_size='sheet-a4', print_count=3)
    # Wide as a new die-cut label starts, it lies on the page
    assert image.size == (3508, 2480)
    assert {key: state[key] for key in ('labels', 'start', 'filled', 'sheets', 'fill')} == {
        'labels': [[0, 0, 210, 297]], 'start': 1, 'filled': 1, 'sheets': 3, 'fill': None}


def test_start_off_the_sheet_refuses_the_preview(client):
    response = client.post(f'{API}/preview', data={'label_size': 'sheet-l7160', 'text': TEXT, **DEMO, 'sheet_start': 22})
    assert (response.status_code, response.get_json()['message']) == (400, 'sheet_start must be 1 to 21 or next')


def test_other_label_sizes_take_no_start(client):
    response = client.post(f'{API}/preview', data={'label_size': '62', 'text': TEXT, 'sheet_start': 99})
    assert response.status_code == 200 and 'X-Label-Sheet' not in response.headers


def test_print_fills_sheets_from_its_start(client, shots):
    response = client.post(f'{API}/print', data={'label_size': 'sheet-l7160', 'text': TEXT, **DEMO, 'print_count': 5, 'sheet_start': 20,
                                                 'return_format': 'png'})
    assert outcome(client, response) == ('printed', '')
    assert [inked(page) for page in shots] == [[False] * 19 + [True, True], [True] * 3 + [False] * 18]
    # The page of the first sheet, as the printer gets it
    assert Image.open(io.BytesIO(base64.b64decode(response.get_json()['image']))).size == (2480, 3508)
    assert client.get(f'{API}/settings', query_string={'printer': 'simulation'}).get_json()['continue_at'] == {'sheet-l7160': 4}


def test_saved_label_prints_from_the_start_asked_for(client, shots):
    saved = client.post(f'{API}/repository/save', json={'name': 'Badge', 'label_size': 'sheet-l7160', 'text': json.loads(TEXT)})
    assert saved.status_code == 200, saved.get_json()
    response = client.post(f'{API}/repository/print', json={'name': 'Badge.json', **DEMO, 'sheet_start': 3, 'print_count': 2})
    assert outcome(client, response) == ('printed', '')
    assert inked(shots[0]) == [False, False, True, True] + [False] * 17


def test_webhook_images_go_on_from_the_start(tmp_path, client, shots):
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    black = io.BytesIO()
    Image.new('L', (300, 180), 0).save(black, 'PNG')
    response = client.post(f'{API}/webhook/print', data={'password': 'secret', **DEMO, 'sheet_start': 21,
                                                          'images': [(io.BytesIO(black.getvalue()), 'a.png'), (io.BytesIO(black.getvalue()), 'b.png')]})
    assert outcome(client, response) == ('printed', '')
    assert [inked(page)[20] for page in shots] == [True, False] and inked(shots[1])[0]
    refused = client.post(f'{API}/webhook/print', data={'password': 'secret', **DEMO, 'sheet_start': 0,
                                                         'images': [(io.BytesIO(black.getvalue()), 'a.png')]})
    assert (refused.status_code, refused.get_json()['message']) == (400, 'sheet_start must be 1 to 21 or next')


def test_printer_keeps_its_paper(client):
    answer = client.post(f'{API}/settings', data={'printer': 'simulation', 'cut_lines': '1'}).get_json()
    assert answer['settings']['cut_lines'] == '1'
    refused = client.post(f'{API}/settings', data={'printer': 'simulation', 'cut_lines': 'yes'})
    assert refused.status_code == 400


def test_status_tells_the_sheets_and_where_one_goes_on(client, shots):
    client.post(f'{API}/print', data={'label_size': 'sheet-l7160', 'text': TEXT, **DEMO, 'print_count': 2})
    [demo] = [p for p in client.get(f'{API}/printer_status').get_json()['printers'] if p['path'] == 'simulation']
    assert demo['takes'][:2] == ['sheet-a4', 'sheet-l7160'] and demo['label_size'] == 'sheet-l7160'
    assert demo['settings']['continue_at'] == {'sheet-l7160': 3}


def test_designer_gets_each_sheet(client):
    html = client.get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'DESIGNER_CONFIG = (.*);</script>', html).group(1))
    sizes = {size['id']: size for size in config['label_sizes']}
    sheet = sizes['sheet-l7160']['sheet']
    assert {key: sheet[key] for key in ('size', 'corner', 'page', 'short', 'chip')} == {
        'size': [210, 297], 'corner': 1.76, 'page': 'A4', 'short': 'Avery L7160', 'chip': 'L7160'}
    assert len(sheet['labels']) == 21 and sheet['labels'][1] == [73.25, 15.15, 63.5, 38.1]
    assert sizes['sheet-5160']['sheet']['page'] == 'US Letter'
    assert 'sheet' not in sizes['niimbot15x8-2x3'] and 'sheet' not in sizes['62']
    assert not [size for size in config['takes'] if size.startswith('sheet-')]


def test_print_job_asks_for_a_page_by_its_name_on_one_side():
    request = ipp.encode_print_job('ipp://192.0.2.1/ipp/print', 'image/pwg-raster', (21000, 29700),
                                   ipp.Paper(None, 'iso_a4_210x297mm', 'one-sided'))
    told = ipp.decode_response(request)
    assert (told['media'], told['sides'], told['print-scaling']) == ('iso_a4_210x297mm', 'one-sided', 'none')
    assert 'media-col' not in told
    with_margins = ipp.decode_response(ipp.encode_print_job('ipp://192.0.2.1/ipp/print', 'image/pwg-raster', (21000, 29700),
                                                            ipp.Paper(423)))
    assert with_margins['media-col']['media-top-margin'] == 423 and 'sides' not in with_margins


@pytest.mark.parametrize('size, name, points', [((21000, 29700), 'iso_a4_210x297mm', (595, 842)),
                                                ((21590, 27940), 'na_letter_8.5x11in', (612, 792)),
                                                ((10160, 15240), 'custom_101.6x152.4mm_101.6x152.4mm', (288, 432))])
def test_pages_are_named_and_measured_as_pwg_does(size, name, points):
    assert documents.size_name(size) == name
    head = documents.page_header(100, 100, 300, size, 1)
    assert struct.unpack('>II', head[352:360]) == points
