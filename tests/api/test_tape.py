"""The tape in each printer: the one it reports, else the label size set by
hand in the printer settings."""

import base64
import io
import json
import os
import re

import pytest
from PIL import Image

from app.labeldesigner import drivers, printer, printer_settings, tape
from app.labeldesigner.drivers import devices, usbip
from app.labeldesigner.label import LabelOrientation
from app.labeldesigner.printer import PrinterQueue
from app.labeldesigner.routes import _loaded_tape, _resolved_printer, _settings_of
from tests.helpers import EXAMPLE_FORMDATA, _red_square, make_client, outcome, pixels

STATUS = {'errors': [], 'path': 'simulation', 'model': 'QL-800', 'media_width': 29, 'media_length': 90}
SETTINGS = '/labeldesigner/api/settings'


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printers these tests name are known, as if the search found them
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://192.0.2.10', 'vc500w://192.0.2.20'})


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**STATUS, 'printers': [dict(STATUS)], 'selected': 'simulation'})
    # Its own label folder, else the client writes into labels/ of the working tree
    return make_client(tmp_path, empty_repo=True, model='QL-800')


def tape_of(client):
    return client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['tape']


def kept(client) -> dict:
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE)) as fh:
        return json.load(fh)['printers']


def loaded(client, device, model):
    with client.application.app_context():
        return _loaded_tape(device, model, _settings_of(device))


@pytest.fixture
def untold(client, monkeypatch):
    """The simulator, which does not tell its tape."""
    state = {**STATUS, 'media_width': None, 'media_length': 0}
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': 'simulation'})
    return client


def test_status_tells_each_printer_its_tape(client):
    t = tape_of(client)
    assert (t['id'], t['known'], t['manual']) == ('29x90', True, False)
    assert t['place']['62|standard']['label_size'] == '29x90'


def test_tape_set_by_hand_is_the_label_size_of_the_printer_settings(untold):
    r = untold.post(SETTINGS, data={'printer': 'simulation', 'label_size': '29'})
    assert r.status_code == 200 and r.get_json()['success'] is True
    assert (r.get_json()['tape']['id'], r.get_json()['tape']['manual']) == ('29', True)
    assert (tape_of(untold)['id'], tape_of(untold)['manual']) == ('29', True)
    assert kept(untold) == {'simulation': {'label_size': '29'}}
    t = untold.post(SETTINGS, data={'printer': 'simulation', 'label_size': ''}).get_json()['tape']
    assert (t['known'], t['manual']) == (False, False)
    assert (tape_of(untold)['known'], tape_of(untold)['manual']) == (False, False)


def test_settings_that_leave_the_label_size_alone_tell_the_tape_as_well(untold):
    untold.post(SETTINGS, data={'printer': 'simulation', 'label_size': '29'})
    t = untold.post(SETTINGS, json={'printer': 'simulation', 'cut': 'last'}).get_json()['tape']
    assert (t['id'], t['manual']) == ('29', True)


@pytest.mark.parametrize('content', ['not json', '[1]', '{"printers": [1]}', '{"printers": {"simulation": "62"}}',
                                     '{"printers": {"simulation": {"label_size": "nope"}}}',
                                     '{"printers": {"simulation": {"label_size": 62}}}',
                                     '{"printers": {"simulation": {"label_size": [1]}}}',
                                     '{"printers": {"simulation": {"label_size": {"a": 1}}}}'])
def test_broken_settings_file_counts_as_no_tape_set_by_hand(untold, content):
    path = os.path.join(untold.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE)
    with open(path, 'w') as fh:
        fh.write(content)
    assert (tape_of(untold)['known'], tape_of(untold)['manual']) == (False, False)
    assert not loaded(untold, 'simulation', 'QL-800').known
    r = untold.post(SETTINGS, data={'printer': 'simulation', 'cut': 'last'})
    if content in ('not json', '[1]', '{"printers": [1]}'):
        # Not replaced by a change, it holds no settings
        assert (r.status_code, r.get_json()['message']) == (500, printer_settings.BROKEN)
    else:
        assert r.status_code == 200 and not r.get_json()['tape']['known']


def test_loaded_tape_of_auto_detection_is_the_one_set_for_the_simulator(client):
    client.post(SETTINGS, data={'printer': 'simulation', 'label_size': '62'})
    t = loaded(client, '?', 'QL-800')
    assert (t.id, t.manual) == ('62', True)


def test_tape_set_for_auto_detection_lands_under_the_simulator(untold):
    r = untold.post(SETTINGS, data={'printer': '?', 'label_size': '62'})
    assert r.status_code == 200 and r.get_json()['tape']['id'] == '62'
    assert kept(untold) == {'simulation': {'label_size': '62'}}
    assert (tape_of(untold)['id'], tape_of(untold)['manual']) == ('62', True)


NETWORK = 'tcp://192.0.2.10'


@pytest.fixture
def network(client, monkeypatch):
    """A QL-800 on the network that holds 29x90 labels."""
    state = {**STATUS, 'path': NETWORK}
    asked = []

    def device_state(device, model):
        asked.append(device)
        return dict(state, path=device)
    monkeypatch.setattr('app.labeldesigner.routes.device_state', device_state)
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': NETWORK})
    client.application.config['PRINTER_PRINTER'] = NETWORK
    client.application.config['PRINTER_MODEL'] = 'QL-800'
    client.asked = asked
    return client


def test_tape_the_printer_reports_wins_over_one_set_by_hand(network):
    assert network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '62'}).status_code == 200
    t = loaded(network, NETWORK, 'QL-800')
    assert (t.id, t.known, t.manual) == ('29x90', True, False) and network.asked == [NETWORK]
    assert (tape_of(network)['id'], tape_of(network)['manual']) == ('29x90', False)


def test_settings_answer_tells_the_tape_without_asking_the_printer(network):
    # Not answered yet, the tape set by hand counts until it does
    t = network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '62'}).get_json()['tape']
    assert (t['id'], t['manual']) == ('62', True) and network.asked == []
    # Its last answer told the tape
    devices.status_cache[devices.lock_key(NETWORK)] = dict(STATUS, path=NETWORK)
    t = network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '62'}).get_json()['tape']
    assert (t['id'], t['manual']) == ('29x90', False) and network.asked == []
    assert t['place']['62|standard']['label_size'] == '29x90'


def test_tape_set_by_hand_is_used_where_the_printer_reports_none(network, monkeypatch):
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {**STATUS, 'path': device, 'media_width': None, 'media_length': 0})
    t = network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '12'}).get_json()['tape']
    assert (t['id'], t['manual']) == ('12', True)
    assert (loaded(network, NETWORK, 'QL-800').id, loaded(network, NETWORK, 'QL-800').manual) == ('12', True)
    # Removed, the label keeps its size
    network.post(SETTINGS, data={'printer': NETWORK, 'label_size': ''})
    assert not loaded(network, NETWORK, 'QL-800').known


def test_printer_that_does_not_tell_its_tape_is_not_asked(network):
    assert not loaded(network, 'file:///dev/usb/lp3', 'Epson/ESC-POS').known and network.asked == []


def test_last_known_tape_is_used_when_the_status_fails(network, monkeypatch, caplog):
    def fails(device, model):
        raise RuntimeError('No status')
    monkeypatch.setattr('app.labeldesigner.routes.device_state', fails)
    assert not loaded(network, NETWORK, 'QL-800').known
    devices.status_cache[devices.lock_key(NETWORK)] = dict(STATUS, path=NETWORK)
    # Asked again once a moment has passed
    devices._unreached[devices.lock_key(NETWORK)] -= usbip.UNREACHABLE_FOR
    assert loaded(network, NETWORK, 'QL-800').id == '29x90'
    warnings = [r for r in caplog.records if r.levelname == 'WARNING' and r.getMessage() == 'Printer status unavailable']
    assert len(warnings) == 2


@pytest.fixture
def silent(client, monkeypatch):
    """The QL-800 on the network does not answer."""
    from app.labeldesigner.drivers.brother import BrotherRaster

    def fails(self, device, model, unconfirmed=False):
        raise TimeoutError()
    monkeypatch.setattr(BrotherRaster, 'status', fails)
    client.application.config['PRINTER_PRINTER'] = NETWORK
    client.application.config['PRINTER_MODEL'] = 'QL-800'
    return client


def test_tape_is_the_last_known_one_when_the_printer_does_not_answer(silent):
    devices.status_cache[devices.lock_key(NETWORK)] = {
        'model': 'QL-800', 'media_width': 29, 'media_length': 90, 'media_type': 'Die-cut labels', 'red_roll': False}
    assert loaded(silent, NETWORK, 'QL-800').id == '29x90'


def test_tape_is_unknown_when_the_printer_never_answered(silent):
    assert not loaded(silent, NETWORK, 'QL-800').known


def hand_set(client, device, size, model):
    """A tape set by hand in the printer settings, for a printer of the model."""
    r = client.post(SETTINGS, data={'printer': device, 'model': model, 'label_size': size})
    assert r.status_code == 200


@pytest.mark.parametrize('state, manual, expected', [
    ({'path': NETWORK, 'model': 'QL-800', 'media_width': 29, 'media_length': 90}, None, '29x90'),
    # Set for the QL-800 that was at this address before
    ({'path': 'file:///dev/usb/lp0', 'model': 'PT-P750W', 'media_width': 12, 'media_length': 0}, ('62', 'QL-800'), 'pt12'),
    ({'path': 'file:///dev/usb/lp0', 'model': 'PT-P750W', 'media_width': None, 'media_length': 0}, ('62', 'QL-800'), None),
    # Its driver does not tell the paper, whatever the state holds
    ({'path': 'file:///dev/usb/lp3', 'model': 'Epson/ESC-POS', 'media_width': 58, 'media_length': 0}, None, None),
    ({'path': 'simulation', 'model': 'QL-800', 'media_width': None, 'media_length': 0}, ('62', 'QL-800'), '62'),
    ({'path': NETWORK, 'model': 'QL-800', 'media_width': 29, 'media_length': 90}, ('62', 'QL-800'), '29x90'),
    ({'path': NETWORK, 'model': 'QL-800', 'media_width': None, 'media_length': 0}, ('62', 'QL-800'), '62'),
    # A size no label size matches
    ({'path': NETWORK, 'model': 'QL-800', 'media_width': 40, 'media_length': 0}, ('62', 'QL-800'), '62'),
], ids=['reported', 'set for another model', 'set for another model, none reported', 'not told', 'simulator',
        'reported over set by hand', 'set by hand, none reported', 'set by hand, unknown reported'])
def test_status_tells_the_tape_a_label_prints_on(client, monkeypatch, state, manual, expected):
    state = {'errors': [], **state}
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': state['path']})
    monkeypatch.setattr('app.labeldesigner.routes.device_state', lambda device, model: dict(state))
    if manual:
        hand_set(client, state['path'], *manual)
    shown = tape_of(client)
    assert shown == tape.describe(loaded(client, state['path'], state['model']))
    assert (shown['id'] if shown['known'] else None) == expected


@pytest.fixture
def zpl(client, monkeypatch):
    """A ZPL printer on the network that measured its labels 50 mm long,
    which labels of two widths are."""
    state = {'errors': [], 'path': NETWORK, 'model': 'Zebra/ZPL', 'media_width': None, 'media_length': 50}
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': NETWORK})
    monkeypatch.setattr('app.labeldesigner.routes.device_state', lambda device, model: dict(state))
    client.application.config.update(PRINTER_PRINTER=NETWORK, PRINTER_MODEL='Zebra/ZPL')
    return client


def test_printer_that_measures_only_the_length_takes_the_labels_set_by_hand_of_it(zpl, monkeypatch):
    seen = queued(monkeypatch)
    # Set for the labels before, it counts as none: the label keeps its size
    hand_set(zpl, NETWORK, 'zpl102x51', 'Zebra/ZPL')
    t = tape_of(zpl)
    assert (t['known'], t['unknown'], t['candidates']) == (False, '50 mm long', ['zpl100x50', 'zpl80x50'])
    assert printed(zpl, 'zpl100x50').status_code == 200
    hand_set(zpl, NETWORK, 'zpl80x50', 'Zebra/ZPL')
    t = tape_of(zpl)
    assert (t['id'], t['known'], t['manual']) == ('zpl80x50', True, True)
    assert printed(zpl, 'zpl100x50').status_code == 200
    assert [s[0] for s in seen] == ['zpl100x50', 'zpl80x50']


SERIAL = 'MFG:Brother;MDL:QL-800;SN:000F1Z123;'


def test_tape_set_by_hand_for_a_usb_printer_is_found_by_its_serial(client, monkeypatch):
    device_id = SERIAL
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: device_id)
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {**STATUS, 'path': device, 'media_width': None, 'media_length': 0})
    hand_set(client, 'file:///dev/usb/lp0', '62', 'QL-800')
    assert kept(client) == {'usb:QL-800/000F1Z123': {'label_size': '62'}}
    # Plugged in after another printer, the same printer on another device
    for device in ('file:///dev/usb/lp0', '/dev/usb/lp1'):
        t = loaded(client, device, 'QL-800')
        assert (t.id, t.manual) == ('62', True), device
        state = {**STATUS, 'path': device, 'media_width': None, 'media_length': 0}
        monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                            lambda config, added=(): {**state, 'printers': [dict(state)], 'selected': device})
        assert (tape_of(client)['id'], tape_of(client)['manual']) == ('62', True), device
    # Switched off it tells no device ID, the printer last seen there
    device_id = ''
    assert loaded(client, 'file:///dev/usb/lp0', 'QL-800').id == '62'
    assert client.get(SETTINGS, query_string={'printer': 'file:///dev/usb/lp0'}).get_json() == {'label_size': '62'}


def test_status_tells_the_label_sizes_each_printer_takes(client, monkeypatch):
    printers = [dict(STATUS), {**STATUS, 'path': NETWORK, 'model': 'Unknown'}]
    monkeypatch.setattr('app.labeldesigner.routes.get_ptr_status',
                        lambda config, added=(): {**STATUS, 'printers': printers, 'selected': 'simulation'})
    ql, unknown = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    assert '62' in ql['takes'] and 'pt12' not in ql['takes']
    # A model no label size names takes every size that has printers, each
    # paper once: not as the size of a model shown as another one
    assert unknown['takes'] == [s for s in tape.SIZES if tape.TAKEN_BY[s] and not s.startswith(('zpl300-', 'tspl300-', 'dymo203-', 'ipp'))
                                and not s.endswith('-180')]


def test_label_designer_gets_the_sizes_taken_without_a_known_printer(client):
    html = client.get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'DESIGNER_CONFIG = (.*);</script>', html).group(1))
    assert config['takes'] == tape.takes(None)


def test_resolved_printer_is_the_model_its_address_tells(client):
    with client.application.app_context():
        assert _resolved_printer('vc500w://192.0.2.20', None) == ('vc500w://192.0.2.20', 'VC-500W')
        assert _resolved_printer(NETWORK, 'QL-1100') == (NETWORK, 'QL-1100')
        with pytest.raises(ValueError, match='Unsupported printer'):
            _resolved_printer('nowhere', None)


def test_printer_queue_takes_a_resolved_model_as_it_is(monkeypatch):
    def asks(device, model):
        raise AssertionError('resolved again')
    monkeypatch.setattr(drivers, 'resolve', asks)
    queue = PrinterQueue(model='QL-800', device_specifier=NETWORK, label_size='62', resolved=True)
    assert (queue.model, queue.driver) == ('QL-800', drivers.for_model('QL-800'))


def test_tape_is_documented():
    with open(os.path.join(os.path.dirname(__file__), '../../app/labeldesigner/openapi.json'), encoding='utf-8') as fh:
        spec = json.load(fh)
    state = spec['components']['schemas']['PrinterState']['properties']
    assert state['tape'] == {'$ref': '#/components/schemas/Tape'} and state['takes']['type'] == 'array'
    answer = spec['paths']['/labeldesigner/api/settings']['post']['responses']['200']['content']['application/json']['schema']
    assert answer['properties']['tape'] == {'$ref': '#/components/schemas/Tape'}


def queued(monkeypatch):
    """The labels a print queues, as (label size of the queue, orientation,
    width, height)."""
    seen = []

    def add(self, label, high_res=False, fast=False):
        seen.append((self.label_size, label.label_orientation, label._width, label._height))
    monkeypatch.setattr('app.labeldesigner.printer.PrinterQueue.add_label_to_queue', add)
    monkeypatch.setattr('app.labeldesigner.printer.PrinterQueue.process_queue', lambda self: '')
    return seen


def printed(client, label_size=None, **fields):
    """Print the example label, without a label size where none is given."""
    data = {key: value for key, value in EXAMPLE_FORMDATA.items() if key != 'label_size'}
    if label_size is not None:
        data['label_size'] = label_size
    return client.post('/labeldesigner/api/print', data={**data, **fields})


def webhook(client, label_size=None, **fields):
    """Print an image with the webhook, without a label size where none is
    given."""
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    buffer = io.BytesIO()
    _red_square().save(buffer, 'PNG')
    image = {'data': base64.b64encode(buffer.getvalue()).decode('ascii'), 'mime': 'image/png'}
    body = {'password': 'secret', 'images': [image], **fields}
    if label_size is not None:
        body['label_size'] = label_size
    return client.post('/labeldesigner/api/webhook/print', json=body)


def saved(client, label_size='62', name='shelf.json'):
    """Save the QR example label for the label size."""
    with open(os.path.join(os.path.dirname(__file__), '../../labels/QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    label.update(name=name, label_size=label_size)
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200


def test_print_places_the_label_on_the_loaded_tape(network, monkeypatch):
    seen = queued(monkeypatch)
    assert printed(network, '62').status_code == 200
    assert [s[0] for s in seen] == ['29x90']
    assert network.asked == [NETWORK]


def test_print_places_orientation_and_cable_flag_with_the_size(network, monkeypatch):
    seen = queued(monkeypatch)
    # A cable flag is refused on a die-cut label, so it has to be dropped with the size
    r = printed(network, '62', orientation='rotated', cable_flag='1', cable_diameter='5')
    assert r.status_code == 200
    size, orientation, width, height = seen[0]
    assert (size, orientation) == ('29x90', LabelOrientation.STANDARD) and width > height


def test_label_on_the_tape_stays_where_it_is(network, monkeypatch):
    seen = queued(monkeypatch)
    assert printed(network, '29x90', orientation='rotated').status_code == 200
    assert seen[0][:2] == ('29x90', LabelOrientation.ROTATED)


@pytest.mark.parametrize('send', [printed, webhook], ids=['print', 'webhook'])
def test_label_size_set_by_hand_gives_way_to_the_tape_the_printer_reports(network, monkeypatch, send):
    assert network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '62'}).status_code == 200
    seen = queued(monkeypatch)
    # Left out, it is the label size of the tape the printer reports
    assert send(network).status_code == 200
    assert send(network, '62').status_code == 200
    assert seen[0][0] == '29x90' and seen[0] == seen[1]


def preview_size(client, **fields) -> tuple:
    """The size of the preview of the example label, without a label size
    where fields give none."""
    data = {key: value for key, value in EXAMPLE_FORMDATA.items() if key != 'label_size'}
    r = client.post('/labeldesigner/api/preview', data={**data, 'printer': NETWORK, **fields})
    assert r.status_code == 200, r.get_json()
    return Image.open(io.BytesIO(r.data)).size


def saved_without_a_size(client, orientation, name='unsized.json'):
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]
    label = {'name': name, 'print_type': 'text', 'orientation': orientation, 'text': text}
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200


@pytest.mark.parametrize('left_out', [{}, {'label_size': ''}], ids=['left out', 'empty'])
def test_label_size_left_out_is_that_of_the_tape_the_printer_reports(network, monkeypatch, left_out):
    # Made for that tape as the request asks, there is nothing to place
    seen = queued(monkeypatch)
    assert printed(network, left_out.get('label_size'), orientation='rotated').status_code == 200
    assert seen[0][:2] == ('29x90', LabelOrientation.ROTATED)
    # 29 x 90 mm at 300 dpi, along the label
    assert preview_size(network, orientation='rotated', **left_out) == (306, 991)
    assert webhook(network, left_out.get('label_size')).status_code == 200
    assert seen[1][0] == '29x90'


def test_saved_label_without_a_size_is_made_for_the_tape_the_printer_reports(network, monkeypatch):
    seen = queued(monkeypatch)
    saved_without_a_size(network, 'rotated')
    r = network.get('/labeldesigner/api/repository/preview', query_string={'name': 'unsized.json'})
    assert r.status_code == 200 and Image.open(io.BytesIO(r.data)).size == (306, 991)
    assert network.post('/labeldesigner/api/repository/print', json={'name': 'unsized.json'}).status_code == 200
    assert seen[0][:2] == ('29x90', LabelOrientation.ROTATED)


def test_label_size_left_out_without_a_tape_known_is_the_default_one(client, monkeypatch):
    # The simulator reports no tape and none is set by hand for it
    client.application.config['LABEL_DEFAULT_SIZE'] = '29'
    seen = queued(monkeypatch)
    assert printed(client).status_code == 200
    assert webhook(client).status_code == 200
    assert [s[0] for s in seen] == ['29', '29']


@pytest.fixture
def endless(network, monkeypatch):
    """The QL-800 on the network holds 62 mm endless tape, 29x90 is still
    set by hand for the labels before."""
    def device_state(device, model):
        network.asked.append(device)
        return {**STATUS, 'path': device, 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr('app.labeldesigner.routes.device_state', device_state)
    assert network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '29x90'}).status_code == 200
    return network


def test_label_size_left_out_does_not_follow_one_set_by_hand_for_another_tape(endless, monkeypatch):
    seen = queued(monkeypatch)
    assert printed(endless, orientation='standard').status_code == 200
    assert webhook(endless, orientation='standard').status_code == 200
    saved_without_a_size(endless, 'standard')
    assert endless.post('/labeldesigner/api/repository/print', json={'name': 'unsized.json'}).status_code == 200
    assert [s[:2] for s in seen] == [('62', LabelOrientation.STANDARD)] * 3
    # Across the 62 mm tape, 696 dots at 300 dpi
    assert preview_size(endless, orientation='standard')[0] == 696


def test_saved_label_prints_on_the_loaded_tape(network, monkeypatch):
    seen = queued(monkeypatch)
    saved(network)
    assert network.post('/labeldesigner/api/repository/print', json={'name': 'shelf.json'}).status_code == 200
    assert seen and {s[0] for s in seen} == {'29x90'}


def test_demo_label_prints_on_the_loaded_tape(network, monkeypatch):
    seen = queued(monkeypatch)
    r = network.post('/labeldesigner/api/demos/print', json={'id': 'signs/001-fire-extinguisher-62.json'})
    assert outcome(network, r) == ('printed', '')
    assert seen[0][0] == '29x90'


def test_preview_of_a_saved_label_is_on_the_loaded_tape(network):
    saved(network)
    r = network.get('/labeldesigner/api/repository/preview', query_string={'name': 'shelf.json'})
    assert r.status_code == 200
    # 29 x 90 mm at 300 dpi
    assert sorted(Image.open(io.BytesIO(r.data)).size) == [306, 991]
    assert network.asked == [NETWORK]


def test_preview_of_a_saved_label_is_as_it_prints(endless, monkeypatch):
    # A label without a size is made for the 62 mm tape the printer reports,
    # one saved for 29x90 is placed on it
    network = endless
    jobs = []
    monkeypatch.setattr(PrinterQueue, 'add_label_to_queue',
                        lambda self, label, high_res=False, fast=False: jobs.append((self.label_size, label)))
    monkeypatch.setattr(PrinterQueue, 'process_queue', lambda self: '')
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]
    for name, size, orientation in (('u', {}, LabelOrientation.STANDARD), ('s', {'label_size': '29x90'}, LabelOrientation.ROTATED)):
        design = {'name': name, 'print_type': 'text', 'orientation': 'standard', 'text': text, **size}
        assert network.post('/labeldesigner/api/repository/save', json=design).status_code == 200
        preview = network.get('/labeldesigner/api/repository/preview', query_string={'name': f'{name}.json'})
        assert network.post('/labeldesigner/api/repository/print', json={'name': f'{name}.json'}).status_code == 200
        label_size, label = jobs[-1]
        assert (label_size, label.label_orientation) == ('62', orientation), name
        image = io.BytesIO()
        label.generate().save(image, 'PNG')
        assert pixels(preview.data) == pixels(image.getvalue()), name


def test_previews_of_the_label_designer_are_not_placed(network):
    # The label designer places its labels itself, from the status, and
    # shows its demo labels as they are made
    r = network.get('/labeldesigner/api/demos/preview', query_string={'id': 'signs/001-fire-extinguisher-62.json'})
    assert r.status_code == 200 and 696 in Image.open(io.BytesIO(r.data)).size
    r = network.post('/labeldesigner/api/preview', data={**EXAMPLE_FORMDATA, 'printer': NETWORK})
    assert r.status_code == 200 and 696 in Image.open(io.BytesIO(r.data)).size
    assert network.asked == []


def test_webhook_prints_on_the_loaded_tape(network, monkeypatch):
    seen = queued(monkeypatch)
    assert webhook(network, '62').status_code == 200
    size, orientation, width, height = seen[0]
    assert size == '29x90' and orientation == LabelOrientation.STANDARD and (width, height) == (991, 306)


def test_unknown_label_size_is_still_refused(network, monkeypatch):
    seen = queued(monkeypatch)
    assert printed(network, 'foo').status_code == 400
    assert webhook(network, 'foo').status_code == 400
    assert not seen


def test_label_for_auto_detection_prints_on_the_tape_set_for_the_simulator(network, monkeypatch):
    # '?' prints on the simulator, which reports no tape, whatever printer is selected
    network.application.config['PRINTER_PRINTER'] = '?'
    seen = queued(monkeypatch)
    assert printed(network, '62').status_code == 200
    assert network.post(SETTINGS, data={'label_size': '29x90'}).status_code == 200
    assert printed(network, '62').status_code == 200
    assert printed(network, '62', printer='simulation').status_code == 200
    assert [s[0] for s in seen] == ['62', '29x90', '29x90'] and network.asked == []


def test_label_prints_on_the_tape_set_by_hand_where_the_printer_reports_none(network, monkeypatch):
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {**STATUS, 'path': device, 'media_width': None, 'media_length': 0})
    assert network.post(SETTINGS, data={'printer': NETWORK, 'label_size': '12'}).status_code == 200
    seen = queued(monkeypatch)
    assert printed(network, '62').status_code == 200
    assert [s[0] for s in seen] == ['12']


def test_tape_the_model_cannot_print_leaves_the_label_as_it_is(network, monkeypatch):
    # A QL-500 has no red tape, the status is that of a QL-800 with a red roll
    seen = queued(monkeypatch)
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {**STATUS, 'path': device, 'media_width': 62, 'media_length': 0, 'red_roll': True})
    assert printed(network, '62', model='QL-500').status_code == 200
    assert [s[0] for s in seen] == ['62']


def test_label_keeps_its_size_when_the_status_fails(network, monkeypatch, caplog):
    def fails(device, model):
        raise RuntimeError('No status')
    monkeypatch.setattr('app.labeldesigner.routes.device_state', fails)
    seen = queued(monkeypatch)
    assert printed(network, '62').status_code == 200
    assert [s[0] for s in seen] == ['62']
    assert any(r.levelname == 'WARNING' and r.getMessage() == 'Printer status unavailable' for r in caplog.records)


def test_label_goes_on_the_last_known_tape_when_the_printer_does_not_answer(silent, monkeypatch):
    devices.status_cache[devices.lock_key(NETWORK)] = {
        'model': 'QL-800', 'media_width': 29, 'media_length': 90, 'media_type': 'Die-cut labels', 'red_roll': False}
    seen = queued(monkeypatch)
    assert printed(silent, '62').status_code == 200
    assert [s[0] for s in seen] == ['29x90']


def test_label_keeps_its_size_when_the_printer_never_answered(silent, monkeypatch):
    seen = queued(monkeypatch)
    assert printed(silent, '62').status_code == 200
    assert [s[0] for s in seen] == ['62']


def test_printer_that_did_not_answer_is_asked_for_its_tape_again_after_a_moment(client, monkeypatch):
    # Switched off: each job tries it, only the first print waits for its status
    from app.labeldesigner.drivers.brother import BrotherRaster
    asked = []

    def fails(self, device, model, unconfirmed=False):
        asked.append(device)
        raise TimeoutError()
    monkeypatch.setattr(BrotherRaster, 'status', fails)
    client.application.config.update(PRINTER_PRINTER=NETWORK, PRINTER_MODEL='QL-800')
    seen = queued(monkeypatch)
    assert printed(client, '62').status_code == 200
    assert printed(client, '62').status_code == 200
    assert (len(asked), len(seen)) == (1, 2)
    devices._unreached[devices.lock_key(NETWORK)] -= usbip.UNREACHABLE_FOR
    assert printed(client, '62').status_code == 200
    assert (len(asked), len(seen)) == (2, 3)


def test_label_prints_on_the_tape_set_for_a_usb_printer_by_its_serial(client, monkeypatch):
    monkeypatch.setattr(printer_settings, 'usb_device_id', lambda device, connect=True: SERIAL)
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {**STATUS, 'path': device, 'media_width': None, 'media_length': 0})
    client.application.config['PRINTER_PRINTER'] = 'file:///dev/usb/lp0'
    hand_set(client, 'file:///dev/usb/lp0', '12', 'QL-800')
    seen = queued(monkeypatch)
    # Plugged in after another printer, the same printer on another device
    assert printed(client, '62', printer='file:///dev/usb/lp1').status_code == 200
    assert [s[0] for s in seen] == ['12']


VC500W = 'vc500w://192.0.2.20'


def test_label_size_set_by_hand_is_checked_against_the_model_the_prints_use(client):
    # A printer known by its address is that model, whatever model is configured
    client.application.config['PRINTER_PRINTER'] = VC500W
    client.application.config['PRINTER_MODEL'] = 'QL-800'
    r = client.post(SETTINGS, data={'label_size': 'vc25'})
    assert r.status_code == 200 and r.get_json()['settings'] == {'label_size': 'vc25'}
    assert client.post(SETTINGS, data={'label_size': '62'}).status_code == 400


@pytest.fixture
def vc500w(client, monkeypatch):
    """A VC-500W known by its address while a QL-800 is configured, which
    does not report its tape, with the tape set by hand."""
    monkeypatch.setattr('app.labeldesigner.routes.device_state',
                        lambda device, model: {'errors': [], 'path': device, 'model': model, 'media_width': None})
    client.application.config['PRINTER_PRINTER'] = VC500W
    client.application.config['PRINTER_MODEL'] = 'QL-800'
    assert client.post(SETTINGS, data={'label_size': 'vc25'}).status_code == 200
    return client


@pytest.mark.parametrize('send', [printed, webhook], ids=['print', 'webhook'])
def test_label_prints_on_the_tape_of_a_printer_known_by_its_address(vc500w, monkeypatch, send):
    seen = queued(monkeypatch)
    assert send(vc500w, '62').status_code == 200
    assert [s[0] for s in seen] == ['vc25']


SHARED = 'usbip://192.0.2.1'


@pytest.fixture
def unreachable(client, monkeypatch):
    """A QL-800 shared over USB/IP that cannot be reached, with the
    connections tried and the status queries."""
    tried, asked = [], []

    def connect(address):
        tried.append(address)
        raise ConnectionRefusedError()

    def device_state(device, model):
        asked.append(device)
        return printer.device_state(device, model)
    monkeypatch.setattr(usbip, 'connect', connect)
    monkeypatch.setattr('app.labeldesigner.routes.device_state', device_state)
    client.application.config.update(PRINTER_PRINTER=SHARED, PRINTER_MODEL='QL-800')
    client.tried, client.asked = tried, asked
    return client


def test_preview_does_not_ask_the_printer(unreachable):
    # The label designer sends the label size, the quality and the model
    data = {**EXAMPLE_FORMDATA, 'printer': SHARED, 'model': 'QL-800', 'high_res': '0'}
    r = unreachable.post('/labeldesigner/api/preview', data=data)
    assert r.status_code == 200 and unreachable.tried == [] and unreachable.asked == []


def seen_by_its_serial(client):
    """Settings kept for the printer by the serial number it told before, as
    the printer settings keep those of a USB printer."""
    printer = 'usb:QL-800/000F1Z123'
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w') as fh:
        json.dump({'printers': {printer: {'label_size': '62'}}, 'seen': {devices.lock_key(SHARED): printer}}, fh)


SETTINGS_KEPT = pytest.mark.parametrize('kept_before', [None, seen_by_its_serial], ids=['no settings', 'seen by its serial'])


@SETTINGS_KEPT
def test_preview_without_a_label_size_resolves_the_printer_once(unreachable, kept_before):
    if kept_before:
        kept_before(unreachable)
    data = {key: value for key, value in EXAMPLE_FORMDATA.items() if key != 'label_size'}
    r = unreachable.post('/labeldesigner/api/preview', data={**data, 'printer': SHARED})
    # The device ID once; not reached, it is not asked for its tape either
    assert r.status_code == 200 and unreachable.asked == [] and len(unreachable.tried) == 1


PRINTS = pytest.mark.parametrize('send', [
    lambda client: printed(client, '62'),
    lambda client: client.post('/labeldesigner/api/demos/print', json={'id': 'signs/001-fire-extinguisher-62.json'}),
    lambda client: webhook(client, '62'),
], ids=['print', 'demo', 'webhook'])


@PRINTS
def test_print_asks_the_printer_for_its_tape_once(network, send, monkeypatch):
    queued(monkeypatch)
    assert send(network).status_code == 200 and network.asked == [NETWORK]


@SETTINGS_KEPT
@PRINTS
def test_printer_that_cannot_be_reached_is_tried_for_its_model_and_the_job(unreachable, send, kept_before):
    if kept_before:
        kept_before(unreachable)
    send(unreachable)
    # Not reached a moment ago, it is not asked for its tape, the job tries it
    assert unreachable.asked == [] and len(unreachable.tried) == 2


@pytest.mark.parametrize('printer', ['network', 'unreachable'])
def test_webhook_without_images_does_not_ask_the_printer(request, printer):
    client = request.getfixturevalue(printer)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    r = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret'})
    assert r.get_json()['message'] == 'No images provided'
    assert client.asked == [] and getattr(client, 'tried', []) == []


def test_print_for_a_printer_of_unknown_model_asks_it_once(client, monkeypatch):
    # Asked for its model, the printer tells its tape as well
    from app.labeldesigner.drivers.brother import BrotherRaster
    asked = []

    def status(self, device, model, unconfirmed=False):
        asked.append(device)
        return dict(STATUS, path=device)
    monkeypatch.setattr(BrotherRaster, 'status', status)
    seen = queued(monkeypatch)
    assert printed(client, '62', printer=NETWORK, model='Unknown').status_code == 200
    assert asked == [NETWORK] and [s[0] for s in seen] == ['29x90']
