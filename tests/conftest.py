import os

import pytest

from app.labeldesigner import printer, printer_settings
from app.labeldesigner.drivers import backends, devices, usbip
from app.labeldesigner.drivers.brother import usb
from app.labeldesigner.drivers.escpos import protocol as escpos


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
