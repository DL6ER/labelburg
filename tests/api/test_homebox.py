"""Labels for Homebox: its external label service gets the label as an image,
its print command sends that image back to be printed."""

import io

import pytest
from PIL import Image

from app.labeldesigner import printer, routes
from tests.helpers import make_client, pixels
from tests.repository.helpers import SAVE_URL

URL = '/labeldesigner/api/homebox'
SETTINGS_URL = '/labeldesigner/api/settings'
DEMO = 'storage/013-homebox-62.json'
# What Homebox 0.27 sends to its external label service
# (HBOX_LABEL_MAKER_LABEL_SERVICE_URL) for an item, with its default sizes
HOMEBOX_QUERY = {
    'Width': '526', 'Height': '200', 'QrSize': '136', 'Margin': '32', 'ComponentPadding': '32',
    'TitleText': '000-123', 'TitleFontSize': '32.000000', 'DescriptionText': 'Cordless drill',
    'DescriptionFontSize': '25.600000', 'Dpi': '72.000000', 'URL': 'https://homebox.example/a/000-123',
    'DynamicLength': 'true', 'AdditionalInformation': 'Property of Ada',
}
HOMEBOX_HEADERS = {'User-Agent': 'Homebox-LabelMaker/1.0', 'Accept': 'image/*'}


def _status(model='QL-800', path='simulation', **status):
    state = {'errors': [], 'path': path, 'model': model, 'media_width': None, 'media_length': 0, **status}
    return {**state, 'printers': [state], 'selected': path}


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, empty_repo=True)


@pytest.fixture
def queued(monkeypatch):
    """The labels printed, and the label size of their job. Each label tells
    how it was printed and its job."""
    labels = []

    def add(self, label, high_res=False, fast=False):
        label.high_res, label.fast, label.job = high_res, fast, self
        labels.append((self.label_size, label))
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', add)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    return labels


def _size(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def test_label_for_homebox(client):
    response = client.get(URL, query_string=HOMEBOX_QUERY, headers=HOMEBOX_HEADERS)
    assert response.status_code == 200 and response.content_type == 'image/png'
    # The demo label "Homebox" with what Homebox sends, at the resolution of
    # the printer, not Homebox's sizes in pixels
    demo = client.get('/labeldesigner/api/demos/preview', query_string={'id': DEMO, **HOMEBOX_QUERY})
    assert pixels(response.data) == pixels(demo.data)
    assert _size(response.data)[1] == 696


@pytest.mark.parametrize('status, size', [
    # Die-cut labels across, endless tape along the tape, as on 62 mm tape
    (_status(media_width=29, media_length=90), (991, 306)),
    (_status('PT-P750W', media_width=12), None),
    (_status('Niimbot/D110_M', media_width=12, media_length=40), (320, 96)),
])
def test_label_for_homebox_on_the_tape_in_the_printer(client, monkeypatch, status, size):
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config: status)
    response = client.get(URL, query_string=HOMEBOX_QUERY, headers=HOMEBOX_HEADERS)
    assert response.status_code == 200
    if size:
        assert _size(response.data) == size
    else:
        # 12 mm TZe tape is 70 dots across, the label runs along it
        width, height = _size(response.data)
        assert height == 70 and width > height


def test_saved_label_for_homebox(client):
    label = {'name': 'Bin', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': '{{blank:URL}}',
             'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:DescriptionText}} ({{blank:TitleText}})'}]}
    assert client.post(SAVE_URL, json=label).status_code == 200
    expected = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Bin.json', **HOMEBOX_QUERY})
    # Homebox replaces the query of the address, the name is in its path
    for name in ('Bin', 'Bin.json'):
        response = client.get(f'{URL}/{name}', query_string=HOMEBOX_QUERY, headers=HOMEBOX_HEADERS)
        assert response.status_code == 200 and response.content_type == 'image/png'
        assert pixels(response.data) == pixels(expected.data)


def test_unknown_saved_label_for_homebox(client):
    response = client.get(f'{URL}/Nothing', query_string=HOMEBOX_QUERY)
    assert response.status_code == 404
    assert response.get_json() == {'success': False, 'message': 'Not found'}


def test_label_for_homebox_is_not_printed(client, queued):
    # Homebox shows the label it gets, it prints it with its print command
    assert client.get(URL, query_string=HOMEBOX_QUERY).status_code == 200
    assert queued == []


def test_homebox_prints_the_label_it_got(client, queued):
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    # Its print command sends the file, e.g. with wget --post-file
    response = client.post(URL, data=image, headers={'Content-Type': 'image/png', 'User-Agent': 'Wget/1.25.0'})
    assert response.get_json() == {'success': True}
    [(label_size, label)] = queued
    assert label_size == '62'
    # Printed as it was shown, dot for dot
    printed = io.BytesIO()
    label.generate().save(printed, 'PNG')
    assert pixels(printed.getvalue()) == pixels(image)


def test_homebox_prints_on_the_tape_in_the_printer(client, queued, monkeypatch):
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config: _status(media_width=29, media_length=90))
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    assert client.post(URL, data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    [(label_size, label)] = queued
    assert label_size == '29x90'
    printed = io.BytesIO()
    label.generate().save(printed, 'PNG')
    assert pixels(printed.getvalue()) == pixels(image)


def test_homebox_prints_a_saved_label_on_its_size(client, queued):
    label = {'name': 'Small', 'label_size': '29x90', 'print_type': 'text',
             'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:TitleText}}'}]}
    assert client.post(SAVE_URL, json=label).status_code == 200
    image = client.get(f'{URL}/Small', query_string=HOMEBOX_QUERY).data
    assert client.post(f'{URL}/Small', data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    assert [label_size for label_size, _ in queued] == ['29x90']


def test_homebox_prints_a_label_of_its_own_templates(client, queued):
    # Labels of Homebox's own templates go through the same print command,
    # they are fitted to the label
    image = io.BytesIO()
    Image.new('L', (400, 160), 'white').save(image, 'PNG')
    assert client.post(URL, data=image.getvalue(), headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    [(label_size, label)] = queued
    assert label_size == '62'


@pytest.mark.parametrize('body, headers', [
    (b'', {'Content-Type': 'image/png'}),
    (b'not an image', {'Content-Type': 'image/png'}),
])
def test_homebox_print_without_an_image(client, queued, body, headers):
    response = client.post(URL, data=body, headers=headers)
    assert response.status_code == 400
    assert response.get_json() == {'success': False, 'message': 'Send the label as an image, e.g. the file Homebox made'}
    assert queued == []


def test_homebox_print_of_an_unknown_saved_label(client, queued):
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    response = client.post(f'{URL}/Nothing', data=image, headers={'Content-Type': 'image/png'})
    assert response.status_code == 404
    assert queued == []


def test_homebox_print_from_another_website_is_refused(client, queued):
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    response = client.post(URL, data=image, headers={'Content-Type': 'image/png', 'Origin': 'https://elsewhere.example'})
    assert response.status_code == 403
    assert queued == []


def test_homebox_prints_a_saved_label_at_its_resolution(client, queued):
    label = {'name': 'Sharp', 'label_size': '62', 'print_type': 'text', 'high_res': '1',
             'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:TitleText}}'}]}
    assert client.post(SAVE_URL, json=label).status_code == 200
    image = client.get(f'{URL}/Sharp', query_string=HOMEBOX_QUERY).data
    assert client.post(f'{URL}/Sharp', data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    [(label_size, printed_label)] = queued
    # Made at 600 dpi, printed at 600 dpi dot for dot
    assert printed_label.high_res is True
    printed = io.BytesIO()
    printed_label.generate().save(printed, 'PNG')
    assert pixels(printed.getvalue()) == pixels(image)


def _printed(label) -> bytes:
    printed = io.BytesIO()
    label.generate().save(printed, 'PNG')
    return printed.getvalue()


def test_homebox_prints_on_the_tape_set_by_hand(client, queued, monkeypatch):
    # As in the label designer, a tape set in the printer settings rather
    # than the one the printer reports
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config: _status(media_width=62))
    assert client.post(SETTINGS_URL, data={'printer': 'simulation', 'label_size': '29x90'}).status_code == 200
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    assert _size(image) == (991, 306)
    assert client.post(URL, data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    [(label_size, label)] = queued
    assert label_size == '29x90'
    assert pixels(_printed(label)) == pixels(image)


@pytest.mark.parametrize('quality, high_res, fast', [('high', True, False), ('fast', False, True), ('quality', False, False)])
def test_homebox_prints_in_the_quality_set_for_the_printer(client, queued, quality, high_res, fast):
    # Also a label saved in the label designer, which saves high_res 0
    label = {'name': 'Bin', 'label_size': '62', 'print_type': 'text', 'high_res': '0', 'fast_printing': '1',
             'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:TitleText}}'}]}
    assert client.post(SAVE_URL, json=label).status_code == 200
    assert client.post(SETTINGS_URL, data={'printer': 'simulation', 'quality': quality}).status_code == 200
    for name in ('', '/Bin'):
        queued.clear()
        image = client.get(URL + name, query_string=HOMEBOX_QUERY).data
        assert client.post(URL + name, data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
        [(_, printed)] = queued
        assert (printed.high_res, printed.fast) == (high_res, fast)
        # Made and printed at the same resolution, dot for dot
        assert pixels(_printed(printed)) == pixels(image)


def test_homebox_cuts_and_leaves_first_as_set_for_the_printer(client, queued):
    settings = {'printer': 'simulation', 'cut': 'none', 'leaves_first': 'bottom'}
    assert client.post(SETTINGS_URL, data=settings).status_code == 200
    image = client.get(URL, query_string=HOMEBOX_QUERY).data
    assert client.post(URL, data=image, headers={'Content-Type': 'image/png'}).get_json() == {'success': True}
    [(_, label)] = queued
    assert (label.job.cut, label.job.leaves_first) == ('none', 'bottom')
