"""Label printers on ZPL: the bytes of a label, sent to a device file and
over the network, and printing and status through the label designer."""
import json
import socket
import threading
import time

import pytest

from app.labeldesigner.drivers import backends, devices, zpl
from app.labeldesigner.drivers.zpl import protocol
from tests.helpers import make_client, outcome, without_status

DEVICE = 'file:///dev/usb/lp_zpl'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, model='Zebra/ZPL', printer=DEVICE)


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(protocol, 'write', lambda device, data: out.append((device, data)))
    return out


def test_label_as_a_graphic_field():
    # As labelprint sent its labels to a Datamax-O'Neil E-4204B
    assert protocol.label([b'\x80\x00', b'\x00\x01'], 2) == b'^XA\n^PW16\n^LL2\n^FO0,0^GFA,4,4,2,80000001\n^XZ\n'


@pytest.mark.parametrize('device, address', [('tcp://zebra.lan', ('zebra.lan', 9100)), ('tcp://192.0.2.1:6101', ('192.0.2.1', 6101)),
                                             ('tcp://[2001:db8::1]', ('2001:db8::1', 9100)), ('tcp://[2001:db8::1]:9100', ('2001:db8::1', 9100))])
def test_network_address(device, address):
    assert devices.tcp_address(device, protocol.PORT) == address


def test_label_is_printed_as_large_as_it_is(client, sent):
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl102x76', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    (device, data), = sent
    assert device == DEVICE
    assert data.startswith(b'^XA\n^PW816\n^LL608\n^FO0,0^GFA,62016,62016,102,') and data.endswith(b'\n^XZ\n')
    # Black is printed
    assert b'F' in data


def test_wider_label_than_the_printhead(client, sent):
    # A6 labels are 105 mm wide, a GK420t prints 104 mm
    client.post('/labeldesigner/api/print', data={'label_size': 'zpl105x148', 'print_type': 'text', 'text': TEXT})
    assert sent[0][1].startswith(b'^XA\n^PW832\n^LL1184\n')


def test_copies_are_one_label_each(client, sent):
    client.post('/labeldesigner/api/print', data={'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert sent[0][1].count(b'^XA') == 2


def test_rows_end_of_a_lying_label_first():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # A 102 x 152 mm label drawn lying, its start black
            img = Image.new('RGB', (1216, 816), 'white')
            img.paste((0, 0, 0), (0, 0, 10, 816))
            return img
    _, rows, width = zpl.label_rows(_Label(), 'zpl102x152')
    assert (len(rows), width) == (1216, 102)
    # Its end leaves the printer first, as on the other printers, the last
    # row of a ZPL label does
    assert all(byte == 0xff for byte in rows[0]) and not any(rows[-1])


def test_rows_top_of_a_label_wider_than_long_first():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # A 102 x 51 mm label, its top black
            img = Image.new('RGB', (816, 408), 'white')
            img.paste((0, 0, 0), (0, 0, 816, 10))
            return img
    _, rows, width = zpl.label_rows(_Label(), 'zpl102x51')
    assert (len(rows), width) == (408, 102)
    # Its top leaves the printer first, as on the other printers, the last
    # row of a ZPL label does
    assert not any(rows[0]) and all(byte == 0xff for byte in rows[-1])


def test_label_over_the_network(client, monkeypatch):
    without_status(monkeypatch)
    server = socket.create_server(('127.0.0.1', 0))
    received = []

    def accept():
        conn, _ = server.accept()
        with conn:
            while data := conn.recv(65536):
                received.append(data)
    thread = threading.Thread(target=accept)
    thread.start()
    monkeypatch.setattr(protocol, 'PORT', server.getsockname()[1])
    device = 'tcp://127.0.0.1'
    response = client.post('/labeldesigner/api/print', data={
        'printer': device, 'label_size': 'zpl51x25', 'print_type': 'text', 'text': TEXT})
    thread.join(5)
    server.close()
    assert outcome(client, response) == ('printed', '')
    assert b''.join(received).startswith(b'^XA\n^PW408\n^LL200\n')


def test_printer_on_usb_that_is_not_there(client, sent):
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer is switched off or not connected']
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT})
    # Printing does not ask it
    assert outcome(client, response) == ('printed', '')


def test_600_dpi_is_refused(client, sent):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    assert response.get_json() == {'success': False, 'message': 'The Zebra ZPL printer cannot print at 600 dpi, turn off 600 dpi printing'}
    assert sent == []


# As a Datamax-O'Neil E-4204B with 100 x 30 mm labels answered ~HS
DATAMAX_HS = (b'\x02030,0,0,0235,044,0,0,0,000,0,0,0\x03\r\n'
              b'\x02000,0,0,0,0,1,5,0,00000000,1,000\x03\r\n\x020000,0\x03\r\n')


def test_host_status():
    assert protocol.parse_host_status(DATAMAX_HS) == {
        'paper_out': False, 'paused': False, 'length': 235, 'under_temperature': False,
        'over_temperature': False, 'head_up': False, 'ribbon_out': False}
    assert protocol.parse_host_status(b'') is None


@pytest.mark.parametrize('length, size', [(235, None), (1214, (102, 152)), (1184, (105, 148)), (500, None)])
def test_labels_by_their_length(length, size):
    # 4 x 6 in labels measured 1214 dots on a GK420t
    assert zpl.loaded(length) == size


def _answering(monkeypatch, answer):
    server = socket.create_server(('127.0.0.1', 0))

    def serve():
        conn, _ = server.accept()
        with conn:
            assert conn.recv(16) == b'~HS'
            conn.sendall(answer)
    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(protocol, 'PORT', server.getsockname()[1])
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.5)
    return thread, server


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    from app.labeldesigner.drivers import devices
    monkeypatch.setattr(zpl, '_ask_after', {})
    monkeypatch.setattr(devices, 'status_cache', {})


def test_status_over_the_network(monkeypatch):
    # The head of a GK420t with 4 x 6 in labels open
    answer = DATAMAX_HS.replace(b'0235', b'1214').replace(b'000,0,0,0,0', b'000,0,1,0,0')
    thread, server = _answering(monkeypatch, answer)
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == ['The printhead is open']
    assert (state['media_width'], state['media_length']) == (102, 152)


def test_only_the_length_of_labels_of_several_widths(monkeypatch):
    # 235 dots are 30 mm, which labels 100, 50 and 40 mm wide are long
    thread, server = _answering(monkeypatch, DATAMAX_HS)
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert (state['media_width'], state['media_length']) == (None, 30)


def test_status_without_an_answer(monkeypatch):
    thread, server = _answering(monkeypatch, b'')
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == [] and state['media_width'] is None


def test_asked_at_most_every_10_s_then_silence_is_an_error(monkeypatch):
    thread, server = _answering(monkeypatch, DATAMAX_HS.replace(b'0235', b'1214'))
    assert zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')['media_length'] == 152
    thread.join(5)
    server.close()
    # Not asked again within 10 s, told as it was
    assert zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')['media_length'] == 152
    # A Datamax with its printhead open does not answer
    monkeypatch.setitem(zpl._ask_after, 'tcp://127.0.0.1:9100', 0)
    thread, server = _answering(monkeypatch, b'')
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == [zpl.NO_ANSWER] and state['media_length'] == 152


def test_a_printer_that_never_answered_is_asked_at_most_every_10_s(monkeypatch, tmp_path):
    device = tmp_path / 'lp0'
    device.write_bytes(b'')
    asked = []
    monkeypatch.setattr(protocol, 'host_status', lambda device: asked.append(device) or b'')
    for _ in range(3):
        zpl.DRIVER.status(f'file://{device}', 'Zebra/ZPL')
    assert len(asked) == 1


def test_two_spellings_of_a_printer_are_asked_at_most_every_10_s(monkeypatch, tmp_path):
    # A Datamax leaves a ~HS within 5 s of the one before unanswered
    device = tmp_path / 'lp0'
    device.write_bytes(b'')
    asked = []
    monkeypatch.setattr(protocol, 'host_status', lambda device: asked.append(device) or DATAMAX_HS)
    for spelling in (f'file://{device}', str(device)):
        zpl.DRIVER.status(spelling, 'Zebra/ZPL')
    assert len(asked) == 1


@pytest.mark.parametrize('answer, asked', [(b'', [0, 20, 40]), (DATAMAX_HS, [0, 10, 20, 30, 40, 50])],
                         ids=['silent', 'answering'])
def test_asked_again_after_20_s_without_an_answer(monkeypatch, tmp_path, answer, asked):
    # Polled every 2 s for a minute: a ~HS left unanswered is outstanding
    # for 20 s, a printer that answers is asked every 10 s
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(zpl, 'time', clock)
    device = tmp_path / 'lp0'
    device.write_bytes(b'')
    times = []
    monkeypatch.setattr(protocol, 'host_status', lambda device: times.append(clock.now - 1000) or answer)
    end = clock.now + 60
    while clock.now < end:
        zpl.DRIVER.status(f'file://{device}', 'Zebra/ZPL')
        clock.sleep(2)
    assert times == asked


def test_printer_through_pyusb(monkeypatch):
    printers = []

    class Printer:
        def __init__(self, device):
            assert device == 'usb://0x0a5f:0x0080/12J123456'
            self.reads, self.written, self.disposed = [b'old', b'', DATAMAX_HS], [], False
            printers.append(self)

        def read(self, length):
            return self.reads.pop(0) if self.reads else b''

        def write(self, data):
            self.written.append(data)

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4)
    device = 'usb://0x0a5f:0x0080/12J123456'
    # An answer from before is not taken for this one
    assert protocol.host_status(device) == DATAMAX_HS
    protocol.write(device, b'^XA^XZ\n')
    assert printers[0].written == [b'~HS'] and printers[1].written == [b'^XA^', b'XZ\n']
    assert all(printer.disposed for printer in printers)


def _host_status_aside(device):
    """protocol.host_status(device) run aside, so that a hang fails the test."""
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(protocol.host_status(device)), daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    return outcome[0]


def test_printer_that_keeps_sending_through_usblp(monkeypatch):
    # /dev/zero has always something to read
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.01)
    assert set(_host_status_aside('/dev/zero')) == {0}


def test_printer_that_keeps_sending_through_pyusb(monkeypatch):
    reads = []

    class Printer:
        def __init__(self, device):
            self.written = []

        def read(self, length):
            if len(reads) > 10 * backends.DISCARD_READS:
                raise OSError('read on and on')
            reads.append(list(self.written))
            return DATAMAX_HS if self.written else b'old'

        def write(self, data):
            self.written.append(data)

        def dispose(self):
            pass
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    assert _host_status_aside('usb://0x0a5f:0x0080/12J123456') == DATAMAX_HS
    # What came before ~HS is read a while only
    assert reads.index([b'~HS']) == backends.DISCARD_READS


@pytest.mark.parametrize('device_id, model', [
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;CLS:PRINTER;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:EPL,ZPL;MODEL:ZD420;', 'Zebra/ZPL'),
    ("MFG:DatamaxONeil ;CMD:DPL;MDL:E4204B Mark III;CLS:PRINTER;", None),
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;CLS:PRINTER;', None)])
def test_known_by_its_device_id(monkeypatch, device_id, model):
    from app.labeldesigner.drivers import devices
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: device_id)
    assert zpl.DRIVER.model_of('file:///dev/usb/lp0') == model


@pytest.mark.parametrize('length', [808, 1192, 1208])
def test_a_length_as_near_to_two_lengths_picks_none(length):
    # 101, 149 and 151 mm: as near to 100 as to 102, to 148 as to 150 and to
    # 150 as to 152 mm
    assert zpl.loaded(length) is None and zpl.measured(length) is None


def test_two_status_queries_at_once_ask_once(monkeypatch):
    # A Datamax leaves a ~HS within 5 s of the one before unanswered
    server = socket.create_server(('127.0.0.1', 0))
    asked = []

    def serve():
        for _ in range(2):
            conn, _ = server.accept()
            with conn:
                asked.append(conn.recv(16))
                if len(asked) == 1:
                    time.sleep(0.3)
                    conn.sendall(DATAMAX_HS.replace(b'0235', b'1214'))
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(protocol, 'PORT', server.getsockname()[1])
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.5)
    # Asked more than 10 s ago
    monkeypatch.setitem(devices.status_cache, 'tcp://127.0.0.1:9100', {'errors': [], 'media_width': 102, 'media_length': 152})
    monkeypatch.setitem(zpl._ask_after, 'tcp://127.0.0.1:9100', time.monotonic() - 60)
    start = threading.Barrier(2)
    states = []

    def query():
        start.wait()
        states.append(zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL'))
    queries = [threading.Thread(target=query) for _ in range(2)]
    for query_ in queries:
        query_.start()
    for query_ in queries:
        query_.join(5)
    server.close()
    assert asked == [b'~HS'] and [state['errors'] for state in states] == [[], []]


def test_asked_right_after_boot(monkeypatch):
    # The monotonic clock starts at boot
    monkeypatch.setattr(time, 'monotonic', lambda: 5.0)
    asked = []
    monkeypatch.setattr(protocol, 'host_status', lambda device: asked.append(device) or DATAMAX_HS)
    zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    assert asked == ['tcp://127.0.0.1']


# Device IDs as LPrint lists them in lprint-zpl.h, and ones of other printers
@pytest.mark.parametrize('device_id, model', [
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD611-203dpi ZPL;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD611-300dpi ZPL;', 'Zebra/ZPL-300'),
    ('MFG:4BARCODE;CMD:CEZD;MDL:4B-2054A;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX420d;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX430d;', 'Zebra/ZPL-300'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'Zebra/ZPL-300'),
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD621-203dpi ZPL;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX420t;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX430t;', 'Zebra/ZPL-300'),
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD621-300dpi ZPL;', 'Zebra/ZPL-300'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZD420 300 DPI;', 'Zebra/ZPL-300'),
    # Other spellings of the known ones
    ('MFG:4barcode;CMD:CEZD;MDL:4b-2054a;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra  Technologies;COMMAND SET:ZPL;MODEL: ztc  GX430t ;', 'Zebra/ZPL-300'),
    ('MFG:hp;CMD:Bixolon_01;MDL:ke103;', 'Zebra/ZPL-300'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZTC ZD611_300dpi ZPL;', 'Zebra/ZPL-300'),
    # 200 dpi as some Zebras name their 203 dpi
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZT230-200dpi ZPL;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZP 450-200dpi;', 'Zebra/ZPL'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZM400 200 dpi;', 'Zebra/ZPL'),
    # Of a resolution without a model, its labels would come out a third as large
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZT610-600dpi ZPL;', None),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:ZT411 600 DPI;', None),
    # Other models of the same makers and the TSPL printers of lprint-tspl.h
    ('MFG:4BARCODE;CMD:CEZD;MDL:4B-3064TB;', None),
    ('MFG:HP;CMD:PJL,PCL,PCLXL,URF;MDL:HP LaserJet M110w;', None),
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', None),
    ('MFG:Marklife ;MDL:X4;CMD:XPP,XL;', None),
    ('MFG:;MDL:PM-241-BT;CMD:XPP,XL;', None),
    ('MFG:VEVOR ;CMD: ;MODEL:Y428BT;DES:LabelPrinter;', None)])
def test_model_in_device_ids_lprint_publishes(device_id, model):
    from app.labeldesigner import drivers
    assert zpl.DRIVER.model_in(device_id) == model
    # No other driver takes it for one of its printers
    assert drivers.told_model(device_id) == model


def test_300_dpi_labels_are_the_same_paper():
    from app.labeldesigner.label_names import label_dpi, label_fits, label_name
    low = [label for label in zpl.LABELS if label.models == ('Zebra/ZPL',)]
    high = [label for label in zpl.LABELS if label.models == ('Zebra/ZPL-300',)]
    # The names of the 203 dpi sizes stay, saved labels name them
    assert [label.identifier for label in low] == [f'zpl{w}x{l}' for w, l in zpl.SIZES]
    assert [label.identifier for label in high] == [f'zpl300-{w}x{l}' for w, l in zpl.SIZES]
    assert [label.tape_size for label in low] == [label.tape_size for label in high]
    assert [label_name(label) for label in low] == [label_name(label) for label in high]
    by_id = {label.identifier: label for label in zpl.LABELS}
    # 102 x 152 mm (4 x 6 in) at 300 dpi, 105 mm wider than a printhead of 104 mm
    assert by_id['zpl300-102x152'].dots_total == (1205, 1795) and by_id['zpl300-102x152'].dots_printable == (1205, 1795)
    assert by_id['zpl300-105x148'].dots_total == (1240, 1748) and by_id['zpl300-105x148'].dots_printable == (1228, 1748)
    assert by_id['zpl105x148'].dots_printable == (832, 1184)
    assert (label_dpi('zpl300-57x32', False), label_dpi('zpl57x32', False)) == (300, 203)
    assert label_fits(by_id['zpl300-57x32'], 'Zebra/ZPL-300') and not label_fits(by_id['zpl300-57x32'], 'Zebra/ZPL')
    assert label_fits(by_id['zpl57x32'], 'Zebra/ZPL') and not label_fits(by_id['zpl57x32'], 'Zebra/ZPL-300')


@pytest.mark.parametrize('saved, model, printed', [
    ('zpl102x152', 'Zebra/ZPL-300', 'zpl300-102x152'),
    ('zpl300-102x152', 'Zebra/ZPL', 'zpl102x152'),
    ('zpl57x32', 'Zebra/ZPL', 'zpl57x32'),
    ('zpl300-57x32', 'Zebra/ZPL-300', 'zpl300-57x32'),
    ('62', 'Zebra/ZPL-300', '62')])
def test_label_for_the_same_paper_of_the_other_model(saved, model, printed):
    from app.labeldesigner import tape
    assert zpl.DRIVER.label_for(saved, model) == printed
    assert tape.same_paper(saved, model) == printed


def test_300_dpi_model_is_shown_as_the_other_one():
    from app.labeldesigner.drivers.base import shown
    assert shown('Zebra/ZPL-300') == shown('Zebra/ZPL') == 'Zebra ZPL'
    assert zpl.DRIVER.capabilities('Zebra/ZPL-300')['model_shown'] == 'ZPL'
    assert zpl.DRIVER.resolution_problem('Zebra/ZPL-300', True) == 'The Zebra ZPL printer cannot print at 600 dpi, turn off 600 dpi printing'


@pytest.mark.parametrize('label_size', ['zpl300-102x152', 'zpl102x152'])
def test_300_dpi_label_is_printed_in_its_dots(tmp_path, sent, label_size):
    client = make_client(tmp_path, model='Zebra/ZPL-300', printer=DEVICE)
    response = client.post('/labeldesigner/api/print', data={'label_size': label_size, 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    (_, data), = sent
    # 1205 dots across, sent as 151 bytes a row, and 1795 rows
    assert data.startswith(b'^XA\n^PW1208\n^LL1795\n^FO0,0^GFA,271045,271045,151,')


def test_label_length_at_300_dpi():
    from types import SimpleNamespace
    from PIL import Image
    label = SimpleNamespace(label_content=None, generate=lambda: Image.new('1', (1205, 1795), 1))
    job = zpl.DRIVER.render(SimpleNamespace(label_size='zpl300-102x152', sensing=None), [{'label': label, 'cut': True}])
    assert zpl.DRIVER.label_lengths(job) == [pytest.approx(152, abs=0.1)]


@pytest.mark.parametrize('model, length, size', [
    ('Zebra/ZPL-300', 1795, (102, 152)),
    ('Zebra/ZPL-300', 1214, (102, 102)),
    ('Zebra/ZPL', 1214, (102, 152))])
def test_labels_by_their_length_in_the_dots_of_the_model(model, length, size):
    assert zpl.loaded(length, zpl.DPI[model]) == size


def test_status_of_a_300_dpi_printer(monkeypatch):
    from app.labeldesigner import tape
    # 4 x 6 in labels, 1795 dots long at 300 dpi
    thread, server = _answering(monkeypatch, DATAMAX_HS.replace(b'0235', b'1795'))
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL-300')
    thread.join(5)
    server.close()
    assert (state['media_width'], state['media_length']) == (102, 152)
    assert tape.in_printer(state, 'Zebra/ZPL-300', '', '62').id == 'zpl300-102x152'


@pytest.mark.parametrize('device_id, name', [
    ('MANUFACTURER:Zebra Technologies ;COMMAND SET:ZPL;MODEL:ZTC ZD611-300dpi ZPL;', 'Zebra ZD611'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC GX420t;SN:12J123456;', 'Zebra GX420t'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ztc GX420t;', 'Zebra GX420t'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZD611_300dpi ZPL;', 'Zebra ZD611'),
    ('MFG:4BARCODE;CMD:CEZD;MDL:4B-2054A;', '4BARCODE 4B-2054A'),
    ('MFG:HP ;CMD:Bixolon_01;MODEL:KE103;', 'HP KE103'),
    ('CMD:ZPL;', None)])
def test_status_names_the_printer_by_its_device_id(monkeypatch, device_id, name):
    from app.labeldesigner import drivers
    device = 'usb://0x0a5f:0x0080/12J123456'
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(backends, 'device_id', lambda device: device_id)
    monkeypatch.setattr(protocol, 'host_status', lambda device: b'')
    driver, model = drivers.resolve(device, 'Zebra/ZPL')
    state = driver.status(device, model)
    assert state.get('device_name') == name


def test_printer_of_600_dpi_is_left_to_be_configured(client, monkeypatch):
    from app.labeldesigner import printer
    from app.labeldesigner.drivers import devices
    # Not found on USB as a ZPL printer
    told = {'usb://0x0a5f:0x0120/ZT610': 'MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:ZTC ZT610-600dpi ZPL;'}
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: told.get(device, ''))
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: (list(told), []))
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    assert [p['path'] for p in found] == ['simulation']
    assert zpl.DRIVER.model_of('usb://0x0a5f:0x0120/ZT610') is None


@pytest.mark.parametrize('sensing, command', [('gap', b'^MNY\n'), ('mark', b'^MNM\n'), ('endless', b'^MNN\n'), (None, b'')])
def test_how_the_printer_finds_the_labels(sensing, command):
    # Not set, the printer finds them as it is set up
    assert protocol.label([b'\x80\x00', b'\x00\x01'], 2, sensing) == (
        b'^XA\n' + command + b'^PW16\n^LL2\n^FO0,0^GFA,4,4,2,80000001\n^XZ\n')


@pytest.mark.parametrize('sensing, command', [('mark', b'^MNM\n'), ('endless', b'^MNN\n'), ('gap', b'^MNY\n')])
def test_each_label_tells_how_the_printer_finds_them_as_set_for_it(tmp_path, sent, sensing, command):
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer=DEVICE)
    assert client.post('/labeldesigner/api/settings', data={'sensing': sensing}).status_code == 200
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    (_, data), = sent
    assert data.count(b'^XA\n' + command + b'^PW456\n^LL256\n') == 2
    job = [(label, 203) for label in data.split(b'^XZ\n')[:-1]]
    assert zpl.DRIVER.label_lengths(job) == [32, 32]


def test_the_status_names_the_roll_as_set(tmp_path, monkeypatch):
    # 4 x 6 in labels measured, or sent last on a continuous roll
    monkeypatch.setattr(protocol, 'host_status', lambda device: DATAMAX_HS.replace(b'0235', b'1214'))
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer='tcp://127.0.0.1')

    def status():
        found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
        return next(p for p in found if p['path'] == 'tcp://127.0.0.1')
    shown = status()
    assert (shown['media_type'], shown['media_length'], shown['tape']['id'], shown['tape']['roll']) == (
        'Die-cut labels', 152, 'zpl102x152', 'Die-cut labels')
    r = client.post('/labeldesigner/api/settings', data={'sensing': 'mark'})
    assert (r.get_json()['tape']['id'], r.get_json()['tape']['roll']) == ('zpl102x152', 'Labels with black marks')
    shown = status()
    assert (shown['media_type'], shown['media_length'], shown['tape']['known']) == ('Labels with black marks', 152, True)
    # The labels are as long as the label size, set by hand or of the label
    r = client.post('/labeldesigner/api/settings', data={'sensing': 'endless'})
    assert (r.get_json()['tape']['known'], r.get_json()['tape']['roll']) == (False, 'Continuous roll')
    shown = status()
    assert (shown['media_type'], shown['media_width'], shown['media_length'], shown['tape']['known']) == ('Continuous roll', None, 0, False)
    client.post('/labeldesigner/api/settings', data={'label_size': 'zpl102x76'})
    assert status()['tape']['id'] == 'zpl102x76' and status()['tape']['manual']
    # The label size ids stay
    assert status()['label_size'] == 'zpl102x76'


def test_printers_that_find_their_labels_by_themselves_name_no_roll(tmp_path):
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    assert next(p for p in found if p['path'] == 'simulation')['tape']['roll'] == ''


def test_the_roll_set_leaves_the_status_the_printer_told(tmp_path, monkeypatch, sent):
    # Asked once within 10 s, a continuous roll set meanwhile does not
    # change what it measured
    asked = []
    monkeypatch.setattr(protocol, 'host_status', lambda device: asked.append(device) or DATAMAX_HS.replace(b'0235', b'1214'))
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer='tcp://127.0.0.1')

    def shown(**query):
        found = client.get('/labeldesigner/api/printer_status', query_string=query).get_json()['printers']
        return next(p for p in found if p['path'] == 'tcp://127.0.0.1')
    client.post('/labeldesigner/api/settings', data={'sensing': 'endless'})
    assert shown()['media_length'] == shown(printer='tcp://127.0.0.1')['media_length'] == 0
    client.post('/labeldesigner/api/settings', data={'sensing': 'gap'})
    for told in (shown(), shown(printer='tcp://127.0.0.1')):
        assert (told['media_length'], told['tape']['id'], told['tape']['known']) == (152, 'zpl102x152', True)
    response = client.post('/labeldesigner/api/print', data={'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert sent[0][1].startswith(b'^XA\n^MNY\n^PW816\n^LL1216\n')
    assert len(asked) == 1


def test_nothing_is_sent_once_the_setting_is_removed(tmp_path, sent):
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer=DEVICE)
    job = {'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT}
    client.post('/labeldesigner/api/settings', data={'sensing': 'mark'})
    client.post('/labeldesigner/api/print', data=job)
    client.post('/labeldesigner/api/settings', data={'sensing': ''})
    response = client.post('/labeldesigner/api/print', data=job)
    assert outcome(client, response) == ('printed', '')
    assert sent[0][1].startswith(b'^XA\n^MNM\n^PW456\n') and sent[1][1].startswith(b'^XA\n^PW456\n')


def test_a_300_dpi_label_tells_how_the_printer_finds_them(tmp_path, sent):
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL-300', printer=DEVICE)
    client.post('/labeldesigner/api/settings', data={'sensing': 'endless'})
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl300-102x152', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert sent[0][1].startswith(b'^XA\n^MNN\n^PW1208\n^LL1795\n^FO0,0^GFA,271045,271045,151,')


@pytest.mark.parametrize('written', [['gap'], {'gap': 1}, 1, 'holes'])
def test_a_setting_written_wrong_by_hand_is_none(tmp_path, monkeypatch, sent, written):
    from app.labeldesigner import printer_settings
    monkeypatch.setattr(protocol, 'host_status', lambda device: DATAMAX_HS.replace(b'0235', b'1214'))
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer='tcp://127.0.0.1')
    (tmp_path / 'label_repo' / printer_settings.FILE).write_text(json.dumps({'printers': {'tcp://127.0.0.1:9100': {'sensing': written}}}))
    r = client.get('/labeldesigner/api/printer_status')
    assert r.status_code == 200
    shown = next(p for p in r.get_json()['printers'] if p['path'] == 'tcp://127.0.0.1')
    assert (shown['settings'], shown['media_length']) == ({}, 152)
    # Printed on the labels measured
    response = client.post('/labeldesigner/api/print', data={'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert sent[0][1].startswith(b'^XA\n^PW816\n^LL1216\n')


def test_a_zpl_printer_added_again_forgets_the_roll_of_the_one_before(tmp_path, monkeypatch, sent):
    from app.labeldesigner import adding, printer_settings
    monkeypatch.setattr(protocol, 'host_status', lambda device: b'')
    monkeypatch.setattr(adding, 'identify', lambda address, mode, keep=False: {'address': address, 'model': 'Zebra/ZPL'})
    client = make_client(tmp_path, empty_repo=True, model='QL-800')
    settings = tmp_path / 'label_repo' / printer_settings.FILE
    settings.write_text(json.dumps({'printers': {'tcp://192.0.2.10:9100': {'sensing': 'endless', 'cut': 'none'}}}))
    assert client.post('/labeldesigner/api/printers/add', json={'address': 'tcp://192.0.2.10'}).status_code == 200
    assert json.loads(settings.read_text())['printers'] == {'tcp://192.0.2.10:9100': {'cut': 'none'}}
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'tcp://192.0.2.10', 'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert sent[0][1].startswith(b'^XA\n^PW456\n')
    # Removed, it forgets it as well
    client.post('/labeldesigner/api/settings', data={'printer': 'tcp://192.0.2.10', 'sensing': 'mark'})
    assert client.post('/labeldesigner/api/printers/remove', json={'printer': 'tcp://192.0.2.10'}).status_code == 200
    assert json.loads(settings.read_text())['printers'] == {'tcp://192.0.2.10:9100': {'cut': 'none'}}
