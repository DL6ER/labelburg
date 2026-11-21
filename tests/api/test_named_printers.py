"""Network printers a request names by their address that the server was not
told about: neither configured, added in the Settings nor found by the
search. ADD_PRINTERS decides where they may be, as for adding printers;
configured, found and added ones are not limited, nor printers on USB or
Bluetooth."""
import io
import json
import os

import pytest
from flask import has_app_context, has_request_context

from app.labeldesigner import adding, printer, printer_settings, routes
from app.labeldesigner.drivers import BROTHER, reach
from tests.api.test_mcp import tool
from tests.helpers import _red_square, make_client, outcome
from tests.printing.test_reach import names  # noqa: F401

API = '/labeldesigner/api'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Shelf', 'size': '40'}])
JOB = {'label_size': '62', 'print_type': 'text', 'text': TEXT, 'model': 'QL-800'}


def state(path, model='QL-800'):
    return {'errors': [], 'path': path, 'model': model, 'name': '', 'media_width': 62, 'media_length': 0,
            'media_type': 'Continuous length tape', 'status_type': 'Reply to status request', 'phase_type': 'Waiting to receive'}


@pytest.fixture(autouse=True)
def nothing_watched(monkeypatch):
    monkeypatch.setattr(reach, '_watched', {})


@pytest.fixture
def client(tmp_path, monkeypatch):
    client = make_client(tmp_path, empty_repo=True)
    # Printers are not asked for their tape here
    monkeypatch.setattr(routes, 'device_state', lambda device, model: state(device, model))
    return client


@pytest.fixture
def sent(monkeypatch):
    """The devices labels went to, none is sent."""
    devices = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: devices.append(self.device_specifier) or '')
    return devices


def message(answer) -> str | None:
    if answer.status_code < 400:
        return None
    told = answer.get_json(silent=True) or {}
    return told.get('message') or told.get('error')


def print_on(client, device, **values):
    return client.post(f'{API}/print', data={**JOB, 'printer': device, **values})


@pytest.mark.parametrize('device', ['tcp://8.8.8.8', 'tcp://printer.test', 'tcp://8.8.8.8:9100', 'vc500w://8.8.8.8',
                                    'tcp://[2001:4860:4860::8888]'])
def test_addresses_outside_the_local_network_are_refused(client, sent, names, device):  # noqa: F811
    names['printer.test'] = ['192.168.1.60', '8.8.8.8']
    for answer in (print_on(client, device), print_on(client, device, model=''),
                   client.post(f'{API}/preview', data={**JOB, 'printer': device}),
                   client.post(f'{API}/settings', data={'printer': device, 'cut': 'last'}),
                   client.get(f'{API}/settings', query_string={'printer': device})):
        assert (answer.status_code, message(answer)) == (400, adding.NAMED_OUTSIDE)
    assert sent == []


@pytest.mark.parametrize('mode', ['local', 'any'])
def test_addresses_in_the_local_network_are_taken(client, sent, names, mode):  # noqa: F811
    client.application.config['ADD_PRINTERS'] = mode
    names['printer.test'] = ['192.168.1.60', 'fd00::60']
    devices = ['tcp://192.168.1.60', 'tcp://printer.test', 'tcp://[fd00::60]', 'tcp://10.0.0.5:9100', 'tcp://100.64.1.2']
    for device in devices:
        assert outcome(client, print_on(client, device)) == ('printed', ''), device
    assert sent == devices


def test_any_takes_addresses_outside(client, sent):
    client.application.config['ADD_PRINTERS'] = 'any'
    assert outcome(client, print_on(client, 'tcp://8.8.8.8')) == ('printed', '')
    assert sent == ['tcp://8.8.8.8']


@pytest.mark.parametrize('mode', ['local', 'any', 'off'])
@pytest.mark.parametrize('device', ['tcp://169.254.169.254', 'vc500w://169.254.1.2', 'tcp://[fe80::1]', 'tcp://100.100.100.200',
                                    'tcp://192.0.2.10', 'tcp://0.0.0.0', 'tcp://224.0.0.251'])
def test_addresses_of_no_printer_are_refused_in_every_mode(client, sent, mode, device):
    client.application.config['ADD_PRINTERS'] = mode
    answer = print_on(client, device)
    assert (answer.status_code, message(answer)) == (400, adding.NAMED_OFF if mode == 'off' else adding.NAMED_NEVER)
    assert sent == []


def test_a_name_that_is_not_found_is_refused(client, sent, names):  # noqa: F811
    names['nowhere.test'] = []
    answer = print_on(client, 'tcp://nowhere.test')
    assert (answer.status_code, message(answer)) == (400, adding.UNKNOWN_NAME)


def test_off_takes_configured_and_found_printers_only(client, sent, monkeypatch):
    client.application.config.update(ADD_PRINTERS='off', PRINTER_PRINTER='?')
    found = 'tcp://192.168.1.61'
    monkeypatch.setattr(printer, '_cached_printers', [state(found, 'QL-810W')])
    for device in ('tcp://192.168.1.60', 'vc500w://192.168.1.60', 'tcp://127.0.0.1'):
        answer = print_on(client, device)
        assert (answer.status_code, message(answer)) == (400, adding.NAMED_OFF), device
    assert outcome(client, print_on(client, found)) == ('printed', '')
    # Another spelling of the printer found is a printer of its own
    for device in ('tcp://192.168.1.61:9100', 'tcp://192.168.1.61.'):
        assert message(print_on(client, device)) == adding.NAMED_OFF, device
    # Printers on USB, on Bluetooth and the simulator as before
    for device in ('simulation', '/dev/usb/lp0', 'usb://0x04f9:0x2042', 'bt://00:11:22:33:44:55'):
        assert outcome(client, print_on(client, device)) == ('printed', ''), device
    client.application.config['PRINTERS'] = [('QL-800', 'tcp://192.168.1.62', 'Office')]
    assert outcome(client, print_on(client, 'Office')) == ('printed', '')
    assert outcome(client, print_on(client, 'tcp://192.168.1.62')) == ('printed', '')
    assert sent == [found, 'simulation', '/dev/usb/lp0', 'usb://0x04f9:0x2042', 'bt://00:11:22:33:44:55',
                    'tcp://192.168.1.62', 'tcp://192.168.1.62']


def test_configured_printers_are_not_limited(client, sent):
    client.application.config['PRINTERS'] = [('QL-800', 'tcp://8.8.8.8', 'Far'), ('QL-800', 'tcp://169.254.1.9', 'Near')]
    for device in ('Far', 'tcp://8.8.8.8', 'Near', 'tcp://169.254.1.9'):
        assert outcome(client, print_on(client, device)) == ('printed', ''), device
    client.application.config.update(PRINTERS=[], PRINTER_PRINTER='tcp://8.8.8.8', ADD_PRINTERS='off')
    assert outcome(client, print_on(client, 'tcp://8.8.8.8')) == ('printed', '')
    assert outcome(client, client.post(f'{API}/print', data=JOB)) == ('printed', '')
    assert sent == ['tcp://8.8.8.8', 'tcp://8.8.8.8', 'tcp://169.254.1.9', 'tcp://169.254.1.9', 'tcp://8.8.8.8', 'tcp://8.8.8.8']


def test_other_spellings_of_an_added_printer_are_checked(client, sent, names):  # noqa: F811
    names['printer.test'] = ['192.168.1.70']
    names['printer.test.'] = ['8.8.8.8']
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w') as fh:
        json.dump({'printers': {}, 'added': [{'address': 'tcp://printer.test', 'model': 'QL-800', 'name': 'Shed'}]}, fh)
    assert outcome(client, print_on(client, 'Shed')) == ('printed', '')
    assert message(print_on(client, 'tcp://printer.test.')) == adding.NAMED_OUTSIDE
    assert sent == ['tcp://printer.test']


def test_each_connection_to_a_named_printer_is_checked(tmp_path, names, monkeypatch):  # noqa: F811
    # The name leads elsewhere once it is asked again, e.g. a rebinding attack
    answers = iter([['192.168.1.60']])
    names['printer.test'] = lambda: next(answers, ['8.8.8.8'])
    client = make_client(tmp_path, empty_repo=True)
    monkeypatch.setattr(routes, 'device_state', lambda device, model: state(device, model))
    answer = print_on(client, 'tcp://printer.test')
    # Also once the request is answered, when its job prints
    told_state, told = outcome(client, answer)
    assert answer.status_code == 200 and told_state == 'failed' and told.endswith(reach.NOT_ALLOWED)
    # Only in that request
    assert reach._checking.get() is None


def test_a_job_prints_with_the_app_and_the_checks_of_its_request_only(client, monkeypatch):
    client.application.config['ADD_PRINTERS'] = 'local'
    seen = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: seen.append((reach.checking_mode(), has_request_context(), has_app_context())) or '')
    assert outcome(client, print_on(client, 'tcp://192.168.1.60')) == ('printed', '')
    assert outcome(client, print_on(client, 'simulation')) == ('printed', '')
    # The request is not kept for it
    assert seen == [('local', False, True), (None, False, True)]


def test_saved_and_ready_made_labels_name_printers_alike(client, sent):
    answer = client.post(f'{API}/demos/print', json={'id': 'storage/013-homebox-62.json', 'printer': 'tcp://8.8.8.8'})
    assert (answer.status_code, message(answer)) == (400, adding.NAMED_OUTSIDE)
    assert outcome(client, client.post(f'{API}/demos/print', json={'id': 'storage/013-homebox-62.json', 'printer': 'tcp://192.168.1.60'})) == ('printed', '')
    assert sent == ['tcp://192.168.1.60']


def test_homebox_prints_on_the_printer_of_the_status_also_when_off(client, sent, monkeypatch):
    client.application.config['ADD_PRINTERS'] = 'off'
    shown = state('tcp://192.168.1.61', 'QL-810W')
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**shown, 'printers': [shown], 'selected': shown['path']})
    image = client.get(f'{API}/homebox').data
    answer = client.post(f'{API}/homebox', data=image, headers={'Content-Type': 'image/png'})
    assert outcome(client, answer) == ('printed', '')
    assert sent == ['tcp://192.168.1.61']


def test_mcp_prints_on_a_printer_found_also_when_off(tmp_path, monkeypatch):
    # Rendered as for printing, the image is told
    sent = []
    monkeypatch.setattr(type(BROTHER), 'send', lambda self, queue, job: sent.append(queue.device_specifier) or '')
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['ADD_PRINTERS'] = 'off'
    found = state('tcp://192.168.1.61', 'QL-810W')
    monkeypatch.setattr(printer, '_cached_printers', [found])
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**found, 'printers': [found], 'selected': found['path']})
    monkeypatch.setattr(routes, 'device_state', lambda device, model: state(device, model))
    result = tool(client, 'print_text', printer='QL-810W', label_size='62', count=1, lines=['x'])
    assert result['content'][0]['text'].splitlines()[0].startswith('Added 1 label')
    assert sent == ['tcp://192.168.1.61']


def test_the_webhook_tells_why_a_printer_is_refused(client, sent):
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    _red_square().save(image, 'PNG')
    answer = client.post(f'{API}/webhook/print', headers={'Authorization': 'Bearer secret'},
                         data={'printer': 'tcp://8.8.8.8', 'label_size': '62', 'images': (io.BytesIO(image.getvalue()), 'label.png')})
    assert (answer.status_code, message(answer)) == (400, adding.NAMED_OUTSIDE)
    assert sent == []


def test_the_webhook_does_not_tell_back_what_it_got(client, sent):
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    _red_square().save(image, 'PNG')
    answer = client.post(f'{API}/webhook/print', headers={'Authorization': 'Bearer secret'},
                         data={'label_size': '<script>62', 'images': (io.BytesIO(image.getvalue()), 'label.png')})
    assert (answer.status_code, message(answer)) == (400, 'Unknown label_size')
