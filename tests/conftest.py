import errno
import ipaddress
import os
import socket

import pytest

from app.labeldesigner import print_queue, printer, printer_settings
from app.labeldesigner.drivers import backends, devices, usbip
from app.labeldesigner.drivers.brother import usb
from app.labeldesigner.drivers.escpos import protocol as escpos


@pytest.fixture(autouse=True)
def only_this_host(monkeypatch):
    """The tests connect to this host only: a connection elsewhere, e.g. to
    an address a check should have refused, fails and fails the test. The
    addresses tried are yielded."""
    tried = []
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex

    def elsewhere(sock, address):
        # Nor to printers on Bluetooth, the tests fake them
        if sock.family == getattr(socket, 'AF_BLUETOOTH', None):
            return True
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return False
        try:
            return not ipaddress.ip_address(str(address[0]).split('%')[0]).is_loopback
        except ValueError:
            return True

    def checked(real):
        def to(sock, address):
            if elsewhere(sock, address):
                tried.append(tuple(address[:2]))
                raise OSError(errno.ENETUNREACH, f'The tests reach no other host than this one, not {address[0]}')
            return real(sock, address)
        return to
    monkeypatch.setattr(socket.socket, 'connect', checked(connect))
    monkeypatch.setattr(socket.socket, 'connect_ex', checked(connect_ex))
    yield tried
    assert tried == [], f'A test tried to connect to {tried}'


@pytest.fixture(autouse=True)
def printer_output(tmp_path, monkeypatch):
    # Keep lock files and simulated labels of the tests out of the system temp
    # directory and the working tree
    monkeypatch.setattr(devices, 'LOCK_DIR', str(tmp_path))
    monkeypatch.setattr(printer, 'SIMULATED_LABELS_DIR', str(tmp_path / 'simulated_labels'))
    # Network printers of the machine running the tests are neither detected
    # nor discovered.
    # Tests of USB detection fake the devices they need.
    monkeypatch.setattr(printer, 'ARP_TABLE', os.devnull)
    # Nor are the device IDs of its USB printers read
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'no-usb-device-id-{name}'))
    # Nor its printers on PyUSB, nor those it shares over USB/IP
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: ([], []))
    monkeypatch.setattr(backends, 'USBIP_HOST', str(tmp_path / 'no-usbip-host'))
    monkeypatch.setattr(printer, 'NETWORK_DISCOVERY', False)
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', False)
    # Nor the printer settings of a server run from here, e.g. with
    # docker-compose.yml, which keeps them in labels/
    monkeypatch.setattr(printer_settings, 'FILE', '.printers-of-the-tests.json')
    # Nor the status requests the printers of other tests left unanswered
    monkeypatch.setattr(usb, '_unanswered', {})
    monkeypatch.setattr(usb, '_asked', {})
    monkeypatch.setattr(usb, '_retry_answered', set())
    monkeypatch.setattr(escpos, '_asked', {})


@pytest.fixture(autouse=True)
def no_known_printers(monkeypatch):
    # What one test found stays out of the next
    monkeypatch.setattr(printer, '_cached_printers', [])
    monkeypatch.setattr(printer, '_cached_hints', [])
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_last_states', {})
    monkeypatch.setattr(printer, '_status_queries', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, '_unreached', {})


@pytest.fixture(autouse=True)
def shared_printers(monkeypatch):
    # Printers shared over USB/IP stay imported, each test begins without any
    printers = {}
    monkeypatch.setattr(usbip, '_printers', printers)
    monkeypatch.setattr(usbip, '_unreachable', {})
    monkeypatch.setattr(usbip, '_device_ids', {})
    yield
    for shared in printers.values():
        shared.close()


@pytest.fixture(autouse=True)
def jobs_end_before_the_answer(request, monkeypatch):
    """A print request is answered once its job ended, so that a test reads
    what was printed right after it. Tests of the queue itself use queued."""
    print_queue.reset()
    if 'queued' not in request.fixturenames:
        add = print_queue.add

        def ended(*args, **kwargs):
            job = add(*args, **kwargs)
            job.done.wait(60)
            return job
        monkeypatch.setattr(print_queue, 'add', ended)
    yield
    print_queue.reset()


@pytest.fixture
def queued():
    """Print requests are answered once their job is in the queue."""
