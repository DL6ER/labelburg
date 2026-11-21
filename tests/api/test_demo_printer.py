"""The demo printer: the simulator, of the model chosen in the Settings."""

import pytest

from app.labeldesigner import printer, printer_settings, tape

from tests.helpers import EXAMPLE_FORMDATA, make_client

URL = '/labeldesigner/api/settings'
STATUS = '/labeldesigner/api/printer_status'


def demo(client) -> dict:
    printers = client.get(STATUS).get_json()['printers']
    shown = [p for p in printers if p['path'] == 'simulation']
    assert len(shown) == 1
    return shown[0]


@pytest.fixture
def labels_printed(monkeypatch):
    """The model and label size of each label printed."""
    labels = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue',
                        lambda self, label, high_res=False, fast=False: labels.append((self.model, self.label_size)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    return labels


def test_the_demo_printer_starts_as_the_configured_model(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    shown = demo(client)
    assert (shown['name'], shown['model'], shown['takes']) == ('Demo printer', 'QL-800', tape.takes('QL-800'))
    assert 'demo_model' not in shown['settings']


def test_the_demo_printer_takes_the_model_chosen_in_the_settings(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, data={'printer': 'simulation', 'model': 'QL-800', 'demo_model': 'VC-500W'})
    assert r.status_code == 200
    # With the first roll of the model, as if set by hand
    assert r.get_json()['settings'] == {'demo_model': 'VC-500W', 'label_size': 'vc9'}
    assert (r.get_json()['tape']['id'], r.get_json()['tape']['manual']) == ('vc9', True)
    shown = demo(client)
    assert (shown['name'], shown['model'], shown['takes']) == ('Demo printer', 'VC-500W', tape.takes('VC-500W'))
    assert (shown['color_support'], shown['red_support'], shown['tape']['id']) == (True, False, 'vc9')
    assert client.get(URL, query_string={'printer': 'simulation'}).get_json() == {'demo_model': 'VC-500W', 'label_size': 'vc9'}
    # An empty one is the configured model again, with its roll
    client.post(URL, data={'printer': 'simulation', 'demo_model': ''})
    shown = demo(client)
    assert (shown['model'], shown['settings'], shown['tape']['id']) == ('QL-800', {'label_size': '62'}, '62')


def test_the_demo_printer_has_the_roll_of_its_model(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    # Assumed until a model or a roll is chosen, as for a printer that does not tell it
    told = demo(client)['tape']
    assert (told['id'], told['assumed']) == ('62', True)
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'Epson/ESC-POS'})
    told = demo(client)['tape']
    assert (told['id'], told['known'], told['manual']) == ('escpos80', True, True)
    # The same paper of the other model
    client.post(URL, data={'printer': 'simulation', 'label_size': 'escpos58'})
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'Epson/ESC-POS-180'})
    assert demo(client)['tape']['id'] == 'escpos58-180'


def test_prints_on_the_demo_printer_are_made_for_its_model_and_roll(tmp_path, labels_printed):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    on_vc = {**EXAMPLE_FORMDATA, 'label_size': 'vc25', 'printer': 'simulation'}
    assert client.post('/labeldesigner/api/print', data=on_vc).status_code == 400
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'VC-500W'})
    # Placed on its roll, as on a printer with a roll set by hand
    assert client.post('/labeldesigner/api/print', data=on_vc).status_code == 200
    client.post(URL, data={'printer': 'simulation', 'label_size': 'vc25'})
    assert client.post('/labeldesigner/api/print', data=on_vc).status_code == 200
    # A request without a printer, where none is configured, too
    assert client.post('/labeldesigner/api/print', data={**on_vc, 'printer': ''}).status_code == 200
    assert labels_printed == [('VC-500W', 'vc9'), ('VC-500W', 'vc25'), ('VC-500W', 'vc25')]


@pytest.mark.parametrize('printer, model', [('tcp://192.0.2.10', 'QL-1100'), ('simulation', 'QL-9000'),
                                            ('simulation', 'Unknown'), ('simulation', 'vc25')])
def test_only_the_demo_printer_takes_a_model_it_knows(tmp_path, monkeypatch, printer, model):
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://192.0.2.10'})
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, data={'printer': printer, 'demo_model': model})
    assert (r.status_code, r.get_json()['message']) == (400, printer_settings.SETTINGS_MESSAGE)
    assert client.get(URL, query_string={'printer': printer}).get_json() == {}


def test_the_settings_of_a_change_are_checked_for_the_new_model(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, data={'printer': 'simulation', 'model': 'QL-800', 'demo_model': 'VC-500W', 'label_size': 'vc25'})
    assert r.status_code == 200
    assert r.get_json()['tape']['id'] == 'vc25'
    r = client.post(URL, data={'printer': 'simulation', 'demo_model': 'QL-1100', 'label_size': 'vc25'})
    assert r.status_code == 400
    assert demo(client)['model'] == 'VC-500W'


def test_a_new_model_keeps_the_roll_and_cutting_it_can_take(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'label_size': '29', 'cut': 'none', 'leaves_first': 'bottom'})
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'QL-1100'})
    assert client.get(URL, query_string={'printer': 'simulation'}).get_json() == {
        'demo_model': 'QL-1100', 'label_size': '29', 'cut': 'none', 'leaves_first': 'bottom'}
    # The VC-500W takes neither, it starts on its first tape
    r = client.post(URL, data={'printer': 'simulation', 'demo_model': 'VC-500W'})
    assert r.get_json()['settings'] == {'demo_model': 'VC-500W', 'label_size': 'vc9', 'leaves_first': 'bottom'}
    assert demo(client)['tape']['id'] == 'vc9'


def test_configured_simulators_are_one_demo_printer(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('QL-700', 'simulation'), ('QL-1100', 'simulation', 'Office')]
    shown = demo(client)
    assert (shown['name'], shown['model']) == ('Demo printer', 'QL-700')
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'Niimbot/B1'})
    assert demo(client)['model'] == 'Niimbot/B1'


def test_prints_by_the_name_of_a_configured_simulator_are_on_the_demo_printer(tmp_path, labels_printed):
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('QL-700', 'simulation'), ('QL-1100', 'simulation', 'Office')]
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'Niimbot/B1'})
    r = client.post('/labeldesigner/api/print', data={**EXAMPLE_FORMDATA, 'label_size': 'niimbot50x30', 'printer': 'Office'})
    assert r.status_code == 200, r.get_json()
    assert labels_printed == [('Niimbot/B1', 'niimbot40x20')]


def test_a_configured_name_names_the_demo_printer(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('QL-1100', 'simulation', 'Try-out')]
    assert demo(client)['name'] == 'Try-out'
