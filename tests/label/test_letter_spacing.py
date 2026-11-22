"""Letter spacing of a line: room added between its characters, in parts of
its size, measured as drawn wherever the text is laid out."""

import io
import json

import pytest
from PIL import Image, ImageChops

from app.labeldesigner import refusals
from app.labeldesigner.label import LETTER_SPACING_REFUSED, _graphemes
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import EXAMPLE_FORMDATA, make_client, pixels

# The steps of the label designer: narrow, normal, wide and wider
STEPS = (-0.05, 0, 0.1, 0.2)


def line(text='HHHH', size=50, **more):
    return dict({'font': 'DejaVu Sans,Book', 'text': text, 'size': str(size), 'align': 'center'}, **more)


def preview(client, lines, **settings):
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text_fit='none', border_thickness='0', text=json.dumps(lines))
    response = client.post('/labeldesigner/api/preview', data=dict(data, **settings))
    assert response.status_code == 200, response.data
    return response


def image(response) -> Image.Image:
    return Image.open(io.BytesIO(response.data)).convert('RGB')


def boxes(response) -> list:
    return [part['box'] for part in json.loads(response.headers['X-Label-Parts']) if part['part'] == 'line']


def ink(img: Image.Image) -> tuple:
    return ImageChops.invert(img.convert('L')).point(lambda v: 255 if v > 40 else 0).getbbox()


@pytest.mark.parametrize('value', [0, '0', '', None])
def test_lines_without_letter_spacing_stay_as_they_were(tmp_path, value):
    client = make_client(tmp_path)
    plain = preview(client, [line('Shelf 3'), line('spices {{mdi:leaf}}', 30)])
    unset = preview(client, [line('Shelf 3', letter_spacing=value), line('spices {{mdi:leaf}}', 30, letter_spacing=value)])
    assert pixels(unset.data) == pixels(plain.data)


def test_each_step_spaces_the_letters(tmp_path):
    client = make_client(tmp_path)
    plain = boxes(preview(client, [line()]))[0]
    widths = []
    for step in STEPS:
        box = boxes(preview(client, [line(letter_spacing=step)]))[0]
        widths.append(box[2] - box[0])
        # The letters keep their size
        assert (box[1], box[3]) == (plain[1], plain[3])
    assert widths == sorted(widths) and len(set(widths)) == len(STEPS)
    # Between the four letters, not after the last one
    for step, width in zip(STEPS, widths):
        assert abs(width - widths[1] - 3 * step * 50) <= 1


def test_a_single_character_has_nothing_to_space(tmp_path):
    client = make_client(tmp_path)
    assert pixels(preview(client, [line('H', letter_spacing=1)]).data) == pixels(preview(client, [line('H')]).data)


def test_icons_are_spaced_like_letters(tmp_path):
    client = make_client(tmp_path)
    plain = boxes(preview(client, [line('{{mdi:leaf}}{{mdi:leaf}}A')]))[0]
    spaced = boxes(preview(client, [line('{{mdi:leaf}}{{mdi:leaf}}A', letter_spacing=0.25)]))[0]
    assert abs((spaced[2] - spaced[0]) - (plain[2] - plain[0]) - 2 * 0.25 * 50) <= 1


def test_marks_stay_with_their_letter():
    assert _graphemes('Cafe\u0301 ok') == ['C', 'a', 'f', 'e\u0301', ' ', 'o', 'k']
    # The parts of an emoji, its skin tone and a flag of two letters
    assert _graphemes('\U0001F469\u200d\U0001F52C\U0001F44D\U0001F3FDx\U0001F1E9\U0001F1EA\U0001F1FA') == [
        '\U0001F469\u200d\U0001F52C', '\U0001F44D\U0001F3FD', 'x', '\U0001F1E9\U0001F1EA', '\U0001F1FA']
    assert _graphemes('') == []


@pytest.mark.parametrize('align', ['left', 'center', 'right'])
def test_spaced_lines_are_aligned_as_drawn(tmp_path, align):
    client = make_client(tmp_path)
    response = preview(client, [line('HHHH', align=align, letter_spacing=0.25), line('HH', align=align)])
    spaced, plain = boxes(response)
    if align == 'left':
        assert spaced[0] == plain[0]
    elif align == 'right':
        assert spaced[2] == plain[2]
    else:
        assert abs((spaced[0] + spaced[2]) - (plain[0] + plain[2])) <= 2
    # Alone, a centered line is in the middle of the label
    alone = preview(client, [line('HHHH', align=align, letter_spacing=0.25)])
    if align == 'center':
        x0, _, x1, _ = ink(image(alone))
        assert abs(x0 - (image(alone).width - x1)) <= 2


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
def test_wider_text_is_fitted_to_the_label(tmp_path, text_fit):
    client = make_client(tmp_path)
    text = 'Flour, sugar and salt'
    plain = preview(client, [line(text, 60)], text_fit=text_fit)
    wider = preview(client, [line(text, 60, letter_spacing=0.25)], text_fit=text_fit)
    assert 'X-Label-Warning' not in wider.headers
    x0, y0, x1, y1 = ink(image(wider))
    assert x0 >= 20 and x1 <= image(wider).width - 20
    # The spaces between the letters take room the letters had
    assert boxes(wider)[0][3] - boxes(wider)[0][1] < boxes(plain)[0][3] - boxes(plain)[0][1]


def test_spaced_text_too_wide_is_warned_about(tmp_path):
    client = make_client(tmp_path)
    assert 'X-Label-Warning' not in preview(client, [line('HHHHHHHHHH', 60)]).headers
    assert 'part of it is cut off' in preview(client, [line('HHHHHHHHHH', 60, letter_spacing=0.5)]).headers['X-Label-Warning']


def test_a_spaced_line_wraps_as_drawn(tmp_path):
    client = make_client(tmp_path)
    text = 'Store in a cool and dry place'
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text_wrap='1', text_fit='none')
    with client.application.test_request_context():
        def drawn(**more):
            label = create_label_from_request(dict(data, text=json.dumps([line(text, 40, **more)])))
            label.generate()
            return [entry[2]['text'] for entry in label._measure()[0]], label.warnings
        (plain, _), (spaced, warnings) = drawn(), drawn(letter_spacing=0.25)
    assert ' '.join(spaced) == text and len(spaced) > len(plain) and warnings == []


def test_the_underline_spans_the_spaced_text(tmp_path):
    client = make_client(tmp_path)
    spaced = preview(client, [line(letter_spacing=0.25), line('Second', 30)])
    underlined = preview(client, [line(letter_spacing=0.25, underline=True), line('Second', 30)])
    bar = ImageChops.difference(image(spaced), image(underlined)).convert('L').point(lambda v: 255 if v else 0).getbbox()
    letters = boxes(spaced)[0]
    assert letters[0] - 50 * .15 <= bar[0] <= letters[0] and letters[2] <= bar[2] <= letters[2] + 50 * .15


def test_white_on_black_holds_the_spaced_text(tmp_path):
    client = make_client(tmp_path)
    plain = ink(image(preview(client, [line(inverted=True)])))
    spaced = preview(client, [line(inverted=True, letter_spacing=0.25)])
    box = ink(image(spaced))
    assert abs((box[2] - box[0]) - (plain[2] - plain[0]) - 3 * 0.25 * 50) <= 2
    # The letters are white within it
    letters = ImageChops.invert(image(spaced).convert('L')).crop(box)
    assert letters.getbbox() and letters.point(lambda v: 255 if v < 128 else 0).getbbox()


@pytest.mark.parametrize('value', [-0.2, '1', 1.0, ' 0.1 '])
def test_letter_spacing_within_its_range_is_taken(tmp_path, value):
    client = make_client(tmp_path)
    preview(client, [line(letter_spacing=value)])


@pytest.mark.parametrize('value', [1.5, -0.3, '-0.21', 'wide', 'nan', 'inf', True, [0.1], {'em': 0.1}])
def test_letter_spacing_out_of_its_range_is_refused(tmp_path, value):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=json.dumps([line(letter_spacing=value)])))
    assert response.status_code == 400
    assert response.get_json()['message'] == LETTER_SPACING_REFUSED == 'letter_spacing must be a number from -0.2 to 1'
    assert LETTER_SPACING_REFUSED in refusals.MESSAGES


def test_a_saved_label_keeps_its_letter_spacing(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    lines = [line('Shelf 3', letter_spacing=0.1)]
    label = dict(EXAMPLE_FORMDATA, name='Spaced', label_size='62x29', text_fit='none', border_thickness='0', text=lines)
    assert client.post('/labeldesigner/api/repository/save', json=label).get_json()['success']
    assert json.loads(client.get('/labeldesigner/api/repository/load?name=Spaced.json').get_json()['text'])[0]['letter_spacing'] == 0.1
    saved = client.post('/labeldesigner/api/repository/preview', json={'name': 'Spaced.json'})
    assert pixels(saved.data) == pixels(preview(client, lines).data)
    assert pixels(saved.data) != pixels(preview(client, [line('Shelf 3')]).data)
