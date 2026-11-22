"""The printer status of the API: what each printer reports, and the label
size it prints on."""

from types import SimpleNamespace

import pytest

from app.labeldesigner import printer, routes
from tests.helpers import make_client


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printers these tests name are known, as if the search found them
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://192.0.2.1'})


def _printer(path, model, **status):
    return {'errors': [], 'path': path, 'model': model, 'media_width': None, 'media_length': 0, **status}


def test_tape_is_reported_or_assumed(tmp_path, monkeypatch):
    printers = [_printer('file:///dev/usb/lp0', 'QL-800', media_width=29, media_length=90),
                _printer('tcp://192.0.2.1', 'PUQU/Q00'),
                _printer('simulation', 'QL-800')]
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**printers[0], 'printers': printers})
    client = make_client(tmp_path)
    client.application.config['LABEL_DEFAULT_SIZE'] = '62'
    status = client.get('/labeldesigner/api/printer_status').get_json()
    # The tape reported, else assumed: the default or the first one the
    # printer takes
    assert [(p['tape']['id'], p['tape']['assumed']) for p in status['printers']] == [
        ('29x90', False), ('puqu50x30', True), ('62', True)]
    assert (status['tape']['id'], status['tape']['assumed']) == ('29x90', False)


def test_label_size_is_the_tape_labels_are_placed_on(tmp_path, monkeypatch):
    printers = [_printer('file:///dev/usb/lp0', 'QL-800', media_width=62),
                _printer('tcp://192.0.2.1', 'QL-800'),
                _printer('simulation', 'QL-800')]
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**printers[0], 'printers': printers})
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['LABEL_DEFAULT_SIZE'] = '62x29'
    client.post('/labeldesigner/api/settings', data={'printer': 'tcp://192.0.2.1', 'model': 'QL-800', 'label_size': '29'})
    status = client.get('/labeldesigner/api/printer_status').get_json()
    # Reported, set by hand, assumed: one set by hand is not assumed
    assert [(p['label_size'], p['label_size_assumed']) for p in status['printers']] == [
        ('62', False), ('29', False), ('62x29', True)]
    assert all((p['label_size'], p['label_size_assumed']) == (p['tape']['id'], p['tape']['assumed'])
               for p in [status, *status['printers']])


def test_tape_of_each_printer(tmp_path, monkeypatch):
    printers = [_printer('file:///dev/usb/lp0', 'QL-800', media_width=29, media_length=90),
                _printer('tcp://192.0.2.1', 'PUQU/Q00'),
                _printer('simulation', 'QL-800')]
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**printers[0], 'printers': printers})
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['LABEL_DEFAULT_SIZE'] = '62'
    client.post('/labeldesigner/api/settings', data={'printer': 'simulation', 'model': 'QL-800', 'label_size': '29'})
    status = client.get('/labeldesigner/api/printer_status').get_json()
    # Reported, assumed where the driver does not tell it, set by hand
    assert [(p['tape']['id'], p['tape']['known'], p['tape']['manual']) for p in status['printers']] == [
        ('29x90', True, False), ('puqu50x30', False, False), ('29', True, True)]
    assert [('62' in p['takes'], 'puqu50x30' in p['takes']) for p in status['printers']] == [
        (True, False), (False, True), (True, False)]
    assert (status['tape'], status['takes']) == (status['printers'][0]['tape'], status['printers'][0]['takes'])


def test_status_of_one_printer_names_it_as_listed(monkeypatch):
    listed, asked = 'tcp://192.0.2.1', ''.join(['tcp://', '192.0.2.1'])
    asked_driver = []
    driver = SimpleNamespace(followed_status=lambda device, model: asked_driver.append(device) or {})
    monkeypatch.setattr(printer.drivers, 'resolve', lambda device, model, assumed=False: (driver, model))
    config = {'PRINTER_PRINTER': listed, 'PRINTER_MODEL': 'QL-800'}
    state = printer.status_of_printer(config, asked)
    # Equal, but what goes on is the path from the list
    assert asked == listed and asked is not listed
    assert asked_driver[0] is listed and state['path'] is listed
    assert printer.status_of_printer(config, 'tcp://192.0.2.2') is None
