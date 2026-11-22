"""Where code, image and text are on a label: side, distance, margins,
orientation and what is cut off."""

import io
import json

import pytest
from PIL import Image
from flask.testing import FlaskClient

from app.labeldesigner.enums import LabelOrientation
from app.labeldesigner.label import SMALL_CODE
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _image_and_text_boxes, _upload, EXAMPLE_FORMDATA, make_client, pixels, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    @pytest.mark.parametrize('settings, reference', [
        pytest.param({'margin_top': 0, 'margin_bottom': 0}, 'simple_no_margins.png', id='standard'),
        pytest.param({'orientation': 'rotated', 'margin_left': 0, 'margin_right': 0}, 'simple_no_margins_rotated.png', id='rotated'),
    ])
    def test_generate_preview_no_margins(self, client: FlaskClient, settings, reference):
        data = dict(EXAMPLE_FORMDATA, **settings)
        data['text'] = json.dumps([
            {'font': 'Droid Serif,Regular', 'text': '!!! URGENT !!!', 'size': '60', 'align': 'center', 'inverted': True},
            {'font': 'Droid Serif,Regular', 'text': 'Ship as soon as possible', 'size': '60', 'align': 'center'},
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, reference)

    def test_image_text_gap(self, client: FlaskClient):
        # Code and text along the tape: the label grows by the distance. The
        # code keeps its size, a fitted one is tested below.
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'qrcode_text', 'orientation': 'rotated', 'code_text': 'https://example.com', 'qrcode_size': '5', 'image_fit': '0',
                     'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Item', 'size': '50', 'align': 'left'}])})
        widths, gaps = {}, {}
        for gap in ('', '0', '60'):
            data['image_text_gap'] = gap
            response = client.post('/labeldesigner/api/preview', data=data)
            assert response.status_code == 200
            widths[gap] = Image.open(io.BytesIO(response.data)).width
            gaps[gap] = response.headers.get('X-Label-Image-Text-Gap')
        assert widths['60'] - widths['0'] == 60
        # Without a distance: two fifths of the largest text
        assert widths[''] - widths['0'] == 20
        assert gaps == {'': '20', '0': None, '60': '60'}
        # A QR code keeps at least two of its modules
        data.update({'image_text_gap': '', 'qrcode_size': '20'})
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.headers['X-Label-Image-Text-Gap'] == '40'
        # Without text there is nothing to keep a distance from
        data['text'] = json.dumps([])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert 'X-Label-Image-Text-Gap' not in response.headers

        data['image_text_gap'] = '-1'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_image_text_gap_of_a_fitted_qr_code(self, client: FlaskClient):
        # Fitted to the tape, the modules of 5 dots become 5.4 times larger,
        # the distance keeps two of them
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'qrcode_text', 'orientation': 'rotated', 'code_text': 'https://example.com', 'qrcode_size': '5',
                     'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Item', 'size': '50', 'align': 'left'}])})
        gaps = {}
        for fit in ('0', '1'):
            data['image_fit'] = fit
            gaps[fit] = client.post('/labeldesigner/api/preview', data=data).headers['X-Label-Image-Text-Gap']
        assert gaps == {'0': '20', '1': '54'}


def test_distance_kept_to_is_used_when_fitting_is_uneven(monkeypatch):
    # Rounded font sizes can make text fitted at a distance larger than at a
    # smaller one. The distance the search ends at is used only if the text
    # fitted at it keeps to it, else the last one it kept to.
    from app.labeldesigner import label as label_module
    own_gap = {48: 30, 30: 40, 35: 36, 36: 38}
    label = label_module.SimpleLabel()
    monkeypatch.setattr(label, '_text_space', lambda width, height, img_width, img_height, gap, largest=False: (gap, None))
    monkeypatch.setattr(label, '_gap', lambda img_width, img_height: 48 if label._text_scale == 1.0 else own_gap[label._text_scale])

    def fit(bboxes, textsize, gap, max_height):
        label._text_scale = gap
        return bboxes, textsize
    monkeypatch.setattr(label, '_shrink_text_to_fit', fit)
    label._shrink_text_next_to_image([], (0, 0, 0, 0), 100, 100, 10, 10)
    assert own_gap[label._text_scale] <= label._text_scale


def test_image_turns_to_fit_only_without_lines_of_text(tmp_path):
    # Beside a line of blanks it is laid out as with text, also while they
    # are left empty: the preview with their marks looks like the label
    # printed, and it does not turn once they are filled in
    client = make_client(tmp_path)
    tall = io.BytesIO()
    Image.new('L', (300, 600), 0).save(tall, 'PNG')

    def preview(**values):
        data = dict(label_size='62x100', print_type='image', image=(io.BytesIO(tall.getvalue()), 'tall.png', 'image/png'),
                    **dict({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}])}, **values))
        response = client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')
        assert response.status_code == 200
        return response
    assert 'X-Label-Orientation' not in preview().headers
    assert 'X-Label-Orientation' not in preview(blanks=json.dumps({'Note': 'Garage'})).headers
    assert preview(text='[]').headers.get('X-Label-Orientation') == 'auto'


def _auto_orientation_label(tmp_path, size, **values):
    client = make_client(tmp_path)
    data = {'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '1', 'orientation': 'standard', 'text': '[]',
            'margin_top': '0', 'margin_bottom': '0', 'margin_left': '0', 'margin_right': '0', **values}
    files = {'image': _upload(Image.new('L', size, 'black'), 'image.png')}
    with client.application.test_request_context():
        return create_label_from_request(data, files)


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_portrait_image_on_die_cut_label_is_turned(tmp_path, orientation):
    # A 4 x 6 in shipping label on a 103 x 164 mm label uses the full label
    label = _auto_orientation_label(tmp_path, (1200, 1800), label_size='103x164', orientation=orientation)
    assert (label._width, label._height) == (1200, 1822)
    assert label.label_orientation == LabelOrientation.ROTATED


def test_landscape_image_on_die_cut_label_is_turned(tmp_path):
    label = _auto_orientation_label(tmp_path, (1800, 1200), label_size='103x164', orientation='rotated')
    assert (label._width, label._height) == (1822, 1200)
    assert label.label_orientation == LabelOrientation.STANDARD


@pytest.mark.parametrize('values', [
    # Without auto-fit the chosen orientation is kept
    {'image_fit': '0'},
    # Labels with text keep their layout
    {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': '40'}])},
    # Endless labels have no fixed length to fit
    {'label_size': '62'},
])
def test_orientation_is_kept(tmp_path, values):
    label = _auto_orientation_label(tmp_path, (1200, 1800), **{'label_size': '103x164', **values})
    assert label.label_orientation == LabelOrientation.STANDARD


@pytest.mark.parametrize('size, orientation, expected', [
    # Die-cut labels in standard orientation were shown turned by 90 degrees
    ('62x29', 'standard', (696, 271)),
    ('62x29', 'rotated', (271, 696)),
    ('103x164', 'standard', (1822, 1200)),
])
def test_preview_shows_the_label_upright(tmp_path, size, orientation, expected):
    # The preview shows the label the way it is read, as it is printed
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': size, 'orientation': orientation})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).size == expected


@pytest.mark.parametrize('label_size, orientation', [('29x90', 'standard'), ('17x54', 'standard'), ('d24', 'standard'), ('62x29', 'standard'),
                                                     ('62x29', 'rotated'), ('62x100', 'standard'), ('62x100', 'rotated'), ('60x86', 'rotated')])
def test_a_code_fitted_next_to_text_leaves_the_text_room(tmp_path, label_size, orientation):
    # Fitted to the die-cut label it took the whole label, the text was not
    # there: a code with text, as the label designer starts it
    client = make_client(tmp_path)
    data = {'label_size': label_size, 'orientation': orientation, 'print_type': 'qrcode_text', 'code_text': 'https://example.com',
            'image_fit': '1', 'text_fit': 'shrink', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili', 'size': '60'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    # Half of a 17 mm label is too little for the dots of a link
    assert response.headers.get('X-Label-Warning') == (SMALL_CODE if label_size == '17x54' else None)
    other = client.post('/labeldesigner/api/preview', data=dict(data, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Beans', 'size': '60'}])))
    assert pixels(other.data) != pixels(response.data)


@pytest.mark.parametrize('orientation, text, qrcode_size', [
    # Below the code, a line of 120 dots is too high
    ('standard', 'Chili con carne', '6'),
    # Beside the code, too wide
    ('rotated', 'Chili', '5'),
])
def test_automatic_gap_follows_the_shrunk_text(tmp_path, orientation, text, qrcode_size):
    # The distance is two fifths of the text as printed, not of 120 dots.
    # The larger distance took room from the text, which came out smaller
    # than needed.
    client = make_client(tmp_path)
    data = {'label_size': '29x90', 'print_type': 'qrcode_text', 'orientation': orientation, 'code_text': 'https://example.com',
            'qrcode_size': qrcode_size, 'image_fit': '0', 'shrink_text': '1',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '120'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        image = label.generate()
        assert label.generate().tobytes() == image.tobytes()
        printed = label._line_size(label.text[0])
        assert 1 < printed < 120 and label.warnings == []
        assert label.image_text_gap == max(round(0.4 * printed), 2 * int(qrcode_size))
        # The same as the text of that size from the start
        unshrunk = create_label_from_request(dict(data, shrink_text='0', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': str(printed)}])))
        assert unshrunk.generate().tobytes() == image.tobytes()
        assert unshrunk.warnings == []
        # Larger than the text shrunk to fit at the distance of 120 dots
        before = create_label_from_request(dict(data, image_text_gap=str(max(48, 2 * int(qrcode_size)))))
        before.generate()
        assert printed > before._line_size(before.text[0])


@pytest.mark.parametrize('gap, dots', [(12, 12), (12.0, 12), ('12', 12), (' 7 ', 7), ('', None), (None, None)])
def test_image_text_gap_as_text_or_number(gap, dots):
    # Saved labels and JSON may have it as a number, e.g. 12.0
    from app.labeldesigner.routes import _image_text_gap
    assert _image_text_gap(gap) == dots


@pytest.mark.parametrize('gap', [12.5, True, '12.0'])
def test_image_text_gap_of_a_number_must_be_whole(gap):
    from app.labeldesigner.routes import _image_text_gap
    with pytest.raises(ValueError, match='image_text_gap must be a whole number of dots or empty'):
        _image_text_gap(gap)


@pytest.mark.parametrize('gap', ['2.5', 'abc'])
def test_image_text_gap_must_be_a_whole_number(tmp_path, gap):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', text=TEXT, image_text_gap=gap)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'image_text_gap must be a whole number of dots or empty'


@pytest.mark.parametrize('side', ['above', 'below', 'left', 'right'])
@pytest.mark.parametrize('label_size, orientation', [('60x86', 'standard'), ('60x86', 'rotated'), ('62', 'standard'), ('62', 'rotated')])
def test_the_image_is_on_the_side_of_the_text_chosen(tmp_path, label_size, orientation, side):
    # It was above the text across the tape and left of it along the tape,
    # e.g. a photo left of the name on a landscape name badge was not possible
    client = make_client(tmp_path)
    data = {'label_size': label_size, 'orientation': orientation, 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0',
            'image_position': side, 'text_fit': 'shrink', 'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Erika Mustermann', 'size': '60'}]),
            'image': _upload(Image.new('L', (200, 120), 128), 'photo.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    gap = int(response.headers['X-Label-Image-Text-Gap'])
    width, height = Image.open(io.BytesIO(response.data)).size
    image, text = _image_and_text_boxes(response.data)
    assert (image[2] - image[0], image[3] - image[1]) == (200, 120)
    distance = {'above': text[1] - image[3], 'below': image[1] - text[3], 'left': text[0] - image[2], 'right': image[0] - text[2]}[side]
    assert distance >= gap
    # In the middle across, at the margin on its side
    if side in ('above', 'below'):
        assert abs(image[0] + image[2] - width) <= 1
        assert (image[1] == 20) if side == 'above' else (image[3] == height - 20)
    else:
        assert abs(image[1] + image[3] - height) <= 1
        assert (image[0] == 20) if side == 'left' else (image[2] == width - 20)
    # Endless tape grows along to hold both, side by side not one after the other
    if label_size == '62':
        along = width if orientation == 'rotated' else height
        side_by_side = (side in ('left', 'right')) == (orientation == 'rotated')
        extent = (lambda box: box[2] - box[0]) if orientation == 'rotated' else (lambda box: box[3] - box[1])
        # The margins and the room at the edges of the letters
        if side_by_side:
            assert extent(image) + gap + extent(text) + 40 <= along <= extent(image) + gap + extent(text) + 40 + 20
        else:
            assert max(extent(image), extent(text)) + 40 <= along <= max(extent(image), extent(text)) + 40 + 20


@pytest.mark.parametrize('orientation, side', [('standard', 'left'), ('standard', 'right'), ('rotated', 'above'), ('rotated', 'below')])
def test_an_image_fitted_beside_the_text_across_endless_tape_takes_half_of_it(tmp_path, orientation, side):
    # Fitted to the label it filled the tape across, the text had no room
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': orientation, 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '1', 'image_position': side,
            'text_fit': 'shrink', 'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '80'}]), 'image': _upload(Image.new('L', (100, 100), 128), 'square.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    image, text = _image_and_text_boxes(response.data)
    assert abs((image[2] - image[0]) - (696 - 40) // 2) <= 6 and text


@pytest.mark.parametrize('text_fit, warning', [('none', 'The text does not fit next to the code, it overlaps it.'), ('shrink', None)])
def test_text_over_the_code_is_warned_about(tmp_path, text_fit, warning):
    # Drawn as set over the code, it reported that everything fits
    client = make_client(tmp_path)
    data = {'label_size': '60x86', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '1', 'image_position': 'right',
            'text_fit': text_fit, 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con carne', 'size': '70'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('side', ['left', 'right'])
def test_text_beside_the_image_is_in_the_middle_between_the_margins(tmp_path, side):
    # The margins left and right were taken as equal, wider text started at
    # the edge of the label
    client = make_client(tmp_path)
    data = {'label_size': '60x86', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'image_position': side, 'text_fit': 'none',
            'margin_top': '20', 'margin_bottom': '20', 'margin_left': '120', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}]), 'image': _upload(Image.new('L', (200, 120), 128), 'photo.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    width = Image.open(io.BytesIO(response.data)).width
    image, text = _image_and_text_boxes(response.data)
    room = (image[2] + int(response.headers['X-Label-Image-Text-Gap']), width - 20) if side == 'left' else (120, image[0] - int(response.headers['X-Label-Image-Text-Gap']))
    assert abs((text[0] + text[2]) - (room[0] + room[1])) <= 12
    # Wider than the room it starts at the margin
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A very long line of text', 'size': '60'}])
    data['image'] = _upload(Image.new('L', (200, 120), 128), 'photo.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    image, text = _image_and_text_boxes(response.data)
    assert text[0] >= (image[2] if side == 'left' else 120)


@pytest.mark.parametrize('values, side', [
    ({}, 'above'),
    ({'image_position': 'left'}, 'left'),
    ({'image_position': 'below'}, 'below'),
    ({'orientation': 'rotated'}, 'left'),
    ({'text': '[]'}, None),
])
def test_the_preview_tells_where_the_code_went(tmp_path, values, side):
    # The label designer showed it from its own copy of the rules
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '1', 'qrcode_size': '8',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}])}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Image-Position') == side


def test_a_text_label_keeps_its_margins_whatever_side_a_code_had(tmp_path):
    # Kept from a layout with a code, the side moved the text, also over the
    # margins
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'margin_left': '10', 'margin_right': '60', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}])}
    previews = [pixels(client.post('/labeldesigner/api/preview', data=dict(data, image_position=side)).data) for side in ('', 'above', 'below', 'right')]
    assert all(preview == previews[0] for preview in previews)


@pytest.mark.parametrize('side', ['above', 'below'])
def test_image_and_text_across_are_in_the_middle_between_the_margins(tmp_path, side):
    # Centered on the whole label, they ran over the larger margin
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'image_position': side,
            'margin_left': '10', 'margin_right': '90', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}]), 'image': _upload(Image.new('L', (100, 100), 128), 'square.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    width = Image.open(io.BytesIO(response.data)).width
    image, text = _image_and_text_boxes(response.data)
    middle = 10 + (width - 100) / 2
    assert abs((image[0] + image[2]) / 2 - middle) <= 2 and abs((text[0] + text[2]) / 2 - middle) <= 8


@pytest.mark.parametrize('values', [{}, {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0', 'qrcode_size': '4', 'image_position': 'above'},
                                    {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0', 'qrcode_size': '4', 'image_position': 'right'}])
def test_a_first_letter_reaching_left_is_not_cut_off_along_endless_tape(tmp_path, values):
    # The "j" reaches left of where the text starts: on a label growing in
    # width it was cut off at a margin of 0
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'orientation': 'rotated', 'margin_left': '0', 'margin_right': '0', 'text_fit': 'none',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'jab', 'size': '120'}] * 3)}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


@pytest.mark.parametrize('align', ['left', 'center'])
def test_a_line_reaching_left_has_room_on_a_label_growing_with_it(tmp_path, align):
    # Only the first line was looked at: the tail of a "j" in front of the
    # second line was cut off. Left aligned, the lines were drawn as far
    # left again: at a margin of 0 the "j" lost its tail, else it reached
    # into the margin.
    client = make_client(tmp_path)

    def made(lines, margin):
        data = dict(EXAMPLE_FORMDATA, label_size='62', orientation='rotated', margin_left=str(margin), margin_right='0', border_thickness='0',
                    text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '120', 'text': t, 'align': align} for t in lines]))
        with client.application.test_request_context():
            label = create_label_from_request(data)
            im = label.generate().convert('L')
        ink = im.point(lambda v: 255 if v < 128 else 0).getbbox()
        assert ink[0] >= margin and not label.warnings
        return im.width - margin, ink[2] - ink[0]
    made_alike = {made(lines, margin) for lines in (['jump', 'Ab'], ['Ab', 'jump']) for margin in (0, 50)}
    assert len(made_alike) == 1


@pytest.mark.parametrize('values', [{'text_fit': 'fill', 'text': 'jab', 'size': '300'}, {'text_fit': 'shrink', 'text': 'j' * 29, 'size': '120'}, {'text_fit': 'shrink', 'text': 'jab jab jab', 'size': '200'}])
def test_text_made_to_fit_has_room_for_a_letter_reaching_left(tmp_path, values):
    # Made to fit up to its right edge, a "j" in front reached past the
    # margin: the label told that the text it made to fit was cut off
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'standard', 'margin_left': '0', 'margin_right': '0', 'border_thickness': '0', 'text_fit': values['text_fit'],
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': values['text'], 'size': values['size'], 'align': 'center'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


@pytest.mark.parametrize('values', [{'image_position': 'above', 'image_text_gap': '0'},
                                    {'image_position': 'left', 'margin_top': '0', 'margin_bottom': '0', 'image_fit': '0', 'qrcode_size': '3'},
                                    {'image_position': 'right', 'margin_top': '0', 'margin_bottom': '0', 'image_fit': '0', 'qrcode_size': '3'}])
def test_text_reaching_above_its_first_line_is_placed_from_its_top(tmp_path, values):
    # An icon reaches above the first line: below a code the text was drawn
    # into it, beside it the text was cut off at the top of a label growing
    # with it
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'orientation': 'standard', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'border_thickness': '0',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{mdi:lightbulb}} Hello', 'size': '120'}])}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


def test_image_position_must_be_a_side(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', text=TEXT, image_position='top')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'image_position must be above, below, left or right'


def test_image_text_gap_null_is_chosen_automatically(tmp_path):
    # Like left out, e.g. in a label saved through the API
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'text': TEXT}
    with client.application.test_request_context():
        labels = [create_label_from_request(dict(data, **gap)) for gap in ({'image_text_gap': None}, {'image_text_gap': ''}, {})]
        images = [label.generate().tobytes() for label in labels]
    assert images[0] == images[1] == images[2]
    assert [label.image_text_gap for label in labels] == [labels[2].image_text_gap] * 3 and labels[2].image_text_gap > 0


@pytest.mark.parametrize('values, auto', [
    # An image without text on a die-cut label is turned to fit
    ({}, True),
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'With text', 'size': '40'}])}, False),
    ({'image_fit': '0'}, False),
    ({'label_size': '62'}, False),
])
def test_preview_says_when_the_orientation_is_chosen_automatically(tmp_path, values, auto):
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'label_size': '62x29', 'text': '[]', **values}
    data['image'] = _upload(Image.new('L', (100, 300), 'black'), 'portrait.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Orientation') == ('auto' if auto else None)


def _line(text: str, **settings) -> dict:
    return dict({'font': 'DejaVu Sans,Book', 'text': text, 'size': '40', 'align': 'left'}, **settings)


def _parts_of(client, **settings) -> tuple:
    """The parts of the preview of a label without a frame, each inside it,
    and the preview in gray."""
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, border_thickness='0', **settings))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None
    image = Image.open(io.BytesIO(response.data)).convert('L')
    parts = json.loads(response.headers['X-Label-Parts'])
    for part in parts:
        x0, y0, x1, y1 = part['box']
        assert 0 <= x0 <= x1 <= image.width and 0 <= y0 <= y1 <= image.height
    return parts, image


def _ink(image: Image.Image, box=None):
    """The box around what is printed of image, within box if given."""
    return (image.crop(tuple(box)) if box else image).point(lambda v: 255 if v < 128 else 0).getbbox()


def _uncovered(image: Image.Image, parts: list):
    """The box around what is printed outside the boxes of parts."""
    rest = image.copy()
    for part in parts:
        rest.paste(255, tuple(part['box']))
    return _ink(rest)


THREE_LINES = json.dumps([_line('Flour', inverted=True, align='center'), _line('Type 550, organic', size='30', checkbox=True),
                          _line('Best before 2027', size='24', align='right')])


@pytest.mark.parametrize('settings', [{}, {'orientation': 'rotated'}, {'high_res': '1'}, {'mirror': '1'}],
                         ids=['across', 'along', 'high_res', 'mirrored'])
def test_preview_tells_where_each_line_is(tmp_path, settings):
    # The label designer picks a line with a click on the preview
    client = make_client(tmp_path)
    parts, image = _parts_of(client, text=THREE_LINES, **settings)
    assert [(part['part'], part['index']) for part in parts] == [('line', 0), ('line', 1), ('line', 2)]
    boxes = [part['box'] for part in parts]
    # From top to bottom, each holding its line, nothing printed outside them;
    # the black bar of white on black takes a dot of the room below it
    assert all(above[3] <= below[1] + 1 for above, below in zip(boxes, boxes[1:]))
    assert all(_ink(image, box) for box in boxes)
    assert _uncovered(image, parts) is None


def test_parts_are_told_with_a_preview_in_base64(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=THREE_LINES)
    png = client.post('/labeldesigner/api/preview', data=data)
    text = client.post('/labeldesigner/api/preview', data=dict(data, return_format='base64'))
    assert text.headers['X-Label-Parts'] == png.headers['X-Label-Parts']
    assert png.headers['X-Label-Parts'].startswith('[{"part":"line","index":0,"box":[')


@pytest.mark.parametrize('blank', ['', '   '], ids=['empty', 'spaces'])
def test_a_blank_line_takes_its_room_between_the_others(tmp_path, blank):
    # As wide as the text, so that a click between the lines picks it
    client = make_client(tmp_path)
    parts, image = _parts_of(client, text=json.dumps([_line('Flour'), _line(blank), _line('Sugar and salt')]))
    assert [part['index'] for part in parts] == [0, 1, 2]
    first, blank, last = (part['box'] for part in parts)
    assert first[3] <= blank[1] < blank[3] <= last[1]
    assert (blank[0], blank[2]) == (min(first[0], last[0]), max(first[2], last[2]))
    assert _ink(image, blank) is None


def test_a_line_that_wraps_has_one_box(tmp_path):
    client = make_client(tmp_path)
    text = json.dumps([_line('Whole wheat flour, stone ground in the mill of the village', size='60'), _line('1 kg')])
    parts, image = _parts_of(client, text=text, text_wrap='1')
    assert [part['index'] for part in parts] == [0, 1]
    wrapped, single = (part['box'] for part in parts)
    assert wrapped[3] - wrapped[1] > 2 * (single[3] - single[1])
    assert _uncovered(image, parts) is None


@pytest.mark.parametrize('side', ['above', 'below', 'left', 'right'])
@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_preview_tells_where_the_code_is(tmp_path, orientation, side):
    client = make_client(tmp_path)
    parts, image = _parts_of(client, print_type='qrcode_text', code_text='https://example.com', qrcode_size='5', image_fit='0',
                             orientation=orientation, image_position=side, text=json.dumps([_line('Shelf 4'), _line('Screws', size='30')]))
    assert [(part['part'], part.get('index')) for part in parts] == [('line', 0), ('line', 1), ('code', None)]
    lines, code = [part['box'] for part in parts[:2]], parts[2]['box']
    text = (min(box[0] for box in lines), lines[0][1], max(box[2] for box in lines), lines[1][3])
    assert {'above': code[3] <= text[1], 'below': code[1] >= text[3], 'left': code[2] <= text[0], 'right': code[0] >= text[2]}[side]
    assert _ink(image, code) and _uncovered(image, parts) is None


@pytest.mark.parametrize('print_type, names', [('image', ['line', 'image']), ('image_qrcode_text', ['line', 'image', 'code'])])
def test_preview_tells_where_the_image_is(tmp_path, print_type, names):
    client = make_client(tmp_path)
    parts, image = _parts_of(client, print_type=print_type, image_mode='grayscale', image_fit='0', code_text='https://example.com',
                             qrcode_size='5', image_position='above', text=json.dumps([_line('Erika Mustermann')]),
                             image=_upload(Image.new('L', (200, 120), 128), 'photo.png'))
    assert [part['part'] for part in parts] == names
    box = next(part['box'] for part in parts if part['part'] == 'image')
    assert (box[2] - box[0], box[3] - box[1]) == (200, 120)
    assert image.crop(tuple(box)).getextrema() == (128, 128)
    assert _uncovered(image, parts) is None


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_parts_of_a_cable_flag_are_on_both_labels(tmp_path, orientation):
    # Across the tape the second label is upside down
    client = make_client(tmp_path)
    parts, image = _parts_of(client, label_size='62', cable_flag='1', orientation=orientation,
                             text=json.dumps([_line('Router'), _line('Port 4', size='30')]))
    first, second = parts[:2], parts[2:]
    assert [part['index'] for part in first] == [part['index'] for part in second] == [0, 1]
    for one, other in zip(first, second):
        turned = image.crop(tuple(other['box']))
        if orientation == 'standard':
            turned = turned.transpose(Image.Transpose.ROTATE_180)
        original = image.crop(tuple(one['box']))
        assert _ink(image, one['box']) and (turned.size, turned.tobytes()) == (original.size, original.tobytes())


def _bold(text: str, **settings) -> dict:
    return _line(text, **dict({'font': 'DejaVu Sans,Bold', 'size': '70', 'align': 'center'}, **settings))


def _tight(image: Image.Image, box: list) -> bool:
    """Whether what is printed in box reaches its edges, give or take a dot."""
    ink = _ink(image, box)
    return bool(ink) and ink[0] <= 1 and ink[1] <= 1 and ink[2] >= box[2] - box[0] - 1 and ink[3] >= box[3] - box[1] - 1


def _bands(image: Image.Image) -> list:
    """From where to where down the label something is printed, each run of
    rows with ink."""
    rows = {y for y in range(image.height) if _ink(image, (0, y, image.width, y + 1))}
    starts = sorted(y for y in rows if y - 1 not in rows)
    return [(start, next(y for y in range(start, image.height) if y + 1 not in rows) + 1) for start in starts]


@pytest.mark.parametrize('settings', [{}, {'orientation': 'rotated'}, {'mirror': '1'}, {'cable_flag': '1'}],
                         ids=['across', 'along', 'mirrored', 'cable flag'])
def test_a_line_box_is_what_is_printed_of_it(tmp_path, settings):
    # The box the designer outlines ends at the letters, not at the room of
    # the line, which reaches into the next one
    client = make_client(tmp_path)
    parts, image = _parts_of(client, text=json.dumps([_bold('ABC'), _bold('DEF')]), **settings)
    boxes = sorted((part['box'] for part in parts), key=lambda box: box[1])
    # Without the marks of a cable flag where it folds around the cable
    bands = [band for band in _bands(image) if any(box[1] <= band[1] and band[0] <= box[3] for box in boxes)]
    assert len(bands) == len(boxes)
    for box, (top, bottom) in zip(boxes, bands):
        assert abs(box[1] - top) <= 1 and abs(box[3] - bottom) <= 1
        assert _tight(image, box)
    abc, def_ink = parts[0]['box'], bands[1]
    assert abc[3] < def_ink[0]


@pytest.mark.parametrize('lines', [
    pytest.param([_bold('gy'), _bold('ABC')], id='descenders'),
    pytest.param([_bold('ABC', inverted=True), _bold('DEF')], id='white on black'),
    pytest.param([_bold('ABC', underline=True), _bold('DEF')], id='underlined'),
    pytest.param([_bold('ABC', checkbox=True, align='left'), _bold('DEF', align='left')], id='to-do'),
    pytest.param([_bold('Jam {{mdi:star}}'), _bold('2026')], id='icon'),
])
@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_each_line_box_holds_all_of_its_line_and_nothing_more(tmp_path, lines, orientation):
    client = make_client(tmp_path)
    parts, image = _parts_of(client, text=json.dumps(lines), orientation=orientation)
    boxes = [part['box'] for part in parts]
    assert len(boxes) == 2 and boxes[0][3] <= boxes[1][1]
    assert all(_tight(image, box) for box in boxes)
    assert _uncovered(image, parts) is None
    if lines[0].get('inverted'):
        # The black bar, letters white in it
        x0, y0, x1, y1 = boxes[0]
        assert all(image.getpixel(xy) < 128 for xy in ((x0, y0), (x1 - 1, y0), (x0, y1 - 1), (x1 - 1, y1 - 1)))


@pytest.mark.parametrize('barcode_type', ['code128', 'ean13', 'qr'])
def test_the_box_of_a_code_is_where_it_is_printed(tmp_path, barcode_type):
    # Without the white room kept around the bars
    client = make_client(tmp_path)
    code_text = '4006381333931' if barcode_type == 'ean13' else 'https://example.com'
    parts, image = _parts_of(client, print_type='qrcode_text', code_text=code_text, barcode_type=barcode_type, qrcode_size='5',
                             image_fit='0', image_position='above', text=json.dumps([_line('Shelf 4')]))
    code = next(part['box'] for part in parts if part['part'] == 'code')
    assert _tight(image, code) and _uncovered(image, parts) is None


def test_the_box_of_an_image_leaves_out_its_white_border(tmp_path):
    client = make_client(tmp_path)
    picture = Image.new('L', (200, 120), 255)
    picture.paste(0, (50, 30, 150, 90))
    parts, image = _parts_of(client, print_type='image', image_mode='grayscale', image_fit='0', image_position='above',
                             text=json.dumps([_line('Erika Mustermann')]), image=_upload(picture, 'photo.png'))
    box = next(part['box'] for part in parts if part['part'] == 'image')
    assert (box[2] - box[0], box[3] - box[1]) == (100, 60) and _tight(image, box)


def test_parts_of_many_lines_are_left_out(tmp_path):
    # Proxies take a few KB of headers, nginx 4 KB by default, a longer
    # header would fail the preview
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([_line(f'Line {i}', size='12') for i in range(60)]))
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and 'X-Label-Parts' not in response.headers
    few = client.post('/labeldesigner/api/preview', data=dict(data, text=json.dumps([_line(f'Line {i}', size='12') for i in range(20)])))
    assert len(json.loads(few.headers['X-Label-Parts'])) == 20
