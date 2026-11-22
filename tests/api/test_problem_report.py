"""The details of a problem report: the version, each printer with its
connection and labels, never anything private, and the bug form on GitHub
with them filled in."""
import json
import os
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.labeldesigner import printer, printer_settings, problem_report, routes
from app.labeldesigner.drivers import reach
from tests.helpers import make_client

API = '/labeldesigner/api'
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = {'release': 'v8.0.0', 'commit': '42ef2db6c1d0aa1e2f8c6a2b4d4e7f1f0a9b8c7d', 'date': '2026-11-20 21:17 UTC'}


def state(path, model, **status):
    return {'errors': [], 'path': path, 'model': model, 'media_width': None, 'media_length': 0, **status}


@pytest.fixture(autouse=True)
def nothing_watched(monkeypatch):
    monkeypatch.setattr(reach, '_watched', {})


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.application.extensions['build'] = BUILD
    return client


def listed(monkeypatch, *printers):
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**printers[0], 'printers': list(printers)})


def report(client) -> dict:
    answer = client.get(f'{API}/problem_report')
    assert answer.status_code == 200
    return answer.get_json()


@pytest.mark.parametrize('build, told', [
    (BUILD, 'v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC'),
    ({'release': '', 'commit': '42ef2db6c1d0', 'date': '2026-11-20 21:17 UTC'}, 'commit 42ef2db, built 2026-11-20 21:17 UTC'),
    ({'release': '', 'commit': '42ef2db6c1d0', 'date': ''}, 'commit 42ef2db'),
    ({'release': '', 'commit': '', 'date': ''}, 'A development version'),
])
def test_the_version_is_told_as_the_settings_show_it(build, told):
    assert problem_report.version(build) == told


def test_each_printer_is_told_by_its_model_and_connection(client, monkeypatch):
    listed(monkeypatch,
           state('usb://0x04f9:0x209b', 'QL-800'),
           state('file:///dev/usb/lp0', 'QL-1100'),
           state('/dev/usb/lp1', 'Zebra/ZPL'),
           state('tcp://192.168.1.60', 'QL-810W'),
           state('vc500w://192.168.1.61', 'VC-500W'),
           state('bt://AA:BB:CC:DD:EE:FF', 'Niimbot/B1'),
           state('usbip://192.168.1.62/1-1', 'PT-P750W'))
    assert report(client)['details']['printer'] == (
        'QL-800 over USB; QL-1100 over USB; Zebra ZPL over USB; QL-810W over the network; VC-500W over the network; '
        'Niimbot B1 over Bluetooth; PT-P750W over USB on another device')


@pytest.mark.parametrize('configured', ['?', 'tcp://192.168.1.60'])
def test_the_demo_printer_listed_by_itself_is_left_out_next_to_a_real_printer(client, monkeypatch, configured):
    client.application.config['PRINTER_PRINTER'] = configured
    real = state('tcp://192.168.1.60', 'QL-810W', media_width=62)
    # Found by the search, or configured
    monkeypatch.setattr(printer, '_cached_printers', [real])
    monkeypatch.setattr(printer, '_last_scan_ts', time.time())
    monkeypatch.setattr(printer, 'device_state', lambda device, model: dict(real))
    status = client.get(f'{API}/printer_status').get_json()
    assert [p['path'] for p in status['printers']] == ['tcp://192.168.1.60', 'simulation']
    details = report(client)['details']
    assert (details['printer'], details['label_size']) == (
        'QL-810W over the network', '62 mm endless (DK-22205 / DK-2205), reported by the printer')


def test_the_demo_printer_configured_in_printers_is_told(client, monkeypatch):
    client.application.config['PRINTERS'] = [('QL-810W', 'tcp://192.168.1.60', 'Office'), ('QL-700', 'simulation', '')]
    monkeypatch.setattr(printer, '_query_configured', lambda model, device: state(device, model, media_width=62))
    details = report(client)['details']
    assert details['printer'] == 'QL-810W over the network; QL-700 as the demo printer'
    assert details['label_size'] == ('QL-810W: 62 mm endless (DK-22205 / DK-2205), reported by the printer; '
                                     'Demo printer: 62 mm endless (DK-22205 / DK-2205), assumed as the printer does not report it')


def test_the_demo_printer_set_as_the_printer_is_told_next_to_an_added_one(client, monkeypatch):
    config = client.application.config
    config['PRINTER_PRINTER'] = 'simulation'
    with open(os.path.join(config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w') as fh:
        json.dump({'printers': {}, 'added': [{'address': 'tcp://192.168.1.60', 'model': 'QL-810W', 'name': 'Office'}]}, fh)
    monkeypatch.setattr(printer, '_query_configured', lambda model, device: state(device, model))
    assert report(client)['details']['printer'] == 'QL-800 as the demo printer; QL-810W over the network'


def test_the_demo_printer_as_the_only_printer_is_told(client):
    # The search found nothing else
    assert [p['path'] for p in client.get(f'{API}/printer_status').get_json()['printers']] == ['simulation']
    assert report(client)['details']['printer'] == 'QL-800 as the demo printer'


def test_the_demo_printer_is_told_as_the_model_chosen_for_it(client):
    client.post(f'{API}/settings', data={'printer': 'simulation', 'demo_model': 'PT-P750W'})
    client.application.config['PRINTER_PRINTER'] = 'simulation'
    assert report(client)['details']['printer'] == 'PT-P750W as the demo printer'


def test_an_unknown_model_or_connection_is_not_named(client, monkeypatch):
    listed(monkeypatch, state('file:///srv/printer-of-anna', 'Annas Drucker'))
    assert report(client)['details']['printer'] == 'Unknown model over another connection'


def test_the_firmware_is_told_where_the_status_reports_it(client, monkeypatch):
    listed(monkeypatch, state('tcp://192.168.1.60', 'QL-810W', firmware='1.12'),
           state('tcp://192.168.1.61', 'QL-810W', firmware='1.12 Anna'),
           state('tcp://192.168.1.62', 'QL-810W', firmware=12))
    assert report(client)['details']['printer'] == (
        'QL-810W over the network, firmware 1.12; QL-810W over the network; QL-810W over the network')


def test_the_labels_of_each_printer_are_told_and_where_they_come_from(client, monkeypatch):
    listed(monkeypatch,
           state('file:///dev/usb/lp0', 'QL-800', media_width=29, media_length=90),
           state('tcp://192.168.1.60', 'QL-810W'),
           state('bt://AA:BB:CC:DD:EE:FF', 'PUQU/Q00'),
           state('file:///dev/usb/lp1', 'QL-1100', media_width=40))
    client.post(f'{API}/settings', data={'printer': 'tcp://192.168.1.60', 'model': 'QL-810W', 'label_size': '62x29'})
    assert report(client)['details']['label_size'] == (
        'QL-800: 29 x 90 mm die-cut (DK-11201 / DK-1201), reported by the printer; '
        'QL-810W: 62 x 29 mm die-cut (DK-11209 / DK-1209), chosen in the Settings; '
        'PUQU Q00: 50 x 30 mm die-cut (PUQU), assumed as the printer does not report it; '
        'QL-1100: 40 mm reported by the printer, unknown to Labelburg')


def test_the_labels_of_a_single_printer_are_told_without_its_model(client, monkeypatch):
    listed(monkeypatch, state('file:///dev/usb/lp0', 'QL-800', media_width=62))
    assert report(client)['details']['label_size'] == '62 mm endless (DK-22205 / DK-2205), reported by the printer'


PRIVATE = ['Kitchen', 'Office Desk', 'Garage', 'Craft room', 'Toolshed', 'Basement', '000F1Z401370', 'printer.fritz.box', 'fritz',
           'AA:BB:CC:DD:EE:FF', 'aa:bb:cc:dd:ee:ff', 'AABBCCDDEEFF', 'hubble', '1-4.1.3', '/dev/usb/lp0', 'lp0', '192.168',
           '0x04f9', '209b', 'H2B123456', 'ROLL4711', 'Jam of grandma', 'Plum jam 2026', 'jam.json']


def test_nothing_private_is_told(client, monkeypatch):
    config = client.application.config
    config['PRINTERS'] = [('QL-800', 'usb://0x04f9:0x209b/000F1Z401370', 'Kitchen'),
                          ('QL-810W', 'tcp://printer.fritz.box', 'Office Desk'),
                          ('Niimbot/B1', 'bt://AA:BB:CC:DD:EE:FF', 'Garage'),
                          ('VC-500W', 'usbip://hubble/1-4.1.3', 'Craft room'),
                          ('QL-700', 'file:///dev/usb/lp0', 'Toolshed')]
    repo = config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, printer_settings.FILE), 'w') as fh:
        json.dump({'printers': {}, 'added': [{'address': 'tcp://192.168.178.50', 'model': 'QL-820NWB', 'name': 'Basement'}]}, fh)
    # A saved label, its name and text are the user's own
    with open(os.path.join(repo, 'jam.json'), 'w') as fh:
        json.dump({'label_size': '62', 'text': [{'text': 'Plum jam 2026'}], 'title': 'Jam of grandma'}, fh)
    # What the printers tell of themselves, serial numbers included
    told = {'usb://0x04f9:0x209b/000F1Z401370': {'media_width': 62, 'serial': 'H2B123456'},
            'bt://AA:BB:CC:DD:EE:FF': {'serial': 'H2B123456', 'roll': 'ROLL4711', 'media_width': 50, 'media_length': 30}}
    monkeypatch.setattr(printer, '_query_configured', lambda model, device: state(device, model, **told.get(device, {})))
    answer = report(client)
    assert answer['details']['printer'] == ('QL-800 over USB; QL-810W over the network; Niimbot B1 over Bluetooth; '
                                            'VC-500W over USB on another device; QL-700 over USB; QL-820NWB over the network')
    # The names were listed and the serial numbers told, but none goes on
    status = client.get(f'{API}/printer_status').get_json()
    assert {p.get('name') for p in status['printers']} >= {'Kitchen', 'Basement'}
    assert any(p.get('serial') == 'H2B123456' for p in status['printers'])
    for text in (json.dumps(answer), unquote(answer['url']), answer['text']):
        assert [word for word in PRIVATE if word.lower() in text.lower()] == []


def test_the_url_fills_in_the_bug_form(client, monkeypatch):
    listed(monkeypatch, state('tcp://192.168.1.60', 'QL-810W', media_width=62))
    answer = report(client)
    url = urlsplit(answer['url'])
    assert (url.scheme, url.netloc, url.path) == ('https', 'github.com', '/DL6ER/labelburg/issues/new')
    values = {key: value for key, [value] in parse_qs(url.query, strict_parsing=True).items()}
    assert values == {'template': 'bug_report.yml', **answer['details']}
    assert answer['details'] == {'version': 'v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC',
                                 'printer': 'QL-810W over the network',
                                 'label_size': '62 mm endless (DK-22205 / DK-2205), reported by the printer'}
    assert answer['text'] == ('Version: v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC\n'
                              'Printer: QL-810W over the network\n'
                              'Labels: 62 mm endless (DK-22205 / DK-2205), reported by the printer')


def test_every_field_is_encoded():
    details = {'version': 'v1.0 & more', 'printer': 'A+B #1 = 100% ü', 'label_size': 'x/y?z;w'}
    url = problem_report.url(details)
    assert re.fullmatch(r'[A-Za-z0-9:/?=&._~%-]+', url)
    assert url.count('&') == 3 and url.count('=') == 4
    query = urlsplit(url).query
    assert {key: value for key, [value] in parse_qs(query, strict_parsing=True).items()} == {'template': 'bug_report.yml', **details}


def test_the_fields_are_those_of_the_bug_form():
    with open(os.path.join(ROOT, '.github', 'ISSUE_TEMPLATE', 'bug_report.yml'), encoding='utf-8') as fh:
        form = fh.read()
    inputs = re.findall(r'- type: input\n\s+id: (\w+)', form)
    url = urlsplit(problem_report.url({'version': 'v', 'printer': 'p', 'label_size': 'l'}))
    assert url.path.endswith('/issues/new')
    assert sorted(parse_qs(url.query)) == sorted(['template', *inputs])
    # GitHub takes these as the title, labels and so on of the issue instead
    assert not set(inputs) & {'title', 'body', 'labels', 'milestone', 'assignees', 'projects', 'template', 'type'}


def test_many_printers_keep_the_url_short(client, monkeypatch):
    many = [state(f'tcp://192.168.1.{i}', 'QL-1110NWB', media_width=54) for i in range(40)]
    listed(monkeypatch, *many)
    answer = report(client)
    assert len(answer['url']) < 3000
    # Both name the same printers, the others are counted
    told = answer['details']['printer'].count('QL-1110NWB')
    assert 1 < told == answer['details']['label_size'].count('QL-1110NWB') < 40
    for field in ('printer', 'label_size'):
        assert answer['details'][field].endswith(f'; and {40 - told} more'), field


def test_without_a_printer_the_form_still_opens(client, monkeypatch):
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {'printers': []})
    answer = report(client)
    assert answer['details']['printer'] == 'No printer found'
    assert 'label_size' not in parse_qs(urlsplit(answer['url']).query)
