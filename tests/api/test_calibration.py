"""The calibration of the print position, kept with the settings of a printer
and used for every print on it."""

import base64
import io
import json

import pytest
from PIL import Image

from app.labeldesigner import printer, printer_settings

from tests.helpers import make_client

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])


def test_calibration_is_kept_per_printer():
    data = printer_settings.calibrated({'printers': {}}, 'tcp://192.0.2.1', -0.5, 0.25, 'PZ1TEST00001')
    assert printer_settings.calibration_of(data, 'tcp://192.0.2.1') == {'across': -0.5, 'along': 0.25, 'roll': 'PZ1TEST00001'}
    assert printer_settings.calibration_of(data, 'tcp://192.0.2.2') == {'across': 0.0, 'along': 0.0, 'roll': None}
    # Not one of the settings a print request may leave out or name
    assert 'calibration' not in printer_settings.of_printer(data, 'tcp://192.0.2.1')


@pytest.mark.parametrize('across, along, roll', [(0.3, 0, None), (5.25, 0, None), (0, -5.5, None), (0, 0, 'PZ1/../x')])
def test_calibration_off_the_steps_is_refused(across, along, roll):
    with pytest.raises(ValueError) as e:
        printer_settings.calibrated({'printers': {}}, 'tcp://192.0.2.1', across, along, roll)
    assert str(e.value) == printer_settings.CALIBRATION_MESSAGE


def test_readings_tell_how_far_the_print_sits_right():
    assert printer_settings.from_readings(-1.0, 1.0) == 1.0
    assert printer_settings.from_readings(0, -0.25) == -0.125


@pytest.mark.parametrize('left, right, message', [(-1.5, -1.0, 'READINGS_DISAGREE'), (0.1, 0, 'CALIBRATION_MESSAGE'),
                                                  (3.25, -3.25, 'CALIBRATION_MESSAGE')])
def test_readings_that_do_not_fit_are_refused(left, right, message):
    with pytest.raises(ValueError) as e:
        printer_settings.from_readings(left, right)
    assert str(e.value) == getattr(printer_settings, message)


@pytest.fixture
def offsets(monkeypatch):
    """The offset of each print job."""
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: jobs.append(self.offset) or '')
    return jobs


def _keep(tmp_path, content: str):
    (tmp_path / 'label_repo' / printer_settings.FILE).write_text(content, encoding='utf-8')


def _print(client, path):
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'white').save(image, 'PNG')
    if path == 'print':
        return client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': TEXT, 'label_size': '62'})
    if path == 'repository':
        client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf', 'label_size': '62', 'text': TEXT})
        return client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation'})
    if path == 'webhook':
        client.application.config['WEBHOOK_PASSWORD'] = 'secret'
        return client.post('/labeldesigner/api/webhook/print', json={
            'password': 'secret', 'printer': 'simulation', 'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    return client.post('/labeldesigner/api/homebox', data=image.getvalue(), headers={'Content-Type': 'image/png'})


@pytest.mark.parametrize('path', ['print', 'repository', 'webhook', 'homebox'])
def test_every_print_uses_the_calibration(tmp_path, offsets, path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    _keep(tmp_path, json.dumps(printer_settings.calibrated({'printers': {}}, 'simulation', -0.5, 0.25, None)))
    assert _print(client, path).get_json()['success'] is True
    assert offsets == [(-0.5, 0.25)]


def test_broken_settings_print_without_calibration(tmp_path, offsets):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    _keep(tmp_path, 'not json')
    assert _print(client, 'print').get_json()['success'] is True
    assert offsets == [(0.0, 0.0)]
