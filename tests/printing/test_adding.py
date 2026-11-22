"""What answers at the address of a printer to be added in the Settings,
asked without sending anything to the port it prints on before it is known
as a printer of a model this server prints on."""
import socket
import threading
import time

import pytest

from app.labeldesigner import adding, ipp
from app.labeldesigner.drivers import ipp as other, reach, told_model, usbip
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.drivers.vc500w import protocol as vc500w
from tests.helpers import within
from tests.printing.test_ipp import attribute, fake_printer, fixture, response  # noqa: F401
from tests.printing.test_other_printer import FakePrinter, printer_attributes
from tests.printing.test_reach import names  # noqa: F401
from tests.printing.test_usbip import FakeQL
from tests.printing.test_vc500w import FakeVC500W
from tests.printing.usbip_host import QL1100_ID, VC500W_ID, FakeHost, SharedDevice, TricklingHost

LASER = 'MFG:Brother;CMD:PJL,PCL,PCLXL,URF;MDL:MFC-L3760CDW series;CLS:PRINTER;'
TEXT = 0x41


@pytest.fixture(autouse=True)
def nothing_watched(monkeypatch):
    monkeypatch.setattr(reach, '_watched', {})


@pytest.fixture
def host():
    shared = FakeHost(SharedDevice())
    yield shared
    shared.close()


@pytest.fixture
def connections(monkeypatch):
    """The ports the server connects to over TCP, in their order."""
    ports = []
    real = reach.create_connection

    def recorded(address, timeout, source_address=None):
        ports.append(address[1])
        return real(address, timeout, source_address)
    monkeypatch.setattr(reach, 'create_connection', recorded)
    return ports


def identity(make_and_model: str = '', device_id: str = '') -> bytes:
    """An IPP answer that names a printer."""
    return response(*([attribute(TEXT, 'printer-make-and-model', make_and_model.encode())] if make_and_model else []),
                    *([attribute(TEXT, 'printer-device-id', device_id.encode())] if device_id else []))


def received(device, wait: float = 0.3) -> bytes:
    """What reached the printer behind a shared device."""
    device.printer.settimeout(wait)
    data = b''
    try:
        while chunk := device.printer.recv(65536):
            data += chunk
    except TimeoutError:
        pass
    return data


def let_go(device, resets: int = 1) -> bool:
    deadline = time.monotonic() + 2
    while device.resets < resets and time.monotonic() < deadline:
        time.sleep(0.01)
    return device.resets == resets


@pytest.mark.parametrize('device_id, model', [
    (QL1100_ID, 'QL-1100'),
    (VC500W_ID, 'VC-500W'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;', 'Epson/ESC-POS'),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-T88V;CLS:PRINTER;', 'Epson/ESC-POS-180'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;CLS:PRINTER;', 'Zebra/ZPL'),
    (LASER, None),
    ('', None),
])
def test_models_device_ids_name(device_id, model):
    assert told_model(device_id) == model


@pytest.mark.parametrize('typed, kept', [
    ('usbip://192.168.1.50', 'usbip://192.168.1.50'),
    (' USBIP://Printer-Pi/1-1.2 ', 'usbip://Printer-Pi/1-1.2'),
    ('usbip://127.0.0.1:3241', 'usbip://127.0.0.1:3241'),
    ('192.168.1.60', 'tcp://192.168.1.60'),
    ('printer.lan', 'tcp://printer.lan'),
    ('[fd00::6]', 'tcp://[fd00::6]'),
    ('TCP://192.168.1.60:9100', 'tcp://192.168.1.60:9100'),
    ('vc500w://192.168.1.70', 'vc500w://192.168.1.70'),
    ('/dev/usb/lp1', '/dev/usb/lp1'),
    ('file:///dev/usb/lp1', 'file:///dev/usb/lp1'),
    ('usb://0x04f9:0x2042/A1Z1', 'usb://0x04f9:0x2042/A1Z1'),
    ('IPP://Printer.local/ipp/print', 'ipp://Printer.local/ipp/print'),
    ('ipps://192.168.1.80:8631/ipp/print/zebra', 'ipps://192.168.1.80:8631/ipp/print/zebra'),
    ('ipp://[fd00::8]', 'ipp://[fd00::8]'),
    ('cups://DYMO_LabelWriter_450', 'cups://DYMO_LabelWriter_450'),
])
def test_addresses_as_kept(typed, kept):
    assert adding.address_of(typed) == kept


@pytest.mark.parametrize('typed, refusal', [
    ('', adding.NOT_AN_ADDRESS),
    ('simulation', adding.NOT_AN_ADDRESS),
    ('?', adding.NOT_AN_ADDRESS),
    ('http://192.168.1.60', adding.NOT_AN_ADDRESS),
    ('tcp://192.168.1.60:631', adding.NOT_AN_ADDRESS),
    ('usbip://printer pi', adding.NOT_AN_ADDRESS),
    ('/etc/passwd', adding.NOT_AN_ADDRESS),
    ('usbip://' + 'a' * 300, adding.NOT_AN_ADDRESS),
    ('bt://00:11:22:33:44:55', adding.BLUETOOTH),
    ('usbip://192.168.1.50:9100', adding.PRINT_PORT),
    ('usbip://192.168.1.50:631/1-1', adding.PRINT_PORT),
    ('usbip://192.168.1.50:515', adding.PRINT_PORT),
    ('usbip://192.168.1.50:6101', adding.PRINT_PORT),
    # IPP over HTTP to a raw port would be printed as text
    ('ipp://192.168.1.80:9100/ipp/print', adding.RAW_PORT),
    ('ipps://192.168.1.80:515', adding.RAW_PORT),
    ('ipp://192.168.1.80/ipp print', adding.NOT_AN_ADDRESS),
    ('ipp://192.168.1.80/ipp/print?x=1', adding.NOT_AN_ADDRESS),
    ('ipp://192.168.1.80:99999/ipp/print', adding.NOT_AN_ADDRESS),
    ('cups://queue/other', adding.NOT_AN_ADDRESS),
    # Only the paths of printers on IPP, not another server at the host
    ('ipp://127.0.0.1:2375/containers/labelburg/kill', adding.NOT_AN_ADDRESS),
    ('ipp://192.168.1.80/ipp/../admin', adding.NOT_AN_ADDRESS),
])
def test_addresses_refused(typed, refusal):
    with pytest.raises(ValueError) as refused:
        adding.address_of(typed)
    assert str(refused.value) == refusal


def test_hosts_of_addresses():
    assert adding.host_of('usbip://Printer-Pi:3241/1-1') == 'printer-pi'
    assert adding.host_of('usbip://[fd00::5]') == 'fd00::5'
    assert adding.host_of('tcp://192.168.1.60') == '192.168.1.60'
    assert adding.host_of('tcp://[fd00::6]:9100') == 'fd00::6'
    assert adding.host_of('vc500w://vc.local') == 'vc.local'
    for device in ('file:///dev/usb/lp0', '/dev/usb/lp1', 'usb://0x04f9:0x2042', 'bt://00:11:22:33:44:55', 'simulation'):
        assert adding.host_of(device) is None


def test_shared_printer_is_known_by_its_device_id(host):
    FakeQL(host.devices[0])
    found = adding.identify(host.address(), 'local')
    assert (found['address'], found['kind'], found['model']) == (host.address(), 'usbip', 'QL-1100')
    assert found['device_id'] == {'MFG': 'Brother', 'CMD': 'PT-CBP', 'MDL': 'QL-1100', 'CLS': 'PRINTER'}
    # Asked for its tape once it is known
    assert found['state']['media_width'] == 62
    # Let go again, the server keeps only the printers it uses
    assert let_go(host.devices[0])
    assert usbip.key(host.address()) not in usbip._printers


def test_shared_printer_to_be_added_is_kept(host):
    FakeQL(host.devices[0])
    adding.identify(host.address(), 'local', keep=True)
    assert usbip.key(host.address()) in usbip._printers
    assert host.devices[0].resets == 0


def test_shared_device_of_another_kind_gets_nothing():
    device = SharedDevice(device_id=LASER)
    shared = FakeHost(device)
    try:
        with pytest.raises(ValueError) as refused:
            adding.identify(shared.address(), 'local')
        assert str(refused.value) == adding.NO_PRINTER
        # Neither data nor a status request, only the requests that read its descriptor and device ID
        assert received(device) == b''
        assert {(kind, request) for kind, request, _, _ in device.requests} <= {(0x80, 6), (0xA1, 0), (0x00, 9), (0x01, 11)}
        assert let_go(device)
    finally:
        shared.close()


@pytest.mark.parametrize('shared, refusal', [
    (SharedDevice(interfaces=((8, 80),)), adding.NOT_SHARED),
    (SharedDevice(busid='1-2'), adding.NOT_SHARED),
])
def test_shared_devices_without_a_printer(shared, refusal):
    fake = FakeHost(shared)
    try:
        with pytest.raises(ValueError) as refused:
            adding.identify(fake.address('1-1') if shared.busid == '1-2' else fake.address(), 'local')
        assert str(refused.value) == refusal
    finally:
        fake.close()


def test_shared_printer_another_computer_uses(host):
    host.devices[0].used = True
    with pytest.raises(ValueError) as refused:
        adding.identify(host.address('1-1'), 'local')
    assert str(refused.value) == adding.SHARED_BUSY


def test_nothing_answers():
    with socket.create_server(('127.0.0.1', 0)) as closed:
        address = f'usbip://127.0.0.1:{closed.getsockname()[1]}'
    with pytest.raises(ValueError) as refused:
        adding.identify(address, 'local')
    assert str(refused.value) == adding.NO_ANSWER


@pytest.mark.parametrize('resolved, mode, refusal', [
    (['127.0.0.1', '8.8.8.8'], 'local', adding.ONLY_LOCAL),
    (['127.0.0.1', '169.254.1.2'], 'local', adding.NEVER),
    (['127.0.0.1', '169.254.1.2'], 'any', adding.NEVER),
    (['127.0.0.1'], 'off', adding.OFF),
    ([], 'local', adding.UNKNOWN_NAME),
])
def test_addresses_the_mode_refuses_are_not_asked(names, host, resolved, mode, refusal):  # noqa: F811
    names['printer.test'] = resolved
    with pytest.raises(ValueError) as refused:
        adding.identify(f'usbip://printer.test:{host.port}', mode)
    assert str(refused.value) == refusal
    assert host.connections == []


def test_any_takes_a_name_that_resolves_outside_too(names, host):  # noqa: F811
    FakeQL(host.devices[0])
    names['printer.test'] = ['127.0.0.1', '8.8.8.8']
    assert adding.identify(f'usbip://printer.test:{host.port}', 'any')['model'] == 'QL-1100'


def test_name_that_resolves_elsewhere_while_asked_is_not_followed(names, host):  # noqa: F811
    # Checked first, then asked at the same address only, never at one the check did not see
    answers = iter([['127.0.0.1'], ['8.8.8.8']])
    names['printer.test'] = lambda: next(answers, ['8.8.8.8'])
    with pytest.raises(ValueError) as refused:
        adding.identify(f'usbip://printer.test:{host.port}', 'local')
    assert str(refused.value) == adding.ONLY_LOCAL
    assert host.connections == []


def test_network_printer_known_over_ipp(fake_printer, connections):  # noqa: F811
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    found = adding.identify('tcp://127.0.0.1', 'local')
    assert (found['address'], found['kind'], found['model']) == ('tcp://127.0.0.1', 'network', 'QL-810W')
    assert (found['state']['media_width'], found['state']['media_length']) == (62, 29)
    assert 9100 not in connections


# As an office laser printer tells itself over IPP: formats "Other printer"
# takes, but no labels
LASER_OVER_IPP = printer_attributes(
    formats=('application/octet-stream', 'application/pdf', 'application/postscript', 'application/vnd.hp-PCL',
             'image/jpeg', 'image/pwg-raster', 'image/urf'),
    types=('sgray_8',), resolutions=(600,), default=600, loaded=(21590, 27940), media_type='stationery',
    name=b'HP LaserJet Pro M404dn', kinds=('document', 'envelope'), ready=('na_letter_8.5x11in', 'iso_a4_210x297mm')
)[:-1] + attribute(TEXT, 'printer-device-id', b'MFG:HP;MDL:HP LaserJet Pro M404dn;CMD:PJL,PCL,URF;') + b'\x03'


def test_network_printer_of_another_kind_gets_nothing(fake_printer, connections):  # noqa: F811
    fake_printer['body'] = identity('HP LaserJet Pro M404dn', 'MFG:HP;MDL:HP LaserJet Pro M404dn;CMD:PJL,PCL,URF;')
    with pytest.raises(ValueError) as refused:
        adding.identify('tcp://127.0.0.1', 'local')
    assert str(refused.value) == adding.NO_PRINTER
    assert connections and set(connections) == {network.IPP_PORT}


@pytest.mark.parametrize('on_ipp', [False, True])
def test_office_printer_is_an_office_printer(fake_printer, connections, on_ipp):  # noqa: F811
    # Paper and no labels, by its host or on IPP
    fake_printer['body'] = LASER_OVER_IPP
    found = adding.identify(f'ipp://127.0.0.1:{network.IPP_PORT}/ipp/print' if on_ipp else 'tcp://127.0.0.1', 'local')
    assert (found['model'], found['state']['device_name']) == ('Other/Office', 'HP LaserJet Pro M404dn')
    assert set(connections) == {network.IPP_PORT}


@pytest.mark.parametrize('answer', [
    # By its kind, by the names of its media, by its roll
    printer_attributes(loaded=None, kinds=('label',)),
    printer_attributes(loaded=None, ready=('oe_2.25x1.25-label_2.25x1.25in',)),
    printer_attributes(media_type='continuous'),
])
def test_label_printer_known_by_what_it_tells(fake_printer, answer):  # noqa: F811
    fake_printer['body'] = answer
    assert adding.identify('tcp://127.0.0.1', 'local')['model'] == 'Other/IPP-203'


def test_network_printer_that_tells_nothing_gets_nothing(monkeypatch, connections):
    # Only a print port, e.g. a receipt printer without IPP: never written to
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    monkeypatch.setattr(network, 'IPP_PORT', port)
    monkeypatch.setattr(network, 'SNMP_PORT', port)
    monkeypatch.setattr(network, '_snmp_unavailable', {})
    with pytest.raises(ValueError) as refused:
        adding.identify('tcp://127.0.0.1', 'local')
    assert str(refused.value) == adding.NO_PRINTER
    assert 9100 not in connections


@pytest.fixture
def vc(monkeypatch):
    printer = FakeVC500W()
    monkeypatch.setattr(vc500w, 'PORT', printer.port)
    yield printer
    printer.close()


@pytest.mark.parametrize('typed', ['vc500w://127.0.0.1', 'tcp://127.0.0.1', '127.0.0.1'])
def test_vc500w_known_over_ipp_then_asked_for_its_tape(fake_printer, vc, typed):  # noqa: F811
    fake_printer['body'] = identity('Brother VC-500W')
    found = adding.identify(typed, 'local')
    assert (found['address'], found['model']) == ('vc500w://127.0.0.1', 'VC-500W')
    assert found['state']['media_width'] == 25
    assert vc.status_reads


def test_vc500w_address_of_another_printer_gets_nothing(fake_printer, vc):  # noqa: F811
    # Its XML on the print port of a laser printer would be printed
    fake_printer['body'] = identity('Brother MFC-L3760CDW series', LASER)
    with pytest.raises(ValueError):
        adding.identify('vc500w://127.0.0.1', 'local')
    assert vc.commands == [] and vc.status_reads == 0


def test_network_printer_known_by_its_ipp_device_id(fake_printer, connections):  # noqa: F811
    fake_printer['body'] = identity('ZTC ZD421-203dpi ZPL', 'MFG:Zebra Technologies;CMD:ZPL;MDL:ZD421;')
    found = adding.identify('tcp://127.0.0.1', 'local')
    assert (found['address'], found['model']) == ('tcp://127.0.0.1', 'Zebra/ZPL')
    assert found['device_id']['CMD'] == 'ZPL'
    # The print port only once it is known
    assert connections.index(9100) > connections.index(network.IPP_PORT)


def test_usb_printer_known_by_its_device_id(tmp_path, monkeypatch):
    from app.labeldesigner.drivers import devices
    (tmp_path / 'id-lp7').write_text(QL1100_ID)
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'id-{name}'))
    found = adding.identify('/dev/usb/lp7', 'local')
    assert (found['address'], found['kind'], found['model']) == ('/dev/usb/lp7', 'usb', 'QL-1100')
    with pytest.raises(ValueError) as refused:
        adding.identify('/dev/usb/lp8', 'local')
    assert str(refused.value) == adding.NO_PRINTER


def test_identity_is_asked_for_over_ipp():
    request = ipp.decode_response(ipp.encode_request('ipp://192.0.2.1:631/ipp/print', adding.ASKED_OVER_IPP))
    # Also what tells whether it is one for "Other printer"
    assert request['requested-attributes'] == [
        'printer-make-and-model', 'printer-device-id', 'printer-state', 'printer-state-reasons', 'media-col-ready',
        'document-format-supported', 'pwg-raster-document-type-supported', 'pwg-raster-document-resolution-supported',
        'printer-resolution-default', 'printer-kind', 'media-ready', 'media-supported']


def test_network_printer_no_driver_claims_is_an_other_printer(fake_printer, connections):  # noqa: F811
    fake_printer['body'] = printer_attributes()
    found = adding.identify('tcp://127.0.0.1:9100', 'local')
    # Printed on over IPP, where it answered, never on its raw port
    assert (found['address'], found['kind'], found['model']) == (f'ipp://127.0.0.1:{network.IPP_PORT}/ipp/print', 'network', 'Other/IPP-203')
    assert (found['state']['media_width'], found['state']['media_length'], found['state']['device_name']) == (102, 152, 'Zebra ZD421')
    assert 9100 not in connections


def test_printer_at_an_ipp_address_is_an_other_printer(connections):
    printer = FakePrinter(printer_attributes(name=b'Brother QL-810W', resolutions=(300,), default=300))
    try:
        # Asked there, even where another driver would take its raw port
        found = adding.identify(printer.address, 'local')
        assert (found['address'], found['model'], found['state']['device_name']) == (printer.address, 'Other/IPP', 'Brother QL-810W')
        assert printer.operations() == [ipp.GET_PRINTER_ATTRIBUTES] and set(connections) == {printer.port}
    finally:
        printer.close()


def test_queue_of_cups_is_asked_on_this_computer(monkeypatch, connections):
    printer = FakePrinter(printer_attributes(formats=('application/pdf', 'image/png'), default=300))
    monkeypatch.setattr(ipp, 'PORT', printer.port)
    try:
        found = adding.identify('cups://DYMO_450', 'local')
        assert (found['address'], found['model']) == ('cups://DYMO_450', 'Other/IPP')
        assert printer.paths == ['/printers/DYMO_450']
    finally:
        printer.close()


@pytest.mark.parametrize('answer, refusal', [
    (printer_attributes(formats=('image/urf', 'application/octet-stream')), other.NO_FORMAT),
    # Not found at this path
    (response(status=0x0406), adding.NO_IPP),
])
def test_printer_on_ipp_that_cannot_be_added(answer, refusal):
    printer = FakePrinter(answer)
    try:
        with pytest.raises(ValueError) as refused:
            adding.identify(printer.address, 'local')
        assert str(refused.value) == refusal
    finally:
        printer.close()


def test_nothing_answers_at_an_ipp_address():
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        address = f'ipp://127.0.0.1:{unused.getsockname()[1]}/ipp/print'
    with pytest.raises(ValueError) as refused:
        adding.identify(address, 'local')
    assert str(refused.value) == adding.NO_ANSWER


def test_ipp_addresses_the_mode_refuses_are_not_asked(names, connections):  # noqa: F811
    names['printer.test'] = ['8.8.8.8']
    with pytest.raises(ValueError) as refused:
        adding.identify('ipp://printer.test/ipp/print', 'local')
    assert str(refused.value) == adding.ONLY_LOCAL
    assert connections == []


@pytest.mark.parametrize('device_id', [LASER, 'MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;CLS:PRINTER;'])
def test_shared_device_refused_for_adding_is_let_go(device_id):
    device = SharedDevice(device_id=device_id)
    shared = FakeHost(device)
    try:
        with pytest.raises(ValueError):
            adding.identify(shared.address(), 'local', keep=True)
        assert not usbip.connected(shared.address())
        assert let_go(device)
    finally:
        shared.close()


def test_shared_printer_whose_status_fails_unexpectedly_is_let_go(host, monkeypatch):
    FakeQL(host.devices[0])

    def broken(address, model):
        raise RuntimeError('a bug')
    monkeypatch.setattr(adding, '_state', broken)
    with pytest.raises(RuntimeError):
        adding.identify(host.address(), 'local', keep=True)
    assert not usbip.connected(host.address())


@pytest.mark.parametrize('stage', ['devlist', 'import'])
def test_identify_of_a_host_that_trickles_ends(monkeypatch, stage):
    monkeypatch.setattr(usbip, 'HANDSHAKE_TIMEOUT', 1)
    host = TricklingHost(stage)
    try:
        with pytest.raises(ValueError) as refused:
            within(5, lambda: adding.identify(host.address(), 'local'))
        assert str(refused.value) == adding.NO_ANSWER
    finally:
        host.close()


def test_identify_of_a_vc500w_that_never_stops_sending_ends():
    device = SharedDevice(product=0x2112, device_id=VC500W_ID, interfaces=((7, 4), (7, 2)))
    shared = FakeHost(device)

    def babble():
        try:
            while True:
                device.printer.sendall(b'x' * 4096)
        except OSError:
            pass
    threading.Thread(target=babble, daemon=True).start()
    try:
        found = within(10, lambda: adding.identify(shared.address(), 'local'))
        assert found['model'] == 'VC-500W' and found['state']['errors']
    finally:
        shared.close()
