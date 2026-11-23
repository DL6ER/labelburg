"""Printers added in the Settings: asked what they are first, kept on the
server, listed and printed on like configured ones, as ADD_PRINTERS
allows."""
import json
import logging
import os
import socket
import threading
import time

import pytest

from app import create_app
from app.labeldesigner import adding, printer, printer_settings, refusals
from config import Config
from app.labeldesigner.drivers import devices, reach, usbip
from tests.helpers import make_client, outcome, within
from tests.printing.test_reach import names  # noqa: F401
from tests.printing.test_usbip import TEXT, FakeQL
from tests.printing.usbip_host import QL1100_ID, FakeHost, SharedDevice
from tests.printing.test_adding import LASER, let_go

API = '/labeldesigner/api'
JOB = {'label_size': '62', 'print_type': 'text', 'text': TEXT}


@pytest.fixture(autouse=True)
def nothing_watched(monkeypatch):
    monkeypatch.setattr(reach, '_watched', {})


@pytest.fixture
def host():
    shared = FakeHost(SharedDevice(device_id=QL1100_ID + 'SN:A1Z123456;'))
    shared.ql = FakeQL(shared.devices[0])
    yield shared
    shared.close()


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, empty_repo=True)


def kept(client) -> dict:
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), encoding='utf-8') as fh:
        return json.load(fh)


def listed(client) -> list[dict]:
    return client.get(f'{API}/printer_status').get_json()['printers']


def add(client, address, name=''):
    return client.post(f'{API}/printers/add', json={'address': address, 'name': name})


def test_identify_tells_what_answers_and_keeps_nothing(client, host):
    answer = client.post(f'{API}/printers/identify', data={'address': host.address()})
    assert answer.status_code == 200
    found = answer.get_json()['printer']
    assert (found['address'], found['kind'], found['model'], found['listed']) == (host.address(), 'usbip', 'QL-1100', None)
    assert found['device_id']['MDL'] == 'QL-1100' and 'SN' not in found['device_id']
    assert (found['tape']['id'], found['errors']) == ('62', [])
    assert client.get(f'{API}/printers/added').get_json() == {
        'mode': 'local', 'allows': adding.ALLOWS['local'], 'printers': []}
    assert [p['path'] for p in listed(client)] == ['simulation']


def test_added_printer_is_kept_and_listed(client, host, tmp_path):
    answer = add(client, host.address(), 'Garage')
    assert answer.get_json() == {'success': True, 'printer': {'address': host.address(), 'model': 'QL-1100', 'name': 'Garage'}}
    assert kept(client)['added'] == [{'address': host.address(), 'model': 'QL-1100', 'name': 'Garage'}]
    assert client.get(f'{API}/printers/added').get_json()['printers'] == [
        {'address': host.address(), 'model': 'QL-1100', 'name': 'Garage', 'used': True}]
    shown = next(p for p in listed(client) if p['path'] == host.address())
    assert (shown['name'], shown['model'], shown['added'], shown['media_width'], shown['errors']) == ('Garage', 'QL-1100', True, 62, [])
    # Kept for the next start of the server
    again = create_app()
    again.config['LABEL_REPOSITORY_DIR'] = client.application.config['LABEL_REPOSITORY_DIR']
    assert any(p['path'] == host.address() for p in again.test_client().get(f'{API}/printer_status').get_json()['printers'])


def test_requests_print_on_an_added_printer(client, host):
    assert 'Unsupported printer' in client.post(f'{API}/print', data={**JOB, 'printer': host.address()}).get_json()['message']
    add(client, host.address(), 'Garage')
    for chosen in ('Garage', host.address()):
        before = len(host.ql.received)
        assert outcome(client, client.post(f'{API}/print', data={**JOB, 'printer': chosen})) == ('printed', '')
        assert host.ql.received.endswith(b'\x1a') and len(host.ql.received) > before + 1000
    # Its settings are kept by the same name
    answer = client.post(f'{API}/settings', data={'printer': 'Garage', 'cut': 'last'})
    assert answer.get_json()['settings'] == {'cut': 'last'}


def test_added_printer_is_asked_on_its_own(client, host):
    # As the label designer asks a printer it follows
    assert client.get(f'{API}/printer_status', query_string={'printer': host.address()}).status_code == 404
    add(client, host.address(), 'Garage')
    shown = client.get(f'{API}/printer_status', query_string={'printer': host.address()}).get_json()['printers']
    assert [(p['path'], p['name'], p['model'], p['added'], p['follow']) for p in shown] == [(host.address(), 'Garage', 'QL-1100', True, True)]


def test_added_printer_without_a_name(client, host):
    assert add(client, host.address()).get_json()['printer']['name'] == ''
    assert next(p for p in listed(client) if p['path'] == host.address())['name'] == ''


def test_added_printer_is_removed(client, host):
    add(client, host.address(), 'Garage')
    assert client.post(f'{API}/printers/remove', data={'printer': 'garage'}).get_json() == {'success': True}
    assert kept(client)['added'] == []
    assert [p['path'] for p in listed(client)] == ['simulation']
    assert 'Unsupported printer' in client.post(f'{API}/print', data={**JOB, 'printer': 'Garage'}).get_json()['message']
    # Let go
    assert not usbip.connected(host.address())
    again = client.post(f'{API}/printers/remove', data={'printer': host.address()})
    assert (again.status_code, again.get_json()['message']) == (400, adding.NOT_ADDED)


def test_configured_printers_are_not_removed(client):
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    for wanted in ('Kitchen', 'simulation'):
        answer = client.post(f'{API}/printers/remove', data={'printer': wanted})
        assert (answer.status_code, answer.get_json()['message']) == (400, adding.NOT_ADDED)


def test_added_printers_follow_the_configured_ones(client, host):
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    add(client, host.address(), 'Garage')
    shown = listed(client)
    assert [(p['name'], p.get('added', False)) for p in shown] == [('Kitchen', False), ('Garage', True)]
    client.application.config.update(PRINTERS=[], PRINTER_PRINTER='simulation')
    assert [p['path'] for p in listed(client)] == ['simulation', host.address()]


@pytest.mark.parametrize('name, refusal', [
    ('-Garage', adding.NAME_REFUSED),
    ('a' * 41, adding.NAME_REFUSED),
    ('Simulation', adding.NAME_REFUSED),
    ('kitchen', adding.NAME_TAKEN),
])
def test_names_refused(client, host, name, refusal):
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    answer = add(client, host.address(), name)
    assert (answer.status_code, answer.get_json()['message']) == (400, refusal)
    assert host.devices[0].imports == 0


def test_printer_listed_already(client, host):
    add(client, host.address(), 'Garage')
    # Also when spelled another way
    answer = add(client, host.address() + '/', 'Shed')
    assert (answer.status_code, answer.get_json()['message']) == (400, adding.ALREADY)
    found = client.post(f'{API}/printers/identify', data={'address': host.address()}).get_json()['printer']
    assert found['listed'] == 'Garage'
    client.application.config['PRINTERS'] = [('QL-1100', host.address(), 'Office')]
    os.remove(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    answer = add(client, host.address(), 'Garage')
    assert (answer.status_code, answer.get_json()['message']) == (400, adding.ALREADY)


def test_refused_addresses_are_told_plainly(client, names):  # noqa: F811
    names['printer.test'] = ['8.8.8.8']
    for path in ('identify', 'add'):
        answer = client.post(f'{API}/printers/{path}', data={'address': 'usbip://printer.test'})
        assert (answer.status_code, answer.get_json()) == (400, {'success': False, 'message': adding.ONLY_LOCAL})
    answer = client.post(f'{API}/printers/identify', data={'address': 'http://192.168.1.60'})
    assert answer.get_json()['message'] == adding.NOT_AN_ADDRESS


def test_any_adds_addresses_outside(client, names, host):  # noqa: F811
    client.application.config['ADD_PRINTERS'] = 'any'
    names['printer.test'] = ['127.0.0.1', '8.8.8.8']
    assert add(client, f'usbip://printer.test:{host.port}', 'Away').get_json()['success']
    assert client.get(f'{API}/printers/added').get_json()['allows'] == adding.ALLOWS['any']


def test_each_connection_to_an_added_printer_is_checked(client, names, host):  # noqa: F811
    names['printer.test'] = ['127.0.0.1']
    address = f'usbip://printer.test:{host.port}'
    add(client, address, 'Garage')
    assert outcome(client, client.post(f'{API}/print', data={**JOB, 'printer': 'Garage'})) == ('printed', '')
    # The name now leads outside the local network, e.g. a rebinding attack
    names['printer.test'] = ['8.8.8.8']
    usbip.disconnect(address)
    seen = len(host.connections)
    shown = next(p for p in listed(client) if p['path'] == address)
    assert shown['errors'] == [reach.NOT_ALLOWED]
    told_state, told = outcome(client, client.post(f'{API}/print', data={**JOB, 'printer': 'Garage'}))
    assert told_state == 'failed' and told.endswith(reach.NOT_ALLOWED)
    assert len(host.connections) == seen


def test_off_uses_no_added_printer(client, host):
    add(client, host.address(), 'Garage')
    usbip.disconnect(host.address())
    printer._last_states.clear()
    seen = len(host.connections)
    client.application.config['ADD_PRINTERS'] = 'off'
    for path in ('identify', 'add'):
        answer = client.post(f'{API}/printers/{path}', data={'address': host.address()})
        assert answer.get_json() == {'success': False, 'message': adding.OFF}
    assert client.get(f'{API}/printers/added').get_json() == {
        'mode': 'off', 'allows': adding.ALLOWS['off'],
        'printers': [{'address': host.address(), 'model': 'QL-1100', 'name': 'Garage', 'used': False}]}
    assert [p['path'] for p in listed(client)] == ['simulation']
    assert 'Unsupported printer' in client.post(f'{API}/print', data={**JOB, 'printer': 'Garage'}).get_json()['message']
    time.sleep(0.1)
    assert len(host.connections) == seen
    # Still removed
    assert client.post(f'{API}/printers/remove', data={'printer': 'Garage'}).get_json() == {'success': True}


def test_hand_edits_that_break_an_entry_leave_it_out(client, host):
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w', encoding='utf-8') as fh:
        json.dump({'printers': {}, 'added': [
            {'address': host.address(), 'model': 'QL-1100', 'name': 'Garage'},
            {'address': 'http://192.168.1.60', 'model': 'QL-800'},
            {'address': 'usbip://192.168.1.61', 'model': 'No such model'},
            {'address': 'usbip://192.168.1.62:9100', 'model': 'QL-800'},
            {'address': ['usbip://192.168.1.63'], 'model': 'QL-800'},
            'usbip://192.168.1.64',
        ]}, fh)
    assert [p['address'] for p in client.get(f'{API}/printers/added').get_json()['printers']] == [host.address()]


def test_added_printers_are_limited(client, monkeypatch, host):
    monkeypatch.setattr(printer_settings, 'MAX_ADDED', 1)
    add(client, host.address(), 'Garage')
    other = FakeHost(SharedDevice())
    try:
        FakeQL(other.devices[0])
        answer = add(client, other.address(), 'Shed')
        assert (answer.status_code, answer.get_json()['message']) == (400, printer_settings.TOO_MANY_ADDED)
        # Not kept imported either
        assert not usbip.connected(other.address())
    finally:
        other.close()


def test_refusals_are_told_as_they_are():
    for text in (*adding.MESSAGES, printer_settings.TOO_MANY_ADDED, devices.not_sent(reach.NotAllowed('outside'))):
        assert refusals.message(ValueError(text), '-') == text


@pytest.mark.parametrize('value, mode', [('local', 'local'), ('ANY', 'any'), (' off ', 'off')])
def test_mode_from_the_environment(monkeypatch, value, mode):
    monkeypatch.setenv('ADD_PRINTERS', value)
    assert create_app().config['ADD_PRINTERS'] == mode


def test_mode_from_the_environment_is_checked(monkeypatch):
    monkeypatch.setenv('ADD_PRINTERS', 'everywhere')
    with pytest.raises(ValueError, match='ADD_PRINTERS'):
        create_app()


def test_mode_is_local_by_default():
    assert create_app().config['ADD_PRINTERS'] == 'local'


def test_identify_of_an_address_nothing_answers_at(client):
    with socket.create_server(('127.0.0.1', 0)) as closed:
        address = f'usbip://127.0.0.1:{closed.getsockname()[1]}'
    answer = client.post(f'{API}/printers/identify', data={'address': address})
    assert answer.get_json() == {'success': False, 'message': adding.NO_ANSWER}


def settings_file(client) -> str:
    return os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE)


@pytest.mark.parametrize('device_id', [LASER])
def test_refused_add_lets_the_printer_go(client, device_id):
    device = SharedDevice(device_id=device_id)
    shared = FakeHost(device)
    try:
        answer = add(client, shared.address(), 'Shed')
        assert (answer.status_code, answer.get_json()['message']) == (400, adding.NO_PRINTER)
        assert not usbip.connected(shared.address())
        assert let_go(device)
    finally:
        shared.close()


@pytest.fixture
def importing(host):
    """Another thread imports the printer of the host meanwhile, holding its lock."""
    taken, done = threading.Event(), threading.Event()

    def hold():
        with usbip._lock(usbip.key(host.address())):
            taken.set()
            done.wait(20)
    yield lambda: (threading.Thread(target=hold, daemon=True).start(), taken.wait())
    done.set()


def test_requests_do_not_wait_for_a_printer_being_imported(client, host, importing):
    add(client, host.address(), 'Garage')
    usbip.disconnect(host.address())
    printer._last_states.clear()
    importing()
    shown = within(printer.STATUS_QUERY_WAIT + 2, lambda: listed(client))
    assert any(p['path'] == host.address() for p in shown)
    answer = within(3, lambda: client.post(f'{API}/settings', data={'printer': 'Garage', 'cut': 'last'}))
    assert answer.get_json()['settings'] == {'cut': 'last'}
    assert within(3, lambda: client.post(f'{API}/printers/remove', data={'printer': 'Garage'})).get_json() == {'success': True}


def test_concurrent_add_of_the_same_printer_is_refused(client, host, monkeypatch):
    # Another request added it while this one asked the printer
    real = adding.identify

    def meanwhile(address, mode, keep=False):
        found = real(address, mode, keep)
        with open(settings_file(client), 'w', encoding='utf-8') as fh:
            json.dump({'printers': {}, 'added': [{'address': host.address(), 'model': 'QL-1100', 'name': 'Shed'}]}, fh)
        return found
    monkeypatch.setattr(adding, 'identify', meanwhile)
    answer = add(client, host.address(), 'Garage')
    assert (answer.status_code, answer.get_json()['message']) == (400, adding.ALREADY)
    assert len(kept(client)['added']) == 1


def test_concurrent_add_of_the_same_name_is_refused(client, host, monkeypatch):
    real = adding.identify

    def meanwhile(address, mode, keep=False):
        found = real(address, mode, keep)
        with open(settings_file(client), 'w', encoding='utf-8') as fh:
            json.dump({'printers': {}, 'added': [{'address': 'usbip://192.168.1.77', 'model': 'QL-800', 'name': 'garage'}]}, fh)
        return found
    monkeypatch.setattr(adding, 'identify', meanwhile)
    answer = add(client, host.address(), 'Garage')
    assert (answer.status_code, answer.get_json()['message']) == (400, adding.NAME_TAKEN)
    assert [e['name'] for e in kept(client)['added']] == ['garage']
    assert not usbip.connected(host.address())


@pytest.mark.parametrize('value', ['disabled', 'none', 'no', False, None, 0, 'LOCAL ', ''])
def test_unknown_mode_turns_adding_off(value, caplog):
    class Odd(Config):
        ADD_PRINTERS = value
    with caplog.at_level(logging.WARNING):
        app = create_app(Odd)
    expected = 'local' if value == 'LOCAL ' else 'off'
    assert app.config['ADD_PRINTERS'] == expected
    assert ('ADD_PRINTERS' in caplog.text) is (expected == 'off')


def test_mode_changed_to_an_unknown_value_while_running_is_off(client, caplog):
    client.application.config['ADD_PRINTERS'] = 'disabled'
    with caplog.at_level(logging.WARNING):
        assert client.get(f'{API}/printers/added').get_json()['mode'] == 'off'
    assert 'ADD_PRINTERS' in caplog.text


@pytest.mark.parametrize('content', [b'{"printers": {}, "added": [', b'\xff\xfe not text', b'[]'])
def test_a_broken_settings_file_is_left_as_it_is(client, host, content):
    with open(settings_file(client), 'wb') as fh:
        fh.write(content)
    for answer in (add(client, host.address(), 'Garage'),
                   client.post(f'{API}/printers/remove', data={'printer': 'Garage'}),
                   client.post(f'{API}/settings', data={'printer': 'simulation', 'cut': 'last'}),
                   client.post(f'{API}/settings/labels', data={'collate': '0'})):
        assert (answer.status_code, answer.get_json()['message']) == (500, printer_settings.BROKEN)
    with open(settings_file(client), 'rb') as fh:
        assert fh.read() == content
    assert not usbip.connected(host.address())


def test_a_niimbot_b1_shared_is_added(client, monkeypatch):
    # Its status is not asked for long, the fake printer does not answer it
    monkeypatch.setattr('app.labeldesigner.drivers.niimbot.protocol.ANSWER_WAIT', 0.05)
    shared = FakeHost(SharedDevice(product=0x0002, device_id='MFG:NIIMBOT;CMD:ESC;MDL: B1;'))
    try:
        answer = add(client, shared.address(), 'B1')
        assert answer.get_json()['success'] is True, answer.get_json()
        assert kept(client)['added'][0]['model'] == 'Niimbot/B1'
    finally:
        usbip.disconnect(shared.address())
        shared.close()
