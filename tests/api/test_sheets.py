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
from tests.helpers import make_client

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
    assert response.get_json()['success'] is True, response.get_json()
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


def test_preview_of_a_sheet_is_one_label(client):
    response = client.post(f'{API}/preview', data={'label_size': 'niimbot15x8-2x3', 'text': COUNTER, **B1})
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).size == (120, 64)


def test_config_gives_one_label_and_its_sheet(client):
    html = client.get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'DESIGNER_CONFIG = (.*);</script>', html).group(1))
    sizes = {size['id']: size for size in config['label_sizes']}
    page = sizes['niimbot15x8-2x3']
    assert (page['tape'], page['dots'], page['pieces'], page['sheet']) == ([15, 8], [120, 64], [2, 3, 0], [30, 24])
    assert sizes['niimbot13x35-2x1']['tape'] == [13, 35] and sizes['niimbot13x35-2x1']['sheet'] == [28, 35]
    assert sizes['niimbot50x30']['sheet'] == sizes['niimbot50x30']['tape'] == [50, 30]
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
    assert response.get_json()['success'] is True, response.get_json()
    [sheet] = printed
    assert sheet.size == (240, 192)
    assert [_inked(cell) for cell in _cells(sheet)] == [True, False, False, False, False, False]


def test_homebox_fills_one_label(client, printed, monkeypatch):
    _b1_with_tape(client, monkeypatch)
    image = _png(Image.new('L', (120, 64), 0))
    response = client.post(f'{API}/homebox', data=image, headers={'Content-Type': 'image/png'})
    assert response.get_json() == {'success': True}
    [sheet] = printed
    assert sheet.size == (240, 192)
    assert [_inked(cell) for cell in _cells(sheet)] == [True, False, False, False, False, False]


def test_single_label_sizes_print_as_before(client, printed):
    _print(client, label_size='niimbot50x30', print_count=2)
    assert [image.size for image in printed] == [(384, 240), (384, 240)]
