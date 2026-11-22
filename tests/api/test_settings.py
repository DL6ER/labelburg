"""The settings of the printers kept on the server."""

import json

import pytest

from app.labeldesigner import printer_settings
from app.labeldesigner.drivers.devices import lock_key

from tests.helpers import make_client

URL = '/labeldesigner/api/settings'


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printers these tests name are known, as if the search found them
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://a', 'tcp://b', 'tcp://A:9100', 'tcp://192.0.2.10'})


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
    # A change does not replace it, it may be fixed by hand
    answer = client.post(URL, data={'printer': 'tcp://a', 'cut': 'none'})
    assert (answer.status_code, answer.get_json()['message']) == (500, printer_settings.BROKEN)
    assert (tmp_path / 'label_repo' / printer_settings.FILE).read_text() == '{"printers": ['


def test_a_file_that_is_no_utf_8_is_no_settings(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    (tmp_path / 'label_repo' / printer_settings.FILE).write_bytes(b'\xff\xfe\x00broken')
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {}
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    assert client.post('/labeldesigner/api/preview', data={'printer': 'simulation', 'text': text}).status_code == 200
    answer = client.post(URL, data={'printer': 'tcp://a', 'cut': 'none'})
    assert (answer.status_code, answer.get_json()['message']) == (500, printer_settings.BROKEN)
    assert (tmp_path / 'label_repo' / printer_settings.FILE).read_bytes() == b'\xff\xfe\x00broken'


def test_an_empty_file_is_no_settings_yet(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    (tmp_path / 'label_repo' / printer_settings.FILE).write_text('\n')
    assert client.post(URL, data={'printer': 'tcp://a', 'cut': 'none'}).status_code == 200
    assert json.loads((tmp_path / 'label_repo' / printer_settings.FILE).read_text()) == {'printers': {'tcp://a:9100': {'cut': 'none'}}}


def test_a_usb_printer_is_kept_by_its_serial(monkeypatch):
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: 'MFG:Brother;MDL:QL-800;SN:000F1Z123;')
    assert printer_settings.key('/dev/usb/lp1') == 'usb:QL-800/000F1Z123'
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: 'MFG:Brother;MDL:QL-800;')
    assert printer_settings.key('file:///dev/usb/lp1') == '/dev/usb/lp1'


def test_collate_and_the_date_format_are_no_settings_of_a_printer(tmp_path):
    # Settings of labels, see test_label_settings.py
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
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: device_id)
    lp0 = {'printer': 'file:///dev/usb/lp0'}
    client.post(URL, data={**lp0, 'label_size': '62'})
    device_id = ''
    assert client.get(URL, query_string=lp0).get_json() == {'label_size': '62'}
    client.post(URL, data={**lp0, 'label_size': '29'})
    device_id = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'
    assert client.get(URL, query_string=lp0).get_json() == {'label_size': '29'}


@pytest.mark.parametrize('device_id', ['MFG:Brother;MDL:QL-800;SN:000F1Z123;', ''], ids=['on', 'switched off'])
def test_the_settings_of_a_usb_printer_read_its_device_id_once(monkeypatch, device_id):
    # Over USB/IP each read is a connection
    data = {'printers': {'usb:QL-800/000F1Z123': {'label_size': '62'}},
            'seen': {lock_key('file:///dev/usb/lp0'): 'usb:QL-800/000F1Z123'}}
    read = []
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: read.append(device) or device_id)
    assert printer_settings.of_printer(data, 'file:///dev/usb/lp0') == {'label_size': '62'}
    assert read == ['file:///dev/usb/lp0']


def test_a_printer_last_seen_that_is_no_printer_key_is_left_out(monkeypatch):
    # The file may be edited by hand, a USB/IP printer not reached since the
    # server started tells no device ID
    device = 'usbip://192.0.2.1/1-1'
    data = {'printers': {}, 'quality': 'fast', 'seen': {lock_key(device): ['not', 'a', 'printer']}}
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: '')
    assert printer_settings.of_printer(data, device) == {'quality': 'fast'}
    printer_settings.changed(data, device, {'cut': 'none'})
    assert printer_settings.of_printer(data, device) == {'quality': 'fast', 'cut': 'none'}


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
    assert (r.get_json()['success'], r.get_json()['settings']) == (True, {'label_size': '62', 'chain': '0', 'half_cut': '0'})
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
    # Each label is placed on the tape set by hand, the simulator reports none
    assert jobs == [('29', 'none', [True]), ('29', 'each', [False]), ('29', 'none', [True]), ('29', 'each', [True])]


def test_another_usb_printer_without_a_serial_has_settings_of_its_own(tmp_path, monkeypatch):
    # Only a printer switched off, which tells no device ID, is the one last
    # seen on its device
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    device_id = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: device_id)
    lp0 = {'printer': 'file:///dev/usb/lp0'}
    client.post(URL, data={**lp0, 'label_size': '62'})
    device_id = 'MFG:Brother;CMD:PT-CBP;MDL:QL-1100;CLS:PRINTER;'
    assert client.get(URL, query_string=lp0).get_json() == {}
    client.post(URL, data={**lp0, 'label_size': '29'})
    device_id = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'
    assert client.get(URL, query_string=lp0).get_json() == {'label_size': '62'}


def test_a_saved_label_prints_with_the_quality_set(tmp_path, monkeypatch):
    # The label designer saves high_res 0 with every label, the quality is
    # one of the printer
    from app.labeldesigner import printer
    fast = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: fast.extend(entry['fast'] for entry in self._print_queue) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'quality': 'fast'})
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf', 'label_size': '62', 'high_res': '0', 'text': text})
    for given in ({}, {'fast_printing': '0'}):
        client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation', **given})
    assert fast == [True, False]


def test_a_request_that_names_the_quality_of_a_saved_label_decides_it_alone(tmp_path, monkeypatch):
    # As for /api/print: Dense set and fast_printing 1 is fast printing, not both
    from app.labeldesigner import printer
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: jobs.extend((entry['high_res'], entry['fast']) for entry in self._print_queue) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf', 'label_size': '62', 'high_res': '0', 'text': text})
    for quality, given in (('high', {'fast_printing': '1'}), ('fast', {'high_res': '1'})):
        client.post(URL, data={'printer': 'simulation', 'quality': quality})
        client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': text, 'label_size': '62', **given})
        client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation', **given})
    assert jobs == [(False, True), (False, True), (True, False), (True, False)]


def test_a_request_that_names_the_quality_of_a_saved_label_leaves_out_its_own(tmp_path, monkeypatch):
    # A label saved with fast printing, e.g., by the previous label
    # designer: high_res 1 in the request is 600 dpi, not 600 dpi and fast
    from app.labeldesigner import printer
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: jobs.extend((entry['high_res'], entry['fast']) for entry in self._print_queue) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]
    (tmp_path / 'label_repo' / 'Shelf.json').write_text(json.dumps({'name': 'Shelf.json', 'label_size': '62', 'text': text,
                                                                    'high_res': False, 'fast_printing': True}))
    for given in ({}, {'high_res': '1'}, {'high_res': '0'}):
        client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation', **given})
    assert jobs == [(False, True), (True, False), (False, False)]


def test_settings_refuse_a_printer_that_is_no_text(tmp_path):
    # As printing does, not with an error page
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    for printer in (5.5, ['tcp://a'], {'path': 'tcp://a'}):
        assert client.post(URL, json={'printer': printer, 'quality': 'fast'}).status_code == 400, printer


def test_a_preview_takes_what_it_leaves_out_as_set(tmp_path):
    # As printing does, so that it shows the label printed
    import io
    from PIL import Image
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'label_size': '29', 'quality': 'high'})
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])

    def size(**given):
        r = client.post('/labeldesigner/api/preview', data={'printer': 'simulation', 'text': text, **given})
        return Image.open(io.BytesIO(r.data)).size
    assert size() == size(label_size='29', high_res='1') != size(label_size='29', high_res='0')
    assert size(label_size='62') == size(label_size='62', high_res='1') != size()


def test_which_end_leaves_first_is_a_setting_of_each_printer(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, data={'printer': 'tcp://a', 'leaves_first': 'top'})
    assert (r.get_json()['success'], r.get_json()['settings']) == (True, {'leaves_first': 'top'})
    assert client.get(URL, query_string={'printer': 'tcp://b'}).get_json() == {}
    assert client.post(URL, data={'printer': 'tcp://a', 'leaves_first': 'left'}).status_code == 400
    assert client.post(URL, data={'printer': 'tcp://a', 'leaves_first': ''}).get_json()['settings'] == {}


def test_print_requests_that_leave_out_which_end_leaves_first_take_it_as_set(tmp_path, monkeypatch):
    # Of the request, else of the printer settings, never of a saved label
    import base64
    import io
    from PIL import Image
    from app.labeldesigner import printer
    ends = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: ends.append(self.leaves_first) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'leaves_first': 'top'})
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': text})
    client.post('/labeldesigner/api/print', data={'printer': 'simulation', 'text': text, 'leaves_first': 'bottom'})
    (tmp_path / 'label_repo' / 'Shelf.json').write_text(json.dumps({'name': 'Shelf.json', 'label_size': '62', 'text': json.loads(text), 'leaves_first': 'bottom'}))
    client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation'})
    client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': 'simulation', 'leaves_first': 'bottom'})
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'white').save(image, 'PNG')
    client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'printer': 'simulation',
                                                          'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    assert ends == ['top', 'bottom', 'top', 'bottom', 'top']


def test_a_usb_printer_is_kept_by_its_serial_also_with_spaces_in_its_device_id(monkeypatch):
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: 'MFG:Brother; MDL:QL-800; SN:000F1Z123;')
    assert printer_settings.key('/dev/usb/lp1') == 'usb:QL-800/000F1Z123'


def test_print_requests_that_leave_out_the_label_size_take_the_tape_the_printer_reported(tmp_path, monkeypatch):
    # A size set by hand counts only where the printer reports no tape: one
    # set for another roll makes the printer refuse the job
    import base64
    import io
    from PIL import Image
    from app.labeldesigner import printer
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: jobs.append(self.label_size) or '')
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    network = 'tcp://192.0.2.10'
    client.post(URL, data={'printer': network, 'label_size': '29'})
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    Image.new('RGB', (200, 100), 'white').save(image, 'PNG')
    image = base64.b64encode(image.getvalue()).decode('ascii')

    # Saved without a size of its own
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf', 'text': text}).status_code == 200

    def preview(**given):
        r = client.post('/labeldesigner/api/preview', data={'printer': network, 'text': text, **given})
        return Image.open(io.BytesIO(r.data)).size
    # 40 mm is no tape of a label size
    for reported, size in ((62, '62'), (40, '29'), (None, '29')):
        state = {'model': 'QL-800', 'media_width': reported, 'media_length': 0}
        monkeypatch.setattr('app.labeldesigner.routes.device_state', lambda device, model: dict(state))
        client.post('/labeldesigner/api/print', data={'printer': network, 'text': text})
        client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'printer': network, 'images': [image]})
        client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'printer': network})
        assert preview() == preview(label_size=size)
    assert jobs == ['62'] * 3 + ['29'] * 6


def test_the_tape_a_printer_reported_is_named_for_the_model_it_prints_with(tmp_path, monkeypatch):
    # Its status may be of another printer at the address, or tell no model
    from app.labeldesigner import printer
    jobs = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: jobs.append(self.label_size) or '')
    client = make_client(tmp_path, empty_repo=True, model='PT-P750W')
    client.application.config['PRINTER_PRINTER'] = 'tcp://192.0.2.7'
    assert client.post(URL, data={'label_size': 'pt12'}).status_code == 200
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}])
    for state, size in (({'model': 'Unknown', 'media_width': 24, 'media_length': 0}, 'pt24'),
                        ({'model': 'QL-800', 'media_width': 62, 'media_length': 0}, 'pt12'),
                        ({'model': 'QL-800', 'media_width': 18, 'media_length': 0}, 'pt18')):
        monkeypatch.setattr('app.labeldesigner.routes.device_state', lambda device, model: dict(state))
        assert client.post('/labeldesigner/api/print', data={'text': text}).status_code == 200
    assert jobs == ['pt24', 'pt12', 'pt18']


def test_how_a_printer_finds_its_labels_is_a_setting_of_printers_told_so(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer='tcp://a')
    for sensing in ('gap', 'mark', 'endless'):
        r = client.post(URL, data={'sensing': sensing})
        assert (r.status_code, r.get_json()['settings']) == (200, {'sensing': sensing})
    assert client.get(URL, query_string={'printer': 'tcp://b'}).get_json() == {}
    r = client.post(URL, data={'sensing': 'holes'})
    assert (r.status_code, r.get_json()['message']) == (400, printer_settings.SETTINGS_MESSAGE)
    assert client.post(URL, data={'sensing': ''}).get_json()['settings'] == {}


def test_a_printer_that_finds_its_labels_itself_is_not_told_how(tmp_path):
    from app.labeldesigner import refusals
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    r = client.post(URL, data={'printer': 'tcp://a', 'sensing': 'mark'})
    assert (r.status_code, r.get_json()['message']) == (400, printer_settings.NO_SENSING)
    assert printer_settings.NO_SENSING in refusals.MESSAGES
    assert client.get(URL, query_string={'printer': 'tcp://a'}).get_json() == {}


def test_how_a_printer_finds_its_labels_goes_to_the_job_not_to_the_label():
    # A print request does not name it, see PrinterQueue.sensing
    assert printer_settings.for_printing({'sensing': 'mark', 'cut': 'none'}, 'Zebra/ZPL', {}) == {'cut': 'none'}


def test_the_roll_is_a_setting_of_a_printer_of_two(tmp_path):
    from app.labeldesigner import refusals
    client = make_client(tmp_path, empty_repo=True, model='DYMO/LabelWriter-450-Twin-Turbo', printer='file:///dev/usb/lp_dymo')
    for side in ('right', 'left'):
        r = client.post(URL, data={'side': side})
        assert (r.status_code, r.get_json()['settings']) == (200, {'side': side})
    r = client.post(URL, data={'side': 'middle'})
    assert (r.status_code, r.get_json()['message']) == (400, printer_settings.SETTINGS_MESSAGE)
    assert client.post(URL, data={'side': ''}).get_json()['settings'] == {}
    # A printer of one roll refuses it
    r = client.post(URL, data={'printer': 'file:///dev/usb/lp_other', 'model': 'DYMO/LabelWriter-450', 'side': 'right'})
    assert (r.status_code, r.get_json()['message']) == (400, printer_settings.NO_SIDES)
    assert printer_settings.NO_SIDES in refusals.MESSAGES


def test_a_roll_kept_for_a_printer_of_two_is_not_another_ones():
    # E.g. written by hand, or kept for a Twin Turbo plugged in where another printer is now
    data = {'printers': {'/dev/usb/lp_dymo': {'side': 'right', 'cut': 'none'}, '/dev/usb/lp_hand': {'side': ['right']}}}
    assert printer_settings.of_printer(data, '/dev/usb/lp_dymo', 'DYMO/LabelWriter-450-Twin-Turbo') == {'side': 'right', 'cut': 'none'}
    assert printer_settings.of_printer(data, '/dev/usb/lp_dymo', 'DYMO/LabelWriter-450') == {'cut': 'none'}
    assert printer_settings.of_printer(data, '/dev/usb/lp_hand', 'DYMO/LabelWriter-450-Twin-Turbo') == {}
    # It goes to the job, not to the label
    assert printer_settings.for_printing({'side': 'right', 'cut': 'none'}, 'DYMO/LabelWriter-450-Twin-Turbo', {}) == {'cut': 'none'}


def test_a_demo_printer_of_another_model_forgets_the_roll(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    client.post(URL, data={'printer': 'simulation', 'demo_model': 'DYMO/LabelWriter-450-Twin-Turbo'})
    assert client.post(URL, data={'printer': 'simulation', 'side': 'right'}).status_code == 200
    r = client.post(URL, data={'printer': 'simulation', 'demo_model': 'DYMO/LabelWriter-450'})
    assert 'side' not in r.get_json()['settings']


def test_the_settings_of_every_printer_kept_by_its_device():
    data = {'printers': {'/dev/usb/lp0': {'side': 'right', 'cut': 'none'}, 'tcp://192.0.2.1:9100': {'side': ['right']},
                         'usb:LabelWriter 450 Twin Turbo/01010112345': {'side': 'left'}, 'simulation': {'demo_model': 'QL-800'},
                         'usb:QL-700/000L9Z378866': {'cut': 'last'}},
            'seen': {'usb://0x0922:0x0022/01010112345': 'usb:LabelWriter 450 Twin Turbo/01010112345', '/dev/usb/lp1': 7},
            'quality': 'fast'}
    # Without asking a printer: one of a serial number where it was seen last, a value written wrong by hand left out
    assert printer_settings.by_device(data) == {
        '/dev/usb/lp0': {'side': 'right', 'cut': 'none', 'quality': 'fast'}, 'tcp://192.0.2.1:9100': {'quality': 'fast'},
        'usb://0x0922:0x0022/01010112345': {'side': 'left', 'quality': 'fast'}}
