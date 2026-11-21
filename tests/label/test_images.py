"""Images and PDFs on labels through the API: fit, crop, rotation, scaling,
colors and pages."""

import io
import json
import os
import re
from typing import Union

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageOps
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from app.labeldesigner.enums import LabelContent
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _pdf, _red_square, _upload, EXAMPLE_FORMDATA, make_client, outcome, UPDATE_IMAGES, verify_image


class TestLabelDesignerAPI:
    def run_image_test(self,
                       client: FlaskClient,
                       image_path: Union[str, None] = None,
                       rotated: bool = False,
                       fit: bool = False,
                       text: bool = False,
                       image_mode: str = "grayscale",
                       high_res: bool = False):

        data = EXAMPLE_FORMDATA.copy()
        if image_path is None:
            if high_res:
                image_path = "tests/fixtures/_demo_image_highres.jpg"
            else:
                image_path = "tests/fixtures/_demo_image.jpg"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/jpeg",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = image_mode
        data['high_res'] = 1 if high_res else 0

        if image_mode == "black_and_white":
            data['image_bw_threshold'] = '128'

        data['image_fit'] = '1' if fit else '0'
        data['orientation'] = 'rotated' if rotated else 'normal'
        if text:
            data['text'] = json.dumps([
                {
                    'font': 'DejaVu Sans,Book',
                    'text': 'Test',
                    'size': '40',
                    'align': 'center'
                }
            ])

        expected_img_path = "image" + ("_rotated" if rotated else "") + ("_fit" if fit else "") + ("_text" if text else "") + ("_highres" if high_res else "") + "_" + image_mode + ".png"

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, expected_img_path)

    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    def image_updating_is_disabled(self):
        assert not UPDATE_IMAGES

    @pytest.mark.parametrize('settings', [
        pytest.param({}, id='image'),
        pytest.param({'fit': True}, id='fit'),
        pytest.param({'rotated': True}, id='rotated'),
        pytest.param({'rotated': True, 'fit': True}, id='rotated_fit'),
        pytest.param({'text': True}, id='with_text'),
        pytest.param({'text': True, 'fit': True}, id='with_text_fit'),
        pytest.param({'text': True, 'rotated': True}, id='with_text_rotated'),
        pytest.param({'text': True, 'rotated': True, 'fit': True}, id='with_text_fit_rotated'),
        pytest.param({'image_mode': 'colored', 'fit': True}, id='color_fit'),
        pytest.param({'image_mode': 'red_and_black', 'fit': True}, id='red_and_black_fit'),
        pytest.param({'image_mode': 'black_and_white', 'fit': True}, id='black_fit'),
        pytest.param({'high_res': True}, id='highres'),
        pytest.param({'high_res': True, 'fit': True}, id='highres_fit'),
    ])
    def test_image(self, client: FlaskClient, settings):
        # Compared with the reference image of the settings
        self.run_image_test(client, **settings)

    @pytest.mark.parametrize('fit', [True, False])
    @pytest.mark.parametrize('rotation', [0, 90])
    def test_image_crop(self, client: FlaskClient, fit, rotation):
        data = EXAMPLE_FORMDATA.copy()
        image_path = f"tests/fixtures/_X.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1' if fit else '0'
        data['image_crop'] = '1'
        data['image_rotation'] = rotation
        response = client.post('/labeldesigner/api/preview', data=data)

        # Should work
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, f'image_crop_{"fit" if fit else "nofit"}_{rotation}.png')

    def test_invalid_image_upload(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        fake_file = FileStorage(
            stream=io.BytesIO(b'not an image'),
            filename='fake.txt',
            content_type='text/plain',
        )
        data['print_type'] = 'image'
        data['image'] = fake_file
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert 'message' in data
        assert data['message'] == 'Unsupported file type'

    def test_corrupted_image_upload(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        corrupted = FileStorage(
            stream=io.BytesIO(b'\x89PNG\r\n\x1a\nBADBADBAD'),
            filename='corrupt.png',
            content_type='image/png',
        )
        data['print_type'] = 'image'
        data['image'] = corrupted
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert 'message' in data
        assert data['message'] == 'The image cannot be read'

    def test_image_mode_edge_case(self, client: FlaskClient):
        # An unknown mode was printed in black and white. bw, as the webhook
        # was described, is black and white.
        def preview(mode):
            data = EXAMPLE_FORMDATA.copy()
            data['print_type'] = 'image'
            data['image'] = _upload(_red_square(), 'red.png')
            data['image_mode'] = mode
            return client.post('/labeldesigner/api/preview', data=data)
        response = preview('not_a_mode')
        assert response.status_code == 400
        assert response.get_json()['message'] == 'image_mode must be grayscale, black_and_white, red_and_black or colored'
        assert preview('bw').data == preview('black_and_white').data

    @pytest.mark.parametrize('factor', [0, 1, 10, 100, 150])
    @pytest.mark.parametrize('sign', [1, -1])
    def test_image_manual_scaling(self, client, factor, sign):
        SCALING = factor * sign
        data = EXAMPLE_FORMDATA.copy()
        image_path = "tests/fixtures/_demo_image_simple.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '0'
        data['image_scaling_factor'] = SCALING
        response = client.post('/labeldesigner/api/preview', data=data)

        if SCALING > 0:
            # Should work
            assert response.status_code == 200
            assert response.content_type in ['image/png']

            # Check image
            verify_image(response.data, f'image_manual_scaling_{SCALING}.png')
        else:
            # Should be rejected
            assert response.status_code == 400
            assert response.is_json
            data = response.get_json()
            assert data['message'] == 'Image scaling factor must be > 0.'

    @pytest.mark.parametrize('rotation', [-10, -1, 0, 1, 10, 45, 90, 180, 270, 360, 450])
    def test_image_rotation(self, client: FlaskClient, rotation):
        data = EXAMPLE_FORMDATA.copy()
        image_path = "tests/fixtures/_demo_image_simple.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1'
        data['image_rotation'] = rotation
        response = client.post('/labeldesigner/api/preview', data=data)

        if rotation >= 0 and rotation <= 360:
            # Should work
            assert response.status_code == 200
            assert response.content_type in ['image/png']
            verify_image(response.data, f'image_rotation_{rotation}.png')
        else:
            # Should be rejected
            assert response.status_code == 400
            assert response.is_json
            data = response.get_json()
            assert data['message'] == 'Image rotation must be between 0 and 360 inclusive.'

    @pytest.mark.parametrize('fit', ['fit', 'no_fit'])
    @pytest.mark.parametrize('orientation', ['standard', 'rotated'])
    @pytest.mark.parametrize('label_size', ["12", "62", "62x29", "62x100", "d12"])
    def test_print_pdf_all_permutations(self, client: FlaskClient, label_size, orientation, fit):
        """PDF printing for endless and die-cut labels.

        Uses tests/images/TestPrint.pdf and checks both orientations
        and image-fit enabled/disabled.
        """
        pdf_path = 'tests/images/TestPrint.pdf'
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'image'
        data['label_size'] = label_size
        data['orientation'] = orientation
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1' if fit == 'fit' else '0'

        my_file = FileStorage(
            stream=open(pdf_path, 'rb'),
            filename=os.path.basename(pdf_path),
            content_type='application/pdf',
        )
        data['image'] = my_file

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']
        ls = label_size.replace(' ', '-').lower()
        rot = 'rotated' if orientation == 'rotated' else 'standard'
        verify_image(response.data, f'pdf_printing_{ls}_{rot}_{fit}.png')


def test_image_scaling_factor_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image_fit': '0', 'image_scaling_factor': '501',
                 'image': _upload(Image.new('RGB', (100, 100), 'black'), 'black.png')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Image scaling factor must not exceed 500.'


def test_image_fit_never_scales_to_zero_pixels(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image_fit': '1',
                 'image': _upload(Image.new('RGB', (5000, 3), 'black'), 'wide.png')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200


@pytest.mark.parametrize('filename, save_format', [('image.png', 'PNG'), ('image.pdf', 'PDF')])
def test_print_uploaded_image_several_times(tmp_path, filename, save_format):
    client = make_client(tmp_path)
    buffer = io.BytesIO()
    _red_square().save(buffer, save_format)
    buffer.seek(0)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'print_count': '3', 'image': FileStorage(stream=buffer, filename=filename)})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 200
    assert outcome(client, response) == ('printed', '')


def test_red_and_black_image_mode(tmp_path):
    client = make_client(tmp_path)
    with client.application.test_request_context():
        label = create_label_from_request({'label_size': '62red', 'print_type': 'image', 'image_mode': 'red_and_black'})
    assert label.label_content == LabelContent.IMAGE_RED_BLACK


@pytest.mark.parametrize('image_mode', ['red_and_black', 'colored'])
def test_pdf_upload_keeps_red(tmp_path, image_mode):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': '62red', 'print_type': 'image', 'image_mode': image_mode, 'image_fit': '1',
                 'image': _upload(_red_square(), 'red.pdf')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    red, green, _ = Image.open(io.BytesIO(response.data)).convert('RGB').split()
    assert ImageChops.subtract(red, green).getextrema()[1] > 60


def test_pdf_upload_uses_high_res_dpi(tmp_path):
    client = make_client(tmp_path)
    widths = []
    for high_res in ('0', '1'):
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'border_thickness': '0',
                     'high_res': high_res, 'image': _upload(_red_square(), 'red.pdf')})
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        bbox = ImageOps.invert(Image.open(io.BytesIO(response.data)).convert('L')).getbbox()
        widths.append(bbox[2] - bbox[0])
    # Same physical size, so twice the pixels at 600 dpi
    assert widths[1] >= 1.9 * widths[0]


@pytest.mark.parametrize('model, label_size, high_res, dpi', [
    ('QL-800', '62', '0', 300), ('QL-800', '62', '1', 600), ('PT-E550W', 'pt12', '0', 180), ('PT-P900W', 'pt36', '0', 360)])
def test_pdf_pages_are_rendered_at_the_dpi_of_the_tape(tmp_path, model, label_size, high_res, dpi):
    client = make_client(tmp_path, model=model)
    data = EXAMPLE_FORMDATA.copy()
    # A black page of one inch, along the tape
    data.update({'label_size': label_size, 'orientation': 'standard', 'print_type': 'image', 'image_mode': 'grayscale',
                 'image_fit': '0', 'border_thickness': '0', 'high_res': high_res,
                 'image': FileStorage(stream=io.BytesIO(_pdf((100, 100))), filename='inch.pdf')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    bbox = ImageOps.invert(Image.open(io.BytesIO(response.data)).convert('L')).getbbox()
    assert abs((bbox[3] - bbox[1]) - dpi) <= 2


def test_uploaded_image_is_decoded_once_per_job(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    calls = []
    original = routes.open_images
    monkeypatch.setattr(routes, 'open_images', lambda *args: calls.append(args) or original(*args))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'print_count': '3', 'image': _upload(_red_square(), 'red.png')})
    response = client.post('/labeldesigner/api/print', data=data)
    assert outcome(client, response) == ('printed', '')
    assert len(calls) == 1


def test_pdf_upload_renders_first_page_only(tmp_path, monkeypatch):
    import app.utils
    calls = []
    original = app.utils.convert_from_bytes
    monkeypatch.setattr(app.utils, 'convert_from_bytes', lambda data, **kwargs: calls.append(kwargs) or original(data, **kwargs))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': _upload(_red_square(), 'red.pdf')})
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert calls[0]['first_page'] == 1 and calls[0]['last_page'] == 1


@pytest.mark.parametrize('size, extra', [
    # Refused when decoding
    ((8200, 8000), {}),
    # Small enough, but not when rotated by 45 degrees
    ((16000, 2000), {'image_rotation': '45', 'image_fit': '0'}),
])
def test_large_images_are_refused(tmp_path, size, extra):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': _upload(Image.new('L', size, 'white'), 'large.png'), **extra})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    message = response.get_json()['message']
    assert message in ('The image is too large', 'Label is too large')


def test_pdf_with_huge_page_is_refused_before_rendering(tmp_path, monkeypatch):
    import app.utils
    monkeypatch.setattr(app.utils, 'convert_from_bytes', lambda *args, **kwargs: pytest.fail('PDF was rendered'))
    client = make_client(tmp_path)
    buffer = io.BytesIO()
    # 200 pixels at 1 dpi declare a page of 200 x 200 inches
    Image.new('L', (200, 200), 'white').save(buffer, 'PDF', resolution=1)
    buffer.seek(0)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': FileStorage(stream=buffer, filename='huge.pdf')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'].startswith('Label is too large')


THREE_PAGES = ((300, 200), (200, 300), (400, 100))


@pytest.mark.parametrize('selection, pages', [
    ('', [1]), ('all', [1, 2, 3, 4]), ('1-3, 4', [1, 2, 3, 4]), ('2-', [2, 3, 4]), ('3,1', [3, 1]), (' 2 - 3 ,', [2, 3]),
])
def test_selected_pdf_pages(selection, pages):
    from app.utils import selected_pdf_pages
    assert selected_pdf_pages(selection, 4) == pages


@pytest.mark.parametrize('selection, message', [
    ('5', 'The PDF has 4 pages, there is no page 5'),
    ('2-7', 'The PDF has 4 pages, there is no page 7'),
    ('0', 'The PDF has 4 pages, there is no page 0'),
    ('3-1', '"3-1" is not a page selection, give pages like 1-3, 5 or all'),
    ('first', '"first" is not a page selection, give pages like 1-3, 5 or all'),
    (',', '"," is not a page selection, give pages like 1-3, 5 or all'),
])
def test_invalid_pdf_page_selection(selection, message):
    from app.utils import selected_pdf_pages
    with pytest.raises(ValueError, match=re.escape(message)):
        selected_pdf_pages(selection, 4)


def _pdf_label(**values):
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]', **values}
    data['image'] = (io.BytesIO(_pdf(*THREE_PAGES)), 'pages.pdf', 'application/pdf')
    return data


def test_preview_shows_the_first_selected_pdf_page(tmp_path):
    client = make_client(tmp_path)
    previews = {}
    for selection, selected in (('', '1'), ('2', '1'), ('3-', '1'), ('all', '3')):
        response = client.post('/labeldesigner/api/preview', data=_pdf_label(pdf_pages=selection))
        assert response.status_code == 200
        assert response.headers['X-PDF-Pages'] == '3'
        assert response.headers['X-PDF-Selected-Pages'] == selected
        previews[selection] = Image.open(io.BytesIO(response.data)).size
    # The pages are fitted to the label width, their heights differ
    assert previews[''] == previews['all'] != previews['2'] != previews['3-']
    assert previews['2'][1] > previews[''][1] > previews['3-'][1]


def test_each_selected_pdf_page_is_printed_as_a_label(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=_pdf_label(pdf_pages='all', print_count='2', printer='simulation'))
    assert response.status_code == 200, response.get_json()
    files = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in files]
    # Collated: all pages, then all pages again
    assert len(heights) == 6
    assert heights[:3] == heights[3:]
    assert heights[1] > heights[0] > heights[2]


def test_pdf_pages_count_against_the_print_limit(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=_pdf_label(pdf_pages='all', print_count='40', printer='simulation'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'A print job must not have more than 100 labels'


def test_pdf_page_selection_errors_are_reported(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=_pdf_label(pdf_pages='4'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The PDF has no such page'


def test_pdf_pages_make_several_labels_of_image_labels_only(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = _pdf_label(pdf_pages='all', printer='simulation', print_type='text',
                      text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': '40'}]))
    assert client.post('/labeldesigner/api/print', data=data).status_code == 200
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 1


def _transparent_corners(mode: str) -> Image.Image:
    """A light gray square in the middle of an image that is transparent
    around it, black where it is transparent."""
    image = Image.new('RGBA', (120, 120), (0, 0, 0, 0))
    image.paste((200, 200, 200, 255), (30, 30, 90, 90))
    if mode == 'LA':
        return image.convert('LA')
    if mode == 'P':
        palette = Image.new('P', image.size, 0)
        palette.putpalette([0, 0, 0, 200, 200, 200])
        palette.paste(1, (30, 30, 90, 90))
        palette.info['transparency'] = 0
        return palette
    return image


@pytest.mark.parametrize('image_mode', ['grayscale', 'black_and_white', 'red_and_black', 'colored'])
@pytest.mark.parametrize('source', ['RGBA', 'LA', 'P'])
@pytest.mark.parametrize('crop', ['0', '1'])
def test_transparent_parts_print_white(tmp_path, image_mode, source, crop):
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': '62', 'print_type': 'image', 'image_mode': image_mode, 'image_fit': '1', 'image_crop': crop,
        'image': _upload(_transparent_corners(source), 'corners.png')})
    assert response.status_code == 200
    label = Image.open(io.BytesIO(response.data)).convert('L')
    assert label.getextrema()[0] > 100


def _letter_l() -> Image.Image:
    """A black "L" on white, the top left corner of its box black."""
    image = Image.new('L', (60, 80), 'white')
    image.paste(0, (10, 10, 20, 70))
    image.paste(0, (10, 60, 45, 70))
    return image


def _framed_logo() -> Image.Image:
    """A black square in a black frame, white around the frame."""
    image = Image.new('L', (100, 80), 'white')
    ImageDraw.Draw(image).rectangle((10, 10, 89, 69), outline=0, width=3)
    image.paste(0, (40, 30, 60, 50))
    return image


def _cropped_label(tmp_path, image: Image.Image, rotation: int) -> tuple:
    """The label of image alone, cropped and rotated at its size, and the
    box of what is black on it."""
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'image_crop': '1',
        'image_rotation': str(rotation), 'margin_top': '0', 'margin_bottom': '0', 'image': _upload(image, 'image.png')})
    assert response.status_code == 200
    label = Image.open(io.BytesIO(response.data)).convert('L')
    return label, ImageOps.invert(label).getbbox()


@pytest.mark.parametrize('image', [_letter_l, _framed_logo])
@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_cropped_image_keeps_what_is_in_its_corner(tmp_path, image, rotation):
    # Black in the top left corner of the content is no background
    source = image()
    content = source.crop(ImageOps.invert(source).getbbox()).rotate(-rotation, expand=True)
    label, ink = _cropped_label(tmp_path, source, rotation)
    assert ink is not None
    assert ImageChops.difference(label.crop(ink), content).getbbox() is None


@pytest.mark.parametrize('rotation', [30, 135])
def test_image_turned_by_another_angle_is_cropped_again(tmp_path, rotation):
    # Without the white corners the rotation adds
    label, ink = _cropped_label(tmp_path, _letter_l(), rotation)
    assert ink[1] == 0 and ink[3] == label.height


def _simulated_labels(printer) -> list:
    """The labels printed on the simulator, removed from it."""
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    labels = []
    for name in names:
        path = os.path.join(printer.SIMULATED_LABELS_DIR, name)
        with Image.open(path) as image:
            labels.append(image.tobytes())
        os.remove(path)
    return labels


def test_copies_of_a_job_size_the_image_once(tmp_path, monkeypatch):
    # Cropped, rotated and scaled once for all copies
    from app.labeldesigner import printer
    from app.labeldesigner.label import SimpleLabel
    crops, sized = [], []
    crop, size = SimpleLabel._crop_image_to_content, SimpleLabel._sized
    monkeypatch.setattr(SimpleLabel, '_crop_image_to_content', staticmethod(lambda img: crops.append(1) or crop(img)))
    monkeypatch.setattr(SimpleLabel, '_sized', lambda self, *args, **kwargs: sized.append(1) or size(self, *args, **kwargs))
    data = {'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale', 'image_crop': '1', 'image_rotation': '30',
            'print_count': '4', 'printer': 'simulation'}
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=dict(data, image=_upload(_letter_l(), 'l.png')))
    assert outcome(client, response) == ('printed', '')
    assert len(sized) == 1 and len(crops) == 2
    labels = _simulated_labels(printer)
    assert len(labels) == 4 and len(set(labels)) == 1
    # As printed alone
    response = client.post('/labeldesigner/api/print', data=dict(data, print_count='1', image=_upload(_letter_l(), 'l.png')))
    assert outcome(client, response) == ('printed', '')
    assert _simulated_labels(printer) == labels[:1]


def test_each_image_of_a_job_is_sized_on_its_own(tmp_path):
    # Two images, each copy as the image printed alone
    from app.labeldesigner import printer
    data = {'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale', 'image_crop': '1', 'image_rotation': '90',
            'printer': 'simulation'}
    client = make_client(tmp_path)
    files = [_upload(_letter_l(), 'l.png'), _upload(_framed_logo(), 'logo.png')]
    response = client.post('/labeldesigner/api/print', data=dict(data, print_count='2', image=files))
    assert outcome(client, response) == ('printed', '')
    labels = _simulated_labels(printer)
    alone = []
    for image in (_letter_l(), _framed_logo()):
        response = client.post('/labeldesigner/api/print', data=dict(data, image=_upload(image, 'image.png')))
        assert outcome(client, response) == ('printed', '')
        alone += _simulated_labels(printer)
    assert alone[0] != alone[1]
    assert labels == alone * 2
