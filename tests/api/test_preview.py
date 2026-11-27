"""Previews through the API: images, resolution and gray PNGs."""

import io
import json

import pytest
from PIL import Image
from flask.testing import FlaskClient

from app.labeldesigner import drivers
from app.labeldesigner.drivers import devices, usbip
from app.labeldesigner.drivers.brother.status import parse_status
from tests.helpers import EXAMPLE_FORMDATA, make_client, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    @pytest.mark.parametrize('settings, reference', [
        pytest.param({}, 'simple.png', id='preview'),
        pytest.param({'high_res': 1}, 'simple_high_res.png', id='high_res'),
        pytest.param({'orientation': 'rotated'}, 'rotated.png', id='rotated'),
    ])
    def test_generate_preview(self, client: FlaskClient, settings, reference):
        data = dict(EXAMPLE_FORMDATA, **settings)
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Left', 'size': '60', 'align': 'left'},
            {'font': 'Droid Sans,Mono', 'text': '-- LONG MONO TEXT --', 'size': '50', 'align': 'center'},
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, reference)

    def test_generate_preview_inverted(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! LEFT !!!',
                'size': '50',
                'align': 'left',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! CENTER !!!',
                'size': '50',
                'align': 'center',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! RIGHT !!!',
                'size': '50',
                'align': 'right',
                'inverted': 'true'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'inverted_text.png')

    def test_concurrent_preview_requests(self, client: FlaskClient):
        """
        Spawn multiple threads that each create their own test client instance
        and post to the preview endpoint. Using threads avoids pickling issues
        with Flask test clients when using multiprocessing.Process.
        """
        from threading import Thread

        results = []
        # Protect shared list
        import threading
        results_lock = threading.Lock()

        def make_request(i: int):
            # Each thread creates its own test client to avoid sharing state
            app = client.application
            data = EXAMPLE_FORMDATA.copy()
            data['text'] = json.dumps([
                {'font': 'DejaVu Sans,Book', 'text': str(i), 'size': '32', 'align': 'center'}
            ])
            with app.test_client() as local_client:
                resp = local_client.post('/labeldesigner/api/preview', data=data)
                with results_lock:
                    results.append(resp.status_code)

        threads = [Thread(target=make_request, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        assert all(code == 200 for code in results)


def test_gray_preview_is_made_without_looking_at_each_pixel(tmp_path, monkeypatch):
    # Whether red or color is chosen says it: a large label needed several
    # copies of its pixels to find out again
    def split(self):
        raise AssertionError('split')
    monkeypatch.setattr(Image.Image, 'split', split)
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT))
    assert response.status_code == 200 and Image.open(io.BytesIO(response.data)).mode == 'L'


@pytest.mark.parametrize('settings, mode', [
    ({}, 'L'),
    # With red or color chosen it stays in color, even with nothing red on it
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Red', 'size': '40', 'color': 'red'}])}, 'RGB'),
    # Tape without red prints it black
    ({'label_size': '62', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Red', 'size': '40', 'color': 'red'}])}, 'L'),
    ({'print_color': 'red'}, 'RGB'),
    # A frame is red only where it is drawn
    ({'border_color': 'red', 'border_thickness': '0'}, 'L'),
    ({'border_color': 'red', 'border_thickness': '2'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'colored'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'red_and_black'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'grayscale'}, 'L'),
])
def test_preview_without_red_or_color_is_stored_in_gray(tmp_path, settings, mode):
    # The same pixels in half the bytes, the reference images check them
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62red', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Gray', 'size': '40'}]))
    data.update(settings)
    if data.get('print_type') == 'image':
        image = Image.new('L', (200, 100), 255)
        image.paste(0, (50, 25, 150, 75))
        out = io.BytesIO()
        image.save(out, 'PNG')
        data['image'] = (io.BytesIO(out.getvalue()), 'gray.png', 'image/png')
    response = client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).mode == mode


@pytest.mark.parametrize('label_size, told', [
    pytest.param('62', {'#2563eb': 'black', '#EA580C': 'black', 'red': 'black', '#16a34a': 'black', '#db2777': 'black'}, id='black'),
    pytest.param('62red', {'#2563eb': 'black', '#EA580C': 'red', '#16a34a': 'black', '#db2777': 'red'}, id='black and red'),
    pytest.param('vc25', None, id='color tape'),
])
def test_the_preview_tells_the_colors_the_tape_prints_otherwise(tmp_path, label_size, told):
    # The label designer says so next to the color, the rule stays here
    client = make_client(tmp_path)
    lines = [{'font': 'DejaVu Sans,Book', 'text': text, 'size': '30', 'color': color}
             for text, color in (('Blue', '#2563eb'), ('Orange', '#EA580C'), ('Red', 'red'), ('Black', 'black'), ('Plain', ''))]
    data = dict(EXAMPLE_FORMDATA, label_size=label_size, text=json.dumps(lines), print_type='qrcode_text', code_text='x',
                print_color='#16a34a', border_color='#db2777', border_thickness='2')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    header = response.headers.get('X-Label-Colors')
    assert (json.loads(header) if header else None) == told



SHARED = 'usbip://192.0.2.30/1-1'
# A label the QL-800 does not print, the QL-1100 does
LONG = {'label_size': '62', 'label_length': '1500', 'high_res': '0',
        'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Cable', 'size': '40'}])}


@pytest.fixture
def unasked(monkeypatch):
    """Fails a test that asks a printer for its model."""
    def asked(*args, **kwargs):
        raise AssertionError('The printer was asked for its model')
    monkeypatch.setattr(drivers, 'resolve', asked)
    monkeypatch.setattr(usbip, 'connect', asked)
    monkeypatch.setattr(devices, 'usb_device_id', asked)
    # A network printer named by its address, as if the search found it
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://192.0.2.10', 'tcp://192.0.2.11'})


def ql_status(model_code: int) -> bytes:
    """The status of a QL printer of the model code with 62 mm endless labels."""
    packet = bytearray(32)
    packet[0:5] = b'\x80\x20\x42\x34' + bytes([model_code])
    packet[10], packet[11] = 62, 0x0A
    return bytes(packet)


def too_long(client, **values) -> str:
    response = client.post('/labeldesigner/api/preview', data={**LONG, **values})
    assert response.status_code == 200, response.get_json()
    return response.headers.get('X-Label-Warning', '')


def test_a_preview_takes_the_model_of_a_listed_printer(tmp_path, unasked):
    client = make_client(tmp_path, model='QL-1100')
    client.application.config['PRINTERS'] = [('QL-800', SHARED, 'Desk')]
    # Its last status names another one, the listed one counts as in the status of the printers
    devices.status_cache[devices.lock_key(SHARED)] = parse_status(ql_status(0x43))
    for values in ({'printer': SHARED}, {'printer': 'Desk'}, {}):
        assert too_long(client, **values).endswith('Labels on the QL-800 can be at most 1000 mm long.')
    assert too_long(client, printer=SHARED, model='QL-1100') == ''


def test_a_preview_takes_the_model_the_printer_told_last(tmp_path, unasked):
    client = make_client(tmp_path, printer=SHARED, model='QL-1100')
    for printer in ('tcp://192.0.2.10', SHARED):
        devices.status_cache[devices.lock_key(printer)] = parse_status(ql_status(0x38))
        assert too_long(client, printer=printer).endswith('Labels on the QL-800 can be at most 1000 mm long.')


def test_a_preview_takes_the_configured_model_where_the_printer_told_none(tmp_path, unasked):
    client = make_client(tmp_path, printer=SHARED, model='QL-800')
    for values in ({'printer': SHARED}, {}):
        assert too_long(client, **values).endswith('Labels on the QL-800 can be at most 1000 mm long.')


def test_a_printer_nothing_tells_the_model_of_is_asked(tmp_path, unasked, monkeypatch):
    asked = []
    monkeypatch.setattr(drivers, 'resolve', lambda device, model: asked.append(device) or (drivers.for_model('QL-800'), 'QL-800'))
    client = make_client(tmp_path, model='QL-1100')
    assert too_long(client, printer='tcp://192.0.2.11').endswith('Labels on the QL-800 can be at most 1000 mm long.')
    assert asked == ['tcp://192.0.2.11']
