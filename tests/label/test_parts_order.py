"""The parts of a label in a free order: image, code and text one after the
other in a row or in a column (parts_order, parts_direction)."""

import base64
import io
import itertools
import json
import re

import pytest
from PIL import Image

from app.labeldesigner.label import SMALL_BARCODE_NEXT_TO_IMAGE, check_parts_order
from tests.helpers import make_client, pixels, verify_image
from tests.repository.helpers import SAVE_URL

URL = 'https://example.com'
ORDERS = [','.join(order) for order in itertools.permutations(('image', 'code', 'text'))]
SIDES = {'left': ('row', True), 'right': ('row', False), 'above': ('column', True), 'below': ('column', False)}
MARGIN = 20


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def _label(**values) -> dict:
    """A gray square, a QR code and a line of text on 62 mm endless tape,
    across it."""
    data = {'label_size': '62', 'orientation': 'standard', 'print_type': 'image_qrcode_text', 'image_mode': 'grayscale', 'image_fit': '1',
            'code_text': URL, 'qrcode_size': '4', 'text_fit': 'shrink', 'border_thickness': '0',
            'margin_top': str(MARGIN), 'margin_bottom': str(MARGIN), 'margin_left': str(MARGIN), 'margin_right': str(MARGIN),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'DEF', 'size': '60', 'align': 'left'}])}
    data.update(values)
    if data['print_type'] in ('image', 'image_qrcode_text') and 'image' not in data:
        data['image'] = (io.BytesIO(_png(Image.new('L', (100, 100), 128))), 'square.png', 'image/png')
    return data


def _preview(client, data):
    return client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')


def _boxes(response) -> dict:
    """The boxes of the image, the code and the text (all lines) told with
    the preview."""
    boxes = {}
    for part in json.loads(response.headers['X-Label-Parts']):
        name = 'text' if part['part'] == 'line' else part['part']
        box = boxes.get(name, part['box'])
        boxes[name] = [min(box[0], part['box'][0]), min(box[1], part['box'][1]), max(box[2], part['box'][2]), max(box[3], part['box'][3])]
    return boxes


def _overlap(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# Endless tape, endless tape of a fixed length and a die-cut label
LABELS = {'62': {'label_size': '62'}, '62-80mm': {'label_size': '62', 'label_length': '80'}, '62x29': {'label_size': '62x29'}}
# Placed in the middle, at the top or bottom, and at 600 dpi, where the dots
# of the code take twice as many dots of the label to scan as well
PLACES = {'center': {}, 'top': {'vertical_align': 'top'}, 'bottom': {'vertical_align': 'bottom'},
          'high_res': {'high_res': '1', 'qrcode_size': '8'}}


@pytest.mark.parametrize('place', PLACES)
@pytest.mark.parametrize('fit', ['1', '0'], ids=['fitted', 'scaled'])
@pytest.mark.parametrize('label', LABELS)
@pytest.mark.parametrize('orientation', ['standard', 'rotated'], ids=['across', 'along'])
@pytest.mark.parametrize('direction', ['row', 'column'])
@pytest.mark.parametrize('order', ORDERS)
def test_the_parts_are_placed_in_their_order(tmp_path, order, direction, orientation, label, fit, place):
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order=order, parts_direction=direction, orientation=orientation, image_fit=fit,
                                       **LABELS[label], **PLACES[place]))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    assert response.headers['X-Label-Parts-Order'] == order
    assert response.headers['X-Label-Parts-Direction'] == direction
    width, height = Image.open(io.BytesIO(response.data)).size
    boxes = _boxes(response)
    assert sorted(boxes) == ['code', 'image', 'text']
    # One after the other as the label is read, at least the distance apart
    start, end = (0, 2) if direction == 'row' else (1, 3)
    assert ','.join(sorted(boxes, key=lambda name: boxes[name][start])) == order
    gap = int(response.headers['X-Label-Image-Text-Gap'])
    names = order.split(',')
    for before, after in zip(names, names[1:]):
        assert boxes[after][start] - boxes[before][end] >= gap
    for a, b in itertools.combinations(boxes.values(), 2):
        assert not _overlap(a, b)
    for box in boxes.values():
        assert MARGIN <= box[0] < box[2] <= width - MARGIN and MARGIN <= box[1] < box[3] <= height - MARGIN


@pytest.mark.parametrize('fit', ['1', '0'], ids=['fitted', 'scaled'])
@pytest.mark.parametrize('label_size, orientation', [('62', 'standard'), ('62', 'rotated'), ('62x29', 'standard')])
@pytest.mark.parametrize('vertical_align', ['top', 'center', 'bottom'])
@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('print_type, part', [('image', 'image'), ('qrcode_text', 'code'), ('image_qrcode_text', 'image')])
def test_an_order_like_a_side_is_laid_out_as_at_that_side(tmp_path, print_type, part, side, vertical_align, label_size, orientation, fit):
    # The image or code first is left or above the text, last right or
    # below; next to an image the code is on the other side of the text
    client = make_client(tmp_path)
    direction, first = SIDES[side]
    names = [part, 'text'] + (['code'] if print_type == 'image_qrcode_text' else [])
    order = ','.join(names if first else names[::-1])
    common = dict(print_type=print_type, label_size=label_size, orientation=orientation, vertical_align=vertical_align, image_fit=fit)
    at_side = _preview(client, _label(image_position=side, **common))
    ordered = _preview(client, _label(parts_order=order, parts_direction=direction, **common))
    assert at_side.status_code == ordered.status_code == 200
    assert pixels(ordered.data) == pixels(at_side.data)
    for header in ('X-Label-Parts', 'X-Label-Image-Position', 'X-Label-Parts-Order', 'X-Label-Parts-Direction', 'X-Label-Warning'):
        assert ordered.headers.get(header) == at_side.headers.get(header)
    assert at_side.headers['X-Label-Parts-Order'] == order


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_without_an_order_the_layout_tells_the_one_it_has(tmp_path, orientation):
    client = make_client(tmp_path)
    response = _preview(client, _label(print_type='image', orientation=orientation))
    # Above the text across the tape, left of it along the tape
    assert response.headers['X-Label-Parts-Order'] == 'image,text'
    assert response.headers['X-Label-Parts-Direction'] == ('column' if orientation == 'standard' else 'row')
    # An image with a code: left of the text, the code right of it
    response = _preview(client, _label(orientation=orientation))
    assert (response.headers['X-Label-Parts-Order'], response.headers['X-Label-Parts-Direction']) == ('image,text,code', 'row')
    # Alone, there is no order
    response = _preview(client, _label(print_type='image', text='[]'))
    assert 'X-Label-Parts-Order' not in response.headers and 'X-Label-Parts-Direction' not in response.headers


def test_parts_the_label_does_not_have_are_skipped(tmp_path):
    client = make_client(tmp_path)
    full = _preview(client, _label(print_type='qrcode_text', parts_order='image,text,code', parts_direction='row'))
    two = _preview(client, _label(print_type='qrcode_text', parts_order='text,code', parts_direction='row'))
    assert full.status_code == two.status_code == 200
    assert pixels(full.data) == pixels(two.data)
    assert full.headers['X-Label-Parts-Order'] == 'text,code'
    # Without text the image and the code keep their order
    response = _preview(client, _label(parts_order='code,text,image', parts_direction='row', text='[]', image_fit='0'))
    boxes = _boxes(response)
    assert response.headers['X-Label-Parts-Order'] == 'code,image'
    assert boxes['code'][2] < boxes['image'][0]


def test_parts_left_out_of_the_order_follow_it(tmp_path):
    # e.g. a code added to a label ordered before it had one
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order='text,image', parts_direction='row'))
    assert response.status_code == 200
    assert response.headers['X-Label-Parts-Order'] == 'text,image,code'
    boxes = _boxes(response)
    assert boxes['text'][2] < boxes['image'][0] < boxes['image'][2] < boxes['code'][0]


@pytest.mark.parametrize('orientation, direction', [('standard', 'column'), ('rotated', 'row')])
def test_an_order_alone_takes_the_direction_of_the_label(tmp_path, orientation, direction):
    client = make_client(tmp_path)
    alone = _preview(client, _label(print_type='image', orientation=orientation, parts_order='text,image'))
    side = _preview(client, _label(print_type='image', orientation=orientation, image_position='below' if direction == 'column' else 'right'))
    assert pixels(alone.data) == pixels(side.data)
    assert alone.headers['X-Label-Parts-Direction'] == direction
    # And a direction alone the order of the label
    row = _preview(client, _label(print_type='image', parts_direction='row'))
    assert pixels(row.data) == pixels(_preview(client, _label(print_type='image', image_position='left')).data)


def test_the_order_wins_over_the_side(tmp_path):
    client = make_client(tmp_path)
    both = _preview(client, _label(print_type='image', image_position='left', parts_order='text,image', parts_direction='row'))
    order = _preview(client, _label(print_type='image', parts_order='text,image', parts_direction='row'))
    assert pixels(both.data) == pixels(order.data)
    assert both.headers['X-Label-Image-Position'] == 'right'


def test_fitted_image_and_code_take_a_third_of_a_row_each(tmp_path):
    # The text right of both, where the maintainer wanted it
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order='image,code,text', parts_direction='row'))
    assert response.status_code == 200
    width = Image.open(io.BytesIO(response.data)).width
    third = (width - 2 * MARGIN) // 3
    boxes = _boxes(response)
    assert boxes['image'][0] == MARGIN and boxes['image'][2] - boxes['image'][0] == third
    assert boxes['code'][2] - boxes['code'][0] <= third
    assert boxes['code'][2] < boxes['text'][0]


@pytest.mark.parametrize('text_fit', ['none', 'shrink', 'fill'])
@pytest.mark.parametrize('order', ['image,code,text', 'text,code,image', 'code,text,image'])
def test_text_fits_its_room_in_a_row_on_a_die_cut_label(tmp_path, order, text_fit):
    client = make_client(tmp_path)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry shelf three', 'size': '40', 'align': 'left'}])
    response = _preview(client, _label(label_size='62x29', parts_order=order, parts_direction='row', text=text, text_wrap='1', text_fit=text_fit))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    width, height = Image.open(io.BytesIO(response.data)).size
    boxes = _boxes(response)
    assert ','.join(sorted(boxes, key=lambda name: boxes[name][0])) == order
    for a, b in itertools.combinations(boxes.values(), 2):
        assert not _overlap(a, b)
    for box in boxes.values():
        assert MARGIN <= box[0] and box[2] <= width - MARGIN and MARGIN <= box[1] and box[3] <= height - MARGIN
    # Wrapped into the room between its neighbors
    lines = [part for part in json.loads(response.headers['X-Label-Parts']) if part['part'] == 'line']
    assert len(lines) == 1


@pytest.mark.parametrize('vertical_align', ['top', 'bottom'])
def test_a_column_of_a_fixed_length_is_placed_at_the_top_or_bottom(tmp_path, vertical_align):
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order='code,text,image', parts_direction='column', label_length='100', image_fit='0',
                                       vertical_align=vertical_align))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None
    height = Image.open(io.BytesIO(response.data)).height
    boxes = _boxes(response)
    assert boxes['code'][3] < boxes['text'][1] < boxes['text'][3] < boxes['image'][1]
    if vertical_align == 'top':
        # The white around the code is part of it
        assert MARGIN <= boxes['code'][1] <= MARGIN + 4 * 4 and boxes['image'][3] < height // 2
    else:
        assert boxes['image'][3] == height - MARGIN and boxes['code'][1] > height // 2


@pytest.mark.parametrize('label_size, orientation, values', [
    ('62', 'standard', {'image_position': 'left'}),
    ('62', 'standard', {'parts_order': 'code,image', 'parts_direction': 'row'}),
    ('62x29', 'standard', {'image_position': 'below'}),
    ('62', 'rotated', {'parts_order': 'image,code', 'parts_direction': 'column'}),
])
@pytest.mark.parametrize('gap, barcode_type', [('', 'QR'), ('30', 'QR'), ('30', 'CODE128')])
def test_a_fitted_image_and_code_without_text_keep_their_distance(tmp_path, label_size, orientation, values, gap, barcode_type):
    # They share the side of the label that does not grow
    client = make_client(tmp_path)
    response = _preview(client, _label(label_size=label_size, orientation=orientation, text='[]', image_text_gap=gap, barcode_type=barcode_type,
                                       code_text='DEF', **values))
    # Fitted to half of a 29 mm label, the bars of a barcode are too narrow to scan
    small = barcode_type == 'CODE128' and label_size == '62x29'
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') == (SMALL_BARCODE_NEXT_TO_IMAGE if small else None)
    distance = int(response.headers['X-Label-Image-Text-Gap'])
    assert distance == int(gap or distance) > 0
    width, height = Image.open(io.BytesIO(response.data)).size
    boxes = _boxes(response)
    start, end = (0, 2) if response.headers['X-Label-Parts-Direction'] == 'row' else (1, 3)
    first, last = sorted(boxes.values(), key=lambda box: box[start])
    assert last[start] - first[end] >= distance
    for box in boxes.values():
        assert MARGIN <= box[0] and box[2] <= width - MARGIN and MARGIN <= box[1] and box[3] <= height - MARGIN


@pytest.mark.parametrize('first, second', [(21, 20), (20, 21)])
@pytest.mark.parametrize('values', [{'image_position': 'above'}, {'parts_order': 'image,text', 'parts_direction': 'column'},
                                    {'image_position': 'left', 'orientation': 'rotated'}, {'parts_order': 'text,image', 'parts_direction': 'row', 'orientation': 'rotated'}])
def test_a_fitted_part_stays_within_unequal_margins(tmp_path, values, first, second):
    # The margins across the direction differ by an odd number of dots
    client = make_client(tmp_path)
    margins = {'margin_left': str(first), 'margin_right': str(second), 'margin_top': str(first), 'margin_bottom': str(second)}
    response = _preview(client, _label(print_type='image', **margins, **values))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None
    width, height = Image.open(io.BytesIO(response.data)).size
    image = _boxes(response)['image']
    # It fills the room between them
    assert (image[2] - image[0] == width - first - second) or (image[3] - image[1] == height - first - second)
    assert first <= image[0] and image[2] <= width - second and first <= image[1] and image[3] <= height - second


@pytest.mark.parametrize('orientation', ['standard', 'rotated'], ids=['across', 'along'])
@pytest.mark.parametrize('values', [{'image_position': 'left'}, {'parts_order': 'text,image,code', 'parts_direction': 'row'},
                                    {'parts_order': 'image,code,text', 'parts_direction': 'column'}])
def test_an_image_larger_than_12_mm_tape_is_said_not_to_fit(tmp_path, values, orientation):
    # As it is, not fitted to the tape
    client = make_client(tmp_path)
    image = (io.BytesIO(_png(Image.new('L', (200, 200), 128))), 'large.png', 'image/png')
    response = _preview(client, _label(label_size='12', orientation=orientation, image_fit='0', image=image, **values))
    assert response.status_code == 200
    assert 'The image does not fit on the label, part of it is cut off.' in response.headers['X-Label-Warning']


def test_a_mirrored_label_reads_in_its_order_from_behind(tmp_path):
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order='image,code,text', parts_direction='row', mirror='1'))
    assert response.status_code == 200
    boxes = _boxes(response)
    assert ','.join(sorted(boxes, key=lambda name: boxes[name][0])) == 'text,code,image'


def test_a_cable_flag_has_the_parts_in_order_on_both_halves(tmp_path):
    client = make_client(tmp_path)
    response = _preview(client, _label(parts_order='image,code,text', parts_direction='row', orientation='rotated', cable_flag='1'))
    assert response.status_code == 200
    parts = json.loads(response.headers['X-Label-Parts'])
    halves = parts[:len(parts) // 2], parts[len(parts) // 2:]
    for half in halves:
        named = sorted(half, key=lambda part: part['box'][0])
        assert [part['part'] for part in named] == ['image', 'code', 'line']


@pytest.mark.parametrize('values, message', [
    ({'parts_order': 'image,image'}, 'parts_order must name each part once'),
    ({'parts_order': 'image,logo'}, 'parts_order must name image, code or text, separated by commas'),
    ({'parts_order': 'image;text'}, 'parts_order must name image, code or text, separated by commas'),
    ({'parts_direction': 'diagonal'}, 'parts_direction must be row or column'),
])
def test_an_order_that_is_none_is_refused(tmp_path, values, message):
    client = make_client(tmp_path, empty_repo=True)
    for url in ('/labeldesigner/api/preview', '/labeldesigner/api/print'):
        response = client.post(url, data=_label(printer='simulation', **values), content_type='multipart/form-data')
        assert response.status_code == 400
        assert response.get_json()['message'] == message
    body = {key: value for key, value in _label(**values).items() if key != 'image'}
    response = client.post(SAVE_URL, json=dict(body, name='Ordered'))
    assert response.status_code == 400
    assert response.get_json()['message'] == message


def test_a_saved_label_keeps_its_order(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    values = dict(parts_order='text,code,image', parts_direction='row')
    data = {key: value for key, value in _label(**values).items() if key != 'image'}
    body = dict(data, name='Ordered', text=json.loads(data['text']), image_data=base64.b64encode(_png(Image.new('L', (100, 100), 128))).decode('ascii'),
                image_mime='image/png', image_name='square.png')
    response = client.post(SAVE_URL, json=body)
    assert response.get_json() == {'success': True, 'name': 'Ordered.json'}
    loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Ordered.json'}).get_json()
    assert (loaded['parts_order'], loaded['parts_direction']) == ('text,code,image', 'row')
    stored = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Ordered.json'})
    made = _preview(client, _label(**values))
    assert stored.status_code == 200 and pixels(stored.data) == pixels(made.data)
    assert stored.headers['X-Label-Parts-Order'] == 'text,code,image'


def test_the_order_is_described_in_the_api(tmp_path):
    client = make_client(tmp_path)
    spec = client.get('/labeldesigner/api/openapi.json').get_json()
    properties = spec['components']['schemas']['Label']['properties']
    assert properties['parts_direction']['enum'] == ['row', 'column']
    assert 'parts_order' in properties
    headers = spec['paths']['/labeldesigner/api/preview']['post']['responses']['200']['headers']
    assert {'X-Label-Parts-Order', 'X-Label-Parts-Direction'} <= set(headers)


@pytest.mark.parametrize('value', ['image', 'text,code', 'image,code,text', ' image , text ', 'image, text', 'image,image', 'image,text,image',
                                   'image,code,text,code', 'image,logo', 'Image', 'image;text', 'image,,text'])
def test_the_order_is_described_as_it_is_read(tmp_path, value):
    client = make_client(tmp_path)
    pattern = client.get('/labeldesigner/api/openapi.json').get_json()['components']['schemas']['Label']['properties']['parts_order']['pattern']
    try:
        check_parts_order(value)
        accepted = True
    except ValueError:
        accepted = False
    assert bool(re.search(pattern, value)) == accepted


def test_reference_image(tmp_path):
    # Image, barcode and text in a row on 62 mm tape
    client = make_client(tmp_path)
    data = _label(parts_order='image,code,text', parts_direction='row', barcode_type='CODE128', code_text='DEF', qrcode_size='6',
                  text=json.dumps([{'font': 'DejaVu Sans,Bold', 'text': 'DEF', 'size': '90', 'align': 'left'}]),
                  image=(io.BytesIO(_png(Image.radial_gradient('L').resize((200, 200)))), 'round.png', 'image/png'))
    response = _preview(client, data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None
    verify_image(response.data, 'parts_order_row.png')
