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


URL = '/labeldesigner/api/calibration'
B1 = {'printer': 'bt://00:00:5E:00:53:01', 'model': 'Niimbot/B1'}


@pytest.fixture
def b1(tmp_path):
    return make_client(tmp_path, empty_repo=True, model='Niimbot/B1')


def test_readings_move_the_print(b1):
    r = b1.post(URL, data={**B1, 'left': '-1', 'right': '1', 'roll': 'PZ1TEST00001'})
    assert r.get_json() == {'success': True, 'shift': 1.0,
                            'calibration': {'across': -1.0, 'along': 0.0, 'roll': 'PZ1TEST00001'}}
    assert b1.get(URL, query_string={**B1, 'label_size': 'niimbot40x30'}).get_json() == {
        'across': -1.0, 'along': 0.0, 'roll': 'PZ1TEST00001', 'kind': 'narrow'}
    # Read again on a label printed with it, half a step is rounded
    r = b1.post(URL, json={**B1, 'left': 0, 'right': -0.25})
    assert r.get_json()['calibration']['across'] == -1.0


def test_moving_up_or_down_keeps_the_sideways_offset(b1):
    b1.post(URL, data={**B1, 'left': '-1', 'right': '1'})
    r = b1.post(URL, data={**B1, 'along': '0.25'})
    assert r.get_json()['calibration'] == {'across': -1.0, 'along': 0.25, 'roll': None}
    assert r.get_json()['shift'] is None


def test_not_now_keeps_the_offsets_for_another_roll(b1):
    b1.post(URL, data={**B1, 'across': '0.5', 'along': '-0.25', 'roll': 'PZ1TEST00001'})
    r = b1.post(URL, data={**B1, 'roll': 'PZ1TEST00002'})
    assert r.get_json()['calibration'] == {'across': 0.5, 'along': -0.25, 'roll': 'PZ1TEST00002'}


@pytest.mark.parametrize('values, message', [({'left': '-1.5', 'right': '-1'}, 'READINGS_DISAGREE'),
                                             ({'across': 'far'}, 'CALIBRATION_MESSAGE'),
                                             ({'left': '1'}, 'CALIBRATION_MESSAGE'),
                                             ({}, 'CALIBRATION_MESSAGE')])
def test_refused_calibrations_keep_the_one_before(b1, values, message):
    b1.post(URL, data={**B1, 'across': '0.5'})
    r = b1.post(URL, data={**B1, **values})
    assert r.status_code == 400 and r.get_json()['message'] == getattr(printer_settings, message)
    assert b1.get(URL, query_string=B1).get_json()['across'] == 0.5


def test_printers_without_calibration_are_refused(tmp_path):
    from app.labeldesigner.drivers import niimbot
    client = make_client(tmp_path, empty_repo=True, model='Niimbot/D110_M')
    r = client.post(URL, data={'printer': 'bt://00:00:5E:00:53:01', 'model': 'Niimbot/D110_M', 'across': '0.5'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.NO_CALIBRATION
    assert niimbot.DRIVER.capabilities('Niimbot/B1')['calibration'] is True
    assert niimbot.DRIVER.capabilities('Niimbot/D110_M')['calibration'] is False


@pytest.mark.parametrize('label_size, kind', [('niimbot40x30', 'narrow'), ('niimbot50x30', 'wide')])
def test_the_calibration_label_follows_the_roll(b1, label_size, kind):
    assert b1.get(URL, query_string={**B1, 'label_size': label_size}).get_json()['kind'] == kind
