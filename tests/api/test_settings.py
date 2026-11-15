"""The settings of the printers kept on the server."""

import json

from app.labeldesigner import printer_settings

from tests.helpers import make_client

URL = '/labeldesigner/api/settings'


def test_settings_are_kept_per_printer_and_common(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {}
    r = client.post(URL, data={'printer': 'tcp://a', 'label_size': '62', 'cut': 'last', 'quality': 'fast'})
    assert r.status_code == 200
    assert r.get_json()['settings'] == {'label_size': '62', 'cut': 'last', 'quality': 'fast'}
    # Another printer has its own label size and cutting, the quality is
    # the same for all
    assert client.get(URL, query_string={'printer': 'tcp://b'}).get_json() == {'quality': 'fast'}
    # Only what is named changes, another browser's change stays
    client.post(URL, json={'printer': 'tcp://a', 'half_cut': '0'})
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {
        'label_size': '62', 'cut': 'last', 'quality': 'fast', 'half_cut': '0'}
    # An empty value removes one
    client.post(URL, data={'printer': 'tcp://a', 'label_size': ''})
    assert 'label_size' not in client.get(URL, query_string={'printer': 'tcp://a'}).get_json()
    # The same device however it is written
    assert client.get(URL, query_string={'printer': 'tcp://A:9100'}).get_json()['cut'] == 'last'


def test_printer_status_tells_the_settings(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'label_size': '62', 'quality': 'fast'})
    status = client.get('/labeldesigner/api/printer_status').get_json()
    simulator = next(p for p in status['printers'] if p['path'] == 'simulation')
    assert simulator['settings'] == {'label_size': '62', 'quality': 'fast'}


def test_the_settings_file_is_not_a_label(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'tcp://a', 'label_size': '62'})
    assert client.get('/labeldesigner/api/repository/list').get_json()['files'] == []
    # A label cannot be saved over it, a name loses its leading dot
    r = client.get('/labeldesigner/api/repository/load', query_string={'name': printer_settings.FILE})
    assert r.status_code == 404


def test_settings_are_checked(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    for wrong in ({'label_size': 'pt24'}, {'cut': 'sometimes'}, {'quality': 'best'}, {'chain': '2'},
                  {'colour': 'red'}, {}):
        r = client.post(URL, data={'printer': 'tcp://a', **wrong})
        assert r.status_code == 400, wrong
        assert r.get_json()['message'] in (printer_settings.SETTINGS_MESSAGE, 'The label size 24 mm (TZe tape) cannot be printed on a QL-800')
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {}


def test_a_broken_file_is_no_settings(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    (tmp_path / 'label_repo' / printer_settings.FILE).write_text('{"printers": [')
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {}
    client.post(URL, data={'printer': 'tcp://a', 'cut': 'none'})
    assert json.loads((tmp_path / 'label_repo' / printer_settings.FILE).read_text()) == {'printers': {'tcp://a:9100': {'cut': 'none'}}}


def test_a_usb_printer_is_kept_by_its_serial(monkeypatch):
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device: 'MFG:Brother;MDL:QL-800;SN:000F1Z123;')
    assert printer_settings.key('/dev/usb/lp1') == 'usb:QL-800/000F1Z123'
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device: 'MFG:Brother;MDL:QL-800;')
    assert printer_settings.key('file:///dev/usb/lp1') == '/dev/usb/lp1'


def test_collate_and_the_date_format_stay_in_the_browser(tmp_path):
    # A choice per print job and one of each browser, not of the printer
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    for setting in ({'collate': '0'}, {'date_format': '%d.%m.%Y'}):
        r = client.post(URL, data={'printer': 'tcp://a', **setting})
        assert r.status_code == 400 and r.get_json()['message'] == printer_settings.SETTINGS_MESSAGE


def test_settings_without_a_printer_are_those_of_the_simulator(tmp_path):
    # Where PRINTER_PRINTER is ?, a request without a printer prints there
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.application.config['PRINTER_PRINTER'] = '?'
    client.post(URL, data={'label_size': '29'})
    status = client.get('/labeldesigner/api/printer_status').get_json()
    assert next(p for p in status['printers'] if p['path'] == 'simulation')['settings'] == {'label_size': '29'}


def test_a_usb_printer_switched_off_keeps_its_settings(tmp_path, monkeypatch):
    # Switched off it tells no device ID, its settings are those of the
    # printer last seen on its device
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    device_id = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device: device_id)
    lp0 = {'printer': 'file:///dev/usb/lp0'}
    client.post(URL, data={**lp0, 'label_size': '62'})
    device_id = ''
    assert client.get(URL, query_string=lp0).get_json() == {'label_size': '62'}
    client.post(URL, data={**lp0, 'label_size': '29'})
    device_id = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'
    assert client.get(URL, query_string=lp0).get_json() == {'label_size': '29'}


def test_a_settings_file_that_cannot_be_read_is_kept(tmp_path, monkeypatch):
    # E.g. one written by another user: the settings of the other printers
    # in it stay
    from app.labeldesigner import refusals, routes
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'tcp://a', 'label_size': '62'})
    path = tmp_path / 'label_repo' / printer_settings.FILE
    before = path.read_text()

    def unreadable(file, *args, **kwargs):
        if str(file) == str(path):
            raise PermissionError(13, 'Permission denied')
        return open(file, *args, **kwargs)
    monkeypatch.setattr(routes, 'open', unreadable, raising=False)
    r = client.post(URL, data={'printer': 'tcp://b', 'cut': 'last'})
    assert r.status_code == 500 and r.get_json()['message'] == refusals.SETTINGS_NOT_SAVED
    assert path.read_text() == before


def test_settings_take_json_numbers_as_printing_does(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, json={'printer': 'tcp://a', 'label_size': 62, 'chain': 0, 'half_cut': False})
    assert r.get_json() == {'success': True, 'settings': {'label_size': '62', 'chain': '0', 'half_cut': '0'}}
    assert client.post(URL, json={'printer': 5, 'quality': 'fast'}).status_code == 400


def test_print_requests_take_what_they_leave_out_as_set(tmp_path, monkeypatch):
    # The API and the MCP tools print as the label designer does, a value
    # the request names comes first, then one saved with the label
    import base64
    import io
    from PIL import Image
    from app.labeldesigner import printer
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: jobs.append((self.label_size, self.cut, [entry['fast'] for entry in self._print_queue])) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'label_size': '29', 'cut': 'none', 'quality': 'fast'})
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': text})
    client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': text, 'label_size': '62', 'cut': 'each', 'fast_printing': '0'})
    client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf', 'label_size': '62', 'text': text})
    client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation'})
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'white').save(image, 'PNG')
    client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'printer': 'simulation',
                                                          'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    assert jobs == [('29', 'none', [True]), ('62', 'each', [False]), ('62', 'none', [True]), ('29', 'each', [True])]
