"""Receipt printers on ESC/POS: the bytes of a job, against a fake
printer in place of /dev/usb/lp<N>, and printing and status through the
label designer."""
import json
import os
import select
import socket
import threading

import pytest

from app.labeldesigner.drivers import backends, escpos
from app.labeldesigner.drivers.escpos import protocol
from tests.helpers import make_client

DEVICE = 'file:///dev/usb/lp3'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
# The fixed bits of each status byte
QUIET = 0x12


class FakePrinter:
    def __init__(self, offline=QUIET, paper=QUIET):
        self.sent, self.replies = b'', {protocol.OFFLINE: offline, protocol.PAPER: paper}

    def __call__(self, device):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.sent += data

    def ask(self, request):
        self.write(request)
        return self.replies[request]


@pytest.fixture
def fake(monkeypatch):
    def install(**replies):
        printer = FakePrinter(**replies)
        monkeypatch.setattr(protocol, 'connect', printer)
        return printer
    return install


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Epson/ESC-POS'
    return client


def test_raster_in_bands():
    rows = [b'\xff\x00'] * 300
    data = protocol.raster(rows, 2)
    assert data[:8] == b'\x1dv0\x00\x02\x00\x00\x01'
    assert data[8 + 512:8 + 512 + 8] == b'\x1dv0\x00\x02\x00\x2c\x00'
    assert len(data) == 2 * 8 + 600


def test_job_cuts_where_asked():
    rows = [b'\x80']
    assert protocol.job([(False, rows, 1), (True, rows, 1)]) == b'\x1b@' + protocol.raster(rows, 1) * 2 + b'\x1dVA\x00'


def test_answer_kept_from_before_is_not_taken_for_the_status(monkeypatch):
    import socket
    import threading
    ours, printer = socket.socketpair()
    c = protocol.Connection.__new__(protocol.Connection)
    c.dev = ours.fileno()
    printer.sendall(bytes([QUIET | 0x20]))

    def answer():
        assert printer.recv(3) == protocol.PAPER
        printer.sendall(bytes([QUIET]))
    thread = threading.Thread(target=answer)
    thread.start()
    assert c.ask(protocol.PAPER) == QUIET
    thread.join()
    ours.close(), printer.close()


def test_printer_through_pyusb(monkeypatch):
    class Printer:
        def __init__(self, device):
            assert device == 'usb://0x04b8:0x0e15/TM1'
            self.reads, self.written, self.disposed = [bytes([QUIET | 0x20]), b'', b'', bytes([QUIET])], [], False

        def read(self, length):
            return self.reads.pop(0) if self.reads else b''

        def write(self, data):
            self.written.append(data)

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4)
    with protocol.connect('usb://0x04b8:0x0e15/TM1') as c:
        # An answer kept from before is not taken for the status
        assert c.ask(protocol.PAPER) == QUIET
        c.write(b'0123456789')
    assert c.printer.written == [protocol.PAPER, b'0123', b'4567', b'89'] and c.printer.disposed


def test_answers_of_many_polls_are_not_taken_for_the_status(monkeypatch):
    # Over USB/IP each late answer comes in a transfer, and a read, of its own
    class Printer:
        def __init__(self, device):
            self.unread = [bytes([QUIET | 0x20])] * 30

        def read(self, length):
            return self.unread.pop(0) if self.unread else b''

        def write(self, data):
            self.unread.append(bytes([QUIET]))

        def dispose(self):
            pass
    monkeypatch.setattr(protocol.backends, 'UsbipPrinter', Printer)
    with protocol.connect('usbip://192.0.2.1') as c:
        assert c.ask(protocol.PAPER) == QUIET


def asked_while_sending(c, quiet):
    """c.ask(PAPER) of a printer that keeps sending, run aside so that a hang
    fails the test; then quiet() silences the printer and the thread ends."""
    outcome = []

    def ask():
        try:
            outcome.append(c.ask(protocol.PAPER))
        except protocol.EscposError as e:
            outcome.append(e)
    thread = threading.Thread(target=ask, daemon=True)
    thread.start()
    thread.join(5)
    hung = thread.is_alive()
    quiet()
    thread.join(5)
    assert not hung
    return outcome[0]


def test_printer_that_keeps_sending_through_usblp(monkeypatch):
    # /dev/zero has always something to read, /dev/null nothing
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.1)
    with protocol.Connection('/dev/zero') as c:
        null = os.open(os.devnull, os.O_RDWR)
        try:
            assert asked_while_sending(c, lambda: os.dup2(null, c.dev)) == 0
        finally:
            os.close(null)


def test_printer_that_keeps_sending_through_pyusb(monkeypatch):
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.1)
    quiet = threading.Event()

    class Printer:
        def __init__(self, device):
            pass

        def read(self, length):
            return b'' if quiet.is_set() else bytes([QUIET])

        def write(self, data):
            pass

        def dispose(self):
            pass
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    with protocol.connect('usb://0x04b8:0x0e15/TM1') as c:
        assert asked_while_sending(c, quiet.set) == QUIET


def test_status_bytes():
    assert protocol.errors(QUIET, QUIET) == []
    assert protocol.errors(QUIET | 0x04, QUIET | 0x60) == ['The cover of the printer is open', 'The printer has no paper']
    # Paper near its end still prints
    assert protocol.errors(QUIET, QUIET | 0x0c) == []
    assert protocol.errors(QUIET | 0x40, QUIET) == ['The printer reports an error, remove paper stuck at its cutter or switch it off and on']


def test_rows_are_packed_black_printed():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            image = Image.new('RGB', (384, 10), 'white')
            image.putpixel((0, 0), (0, 0, 0))
            image.putpixel((383, 0), (0, 0, 0))
            return image
    _, rows, width = escpos.label_rows(_Label(), 'escpos58')
    assert (len(rows), width) == (10, 48)
    assert rows[0][0] == 0x80 and rows[0][-1] == 0x01 and rows[1] == bytes(48)


def test_label_is_printed_and_cut(client, fake):
    printer = fake()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT, 'print_count': '2', 'cut': 'last'})
    assert response.get_json() == {'success': True}
    assert printer.sent.startswith(protocol.OFFLINE + protocol.PAPER + protocol.INIT)
    assert printer.sent.count(b'\x1dv0\x00') == 2 and printer.sent.count(protocol.CUT) == 1
    assert printer.sent.endswith(protocol.CUT)


def test_no_paper_does_not_print(client, fake):
    printer = fake(paper=QUIET | 0x60)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': False, 'message': 'The printer has no paper'}
    assert protocol.INIT not in printer.sent


@pytest.mark.parametrize('error, message', [
    (FileNotFoundError(2, 'No such file or directory', '/dev/usb/lp3'),
     'Could not send the label to the printer: The printer is switched off or not connected'),
    (backends.NoLibusb('No backend available'),
     'Could not send the label to the printer: Printers on usb:// need libusb, please install it'),
    (OSError('/app/instance/secret.json is gone'),
     'Could not send the label to the printer, the log of the server names the reason'),
])
def test_send_errors_are_told_as_on_the_other_printers(client, monkeypatch, error, message):
    # The reason where it is known, never the text of the error
    def fail(device):
        raise error
    monkeypatch.setattr(protocol, 'connect', fail)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': False, 'message': message}


def test_paper_is_cut_at_least_after_the_last_label(client, fake):
    fake()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT, 'cut': 'none'})
    assert response.get_json() == {'success': False, 'message': 'The Epson ESC-POS printer cuts the paper at least after the last label'}


def test_status_through_the_label_designer(client, fake):
    fake(offline=QUIET | 0x04)
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['path']) == ('Epson/ESC-POS', DEVICE)
    assert printer_['errors'] == ['The cover of the printer is open']


def test_found_by_its_device_id(client, monkeypatch):
    from app.labeldesigner import printer
    from app.labeldesigner.drivers import devices, escpos
    told = {'usb://0x04b8:0x0e15/5443': 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;',
            'usb://0x04b8:0x0202': 'MFG:EPSON;CMD:ESC/P;MDL:LX-350;',
            'usb://0x0416:0x5011': 'MFG:Printer;CMD:ESC/POS,Raster;MDL:POS58;'}
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: told.get(device, ''))
    assert escpos.DRIVER.model_of('usb://0x04b8:0x0e15/5443') == 'Epson/ESC-POS'
    # A printer of another maker on ESC/POS is one too
    assert escpos.DRIVER.model_of('usb://0x0416:0x5011') == 'Epson/ESC-POS'
    # Another Epson is no receipt printer
    assert escpos.DRIVER.model_of('usb://0x04b8:0x0202') is None
    # Found on USB by the label designer, without PRINTERS
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: (list(told), []))
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    monkeypatch.setattr(escpos.Escpos, 'status', lambda self, device, model, unconfirmed=False: {'path': device, 'model': model, 'errors': []})
    client.application.config['PRINTER_PRINTER'] = '?'
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    assert [(p['path'], p['model']) for p in found if p['path'].startswith('usb://')] == [('usb://0x04b8:0x0e15/5443', 'Epson/ESC-POS'), ('usb://0x0416:0x5011', 'Epson/ESC-POS')]


def test_printer_on_the_network(monkeypatch):
    import socket
    import threading
    server = socket.create_server(('127.0.0.1', 0))
    received = []
    stale_sent = threading.Event()

    def printer():
        conn, _ = server.accept()
        with conn:
            # An answer kept from before is not taken for the status
            conn.sendall(bytes([QUIET | 0x20]))
            stale_sent.set()
            data = b''
            while not data.endswith(protocol.PAPER):
                data += conn.recv(64)
            conn.sendall(bytes([QUIET]))
            while chunk := conn.recv(65536):
                data += chunk
            received.append(data)
    thread = threading.Thread(target=printer)
    thread.start()
    with protocol.connect(f'tcp://127.0.0.1:{server.getsockname()[1]}') as c:
        assert stale_sent.wait(5)
        select.select([c.socket], [], [], 5)
        assert c.ask(protocol.PAPER) == QUIET
        c.write(b'job')
    thread.join(5)
    server.close()
    assert received == [protocol.PAPER + b'job']


def test_printer_on_the_network_that_keeps_sending(monkeypatch):
    # A connection that never runs dry, as one over loopback may between sends
    class Printer:
        quiet = False

        def recv(self, size):
            return b'' if self.quiet else bytes([QUIET]) * size

        def sendall(self, data):
            pass

        def setblocking(self, flag):
            pass

        def settimeout(self, timeout):
            pass

        def close(self):
            pass
    printer = Printer()
    monkeypatch.setattr(protocol.socket, 'create_connection', lambda to, timeout: printer)
    with protocol.connect('tcp://192.0.2.1') as c:
        assert asked_while_sending(c, lambda: setattr(printer, 'quiet', True)) == QUIET


def test_printer_with_a_180_dpi_head(monkeypatch):
    from app.labeldesigner.drivers import devices
    from app.labeldesigner.label_names import label_dpi, label_fits
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T88V;')
    model = escpos.DRIVER.model_of('usb://0x04b8:0x0202')
    # 512 dots on 80 mm paper, 360 on 58 mm
    assert [(label.tape_size[0], label.dots_printable[0], label_dpi(label.identifier, False))
            for label in escpos.LABELS if label_fits(label, model)] == [(80, 512, 180), (58, 360, 180)]


@pytest.mark.parametrize('device, address', [
    ('tcp://192.0.2.10', ('192.0.2.10', 9100)),
    ('tcp://printer.local:9101', ('printer.local', 9101)),
    ('tcp://[2001:db8::1]', ('2001:db8::1', 9100)),
    ('tcp://[2001:db8::1]:9101', ('2001:db8::1', 9101)),
])
def test_address_of_a_printer_on_the_network(monkeypatch, device, address):
    asked = []

    class Socket:
        def close(self):
            pass
    monkeypatch.setattr(protocol.socket, 'create_connection', lambda to, timeout: asked.append(to) or Socket())
    with protocol.connect(device):
        pass
    assert asked == [address]


def test_printer_on_the_network_that_takes_nothing_more(monkeypatch):
    import socket
    import threading
    # A printer without paper takes no data: the job ends, as it holds the printer
    monkeypatch.setattr(protocol, 'WRITE_WAIT', 1)
    server = socket.create_server(('127.0.0.1', 0))
    accepted = []
    threading.Thread(target=lambda: accepted.append(server.accept()[0]), daemon=True).start()
    failed = []

    def send():
        try:
            with protocol.connect(f'tcp://127.0.0.1:{server.getsockname()[1]}') as c:
                c.write(bytes(64 << 20))
        except OSError as e:
            failed.append(e)
    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    thread.join(20)
    still_sending = thread.is_alive()
    for conn in accepted:
        conn.close()
    server.close()
    thread.join(5)
    assert not still_sending
    assert isinstance(failed[0], TimeoutError)


def test_size_names_without_dpi():
    from app.labeldesigner.label_names import label_name
    # Each model lists only its own sizes
    assert [label_name(label) for label in escpos.LABELS] == ['80 mm receipt paper', '58 mm receipt paper'] * 2


def test_both_models_are_shown_alike():
    from app.labeldesigner.drivers.base import shown
    # PRINTER_MODEL and the API keep Epson/ESC-POS-180
    assert shown('Epson/ESC-POS-180') == shown('Epson/ESC-POS') == 'Epson ESC-POS'
    assert escpos.DRIVER.capabilities('Epson/ESC-POS-180')['model_shown'] == 'ESC-POS'


@pytest.mark.parametrize('mdl, model', [
    ('TM-T88V', 'Epson/ESC-POS-180'),
    ('TM-T81', 'Epson/ESC-POS-180'),
    ('TM-T83II', 'Epson/ESC-POS-180'),
    ('TM-T90', 'Epson/ESC-POS-180'),
    ('TM-T70II', 'Epson/ESC-POS-180'),
    ('TM-T20II', 'Epson/ESC-POS'),
    ('TM-m30', 'Epson/ESC-POS'),
    ('TM-T81III', 'Epson/ESC-POS'),
])
def test_heads_of_180_dpi_by_their_model(monkeypatch, mdl, model):
    from app.labeldesigner.drivers import devices
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: f'MFG:EPSON;CMD:ESC/POS;MDL:{mdl};')
    assert escpos.DRIVER.model_of('usb://0x04b8:0x0202') == model


@pytest.mark.parametrize('saved, model, printed, row_bytes', [
    ('escpos80', 'Epson/ESC-POS-180', 'escpos80-180', 64),
    ('escpos58-180', 'Epson/ESC-POS', 'escpos58', 48),
])
@pytest.mark.parametrize('through', ['print', 'repository', 'webhook'])
def test_label_for_the_paper_of_the_other_model(tmp_path, monkeypatch, through, saved, model, printed, row_bytes):
    import base64
    import io
    from PIL import Image
    # The same paper: the label is printed on the size of the printer
    printed_on = []
    original = escpos.Escpos.render

    def render(self, queue, entries, keep=None):
        job = original(self, queue, entries, keep)
        printed_on.append((queue.label_size, job[0][2]))
        return job
    monkeypatch.setattr(escpos.Escpos, 'render', render)
    client = make_client(tmp_path, empty_repo=True)
    if through == 'print':
        response = client.post('/labeldesigner/api/print', data={
            'printer': 'simulation', 'model': model, 'label_size': saved, 'print_type': 'text', 'text': TEXT})
    elif through == 'repository':
        (tmp_path / 'label_repo' / 'receipt.json').write_text(json.dumps(
            {'label_size': saved, 'print_type': 'text', 'text': json.loads(TEXT)}))
        response = client.post('/labeldesigner/api/repository/print', json={
            'name': 'receipt.json', 'printer': 'simulation', 'model': model})
    else:
        client.application.config['WEBHOOK_PASSWORD'] = 'secret'
        image = io.BytesIO()
        Image.new('RGB', (300, 100), 'black').save(image, 'PNG')
        response = client.post('/labeldesigner/api/webhook/print', json={
            'password': 'secret', 'printer': 'simulation', 'model': model, 'label_size': saved,
            'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    assert response.get_json()['success'] is True, response.get_json()
    assert printed_on == [(printed, row_bytes)]
    # Other paper stays refused
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'simulation', 'model': model, 'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False
