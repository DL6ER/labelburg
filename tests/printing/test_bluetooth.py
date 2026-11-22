"""Printers found on Bluetooth LE through the kernel's management interface,
against a fake management socket, and the automatic detection listing them."""
import errno
import socket
import struct
import threading

import pytest

from app.labeldesigner import bluetooth, printer
from app.labeldesigner.drivers import devices, niimbot
from tests.helpers import make_client

NIIMBOT = bytes.fromhex('e7810a7173ae499d8c15faa9aef0c3f2')[::-1]
B1 = '00:00:5E:00:53:01'
OTHER = '00:00:5E:00:53:02'


def event(code, index, params=b''):
    return struct.pack('<HHH', code, index, len(params)) + params


def found(address, kind=1, name='', services=(), raw=b''):
    """A Device Found event, the advertising data with a name and services,
    or raw as it is."""
    data = raw
    if name:
        data += bytes([len(name) + 1, 0x09]) + name.encode()
    if services:
        data += bytes([16 * len(services) + 1, 0x07]) + b''.join(services)
    params = bytes.fromhex(address.replace(':', ''))[::-1] + bytes([kind]) + struct.pack('<bIH', -60, 0, len(data)) + data
    return event(0x0012, 0, params)


class Management:
    """The kernel's management channel as a socket: one controller, a search
    that finds the devices given and then stops."""

    def __init__(self, devices_found=(), start_status=0, controllers=(0,)):
        self.sent, self.incoming = [], []
        self.devices_found, self.start_status, self.controllers = list(devices_found), start_status, controllers

    def __call__(self, family, kind, proto):
        assert (family, kind, proto) == (socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def bind(self, address):
        assert address == (0xffff, 3)

    def fileno(self):
        return -1

    def send(self, data):
        op, index, _ = struct.unpack_from('<HHH', data)
        self.sent.append((op, index, data[6:]))
        if op == 0x0003:
            params = struct.pack('<HBH', op, 0, len(self.controllers)) + struct.pack(f'<{len(self.controllers)}H', *self.controllers)
            self.incoming.append(event(0x0001, 0xffff, params))
        elif op == 0x0023:
            if self.start_status:
                self.incoming.append(event(0x0002, index, struct.pack('<HB', op, self.start_status)))
                return len(data)
            self.incoming.append(event(0x0013, index, bytes([data[6], 1])))
            self.incoming.append(event(0x0001, index, struct.pack('<HB', op, 0) + data[6:7]))
            self.incoming.extend(self.devices_found)
            self.incoming.append(event(0x0013, index, bytes([data[6], 0])))
        return len(data)

    def recv(self, size):
        return self.incoming.pop(0)


@pytest.fixture
def management(monkeypatch):
    def install(*args, **kwargs):
        fake = Management(*args, **kwargs)
        monkeypatch.setattr(bluetooth.socket, 'socket', fake)
        monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: (r if fake.incoming else [], [], []))
        return fake
    return install


def test_devices_on_bluetooth_le_are_found_with_their_names_and_services(management):
    fake = management([found(B1, services=[NIIMBOT]), found(B1, name='B1-TEST0001'), found(OTHER, kind=2)])
    assert bluetooth.discover(1) == [{'address': B1, 'name': 'B1-TEST0001', 'le': True, 'services': [NIIMBOT]},
                                     {'address': OTHER, 'name': '', 'le': True, 'services': []}]
    # Only LE, public and random addresses, and stopped again
    assert [(op, params) for op, _, params in fake.sent] == [(0x0003, b''), (0x0023, b'\x06'), (0x0024, b'\x06')]


def test_a_malformed_advertisement_of_another_device_still_finds_the_b1(management):
    # Any device nearby may advertise anything: a field running past its
    # data, an event too short for its header
    management([found(OTHER, raw=b'\x02\x01\x06\x02'), event(0x0012, 0, b'\x01\x02\x03'), found(B1, name='B1-TEST0001')])
    assert {device['address']: device['name'] for device in bluetooth.discover(1)} == {OTHER: '', B1: 'B1-TEST0001'}


@pytest.mark.parametrize('kwargs', [{'start_status': 0x14}, {'controllers': ()}])
def test_no_search_finds_nothing(management, kwargs):
    # Without the right to control the adapter, or without one
    management([found(B1, name='B1-TEST0001')], **kwargs)
    assert bluetooth.discover(1) == []


def test_no_bluetooth_finds_nothing(monkeypatch):
    def unsupported(*args):
        raise OSError(errno.EAFNOSUPPORT, 'Address family not supported by protocol')
    monkeypatch.setattr(bluetooth.socket, 'socket', unsupported)
    assert bluetooth.discover(1) == []


@pytest.mark.parametrize('device, printer_', [
    ({'address': B1, 'name': 'B1-TEST0001', 'le': True, 'services': []}, (f'bt://{B1}', 'Niimbot/B1')),
    ({'address': B1, 'name': '', 'le': True, 'services': [NIIMBOT]}, (f'bt://{B1}', 'Niimbot/B1')),
    ({'address': B1, 'name': 'D110-TEST0001', 'le': True, 'services': [NIIMBOT]}, (f'bt://{B1}', 'Niimbot/D110')),
    # Not a model Labelburg prints on
    ({'address': B1, 'name': 'K3-TEST0001', 'le': True, 'services': [NIIMBOT]}, None),
    ({'address': OTHER, 'name': 'Phone', 'le': True, 'services': []}, None),
    ({'address': OTHER, 'name': 'B1 Speaker', 'le': True, 'services': []}, None),
])
def test_a_niimbot_b1_is_known_by_its_name_or_its_service(device, printer_):
    assert niimbot.DRIVER.found_on_bluetooth(device) == printer_


@pytest.fixture
def detecting(tmp_path, monkeypatch):
    """The automatic detection with a B1 nearby on Bluetooth, which answers
    its status queries."""
    searches, release = [], threading.Event()
    release.set()

    def discover(timeout):
        searches.append(timeout)
        release.wait(5)
        return [{'address': B1, 'name': 'B1-TEST0001', 'le': True, 'services': [NIIMBOT]}]
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', True)
    monkeypatch.setattr(printer.bluetooth, 'discover', discover)
    monkeypatch.setattr(printer, '_bluetooth', {})
    monkeypatch.setattr(printer, '_bluetooth_searched', None)
    monkeypatch.setattr(printer, '_bluetooth_thread', None)
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: ([], []))
    monkeypatch.setattr(devices, 'looked', 0.0)
    monkeypatch.setattr(niimbot.DRIVER, 'status', lambda device, model, unconfirmed=False: {
        'errors': [], 'path': device, 'model': model, 'status_type': 'Reply to status request'})
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    return client, searches, release


def _listed(client):
    data = client.get('/labeldesigner/api/printer_status').get_json()
    return [(p['path'], p['model']) for p in data['printers'] if p['path'] != 'simulation'], data.get('searching')


def _searched(client):
    printer._bluetooth_thread.join(5)
    printer._last_scan_ts = 0


def test_detection_lists_a_b1_found_on_bluetooth(detecting):
    client, searches, release = detecting
    release.clear()
    # Searched in the background, which the status tells
    assert _listed(client) == ([], True)
    release.set()
    _searched(client)
    assert _listed(client) == ([(f'bt://{B1}', 'Niimbot/B1')], False)
    assert searches == [printer.BLUETOOTH_SEARCH]


def test_bluetooth_is_searched_again_only_when_looking_for_printers(detecting, monkeypatch):
    client, searches, release = detecting
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    _listed(client)
    _searched(client)
    _listed(client)
    assert len(searches) == 1
    client.get('/labeldesigner/api/printer_status?look=1')
    _searched(client)
    assert len(searches) == 2


def test_bluetooth_is_searched_at_most_every_minute(detecting):
    client, searches, release = detecting
    _listed(client)
    _searched(client)
    client.get('/labeldesigner/api/printer_status?look=1')
    assert len(searches) == 1


def test_a_b1_found_stays_listed_when_it_no_longer_advertises(detecting, monkeypatch):
    # Connected, a B1 stops advertising
    client, searches, release = detecting
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    _listed(client)
    _searched(client)
    monkeypatch.setattr(printer.bluetooth, 'discover', lambda timeout: [])
    client.get('/labeldesigner/api/printer_status?look=1')
    _searched(client)
    assert _listed(client)[0] == [(f'bt://{B1}', 'Niimbot/B1')]


def test_no_bluetooth_search_without_automatic_detection(detecting):
    client, searches, release = detecting
    client.application.config['PRINTER_PRINTER'] = 'simulation'
    client.get('/labeldesigner/api/printer_status?look=1')
    assert searches == []
