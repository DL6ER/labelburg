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


@pytest.mark.parametrize('across, along, roll', [(0.3, 0, None), (float('nan'), 0, None), (0, 0, 'PZ1/../x')])
def test_calibration_off_the_steps_is_refused(across, along, roll):
    with pytest.raises(ValueError) as e:
        printer_settings.calibrated({'printers': {}}, 'tcp://192.0.2.1', across, along, roll)
    assert str(e.value) == printer_settings.CALIBRATION_MESSAGE


@pytest.mark.parametrize('across, along', [(5.25, 0), (0, -5.5)])
def test_labels_are_not_moved_further_than_5_mm(across, along):
    with pytest.raises(ValueError) as e:
        printer_settings.calibrated({'printers': {}}, 'tcp://192.0.2.1', across, along, None)
    assert str(e.value) == printer_settings.OFFSET_LIMIT


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


@pytest.mark.parametrize('label_size, kind', [('niimbot40x30', 'narrow'), ('niimbot50x30', 'wide'), ('niimbotd50', 'round')])
def test_the_calibration_label_follows_the_roll(b1, label_size, kind):
    assert b1.get(URL, query_string={**B1, 'label_size': label_size}).get_json()['kind'] == kind


USBIP, BT = 'usbip://192.0.2.7/1-1', 'bt://00:00:5E:00:53:01'


def _told(monkeypatch, *devices_, serial='TEST000001', device_id=''):
    """The status of the devices told the printer's serial number."""
    from app.labeldesigner.drivers import devices
    # Not asked over the network for a device ID
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: device_id)
    monkeypatch.setattr(devices, 'status_cache', {devices.lock_key(d): {'serial': serial, 'model': 'Niimbot/B1'} for d in devices_})


def test_calibration_follows_a_b1_by_its_serial_number(monkeypatch):
    _told(monkeypatch, USBIP, BT)
    data = printer_settings.calibrated({'printers': {}}, USBIP, 0.5, 0, None)
    assert printer_settings.calibration_of(data, BT)['across'] == 0.5
    # Another B1 on the same address has its own
    _told(monkeypatch, USBIP, serial='TEST000002')
    assert printer_settings.calibration_of(data, USBIP)['across'] == 0.0


def test_calibration_is_found_before_the_serial_number_is_asked(monkeypatch):
    _told(monkeypatch, USBIP)
    data = printer_settings.calibrated({'printers': {}}, USBIP, 0.5, 0, None)
    # The server started again, the printer not asked yet
    _told(monkeypatch)
    assert printer_settings.calibration_of(data, USBIP)['across'] == 0.5


def test_settings_from_before_the_serial_number_was_known_are_kept(monkeypatch):
    _told(monkeypatch)
    data = printer_settings.calibrated({'printers': {}}, USBIP, 0.5, 0, None)
    _told(monkeypatch, USBIP)
    assert printer_settings.calibration_of(data, USBIP)['across'] == 0.5
    # Kept by the serial number from the next change on
    data = printer_settings.calibrated(data, USBIP, 0.25, 0, None)
    assert list(data['printers']) == ['serial:Niimbot/B1/TEST000001']


def test_printer_status_tells_the_calibration(b1, monkeypatch):
    from app.labeldesigner import routes
    b1.post(URL, data={**B1, 'across': '0.5', 'roll': 'PZ1TEST00001'})
    state = {'errors': [], 'path': B1['printer'], 'model': 'Niimbot/B1', 'roll': 'PZ1TEST00002'}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': B1['printer']})
    printer_ = b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['calibration'] == {'across': 0.5, 'along': 0.0, 'roll': 'PZ1TEST00001'}
    # Only where the model offers it
    monkeypatch.setitem(state, 'model', 'QL-800')
    assert 'calibration' not in b1.get('/labeldesigner/api/printer_status').get_json()['printers'][0]


def test_a_b1_on_usb_keeps_its_calibration_after_a_restart(monkeypatch):
    # On USB/IP it tells a device ID, without a serial number in it
    b1_id = 'MFG:NIIMBOT;CMD:ESC;MDL: B1;'
    _told(monkeypatch, USBIP, device_id=b1_id)
    data = printer_settings.calibrated({'printers': {}}, USBIP, 0.5, 0, None)
    _told(monkeypatch, device_id=b1_id)
    assert printer_settings.calibration_of(data, USBIP)['across'] == 0.5


def test_seen_that_is_not_a_printer_is_left_out(monkeypatch):
    _told(monkeypatch)
    from app.labeldesigner.drivers import devices
    data = {'printers': {}, 'seen': {devices.lock_key(USBIP): ['not', 'a', 'printer']}}
    assert printer_settings.calibration_of(data, USBIP) == {'across': 0.0, 'along': 0.0, 'roll': None}


def test_settings_that_are_not_text_print_without_calibration(tmp_path, offsets):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    (tmp_path / 'label_repo' / printer_settings.FILE).write_bytes(b'\xff\xfe\x00broken')
    assert _print(client, 'print').get_json()['success'] is True
    assert offsets == [(0.0, 0.0)]


@pytest.mark.parametrize('values', [{'left': '', 'right': ''}, {'left': '-1', 'right': ''}])
def test_readings_left_empty_are_refused(b1, values):
    r = b1.post(URL, data={**B1, **values, 'roll': 'PZ1TEST00001'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.CALIBRATION_MESSAGE
    assert b1.get(URL, query_string=B1).get_json()['roll'] is None


def test_moving_further_than_5_mm_is_refused_plainly(b1):
    b1.post(URL, data={**B1, 'across': '5'})
    r = b1.post(URL, data={**B1, 'left': '-1', 'right': '1'})
    assert r.status_code == 200
    r = b1.post(URL, data={**B1, 'across': '5.25'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.OFFSET_LIMIT


def test_a_failing_calibration_print_answers_plainly(b1, monkeypatch):
    from app.labeldesigner import refusals

    def broken(self):
        raise RuntimeError('broken')
    monkeypatch.setattr(printer.PrinterQueue, 'print_calibration', broken)
    r = b1.post(URL + '/print', data={**B1, 'label_size': 'niimbot40x30'})
    assert r.status_code == 400 and r.get_json()['message'] == refusals.PRINT_FAILED


def test_calibration_is_tried_without_saving(b1):
    r = b1.post(URL, data={**B1, 'across': '0.5', 'along': '-0.25', 'save': '0'})
    assert r.get_json()['calibration'] == {'across': 0.5, 'along': -0.25, 'roll': None}
    assert b1.get(URL, query_string=B1).get_json()['across'] == 0.0


def test_readings_count_from_the_offset_the_label_was_printed_with(b1):
    # Printed moved 0.5 mm right, it still sat 1 mm right
    r = b1.post(URL, data={**B1, 'left': '-1', 'right': '1', 'across': '0.5', 'along': '0.25', 'save': '0'})
    assert r.get_json()['calibration'] == {'across': -0.5, 'along': 0.25, 'roll': None}
    assert r.get_json()['shift'] == 1.0


def test_calibration_label_prints_with_offsets_not_saved_yet(b1, monkeypatch):
    offsets = []
    monkeypatch.setattr(printer.PrinterQueue, 'print_calibration', lambda self: offsets.append(self.offset) or '')
    b1.post(URL, data={**B1, 'across': '0.25'})
    b1.post(URL + '/print', data={**B1, 'label_size': 'niimbot40x30'})
    b1.post(URL + '/print', data={**B1, 'label_size': 'niimbot40x30', 'across': '-0.5', 'along': '0.25'})
    assert offsets == [(0.25, 0.0), (-0.5, 0.25)]
    r = b1.post(URL + '/print', data={**B1, 'label_size': 'niimbot40x30', 'across': '6'})
    assert r.status_code == 400 and r.get_json()['message'] == printer_settings.OFFSET_LIMIT


def test_calibration_without_a_label_size_is_for_the_roll_in_the_printer(b1, monkeypatch):
    sizes = []
    monkeypatch.setattr(printer.PrinterQueue, 'print_calibration', lambda self: sizes.append(self.label_size) or '')
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {'model': model, 'media_width': 40, 'media_length': 30, 'errors': []})
    assert b1.post(URL + '/print', data=B1).get_json() == {'success': True}
    assert sizes == ['niimbot40x30']
    assert b1.get(URL, query_string=B1).get_json()['kind'] == 'narrow'


@pytest.mark.parametrize('save', [False, 'false', 0])
def test_calibration_tried_with_save_false_is_not_kept(b1, save):
    r = b1.post(URL, json={**B1, 'across': 0.5, 'save': save})
    assert r.get_json()['calibration']['across'] == 0.5
    assert b1.get(URL, query_string=B1).get_json()['across'] == 0.0


def test_a_broken_settings_file_is_left_as_it_is(b1, tmp_path):
    _keep(tmp_path, '{"printers": [')
    answer = b1.post(URL, data={**B1, 'left': '-1', 'right': '1'})
    assert (answer.status_code, answer.get_json()['message']) == (500, printer_settings.BROKEN)
    assert (tmp_path / 'label_repo' / printer_settings.FILE).read_text(encoding='utf-8') == '{"printers": ['


@pytest.mark.parametrize('kind', ['narrow', 'wide', 'round'])
def test_example_picture_is_served_for_each_kind(b1, kind):
    r = b1.get(f'{URL}/example.png', query_string={**B1, 'kind': kind})
    assert r.status_code == 200 and r.headers['Content-type'] == 'image/png'
    assert Image.open(io.BytesIO(r.data)).size[0] > 0
