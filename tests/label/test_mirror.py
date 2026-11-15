"""Labels printed mirror-inverted, to be read through clear tape stuck
behind glass."""

import io
import json

import pytest
from PIL import Image, ImageOps

from app.labeldesigner import printer
from app.labeldesigner.label import SimpleLabel
from tests.helpers import _red_square, _upload, EXAMPLE_FORMDATA, make_client, pixels, verify_image

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pull', 'size': '60', 'align': 'left'},
                   {'font': 'DejaVu Sans,Book', 'text': 'Open from 9 to 5', 'size': '30', 'align': 'left'}])
# Fits the smallest labels
SHORT_TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pull', 'size': '30', 'align': 'left'}])


def mirrored(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        out = io.BytesIO()
        ImageOps.mirror(image).save(out, format='PNG')
        return out.getvalue()


@pytest.mark.parametrize('settings', [
    {},
    # Along the tape left and right swap along it, as the text reads
    {'orientation': 'rotated'},
    {'label_size': '62x29', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_position': 'left'},
    {'label_size': '62x29', 'print_type': 'image', 'image_mode': 'black_and_white', 'image_position': 'right'},
], ids=['text', 'along_the_tape', 'code_and_text', 'image_and_text'])
def test_a_mirrored_label_is_the_label_mirrored(tmp_path, settings):
    # Code, image, text and frame alike, the preview shows it so
    client = make_client(tmp_path)

    def preview(**more):
        data = dict(EXAMPLE_FORMDATA, text=TEXT, **settings, **more)
        if settings.get('print_type') == 'image':
            data['image'] = _upload(_red_square(), 'red.png')
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200 and 'X-Label-Warning' not in response.headers
        return response.data
    label = preview(mirror='1')
    assert pixels(label) == pixels(mirrored(preview())) != pixels(preview())
    if not settings:
        verify_image(label, 'mirrored.png')


@pytest.mark.parametrize('model, label_size', [
    ('QL-800', '62'), ('PT-P750W', 'pt12'), ('VC-500W', 'vc25'), ('PUQU/Q00', 'puqu50x30'),
    ('Niimbot/D110_M', 'niimbot12x40'), ('Epson/TM-T20II', 'escpos58'),
])
@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_a_mirrored_label_is_printed_mirrored(tmp_path, monkeypatch, model, label_size, orientation):
    # Each driver prints the label mirrored once, as it prints that image
    printed = []
    monkeypatch.setattr(printer._Simulator, '__call__', lambda self, image, name: printed.append((image.size, image.tobytes())))
    client = make_client(tmp_path)

    def print_label(mirror):
        response = client.post('/labeldesigner/api/print', data={
            'label_size': label_size, 'orientation': orientation, 'text': SHORT_TEXT, 'mirror': mirror,
            'printer': 'simulation', 'model': model})
        assert response.get_json()['success'] is True
        return printed.pop()
    inverted, normal = print_label('1'), print_label('0')
    generate = SimpleLabel.generate
    monkeypatch.setattr(SimpleLabel, 'generate', lambda label: ImageOps.mirror(generate(label)))
    assert inverted == print_label('0') != normal


@pytest.mark.parametrize('value, mirrors', [('1', True), ('true', True), ('0', False), ('', False), (None, False)])
def test_mirror_is_a_flag(tmp_path, value, mirrors):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=TEXT)
    plain = client.post('/labeldesigner/api/preview', data=data).data
    if value is not None:
        data['mirror'] = value
    response = client.post('/labeldesigner/api/preview', data=data)
    assert pixels(response.data) == pixels(mirrored(plain) if mirrors else plain)


def test_mirror_that_is_no_flag_is_refused(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT, mirror='both'))
    assert response.status_code == 400 and response.get_json()['message'] == 'mirror must be 0 or 1'
