"""Rolls with several labels to a page through the API: a label is made for
one label of the page, the copies fill the pages one after the other."""
import base64
import io
import json
import re

import pytest
from PIL import Image

from app.labeldesigner import paperless, printer, routes
from app.labeldesigner.drivers import niimbot
from app.labeldesigner.sheets import Sheet
from tests.helpers import _pdf, make_client, outcome, printing

API = '/labeldesigner/api'
B1 = {'printer': 'simulation', 'model': 'Niimbot/B1'}
COUNTER = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{counter}}', 'size': '30', 'align': 'center'}])
ASN = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{asn}}', 'size': '30', 'align': 'center'}])


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, empty_repo=True)


@pytest.fixture
def printed(monkeypatch):
    """The images the printer got, one for each page."""
    shots = []
    monkeypatch.setattr(printer._Simulator, '__call__', lambda self, image, name: shots.append(image))
    return shots


def _print(client, label_size='niimbot15x8-2x3', text=COUNTER, **values):
    response = client.post(f'{API}/print', data={'label_size': label_size, 'text': text, **B1, **values})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    return response


def _cells(sheet: Image.Image) -> list[Image.Image]:
    """The six 15 x 8 mm labels of a page, in the order they are filled."""
    return [sheet.crop((x, y, x + 120, y + 64)) for y in (0, 64, 128) for x in (0, 120)]


def _inked(image: Image.Image) -> bool:
    return image.convert('L').getextrema()[0] < 128


def _png(image: Image.Image) -> bytes:
    data = io.BytesIO()
    image.save(data, 'PNG')
    return data.getvalue()


def _b1_with_tape(client, monkeypatch, label_size='niimbot15x8-2x3'):
    """The simulator as a B1, with the tape set by hand in its settings."""
    state = {'errors': [], 'path': 'simulation', 'model': 'Niimbot/B1', 'media_width': None, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [state], 'selected': 'simulation'})
    response = client.post(f'{API}/settings', data={**B1, 'demo_model': 'Niimbot/B1', 'label_size': label_size})
    assert response.status_code == 200, response.get_json()


def preview(client, label_size='niimbot15x8-2x3', **values):
    response = client.post(f'{API}/preview', data={'label_size': label_size, 'text': COUNTER, **B1, **values})
    assert response.status_code == 200, response.get_json()
    return Image.open(io.BytesIO(response.data)), response


def test_preview_of_a_sheet_is_its_first_page(client):
    image, _ = preview(client)
    assert image.size == (240, 192)
    assert [_inked(cell) for cell in _cells(image)] == [True, False, False, False, False, False]


def test_preview_shows_the_copies_with_their_counters(client):
    image, _ = preview(client, print_count=3)
    cells = _cells(image)
    assert [_inked(cell) for cell in cells] == [True, True, True, False, False, False]
    assert len({cell.tobytes() for cell in cells[:3]}) == 3


def test_preview_of_lying_labels_shows_the_page_upright(client):
    # The lanes of 13 x 35 mm labels one above the other, the first on top
    image, response = preview(client, 'niimbot13x35-2x1')
    assert image.size == (280, 224)
    assert _inked(image.crop((0, 0, 280, 104))) and not _inked(image.crop((0, 120, 280, 224)))
    assert all(part['box'][3] <= 104 for part in json.loads(response.headers['X-Label-Parts']))


def test_parts_are_where_the_first_label_is_shown(client):
    # Text along the 30 x 15 mm labels: the first label is on the left
    image, response = preview(client, 'niimbot30x15-1x2', orientation='rotated')
    assert image.size == (240, 240)
    assert _inked(image.crop((0, 0, 120, 240))) and not _inked(image.crop((120, 0, 240, 240)))
    assert all(part['box'][2] <= 120 for part in json.loads(response.headers['X-Label-Parts']))


def test_config_gives_the_sheet_and_its_label(client):
    html = client.get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'DESIGNER_CONFIG = (.*);</script>', html).group(1))
    sizes = {size['id']: size for size in config['label_sizes']}
    page = sizes['niimbot15x8-2x3']
    assert (page['tape'], page['dots'], page['pieces'], page['piece']) == ([30, 24], [240, 192], [2, 3, 0], [15, 8])
    assert sizes['niimbot13x35-2x1']['tape'] == [28, 35] and sizes['niimbot13x35-2x1']['piece'] == [13, 35]
    assert sizes['niimbot50x30']['piece'] == sizes['niimbot50x30']['tape'] == [50, 30]
    assert sizes['niimbot50x30']['pieces'] == [1, 1, 0]


def test_copies_fill_sheets(client, printed):
    _print(client, print_count=8)
    assert [image.size for image in printed] == [(240, 192), (240, 192)]
    assert [_inked(cell) for cell in _cells(printed[0])] == [True] * 6
    assert [_inked(cell) for cell in _cells(printed[1])] == [True, True, False, False, False, False]


def test_counters_go_on_across_sheets(client, monkeypatch):
    rendered = []
    render = niimbot.Niimbot.render

    def keep(self, queue, entries, keep=None):
        if printing():
            rendered.extend(entry['label'] for entry in entries)
        return render(self, queue, entries, keep)
    monkeypatch.setattr(niimbot.Niimbot, 'render', keep)
    _print(client, print_count=8)
    assert all(isinstance(sheet, Sheet) for sheet in rendered)
    assert [[label._counter for label in sheet.labels] for sheet in rendered] == [[0, 1, 2, 3, 4, 5], [6, 7]]


def test_counters_count_up_on_the_page(client, printed):
    _print(client, print_count=8)
    first = [cell.tobytes() for cell in _cells(printed[0])]
    second = [cell.tobytes() for cell in _cells(printed[1])[:2]]
    assert len(set(first + second)) == 8


def test_asn_counts_labels_not_sheets(client, printed, monkeypatch):
    taken = []
    monkeypatch.setattr(paperless, 'next_asn', lambda config, folder, count=0: taken.append(count) or 1)
    _print(client, text=ASN, print_count=8)
    assert taken == [8]
    assert len(printed) == 2


def test_leaves_first_turns_the_sheet(client, printed):
    # The B1 prints the top of a label first, the bottom first turns the page
    _print(client, print_count=3, leaves_first='top')
    _print(client, print_count=3, leaves_first='bottom')
    top, bottom = printed
    # Turned as a whole, the labels filled first are at the other end, each
    # labels text dithered the other way round
    assert [_inked(cell) for cell in _cells(top)] == [True, True, True, False, False, False]
    assert [_inked(cell) for cell in _cells(bottom)] == [False, False, False, True, True, True]
    turned = bottom.rotate(180)
    assert sum(a != b for a, b in zip(turned.tobytes(), top.tobytes())) < len(top.tobytes()) // 50


def test_png_answer_is_the_first_sheet(client, printed):
    response = _print(client, print_count=8, return_format='png')
    image = Image.open(io.BytesIO(base64.b64decode(response.get_json()['image'])))
    assert image.size == (240, 192)


def test_label_made_for_another_size_goes_onto_one_label(client, printed, monkeypatch):
    _b1_with_tape(client, monkeypatch)
    _print(client, label_size='niimbot50x30', print_count=1)
    [sheet] = printed
    assert sheet.size == (240, 192)
    assert [_inked(cell) for cell in _cells(sheet)] == [True, False, False, False, False, False]


def test_webhook_images_fill_one_label(tmp_path, printed):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = _png(Image.new('L', (300, 160), 0))
    response = client.post(f'{API}/webhook/print', data={
        'password': 'secret', 'label_size': 'niimbot15x8-2x3', **B1, 'images': (io.BytesIO(image), 'black.png')})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    [sheet] = printed
    assert sheet.size == (240, 192)
    assert [_inked(cell) for cell in _cells(sheet)] == [True, False, False, False, False, False]


def test_homebox_fills_one_label(client, printed, monkeypatch):
    _b1_with_tape(client, monkeypatch)
    image = _png(Image.new('L', (120, 64), 0))
    response = client.post(f'{API}/homebox', data=image, headers={'Content-Type': 'image/png'})
    assert outcome(client, response) == ('printed', '')
    [sheet] = printed
    assert sheet.size == (240, 192)
    assert [_inked(cell) for cell in _cells(sheet)] == [True, False, False, False, False, False]


def test_single_label_sizes_print_as_before(client, printed):
    _print(client, label_size='niimbot50x30', print_count=2)
    assert [image.size for image in printed] == [(384, 240), (384, 240)]


def sheet_header(client, label_size, **values):
    """How the preview tells the copies fill the last page, None where it does not."""
    response = client.post(f'{API}/preview', data={'label_size': label_size, 'text': COUNTER, **B1, **values})
    assert response.status_code == 200, response.get_json()
    header = response.headers.get('X-Label-Sheet')
    return json.loads(header) if header else None


def test_last_sheet_of_eight_copies(client):
    assert sheet_header(client, 'niimbot15x8-2x3', print_count=8) == {
        'size': [30, 24], 'labels': [[0, 0, 15, 8], [15, 0, 15, 8], [0, 8, 15, 8], [15, 8, 15, 8], [0, 16, 15, 8], [15, 16, 15, 8]],
        'filled': 2, 'fill': 12}


def test_lanes_of_lying_labels_one_above_the_other(client):
    assert sheet_header(client, 'niimbot13x35-2x1') == {'size': [35, 28], 'labels': [[0, 0, 35, 13], [0, 15, 35, 13]], 'filled': 1, 'fill': 2}


def test_text_along_fills_the_lower_label_first(client):
    assert sheet_header(client, 'niimbot30x15-1x2', orientation='rotated')['labels'] == [[0, 0, 15, 30], [15, 0, 15, 30]]


def test_full_sheet_has_no_fill(client):
    assert sheet_header(client, 'niimbot15x8-2x3', print_count=6) | {'labels': None} == {'size': [30, 24], 'labels': None, 'filled': 6, 'fill': None}


def test_labels_per_copy_count(client):
    state = sheet_header(client, 'niimbot15x8-2x3', labels_per_copy=4)
    assert (state['filled'], state['fill']) == (4, 3)


def test_fill_above_the_limit_is_left_out(client):
    client.application.config['PRINT_MAX_COUNT'] = 10
    state = sheet_header(client, 'niimbot15x8-2x3', labels_per_copy=5)
    assert (state['filled'], state['fill']) == (5, None)


def test_pdf_pages_count_per_copy(client):
    state = sheet_header(client, 'niimbot15x8-2x3', print_type='image', pdf_pages='all',
                         image=(io.BytesIO(_pdf((100, 50), (100, 50), (100, 50))), 'pages.pdf'))
    assert (state['filled'], state['fill']) == (3, 2)


def test_pdf_pages_count_only_on_image_labels(client):
    # A text label prints once for each copy, whatever file comes with it
    state = sheet_header(client, 'niimbot15x8-2x3', pdf_pages='all', image=(io.BytesIO(_pdf((100, 50), (100, 50), (100, 50))), 'pages.pdf'))
    assert (state['filled'], state['fill']) == (1, 6)


def test_copies_below_one_count_as_one(client):
    assert sheet_header(client, 'niimbot15x8-2x3', print_count=0)['filled'] == 1


def test_single_sizes_tell_no_sheet(client):
    assert sheet_header(client, 'niimbot50x30', print_count=3) is None


@pytest.mark.parametrize('label_size', ['62', 'niimbot50x30'])
def test_counts_that_are_no_number_leave_other_sizes_alone(client, label_size):
    response = client.post(f'{API}/preview', data={'label_size': label_size, 'text': COUNTER, **B1,
                                                   'print_count': 'abc', 'labels_per_copy': '2.5'})
    assert response.status_code == 200
    assert 'X-Label-Sheet' not in response.headers


def test_counts_that_are_no_number_count_one(client):
    assert sheet_header(client, 'niimbot15x8-2x3', print_count='abc', labels_per_copy='1e3')['filled'] == 1


def test_log_counts_labels_not_pages(client, printed, caplog):
    caplog.set_level('INFO', logger='app.labeldesigner.printer')
    _print(client, print_count=8)
    assert 'Printing 8 labels' in caplog.text
