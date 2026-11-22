"""Label printers the search finds that no driver prints on: found on USB
and Bluetooth without anything sent to them, told in /api/printer_status
with a report that names the kind of printer only."""
import os
from urllib.parse import parse_qs, urlsplit

import pytest

from app.labeldesigner import printer, unknown
from app.labeldesigner.drivers import backends, devices, niimbot
from tests.helpers import make_client
from tests.printing.test_bluetooth import NIIMBOT
from tests.printing.test_printer import _status_packet
from tests.printing.test_usb_backends import FOUND_USB_PRINTERS, Device, pyusb  # noqa: F401

BUILD = {'release': 'v8.0.0', 'commit': '42ef2db6aa', 'date': '2026-11-20 21:17 UTC'}
DYMO = 'MFG:DYMO;CMD: ;MDL:LabelWriter 450;CLS:PRINTER;DES:DYMO LabelWriter 450;SN:12345678901;SERN:AB1234567;'
ROLLO = 'MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;'
LASER = 'MFG:Brother;CMD:PJL,PCL,PCLXL,URF;MDL:HL-L2350DW series;CLS:PRINTER;'
INKJET = 'MFG:EPSON;CMD:ESCPL2,BDC,D4,D4PX,ESCPR7,END4,GENEP;MDL:XP-2100 Series;CLS:PRINTER;'
QL700 = 'MFG:Brother;CMD:PT-CBP;MDL:QL-700;CLS:PRINTER;'
# Lasers the computer draws for, a printer telling PJL only, with their USB IDs
HOST_BASED = [('MFG:Brother;CMD:PJL,HBP;MDL:HL-1110 series;CLS:PRINTER;', (0x04f9, 0x0054)),
              ('MFG:Samsung;CMD:SPL,FWV,PIC,BDN,EXT;MDL:ML-2160 Series;CLS:PRINTER;', (0x04e8, 0x330f)),
              ('MFG:Hewlett-Packard;MDL:HP LaserJet 1020;CMD:ACL;CLS:PRINTER;DES:HP LaserJet 1020;', (0x03f0, 0x2b17)),
              ('MFG:Canon;MDL:LBP2900;CMD:CAPT;VER:1.0;CLS:PRINTER;DES:Canon LBP2900;', (0x04a9, 0x2676)),
              ('MFG:Generic;MDL:GDI Printer;CMD:GDI;CLS:PRINTER;', (0x0483, 0x5743)),
              ('MFG:KYOCERA;MDL:FS-1020D;CMD:PJL;CLS:PRINTER;', (0x0482, 0x0491))]
MAC = '00:00:5E:00:53:01'
STATUS_REQUEST = b'\x1b\x69\x53'


def told(found):
    """Everything a printer found is told with: its report and the form."""
    entry = unknown.entry(found, BUILD)
    return entry['report'] + '\n' + entry['report_url'] + '\n' + str(parse_qs(urlsplit(entry['report_url']).query))


def test_a_usb_printer_is_reported_by_its_ids_and_the_fields_naming_its_model():
    found = unknown.on_usb(DYMO, (0x0922, 0x0020))
    assert found == {'where': 'usb', 'name': 'DYMO LabelWriter 450', 'usb_id': '0922:0020',
                     'device_id': {'MFG': 'DYMO', 'MDL': 'LabelWriter 450', 'CLS': 'PRINTER', 'DES': 'DYMO LabelWriter 450'}}
    assert unknown.report(found, BUILD) == '\n'.join([
        'Version: v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC', 'Printer: DYMO LabelWriter 450', 'Connection: USB',
        'USB ID: 0922:0020', 'Device ID: MFG:DYMO;MDL:LabelWriter 450;CLS:PRINTER;DES:DYMO LabelWriter 450;'])


def test_the_form_for_new_printers_is_filled_in():
    found = unknown.on_usb(DYMO, (0x0922, 0x0020))
    url = urlsplit(unknown.report_url(found, BUILD))
    assert (url.scheme, url.netloc, url.path) == ('https', 'github.com', '/DL6ER/labelburg/issues/new')
    assert parse_qs(url.query) == {
        'template': ['new_printer.yml'], 'title': ['New printer: DYMO LabelWriter 450'],
        'version': ['v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC'], 'printer': ['DYMO LabelWriter 450'],
        'found': ['Connection: USB\nUSB ID: 0922:0020\nDevice ID: MFG:DYMO;MDL:LabelWriter 450;CLS:PRINTER;DES:DYMO LabelWriter 450;']}
    # Its fields are those of the form
    with open(os.path.join(os.path.dirname(__file__), '..', '..', '.github', 'ISSUE_TEMPLATE', 'new_printer.yml')) as form:
        text = form.read()
    assert all(f'id: {field}\n' in text for field in ('version', 'printer', 'found'))


def test_a_long_device_id_keeps_the_address_short():
    found = unknown.on_usb('MFG:' + 'A' * 5000 + ';MDL:' + 'B' * 5000 + ';DES:' + '\u00e4' * 5000 + ';', (0x0922, 0x0020))
    assert all(len(value) <= unknown.LONGEST for value in found['device_id'].values())
    assert len(unknown.report_url(found, BUILD)) < 8000


# Free text after a model, each with what it must not tell
FREE = [
    ('S/N D4J123456789', 'D4J123456789'), ('000L9Z378866', '000L9Z378866'), ('SN 1234-5678-9012', '5678'),
    ('SN=AB12CD34EF', 'AB12CD34EF'), ('sn:XQ-12345', 'XQ-12345'), ('Serial number XQ-1', 'XQ-1'),
    ('MAC F4F5E8AABBCC', 'F4F5E8AABBCC'), ('F4F5E8AABBCC', 'F4F5E8AABBCC'), ('on 00-00-5E-00-53-01', '00-00-5E'),
    ('at ' + MAC, '00:00:5E'), ('00005E005301', '00005E005301'), ('00 00 5E 00 53 01', '5E 00 53'),
    ('0000.5E00.5301', '5E00'), ('192.168.1.50', '192.168.1.50'), ('fe80::5efe:c000:201', '5efe'),
    ('IP ::ffff:192.168.1.5', 'ffff'), ('::ffff:192.168.1.5', '192.168'), ('labels.fritz.box', 'fritz.box'),
    ('shop-printer.local', 'shop-printer'), ('host labelprinter', 'labelprinter'), ('Owner Anna Schmidt', 'Anna'),
    ('/home/anna/labels', 'anna'), ('C:\\Users\\anna', 'anna'),
]


@pytest.mark.parametrize('text, secret', FREE)
@pytest.mark.parametrize('field', ['DES', 'MDL'])
def test_no_report_tells_a_serial_number_an_address_or_free_text(field, text, secret):
    device_id = f'MFG:TSC;MDL:TE210;DES:{text};' if field == 'DES' else f'MFG:TSC;MDL:TE210 {text};'
    found = unknown.on_usb(device_id, (0x1203, 0x0140))
    assert secret.lower() not in told(found).lower()
    assert found['name'].startswith('TSC TE210') and 'DES' not in found['device_id']


@pytest.mark.parametrize('device_id, secret', [
    # Fields not naming the model, e.g. a path or a serial number
    (DYMO, '12345678901'), (DYMO, 'AB1234567'),
    ('MFG:TSC;MDL:TE210;SN:000L9Z378866;', 'L9Z378866'),
    ('MFG:TSC;MDL:TE210;SERN:D4J12345;', 'D4J12345'),
    ('MFG:TSC;MDL:TE210;UID:/dev/usb/lp0;', '/dev/usb/lp0'),
    ('MFG:TSC;MDL:TE210;CID:Office label printer;', 'Office label printer'),
    # A description that tells more than maker and model
    ('MFG:TSC;MDL:TE210;DES:TSC TE210 of the shop;', 'shop'),
])
def test_no_report_tells_other_fields(device_id, secret):
    found = unknown.on_usb(device_id, (0x1203, 0x0140))
    assert secret.lower() not in told(found).lower()


def test_a_description_repeating_maker_and_model_is_told():
    assert unknown.on_usb('MFG:Zebra;MDL:ZD421;DES:ZEBRA zd421;', None)['device_id']['DES'] == 'ZEBRA zd421'


def test_a_usb_device_without_a_device_id_is_named_by_its_maker():
    assert unknown.on_usb('', (0x0922, 0x1001))['name'] == 'DYMO LabelManager PnP'
    assert unknown.on_usb('', (0x1203, 0x0140))['name'] == 'TSC printer'
    assert unknown.on_usb('', (0x1234, 0x5678))['name'] == 'Printer on USB'
    assert unknown.on_usb('', None) == {'where': 'usb', 'name': 'Printer on USB', 'usb_id': '', 'device_id': {}}
    assert unknown.report(unknown.on_usb('', None), {}) == 'Version: A development version\nPrinter: Printer on USB\nConnection: USB'


@pytest.mark.parametrize('device_id, office', [
    (LASER, True), (INKJET, True), ('MFG:Canon;CMD:BJL,BJRaster3,BSCCe,IVEC;MDL:MG3600 series;', True),
    ('MFG:HP;CMD:PJL,PML,POSTSCRIPT,PCLXL,PCL;MDL:LaserJet;', True), ('MFG:Apple;CMD:URF;', True),
    *[(device_id, True) for device_id, _ in HOST_BASED],
    (DYMO, False), (ROLLO, False), (QL700, False), ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;', False), ('', False),
    # A label printer that takes PJL jobs too
    ('MFG:Brother;CMD:PT-CBP,PJL;MDL:TD-4550DNWB;', False)])
def test_office_printers_are_known_by_their_commands(device_id, office):
    assert unknown.office(devices.device_id_fields(device_id)) is office


@pytest.mark.parametrize('device_id, ids, label', [
    *[(device_id, ids, False) for device_id, ids in HOST_BASED],
    # By the maker's vendor ID or name, or by the commands of label printers
    ('', (0x0922, 0x1001), True), ('MFG:DYMO;CMD: ;MDL:LabelWriter 450;', None, True),
    ('MFG:Zebra Technologies;MDL:ZTC GK420t;', None, True), ('MFG:NIIMBOT;CMD:ESC;MDL: K9;', (0x3513, 0x0002), True),
    (ROLLO, None, True), ('MFG:Acme;CMD:EPL2;MDL:LP 2844;', None, True), ('MFG:Acme;CMD:ESCPOS;MDL:P58;', None, True),
    ('MFG:DatamaxONeil ;CMD:DPL;MDL:E4204B Mark III;', None, True),
    # Neither, e.g. a receipt printer of a maker without a driver here
    ('', (0x0416, 0x5011), False), ('', None, False), ('MFG:Acme;CMD:FOO;MDL:Bar;', (0x1234, 0x5678), False),
    ('', (0x0922, 0x8003), False)])
def test_label_printers_are_known_by_their_maker_or_commands(device_id, ids, label):
    assert unknown.label_printer(devices.device_id_fields(device_id), ids) is label


def test_devices_of_makers_of_label_printers():
    assert unknown.label_maker((0x0922, 0x1001)) and unknown.label_maker((0x3513, 0x0001))
    # DYMO's postal scales, a device of another maker
    assert not unknown.label_maker((0x0922, 0x8003)) and not unknown.label_maker((0x04b8, 0x0e15))


def le(name, *services):
    return {'address': MAC, 'name': name, 'le': True, 'services': list(services)}


def classic(name):
    return {'address': MAC, 'name': name, 'le': False, 'services': [], 'classic': True, 'device_class': 0x040680}


NIIMBOT_UUID = 'e7810a71-73ae-499d-8c15-faa9aef0c3f2'


@pytest.mark.parametrize('device, found', [
    (le('B21-H425040311', NIIMBOT),
     {'where': 'bluetooth', 'name': 'Niimbot B21', 'connection': 'Bluetooth LE', 'bluetooth_name': 'B21', 'services': [NIIMBOT_UUID]}),
    (le('D11_H-G1234567'),
     {'where': 'bluetooth', 'name': 'Niimbot D11_H', 'connection': 'Bluetooth LE', 'bluetooth_name': 'D11_H', 'services': []}),
    # A model of two characters with Niimbot's service only
    (le('K3-H1234567', NIIMBOT, bytes(range(16))),
     {'where': 'bluetooth', 'name': 'Niimbot K3', 'connection': 'Bluetooth LE', 'bluetooth_name': 'K3',
      'services': [NIIMBOT_UUID, '0f0e0d0c-0b0a-0908-0706-050403020100']}),
    # Niimbot's service and a name as Niimbot gives them, of no model known
    (le('ANNA-H1234567', NIIMBOT),
     {'where': 'bluetooth', 'name': 'Niimbot printer', 'connection': 'Bluetooth LE', 'bluetooth_name': '', 'services': [NIIMBOT_UUID]}),
    (classic('PT-P710BT6795'),
     {'where': 'bluetooth', 'name': 'Brother PT-P710BT', 'connection': 'classic Bluetooth', 'bluetooth_name': 'PT-P710BT', 'services': []}),
    # A model Labelburg prints on over USB and the network only
    (classic('QL-820NWB1234'),
     {'where': 'bluetooth', 'name': 'Brother QL-820NWB over Bluetooth', 'connection': 'classic Bluetooth',
      'bluetooth_name': 'QL-820NWB', 'services': []}),
    # Models Labelburg prints on, names of other devices with Niimbot's
    # service, e.g. one renamed, and other devices
    (le('D110-H1234567', NIIMBOT), None), (le('D110_M-H1234567', NIIMBOT), None),
    (le('KITCHEN', NIIMBOT), None), (le('ANNA', NIIMBOT), None), (le('MPT-II', NIIMBOT), None),
    (le('H1234567890', NIIMBOT), None), (le('', NIIMBOT), None), (le('B21-SHELF', NIIMBOT), None),
    (le('S1-H1234567'), None), (le('B21'), None), (le('Phone'), None), (le('ANNA-H1234567'), None),
])
def test_label_printers_on_bluetooth_by_their_name_or_service(device, found):
    assert unknown.on_bluetooth(device) == found


def test_a_brother_model_printed_on_over_bluetooth_is_left_out(monkeypatch):
    from app.labeldesigner.drivers.brother.models import MODELS, Model
    monkeypatch.setitem(MODELS, 'PT-P710BT', Model('PT-P710BT', bluetooth=True))
    assert unknown.on_bluetooth(classic('PT-P710BT6795')) is None
    assert unknown.on_bluetooth(classic('PT-P910BT6795'))['name'] == 'Brother PT-P910BT'


@pytest.mark.parametrize('name, secret', [('B21-H425040311', 'H425040311'), ('B21-H425040311', 'H425'),
                                          ('PT-P710BT6795', '6795'), ('ANNA-H1234567', 'ANNA'),
                                          ('ANNA-H1234567', 'H1234567'), ('D11_H-' + MAC.replace(':', ''), '5E00')])
def test_no_bluetooth_report_tells_the_serial_number_or_the_address(name, secret):
    found = unknown.on_bluetooth(le(name, NIIMBOT))
    assert secret not in told(found)
    assert MAC not in told(found) and MAC.replace(':', '') not in told(found)


def test_a_bluetooth_report():
    found = unknown.on_bluetooth({'address': MAC, 'name': 'B21-H425040311', 'le': True, 'classic': True, 'services': [NIIMBOT]})
    assert unknown.report(found, BUILD) == '\n'.join([
        'Version: v8.0.0, commit 42ef2db, built 2026-11-20 21:17 UTC', 'Printer: Niimbot B21',
        'Connection: Bluetooth LE and classic Bluetooth', 'Bluetooth name: B21', 'Services: e7810a71-73ae-499d-8c15-faa9aef0c3f2'])


# --- Detection ---------------------------------------------------------------------


class UsbPrinter:
    """usblp's device of a printer, which answers Brother's status request
    as a QL printer. Each write is kept with its device."""
    written = []

    def __init__(self, device):
        self.device, self.answers = device, []

    def write(self, data):
        UsbPrinter.written.append((self.device, data))
        if data == STATUS_REQUEST:
            self.answers.append(_status_packet(0x00))

    def read(self, length=32):
        return self.answers.pop(0) if self.answers else b''

    def dispose(self):
        pass


@pytest.fixture
def usblp(tmp_path, monkeypatch):
    """Device files /dev/usb/lp<N> plugged in with the device IDs and USB IDs
    given, and the label designer with PRINTER_PRINTER '?'."""
    monkeypatch.setattr(UsbPrinter, 'written', [])
    monkeypatch.setattr(backends, 'KernelPrinter', UsbPrinter)
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'id-{name}'))
    monkeypatch.setattr(devices, 'USB_DESCRIPTOR', str(tmp_path / '{name}-{field}'))
    paths = []
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', paths)
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path in paths or real_exists(path))
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'

    def plug(name, device_id='', ids=None):
        paths.append(f'/dev/usb/{name}')
        if device_id:
            (tmp_path / f'id-{name}').write_text(device_id + '\n')
        for field, value in zip(('idVendor', 'idProduct'), ids or ()):
            (tmp_path / f'{name}-{field}').write_text(f'{value:04x}\n')
    return plug, client


def status(client, query=''):
    return client.get('/labeldesigner/api/printer_status' + query).get_json()


def test_usblp_printer_of_another_maker_is_not_sent_brothers_status_request(usblp):
    plug, client = usblp
    plug('lp0', DYMO, (0x0922, 0x0020))
    plug('lp1', QL700, (0x04f9, 0x2042))
    plug('lp2')
    told = status(client)
    assert [p['path'] for p in told['printers']] == ['file:///dev/usb/lp1', 'file:///dev/usb/lp2', 'simulation']
    assert UsbPrinter.written == [('file:///dev/usb/lp1', STATUS_REQUEST), ('file:///dev/usb/lp2', STATUS_REQUEST)]
    [dymo] = told['unknown']
    assert dymo == unknown.entry(unknown.on_usb(DYMO, (0x0922, 0x0020)), client.application.extensions['build'])
    assert set(dymo) == {'where', 'name', 'report', 'report_url'} and (dymo['where'], dymo['name']) == ('usb', 'DYMO LabelWriter 450')
    assert 'USB ID: 0922:0020' in dymo['report'] and 'lp0' not in str(told['unknown'])


def test_office_printer_on_usblp_is_neither_asked_nor_reported(usblp):
    plug, client = usblp
    plug('lp0', LASER, (0x04f9, 0x0042))
    plug('lp1', INKJET, (0x04b8, 0x1188))
    told = status(client)
    assert [p['path'] for p in told['printers']] == ['simulation'] and told['unknown'] == []
    assert UsbPrinter.written == []


@pytest.mark.parametrize('device_id, ids', HOST_BASED)
def test_office_printer_drawn_for_by_the_computer_is_neither_asked_nor_reported(usblp, device_id, ids):
    plug, client = usblp
    plug('lp0', device_id, ids)
    told = status(client)
    assert [p['path'] for p in told['printers']] == ['simulation'] and told['unknown'] == []
    assert UsbPrinter.written == []


def test_usblp_printer_without_a_device_id_is_asked_only_where_it_may_be_a_brother(usblp):
    plug, client = usblp
    # A POS-58 clone, which takes ESC i as a cut; a Brother; one of no known maker; a DYMO
    plug('lp0', ids=(0x0416, 0x5011))
    plug('lp1', ids=(0x04f9, 0x2042))
    plug('lp2')
    plug('lp3', ids=(0x0922, 0x0020))
    told = status(client)
    assert [p['path'] for p in told['printers']] == ['file:///dev/usb/lp1', 'file:///dev/usb/lp2', 'simulation']
    assert UsbPrinter.written == [('file:///dev/usb/lp1', STATUS_REQUEST), ('file:///dev/usb/lp2', STATUS_REQUEST)]
    assert [found['name'] for found in told['unknown']] == ['DYMO printer']


def test_usblp_printer_of_no_maker_of_label_printers_is_neither_asked_nor_reported(usblp):
    plug, client = usblp
    plug('lp0', 'MFG:Acme;CMD:FOO;MDL:Bar;', (0x1234, 0x5678))
    told = status(client)
    assert [p['path'] for p in told['printers']] == ['simulation'] and told['unknown'] == []
    assert UsbPrinter.written == []


def test_usblp_printer_without_its_usb_ids_is_reported(usblp):
    plug, client = usblp
    plug('lp0', ROLLO)
    [rollo] = status(client)['unknown']
    assert rollo['name'] == 'Thermal Printer' and 'USB ID' not in rollo['report']
    assert UsbPrinter.written == []


def test_unknown_printers_only_where_printers_are_searched_for(usblp):
    plug, client = usblp
    plug('lp0', DYMO, (0x0922, 0x0020))
    plug('lp1', QL700, (0x04f9, 0x2042))
    assert len(status(client)['unknown']) == 1
    # One printer asked alone tells nothing more
    assert 'unknown' not in status(client, '?printer=file:///dev/usb/lp1')
    for config in ({'PRINTER_PRINTER': 'simulation'}, {'PRINTERS': [('QL-700', 'simulation')]}):
        client.application.config.update(config)
        assert status(client)['unknown'] == []


@pytest.fixture
def detected(pyusb, tmp_path, monkeypatch):  # noqa: F811
    """What the printer status tells with PRINTER_PRINTER '?', PyUSB faked."""
    monkeypatch.setattr(devices, 'found_usb_printers', FOUND_USB_PRINTERS)
    monkeypatch.setattr(devices, 'found_usb_devices', backends.found_devices)
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    return lambda: client.get('/labeldesigner/api/printer_status').get_json()


def _untouched(device):
    """Nothing was sent to a device, not even a request for its device ID."""
    device.ctrl_transfer = lambda *args: pytest.fail(f'Asked {args}')
    return device


def test_printer_on_pyusb_no_driver_prints_on_is_reported(detected, pyusb):  # noqa: F811
    rollo = Device(0x0081, vendor=0x0a5f, serial='S1234567890', device_id=ROLLO)
    laser = Device(0x0042, serial='E78123', device_id=LASER)
    others = [Device(ids[1], vendor=ids[0], serial=f'X{i}', device_id=device_id) for i, (device_id, ids) in enumerate(HOST_BASED)]
    others.append(Device(0x5678, vendor=0x1234, serial='Y1', device_id='MFG:Acme;CMD:FOO;MDL:Bar;'))
    pyusb.extend([rollo, laser, *others])
    told = detected()
    assert [p['path'] for p in told['printers']] == ['simulation']
    [found] = told['unknown']
    assert (found['name'], 'USB ID: 0a5f:0081' in found['report']) == ('Thermal Printer', True)
    assert 'S1234567890' not in str(found) and 'E78123' not in str(told['unknown'])
    assert all(device.endpoints[1].written == [] for device in [rollo, laser, *others])


def test_other_devices_of_makers_of_label_printers_are_reported(detected, pyusb, tmp_path, monkeypatch):  # noqa: F811
    # A Niimbot reached as a serial port, a DYMO LabelManager PnP, a DYMO
    # scale, a device of another maker, one shared over USB/IP
    usbip_host = tmp_path / 'usbip-host'
    (usbip_host / '3-2').mkdir(parents=True)
    monkeypatch.setattr(backends, 'USBIP_HOST', str(usbip_host))
    niimbot_, labelmanager = Device(0x0001, vendor=0x3513, classes=(2, 10)), Device(0x1001, vendor=0x0922, classes=(3, 8))
    scale, other, shared = Device(0x8003, vendor=0x0922, classes=(3,)), Device(0x0001, vendor=0x1234, classes=(2, 10)), \
        Device(0x0002, vendor=0x1203, classes=(2, 10))
    for device, port in zip((niimbot_, labelmanager, scale, other, shared), ((1,), (2,), (3,), (4,), None)):
        device.bus, device.port_numbers = (3, (2,)) if port is None else (1, port)
        _untouched(device)
    pyusb.extend([niimbot_, labelmanager, scale, other, shared])
    assert [(found['where'], found['name']) for found in detected()['unknown']] == [('usb', 'Niimbot printer'), ('usb', 'DYMO LabelManager PnP')]
    assert all(device.calls == [] and device.endpoints[1].written == [] for device in (niimbot_, labelmanager, scale, other, shared))


def test_a_driver_reaching_a_device_as_a_serial_port_claims_it(detected, pyusb, monkeypatch):  # noqa: F811
    monkeypatch.setattr(niimbot.DRIVER, 'usb_serial_ids', ((0x3513, 0x0001),))
    pyusb.append(_untouched(Device(0x0001, vendor=0x3513, classes=(2, 10))))
    assert detected()['unknown'] == []


def test_the_same_printer_twice_is_reported_once(detected, pyusb):  # noqa: F811
    pyusb.extend([Device(0x0081, vendor=0x0a5f, serial='S1', device_id=ROLLO),
                  Device(0x0081, vendor=0x0a5f, serial='S2', device_id=ROLLO)])
    assert len(detected()['unknown']) == 1


@pytest.fixture
def nearby(tmp_path, monkeypatch):
    """The automatic detection with devices nearby on Bluetooth."""
    devices_found = []
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', True)
    monkeypatch.setattr(printer.bluetooth, 'discover', lambda timeout, classic=False: devices_found)
    monkeypatch.setattr(printer, '_bluetooth', {})
    monkeypatch.setattr(printer, '_bluetooth_searched', None)
    monkeypatch.setattr(printer, '_bluetooth_thread', None)
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'looked', 0.0)
    monkeypatch.setattr(niimbot.DRIVER, 'status', lambda device, model, unconfirmed=False: {
        'errors': [], 'path': device, 'model': model, 'status_type': 'Reply to status request'})
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'

    def status(query=''):
        client.get('/labeldesigner/api/printer_status' + query)
        printer._bluetooth_thread.join(5)
        # The search may end before the scan that started it
        printer._last_scan_ts = 0
        return client.get('/labeldesigner/api/printer_status').get_json()
    return devices_found, status


def test_label_printers_on_bluetooth_no_driver_prints_on_are_reported(nearby):
    devices_found, status = nearby
    devices_found += [{'address': MAC, 'name': 'B21-H425040311', 'le': True, 'services': [NIIMBOT]},
                      {'address': '00:00:5E:00:53:02', 'name': 'B1-TEST0001', 'le': True, 'services': [NIIMBOT]},
                      {'address': '00:00:5E:00:53:03', 'name': 'Phone', 'le': True, 'services': []}]
    told = status()
    assert [p['path'] for p in told['printers']] == ['bt://00:00:5E:00:53:02', 'simulation']
    [b21] = told['unknown']
    assert (b21['where'], b21['name']) == ('bluetooth', 'Niimbot B21')
    assert 'Bluetooth name: B21\n' in b21['report'] and MAC not in str(b21) and 'H425040311' not in str(b21)


def test_a_printer_on_bluetooth_the_next_search_does_not_find_is_no_longer_reported(nearby, monkeypatch):
    devices_found, status = nearby
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    devices_found.append({'address': MAC, 'name': 'B21-H425040311', 'le': True, 'services': []})
    assert [found['name'] for found in status()['unknown']] == ['Niimbot B21']
    devices_found.clear()
    assert status('?look=1')['unknown'] == []


def test_searching_for_other_devices_without_libusb_finds_none(monkeypatch):
    def no_libusb():
        raise backends.NoLibusb('No backend available')
    monkeypatch.setattr(backends, '_libusb', no_libusb)
    assert backends.found_devices(unknown.LABEL_MAKERS) == []
