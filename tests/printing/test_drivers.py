"""The printer drivers behind the interface of drivers/base.py, and which
one drives a printer."""

import contextlib
import socket
import threading

import pytest

from app.labeldesigner import drivers
from app.labeldesigner.drivers import devices
from app.labeldesigner.drivers.base import Driver
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.label_names import label_name, label_printers


@pytest.mark.parametrize('driver', drivers.DRIVERS, ids=lambda driver: type(driver).__name__)
def test_driver_describes_its_printers(driver):
    assert driver.models and driver.labels and driver.device_patterns and driver.dpi(False) > 0
    for model in driver.models:
        assert drivers.for_model(model) is driver
        assert driver.colors(model) in ('black', 'red', 'color')
    for label in driver.labels:
        assert drivers.for_label(label.identifier) is driver
        assert label_name(label) == driver.label_names[label.identifier]
        assert driver.palette(label) in ('black', 'red', 'color')
        # Only its own printers print its label sizes
        assert set(label_printers(label)) <= set(driver.models)


def test_models_and_label_sizes_belong_to_one_driver():
    assert len(set(drivers.MODELS)) == len(drivers.MODELS)
    assert len({label.identifier for label in drivers.LABELS}) == len(drivers.LABELS)


def test_driver_of_a_printer():
    # The address or device ID tells first, then the model
    assert drivers.resolve('vc500w://192.0.2.1', 'QL-800') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('tcp://192.0.2.1', 'VC-500W') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('simulation', 'VC-500W') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('tcp://192.0.2.1', 'QL-810W') == (drivers.BROTHER, 'QL-810W')
    # Unknown models are left to the Brother driver
    assert drivers.resolve('file:///dev/usb/lp0', 'QL-9999') == (drivers.BROTHER, 'QL-9999')
    assert drivers.for_label('99x99') is None


@pytest.mark.parametrize('device_id, model', [
    # Left at its default, the model is taken only where nothing else tells one
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'TSC/TSPL'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'Zebra/ZPL-300'),
    ('MFG:Printer;CMD:ZPL,TSPL;MDL:LP-420;', 'TSC/TSPL'),
    # A Brother printer tells its model in its status
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;', 'QL-800')])
def test_driver_and_model_of_a_printer_of_the_default_model(monkeypatch, device_id, model):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: device_id)
    assert drivers.resolve('file:///dev/usb/lp0', 'QL-800', assumed=True) == (drivers.for_model(model), model)


@pytest.mark.parametrize('device_id, configured, model', [
    # The other resolution of the same printer as configured, where the
    # device ID names none
    ('MFG:Printer;CMD:TSPL;MDL:LP-420;', 'TSC/TSPL-300', 'TSC/TSPL-300'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZD420;', 'Zebra/ZPL-300', 'Zebra/ZPL-300'),
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80;', 'Epson/ESC-POS-180', 'Epson/ESC-POS-180'),
    # The resolution the device ID names, also by a known model
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T88V;', 'Epson/ESC-POS', 'Epson/ESC-POS-180'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;', 'Epson/ESC-POS-180', 'Epson/ESC-POS'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX430t;', 'Zebra/ZPL', 'Zebra/ZPL-300'),
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD611-300dpi ZPL;', 'Zebra/ZPL', 'Zebra/ZPL-300'),
    ('MFG:VEVOR ;CMD: ;MODEL:Y428BT;DES:LabelPrinter;', 'TSC/TSPL', 'TSC/TSPL-300'),
    ('MFG:TSC;CMD:TSPL2;MDL:TE300 300 dpi;', 'TSC/TSPL', 'TSC/TSPL-300'),
    # The driver of the model keeps a printer it takes, of another name too
    ('MFG:TSC;CMD:EPL2,TSPL-EZ,ZPL2;MDL:TE310;', 'Zebra/ZPL-300', 'Zebra/ZPL-300'),
    ('MFG:Printer;CMD:ZPL,TSPL;MDL:LP-420;', 'Zebra/ZPL', 'Zebra/ZPL'),
    # Another driver takes it by its command set, by the order of the
    # drivers without a model of one
    ('MFG:Printer;CMD:ZPL,TSPL;MDL:LP-420;', 'QL-800', 'TSC/TSPL'),
    ('MFG:Printer;CMD:ZPL,TSPL;MDL:LP-420;', 'Unknown', 'TSC/TSPL'),
    ('MFG:TSC;CMD:TSPL2;MDL:TE200;', 'Zebra/ZPL-300', 'TSC/TSPL'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZD420;', 'TSC/TSPL-300', 'Zebra/ZPL'),
    # Not by a device ID of its table alone, which names no command set
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'Epson/ESC-POS', 'Epson/ESC-POS'),
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'QL-800', 'QL-800'),
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'Unknown', 'TSC/TSPL'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'Epson/ESC-POS', 'Epson/ESC-POS'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'Unknown', 'Zebra/ZPL-300'),
    # TSC's models give the resolution in their leading digit
    ('MFG:TSC;CMD:TSPL2;MDL:TE300;', 'TSC/TSPL', 'TSC/TSPL-300'),
    ('MFG:TSC;CMD:EPL2,TSPL-EZ,ZPL2;MDL:TE310;', 'TSC/TSPL', 'TSC/TSPL-300'),
    ('MFG:TSC;CMD:TSPL2;MDL:TE200;', 'TSC/TSPL-300', 'TSC/TSPL'),
    # Not where it is a 6, which may be the resolution of no model
    ('MFG:TSC;CMD:TSPL2;MDL:TDP-643 Plus;', 'TSC/TSPL-300', 'TSC/TSPL-300'),
    ('MFG:TSC;CMD:TSPL2;MDL:TDP-643 Plus;', 'Unknown', 'TSC/TSPL'),
    # A printer of 600 dpi is taken by no driver, ZPL at 203 dpi neither
    ('MFG:TSC;CMD:TSPL2,ZPL2;MDL:TX600;', 'Unknown', 'Unknown'),
    ('MFG:TSC;CMD:TSPL2,ZPL2;MDL:TX600;', 'QL-800', 'QL-800'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZT610-600dpi ZPL;', 'Unknown', 'Unknown')])
def test_driver_and_model_of_a_configured_printer(monkeypatch, device_id, configured, model):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: device_id)
    driver, known = drivers.resolve('file:///dev/usb/lp0', configured)
    assert (driver, known) == (drivers.for_model(model), model)


@pytest.mark.parametrize('device', ['usb://0x04b8:0x0e15', 'usbip://192.0.2.1', 'file:///dev/usb/lp0'])
def test_device_id_is_read_once_for_all_drivers(monkeypatch, device):
    # On usb:// one that tells none is asked again each time
    read = []
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: read.append(device) or '')
    assert drivers.resolve(device, 'QL-800') == (drivers.BROTHER, 'QL-800')
    assert read == [device]


@pytest.mark.parametrize('driver, device, model', [
    (drivers.BROTHER, 'file:///dev/usb/lp0', 'QL-800'), (drivers.VC500W, 'vc500w://192.0.2.1', 'VC-500W'),
    (drivers.PUQU, 'bt://CD:AC:BB:E0:46:4C', 'PUQU/Q00'), (drivers.NIIMBOT, 'bt://CD:AC:BB:E0:46:4C', 'Niimbot/B1'),
    (drivers.TSPL, 'tcp://192.0.2.1', 'TSC/TSPL')])
def test_printer_a_job_holds_is_told_as_printing(monkeypatch, driver, device, model):
    monkeypatch.setattr(devices, 'device_lock', lambda device, timeout=None: contextlib.nullcontext(False))
    busy = {'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'}
    # Never asked before: the model as configured
    state = driver.status(device, model)
    assert {key: state[key] for key in busy} == busy and state['model'] == model
    # Else as it was last, without its errors
    told = dict(state, media_width=50, errors=['The cover is open'], status_type='Error occurred', phase_type='Waiting to receive')
    devices.status_cache[devices.lock_key(device)] = told
    assert driver.status(device, model) == dict(told, **busy)


def test_a_driver_states_what_it_does_not_do():
    driver = Driver()
    assert driver.model_of('bt://AA:BB:CC:DD:EE:FF/2') is None
    assert driver.announced({'address': '192.0.2.1', 'port': 631, 'service': '_ipp._tcp.local', 'txt': {}}) is None
    assert driver.resolution_problem('Any', True) == ''
    for call in (lambda: driver.fits(None, 'Any'), lambda: driver.dpi(False), lambda: driver.status('bt://x', 'Any'),
                 lambda: driver.render(None, []), lambda: driver.send(None, None), lambda: driver.network_device('x')):
        with pytest.raises(NotImplementedError):
            call()


@pytest.mark.parametrize('device, key', [
    ('file:///dev/usb/lp0', '/dev/usb/lp0'),
    ('/dev/usb/lp0', '/dev/usb/lp0'),
    ('TCP://Printer.local', 'tcp://printer.local:9100'),
    ('tcp://192.0.2.1:9100', 'tcp://192.0.2.1:9100'),
    ('tcp://[2001:DB8::1]', 'tcp://[2001:db8::1]:9100'),
    ('tcp://[2001:db8::1]:9100', 'tcp://[2001:db8::1]:9100'),
    ('vc500w://192.0.2.1', 'vc500w://192.0.2.1:9100'),
    ('usb://0x04F9:0x2042', 'usb://0x04f9:0x2042'),
    # One Bluetooth printer whatever channel names it
    ('bt://AA:BB:CC:DD:EE:FF/2', 'bt://aa:bb:cc:dd:ee:ff'),
    ('bt://AA:BB:CC:DD:EE:FF', 'bt://aa:bb:cc:dd:ee:ff'),
    ('bt://aa:bb:cc:dd:ee:ff/1', 'bt://aa:bb:cc:dd:ee:ff'),
])
def test_every_spelling_of_a_device_has_one_lock(device, key):
    assert devices.lock_key(device) == key


def test_job_is_written_to_port_9100_of_a_network_printer():
    server = socket.create_server(('127.0.0.1', 0))
    received = []

    def accept():
        connection, _ = server.accept()
        with connection:
            data = b''
            while chunk := connection.recv(65536):
                data += chunk
            received.append(data)
    thread = threading.Thread(target=accept)
    thread.start()
    job = bytes(range(256)) * 1000
    try:
        network.send(job, f'tcp://127.0.0.1:{server.getsockname()[1]}')
        thread.join(5)
    finally:
        server.close()
    assert received == [job]


def test_job_is_written_to_a_network_printer_at_an_ipv6_address():
    try:
        server = socket.create_server(('::1', 0), family=socket.AF_INET6)
    except OSError:
        pytest.skip('No IPv6 on this machine')
    received = []

    def accept():
        connection, _ = server.accept()
        with connection:
            received.append(connection.recv(65536))
    thread = threading.Thread(target=accept)
    thread.start()
    try:
        network.send(b'job', f'tcp://[::1]:{server.getsockname()[1]}')
        thread.join(5)
    finally:
        server.close()
    assert received == [b'job']


@pytest.mark.parametrize('device, offered', [('tcp://[2001:db8::1]', True), ('tcp://[2001:db8::1]:9100', True),
                                             ('tcp://[::ffff:192.0.2.1]', True), ('tcp://[2001:db8::1]:6379', False),
                                             ('tcp://2001:db8::1', False)])
def test_network_printer_at_an_ipv6_address(device, offered):
    assert any(pattern.fullmatch(device) for pattern in drivers.DEVICE_PATTERNS) is offered


def test_model_names_are_unique():
    assert len(drivers.MODELS) == len(set(drivers.MODELS))


def test_models_of_other_makers_are_named_with_their_maker():
    for driver in drivers.DRIVERS:
        for model in driver.models:
            if driver.vendor == 'Brother':
                assert '/' not in model and drivers.canonical(f'Brother/{model}') == model
            else:
                assert model.startswith(f'{driver.vendor}/') and drivers.canonical(model) == model


def test_every_name_before_ends_at_a_model():
    # canonical() looks a name up once
    for name in drivers._ALIASES:
        assert drivers.canonical(name) in drivers.MODELS, name


@pytest.mark.parametrize('device_id, vendor, name', [
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;SN:X5JK012345;', 'Epson', 'Epson TM-T20II'),
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD611-300dpi ZPL;', 'Zebra', 'Zebra ZTC ZD611'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZD420 300 DPI;', 'Zebra', 'Zebra ZD420'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZD611_300dpi;', 'Zebra', 'Zebra ZD611'),
    # A number before dpi in the model stays
    ('MFG:Printer;CMD:ZPL;MDL:LP300dpi;', '', 'LP300dpi'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'Zebra', 'HP KE103'),
    ('MFG:Xprinter;CMD:ESC/POS;MDL:Xprinter XP-58IIH;', 'Epson', 'Xprinter XP-58IIH'),
    ('MFG:Printer;CMD:ESC/POS,Raster;MDL:POS58;', 'Epson', 'POS58'),
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', '', 'Thermal Printer'),
    ('MFG:VEVOR ;CMD: ;MODEL:Y428BT;DES:LabelPrinter;', '', 'VEVOR Y428BT'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II\x00\x1b  ;', 'Epson', 'Epson TM-T20II'),
    # Without what tells one printer from another: serial numbers, MAC and IP addresses
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80 SN0123456789;', 'Epson', 'POS-80'),
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80 (S/N:A1B2);', 'Epson', 'POS-80'),
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80 X5JK0123;SN:X5JK0123;', 'Epson', 'POS-80'),
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80 12J123456;', 'Epson', 'POS-80'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II 00:11:22:33:44:55;', 'Epson', 'Epson TM-T20II'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II 00-11-22-AA-BB-CC;', 'Epson', 'Epson TM-T20II'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II 001122AABBCC;', 'Epson', 'Epson TM-T20II'),
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-80 192.168.1.50;', 'Epson', 'POS-80'),
    ('MFG:SNBC;CMD:ESC/POS;MDL:BTP-R880NP;', 'Epson', 'SNBC BTP-R880NP'),
    # Too long to be a name, or none
    ('MFG:Some Printer Manufacturing Company;CMD:ESC/POS;MDL:Thermal Receipt Printer 80;', 'Epson', ''),
    ('MFG:EPSON;CMD:ESC/POS;', 'Epson', ''),
    ('', 'Epson', '')])
def test_device_name(device_id, vendor, name):
    assert devices.device_name(device_id, vendor) == name


def test_known_device_id_asks_no_printer(monkeypatch, tmp_path):
    from app.labeldesigner.drivers import backends, usbip
    told = 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;'
    # As usblp read it when the printer was plugged in
    (tmp_path / 'lp7').mkdir()
    (tmp_path / 'lp7' / 'ieee1284_id').write_text(told)
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / '{name}' / 'ieee1284_id'))
    assert devices.known_device_id('file:///dev/usb/lp7') == devices.known_device_id('/dev/usb/lp7') == told
    # On usb:// only once it was read
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(backends, 'device_id', lambda device: pytest.fail('asked the printer'))
    assert devices.known_device_id('usb://0x04b8:0x0e15/5443') == ''
    monkeypatch.setattr(backends, 'device_id', lambda device: told)
    devices.usb_device_id('usb://0x04b8:0x0e15/5443')
    assert devices.known_device_id('usb://0x04B8:0x0E15/05443') == told
    # Shared over USB/IP, the one it told last, it is not imported for it
    monkeypatch.setattr(usbip, 'device_id', lambda device: pytest.fail('imported the printer'))
    monkeypatch.setattr(usbip, 'known_device_id', lambda device: told)
    assert devices.known_device_id('usbip://192.0.2.1') == told
    assert devices.known_device_id('tcp://192.0.2.1') == ''


def test_device_name_without_the_serial_number_of_the_address(monkeypatch):
    from app.labeldesigner.drivers import backends, escpos
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(backends, 'device_id', lambda device: 'MFG:Printer;CMD:ESC/POS;MDL:POS-58 AB12;')
    devices.usb_device_id('usb://0x0416:0x5011/AB12')
    assert escpos.DRIVER.device_name('usb://0x0416:0x5011/AB12') == 'POS-58'


@pytest.mark.parametrize('device_id, model, name', [
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-58;', 'Epson/ESC-POS', 'POS-58'),
    # Found by the search, of a model not known yet
    ('MFG:Printer;CMD:ESC/POS;MDL:POS-58;', 'Unknown', 'POS-58'),
    # A Brother is named by its model
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;', 'QL-700', None)])
def test_printer_that_did_not_answer_is_named_by_its_device_id(monkeypatch, tmp_path, device_id, model, name):
    (tmp_path / 'lp7').mkdir()
    (tmp_path / 'lp7' / 'ieee1284_id').write_text(device_id)
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / '{name}' / 'ieee1284_id'))
    monkeypatch.setattr(devices, 'status_cache', {})
    assert devices.failed_printer_state('file:///dev/usb/lp7', TimeoutError(), model).get('device_name') == name
