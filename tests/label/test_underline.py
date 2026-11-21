"""Underlined lines: a bar under the text of the line, as thick as its size
asks for, in the color of the line, white on black lines too and along the
tape."""

import io
import json

import pytest
from PIL import Image, ImageChops

from tests.helpers import make_client

PREVIEW = '/labeldesigner/api/preview'


@pytest.fixture
def client(tmp_path):
    yield make_client(tmp_path)


def line(text='HHHH', size=60, **more):
    return dict({'font': 'DejaVu Sans,Book', 'text': text, 'size': str(size), 'align': 'center'}, **more)


def preview(client, lines, **settings):
    data = {'print_type': 'text', 'label_size': '62', 'orientation': 'standard', 'margin_top': '12', 'margin_bottom': '12',
            'margin_left': '20', 'margin_right': '20', 'text': json.dumps(lines)}
    data.update(settings)
    response = client.post(PREVIEW, data=data)
    assert response.status_code == 200, response.data
    return response


def image(response) -> Image.Image:
    return Image.open(io.BytesIO(response.data)).convert('RGB')


def added(plain, underlined) -> tuple:
    """The box of what underlining changed, both labels of the same size."""
    a, b = image(plain), image(underlined)
    assert a.size == b.size
    return ImageChops.difference(a, b).convert('L').point(lambda v: 255 if v else 0).getbbox()


def ink(img: Image.Image, box=None) -> tuple:
    """The box of what is not white, in box if given."""
    part = img.crop(box) if box else img
    found = ImageChops.invert(part.convert('L')).point(lambda v: 255 if v > 40 else 0).getbbox()
    if found and box:
        found = (found[0] + box[0], found[1] + box[1], found[2] + box[0], found[3] + box[1])
    return found


def parts(response) -> list:
    return json.loads(response.headers['X-Label-Parts'])


def test_lines_without_underline_stay_as_they_were(client):
    plain = preview(client, [line(), line('Second', 40)])
    unset = preview(client, [line(underline=False), line('Second', 40, underline=False)])
    assert image(plain).tobytes() == image(unset).tobytes()


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_the_underline_is_under_the_text(client, orientation):
    # The first of two lines, its room reaches below its letters
    plain = preview(client, [line(), line('Second', 40)], orientation=orientation)
    underlined = preview(client, [line(underline=True), line('Second', 40)], orientation=orientation)
    box = added(plain, underlined)
    first = parts(plain)[0]['box']
    letters = ink(image(plain), first)
    # As wide as the text with the room around its letters, a little below
    # the bottom of the "H"s
    assert letters[0] - 60 * .15 <= box[0] <= letters[0] and letters[2] <= box[2] <= letters[2] + 60 * .15
    assert letters[3] <= box[1] <= letters[3] + 60 * .2
    assert box[3] <= parts(plain)[1]['box'][1]
    # A bar of the line's color, nothing else changed
    bar = image(underlined).crop(box)
    assert bar.getcolors() == [(bar.width * bar.height, (0, 0, 0))]
    # The line's part holds it, the next line is where it was
    held = parts(underlined)[0]['box']
    assert held[0] <= box[0] and held[1] <= box[1] and box[2] <= held[2] and box[3] <= held[3]
    assert parts(underlined)[1] == parts(plain)[1]


def test_the_underline_grows_with_the_size(client):
    thickness = {}
    for size in (30, 60, 120):
        box = added(preview(client, [line(size=size), line('Second', 30)]), preview(client, [line(size=size, underline=True), line('Second', 30)]))
        thickness[size] = box[3] - box[1]
    assert 1 <= thickness[30] < thickness[60] < thickness[120]


@pytest.mark.parametrize('label_size, color, rgb', [
    pytest.param('62red', 'red', (255, 0, 0), id='red'),
    pytest.param('vc25', '#0000ff', (0, 0, 255), id='color tape'),
])
def test_the_underline_has_the_color_of_the_line(client, label_size, color, rgb):
    lines = [line(size=40, color=color), line('Second', 30)]
    plain = preview(client, lines, label_size=label_size)
    underlined = preview(client, [dict(lines[0], underline=True), lines[1]], label_size=label_size)
    box = added(plain, underlined)
    bar = image(underlined).crop(box)
    assert bar.getcolors() == [(bar.width * bar.height, rgb)]


def test_white_on_black_has_a_white_underline(client):
    lines = [line(inverted=True), line('Second', 40)]
    plain = preview(client, lines)
    underlined = preview(client, [dict(lines[0], underline=True), lines[1]])
    box = added(plain, underlined)
    bar = image(underlined).crop(box)
    assert bar.getcolors() == [(bar.width * bar.height, (255, 255, 255))]
    # Inside the black bar of the line
    assert image(plain).getpixel((box[0], box[3])) == (0, 0, 0)


def test_the_last_line_gets_room_for_its_underline(client):
    plain = preview(client, [line()])
    underlined = preview(client, [line(underline=True)])
    a, b = image(plain), image(underlined)
    assert b.height > a.height
    # Below the letters, the margin kept below it
    assert ink(b)[3] > ink(a)[3]
    assert b.height - ink(b)[3] >= 12
    # The part of the line holds it
    box = parts(underlined)[0]['box']
    assert box[3] >= ink(b)[3]


def test_a_wrapped_line_is_underlined_in_each_of_its_lines(client):
    text = 'HHHH HHHH HHHH HHHH HHHH HHHH'
    plain = preview(client, [line(text, 80), line('Second', 30)], text_wrap='1', text_fit='none')
    underlined = preview(client, [line(text, 80, underline=True), line('Second', 30)], text_wrap='1', text_fit='none')
    a, b = image(plain), image(underlined)
    diff = ImageChops.difference(a, b).convert('L').point(lambda v: 255 if v else 0)
    rows = [y for y in range(diff.height) if diff.crop((0, y, diff.width, y + 1)).getbbox()]
    bands = 1 + sum(1 for y0, y1 in zip(rows, rows[1:]) if y1 > y0 + 1)
    assert bands >= 2


def test_the_to_do_box_is_not_underlined(client):
    lines = [line(checkbox=True, align='left'), line('Second', 40)]
    plain = preview(client, lines)
    underlined = preview(client, [dict(lines[0], underline=True), lines[1]])
    box = added(plain, underlined)
    first = parts(plain)[0]['box']
    # The box of 48 dots is in front of the text, the underline starts where
    # the text does
    assert box[0] > first[0] + 48
    letters = ink(image(plain), (box[0], first[1], first[2], first[3]))
    assert box[0] <= letters[0] <= box[0] + 60 * .15


@pytest.mark.parametrize('text', ['\u200b', '\u2060', '\u200b\u200b'], ids=['zero width space', 'word joiner', 'two'])
@pytest.mark.parametrize('align', ['left', 'center', 'right'])
@pytest.mark.parametrize('inverted', [False, True], ids=['plain', 'white on black'])
@pytest.mark.parametrize('last', [False, True], ids=['first', 'last'])
def test_a_line_that_draws_nothing_has_no_underline(client, text, align, inverted, last):
    # Pasted text can leave a character that takes no room alone on a line
    empty, other = line(text, align=align, inverted=inverted), line('Second', 40)
    order = (lambda a, b: [b, a]) if last else (lambda a, b: [a, b])
    plain = preview(client, order(empty, other))
    underlined = preview(client, order(dict(empty, underline=True), other))
    assert image(underlined).tobytes() == image(plain).tobytes()
