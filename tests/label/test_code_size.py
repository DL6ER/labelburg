"""Codes that print too small to scan: their dots narrower than
codes.MIN_MODULE_MM, their bars than codes.MIN_BAR_MM, or either than
codes.MIN_DOTS, as printed."""

import io
import json

import pytest
from PIL import Image

from app.labeldesigner import codes
from app.labeldesigner.label import SMALL_BARCODE_NEXT_TO_IMAGE, SMALL_CODE, SMALL_SIZED_CODE

from tests.helpers import make_client

PREVIEW = '/labeldesigner/api/preview'
TOO_SMALL = 'This code may be too small to scan.'
URL = 'https://www.example.com/inventory/garage/shelf-3/box-17?item=cordless-drill-and-charger'


def preview(client, **settings):
    data = {'label_size': '62', 'print_type': 'qrcode', 'orientation': 'rotated', 'image_fit': '1', 'code_text': URL}
    data.update(settings)
    response = client.post(PREVIEW, data=data, content_type='multipart/form-data' if 'image' in data else None)
    assert response.status_code == 200, response.get_json()
    return response


def warning(response) -> str:
    return response.headers.get('X-Label-Warning', '')


def test_the_texts():
    assert SMALL_CODE == 'This code may be too small to scan. Make it larger, or put less text into it.'
    assert SMALL_SIZED_CODE == 'This code may be too small to scan. Make it larger.'
    assert SMALL_BARCODE_NEXT_TO_IMAGE == 'This code may be too small to scan. Next to an image its size cannot be set: use a QR code, or remove the image.'


@pytest.mark.parametrize('model, label_size', [('QL-800', '12'), ('PT-P750W', 'pt12')])
def test_a_long_link_as_a_qr_code_on_12_mm_tape(tmp_path, model, label_size):
    client = make_client(tmp_path, model=model)
    assert SMALL_CODE in warning(preview(client, label_size=label_size))
    # The same code with larger dots of a size set, here wider than the tape
    larger = preview(client, label_size=label_size, image_fit='0', qrcode_size='3' if model == 'QL-800' else '2')
    assert TOO_SMALL not in warning(larger)
    assert 'part of it is cut off' in warning(larger)


def test_less_text_helps_a_fitted_code_only(tmp_path):
    # With a size set, the dots are as large whatever the code holds
    client = make_client(tmp_path)
    assert warning(preview(client, image_fit='0', qrcode_size='2', code_text='A')) == SMALL_SIZED_CODE
    assert warning(preview(client, label_size='12')) == SMALL_CODE


def test_a_short_text_fits_12_mm_tape(tmp_path):
    client = make_client(tmp_path)
    response = preview(client, label_size='12', code_text='Box 17', margin_top='5', margin_bottom='5')
    assert 'X-Label-Warning' not in response.headers


@pytest.mark.parametrize('code_type, value, scale, small', [
    # python-barcode draws the bars of Code 128 0.2 mm wide, as made at 300 dpi
    ('code128', 'ABC-12345', '100', False),
    ('code128', 'ABC-12345', '90', True),
    # and those of EAN-13 0.33 mm
    ('ean13', '400638133393', '100', False),
    ('ean13', '400638133393', '50', True),
])
def test_barcodes_with_narrow_bars(tmp_path, code_type, value, scale, small):
    client = make_client(tmp_path)
    response = preview(client, barcode_type=code_type, code_text=value, image_fit='0', image_scaling_factor=scale,
                       orientation='standard')
    assert warning(response) == (SMALL_SIZED_CODE if small else '')


def test_a_fitted_barcode_along_narrow_tape_is_too_small(tmp_path):
    # Its bars are made as high as the tape is wide
    client = make_client(tmp_path)
    assert SMALL_CODE in warning(preview(client, label_size='29', barcode_type='code128', code_text='ABC-12345'))
    assert TOO_SMALL not in warning(preview(client, label_size='62', barcode_type='code128', code_text='ABC-12345',
                                            orientation='standard'))


@pytest.mark.parametrize('settings, small, small_at_600_dpi', [
    # Dots of a size set are half as large at 600 dpi
    ({'image_fit': '0', 'qrcode_size': '4'}, False, True),
    ({'image_fit': '0', 'qrcode_size': '8'}, False, False),
    ({'barcode_type': 'ean13', 'code_text': '400638133393', 'image_fit': '0', 'orientation': 'standard'}, False, True),
    ({'barcode_type': 'code128', 'code_text': 'ABC-12345', 'image_fit': '0', 'orientation': 'standard'}, False, True),
    # A fitted code has the same size
    ({}, False, False),
    ({'label_size': '12'}, True, True),
])
def test_600_dpi(tmp_path, settings, small, small_at_600_dpi):
    client = make_client(tmp_path)
    assert (TOO_SMALL in warning(preview(client, **settings))) == small
    assert (TOO_SMALL in warning(preview(client, high_res='1', **settings))) == small_at_600_dpi


def test_a_code_turned_is_as_small(tmp_path):
    client = make_client(tmp_path)
    for angle in ('0', '90', '45'):
        assert SMALL_SIZED_CODE in warning(preview(client, image_fit='0', qrcode_size='2', image_rotation=angle))
        assert TOO_SMALL not in warning(preview(client, image_fit='0', qrcode_size='3', image_rotation=angle))


def test_a_code_with_text(tmp_path):
    client = make_client(tmp_path)
    text = json.dumps([{'text': 'Box 17', 'size': '60'}])
    assert SMALL_CODE in warning(preview(client, print_type='qrcode_text', text=text, label_size='12'))
    assert 'X-Label-Warning' not in preview(client, print_type='qrcode_text', text=text, code_text='Box 17').headers


def test_a_code_of_blanks_left_empty_is_not_said_to_be_small(tmp_path):
    # The preview shows their names in its place, printed it has their values
    client = make_client(tmp_path)
    response = preview(client, print_type='qrcode_text', code_text='', label_size='12', mark_blanks='1',
                       text=json.dumps([{'text': '{{blank:Link}}', 'size': '40'}]))
    assert warning(response) == 'The code is empty until its blanks are filled in.'


def image():
    out = io.BytesIO()
    Image.new('RGB', (300, 300), (90, 90, 90)).save(out, 'PNG')
    out.seek(0)
    return out, 'gray.png', 'image/png'


@pytest.mark.parametrize('settings, told', [
    # The size of a barcode next to an image cannot be set
    ({'barcode_type': 'code128', 'code_text': 'ABC-12345', 'image_fit': '0', 'high_res': '1'}, SMALL_BARCODE_NEXT_TO_IMAGE),
    ({'barcode_type': 'code128', 'code_text': 'ABC-12345', 'image_fit': '0'}, None),
    ({'barcode_type': 'code128', 'code_text': 'ABC-12345', 'label_size': '12'}, SMALL_BARCODE_NEXT_TO_IMAGE),
    # That of a 2D code can
    ({'image_fit': '0', 'qrcode_size': '2'}, SMALL_SIZED_CODE),
    ({'label_size': '12'}, SMALL_CODE),
    ({'image_fit': '0', 'qrcode_size': '3'}, None),
    ({'barcode_type': 'ean13', 'code_text': '400638133393', 'image_fit': '0'}, None),
])
def test_a_code_next_to_an_image(tmp_path, settings, told):
    client = make_client(tmp_path)
    response = preview(client, print_type='image_qrcode_text', image=image(), **settings)
    if told is None:
        assert TOO_SMALL not in warning(response)
    else:
        assert told in warning(response)


@pytest.mark.parametrize('size, dpi, bar, small', [
    (2.9, 300, False, True),
    (3, 300, False, False),
    # Bars may be narrower than dots of 2D codes
    (2.9, 300, True, False),
    (2.2, 300, True, True),
    # 0.25 mm at 180 dpi are less than 2 dots
    (1.8, 180, False, True),
    (1.8, 180, True, True),
    (2, 180, False, False),
    (5, 600, False, True),
    (6, 600, False, False),
    (4.5, 600, True, False),
    # Exactly 0.25 mm and 0.19 mm
    (0.2 * 1.25 * 300 / 25.4, 300, False, False),
    (0.19 * 300 / 25.4, 300, True, False),
])
def test_too_small(size, dpi, bar, small):
    assert codes.too_small(size, dpi, bar) == small


@pytest.mark.parametrize('code_type, value, mm', [
    ('code128', 'ABC-12345', 0.2),
    ('code39', 'HELLO', 0.2),
    ('ean13', '400638133393', 0.33),
    ('ean13-guard', '400638133393', 0.33),
    ('upca', '01234567890', 0.33),
    ('isbn10', '316148410', 0.33),
    # Narrow bars of two modules
    ('itf', '123456', 0.2),
    ('codabar', 'A12345B', 0.4),
])
def test_the_narrowest_bar(code_type, value, mm):
    assert codes.narrowest_bar(code_type, value) == pytest.approx(mm * 300 / 25.4)
