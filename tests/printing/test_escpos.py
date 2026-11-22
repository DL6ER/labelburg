"""Receipt printers on ESC/POS: the bytes of a job, against a fake
printer in place of /dev/usb/lp<N>, and printing and status through the
label designer."""
import json
import os
import select
import socket
import threading
import time

import pytest

from app.labeldesigner.drivers import backends, devices, escpos
from app.labeldesigner.drivers.escpos import protocol
from tests.helpers import make_client, outcome, printing

DEVICE = 'file:///dev/usb/lp3'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
# The fixed bits of each status byte
QUIET = 0x12


class FakePrinter:
    def __init__(self, offline=QUIET, paper=QUIET):
        self.sent, self.replies, self.silent = b'', {protocol.OFFLINE: offline, protocol.PAPER: paper}, False

    def __call__(self, device):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.sent += data

    def came(self):
        return False

    def ask(self, request):
        self.write(request)
        if self.silent:
            raise protocol.EscposError('no answer to the status request')
        return self.replies[request]


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    monkeypatch.setattr(protocol, '_asked', {})
    monkeypatch.setattr(protocol, '_answered', {})


def answered_before(device):
    """The printer answered a status request before, as a TM-T20II does."""
    protocol._answered[escpos._answer_key(device)] = True


@pytest.fixture
def fake(monkeypatch):
    def install(**replies):
        printer = FakePrinter(**replies)
        monkeypatch.setattr(protocol, 'connect', printer)
        return printer
    return install


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, model='Epson/ESC-POS', printer=DEVICE)


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


class Silent:
    """A receipt printer shared over USB/IP on a fake clock, on which a read
    waits 10 ms: it takes the status requests and answers none, or each one
    at once with answering."""

    def __init__(self, clock):
        self.clock, self.writes, self.unread, self.answering = clock, [], b'', False

    def read(self, length):
        self.clock.sleep(0.01)
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def write(self, data):
        self.writes.append((self.clock.now, data))
        if self.answering:
            self.unread += bytes([QUIET])

    def dispose(self):
        pass


@pytest.fixture
def silent(monkeypatch):
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(protocol, 'time', clock)
    printer = Silent(clock)
    monkeypatch.setattr(protocol.backends, 'UsbipPrinter', lambda device: printer)
    return printer


SHARED = 'usbip://192.0.2.1'


@pytest.mark.parametrize('before', [True, False], ids=['answered before', 'never answered'])
def test_printer_that_does_not_answer_polled_for_a_minute(silent, before):
    # As the label designer asks every 2 s: a request at most every 20 s,
    # the polls between neither write nor wait. One that never answered is
    # told without errors.
    if before:
        answered_before(SHARED)
    clock = silent.clock
    end = clock.now + 60
    while clock.now < end:
        start, written = clock.now, len(silent.writes)
        if before:
            with pytest.raises(protocol.EscposError):
                escpos.DRIVER.status(SHARED, 'Epson/ESC-POS')
        else:
            assert escpos.DRIVER.status(SHARED, 'Epson/ESC-POS')['errors'] == []
        assert len(silent.writes) > written or clock.now - start < 0.1
        clock.sleep(2)
    asked = [at for at, _ in silent.writes]
    assert len(asked) == 3 and all(later - at >= 20 for at, later in zip(asked, asked[1:]))


def test_printer_whose_answer_came_late_is_asked_again(silent):
    answered_before(SHARED)
    with pytest.raises(protocol.EscposError):
        escpos.DRIVER.status(SHARED, 'Epson/ESC-POS')
    silent.unread, silent.answering = bytes([QUIET]), True
    assert escpos.DRIVER.status(SHARED, 'Epson/ESC-POS')['errors'] == []
    assert [data for _, data in silent.writes] == [protocol.OFFLINE, protocol.OFFLINE, protocol.PAPER]


def test_job_does_not_wait_for_an_outstanding_request(silent):
    answered_before(SHARED)
    with pytest.raises(protocol.EscposError):
        escpos.DRIVER.status(SHARED, 'Epson/ESC-POS')
    start, written = silent.clock.now, len(silent.writes)
    queue = type('Queue', (), {'device_specifier': SHARED})()
    assert escpos.DRIVER.send(queue, [(True, [b'\x80'], 1, 203)]) == 'The printer does not answer'
    assert silent.clock.now - start < 0.1 and len(silent.writes) == written


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
    assert outcome(client, response) == ('printed', '')
    assert printer.sent.startswith(protocol.OFFLINE + protocol.PAPER + protocol.INIT)
    assert printer.sent.count(b'\x1dv0\x00') == 2 and printer.sent.count(protocol.CUT) == 1
    assert printer.sent.endswith(protocol.CUT)


def test_no_paper_does_not_print(client, fake):
    printer = fake(paper=QUIET | 0x60)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer has no paper')
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
    assert outcome(client, response) == ('failed', message)


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
    monkeypatch.setattr(socket, 'create_connection', lambda to, timeout: printer)
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
    monkeypatch.setattr(socket, 'create_connection', lambda to, timeout: asked.append(to) or Socket())
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
        if printing():
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
    assert outcome(client, response) == ('printed', ''), response.get_json()
    assert printed_on == [(printed, row_bytes)]
    # Other paper stays refused
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'simulation', 'model': model, 'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False


@pytest.mark.parametrize('set_by_hand', ['escpos80-180', 'escpos80'], ids=['its own', 'kept for the other model'])
@pytest.mark.parametrize('through', ['print', 'repository', 'webhook'])
def test_label_for_the_paper_set_by_hand_on_the_other_model(tmp_path, monkeypatch, through, set_by_hand):
    import base64
    import io
    from PIL import Image
    from app.labeldesigner import printer_settings
    # 80 mm paper set by hand on a 180 dpi printer: a label for the 80 mm
    # paper of the other model prints on it as it is, without a note
    printed_on = []
    original = escpos.Escpos.render

    def render(self, queue, entries, keep=None):
        job = original(self, queue, entries, keep)
        if printing():
            printed_on.append((queue.label_size, job[0][2]))
        return job
    monkeypatch.setattr(escpos.Escpos, 'render', render)
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('Epson/ESC-POS-180', 'simulation', 'Till')]
    # escpos80 as kept from before the printer was known to have a 180 dpi head
    (tmp_path / 'label_repo' / printer_settings.FILE).write_text(json.dumps({'printers': {'simulation': {'label_size': set_by_hand}}}))
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['tape']
    assert (shown['id'], shown['known'], shown['manual']) == ('escpos80-180', True, True)
    assert 'escpos80' in shown['fits']
    assert shown['place']['escpos80|rotated'] == {'label_size': 'escpos80-180', 'orientation': 'rotated'}
    if through == 'print':
        response = client.post('/labeldesigner/api/print', data={
            'printer': 'Till', 'label_size': 'escpos80', 'print_type': 'text', 'text': TEXT})
    elif through == 'repository':
        (tmp_path / 'label_repo' / 'receipt.json').write_text(json.dumps(
            {'label_size': 'escpos80', 'print_type': 'text', 'text': json.loads(TEXT)}))
        response = client.post('/labeldesigner/api/repository/print', json={'name': 'receipt.json', 'printer': 'Till'})
    else:
        client.application.config['WEBHOOK_PASSWORD'] = 'secret'
        image = io.BytesIO()
        Image.new('RGB', (300, 100), 'black').save(image, 'PNG')
        response = client.post('/labeldesigner/api/webhook/print', json={
            'password': 'secret', 'printer': 'Till', 'label_size': 'escpos80',
            'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    assert printed_on == [('escpos80-180', 64)]
    # Without a label size on the paper set by hand
    response = client.post('/labeldesigner/api/print', data={'printer': 'Till', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    assert printed_on[1:] == [('escpos80-180', 64)]


@pytest.mark.parametrize('default, size, dots', [('escpos80', 'escpos80-180', 512), ('escpos58', 'escpos58-180', 360)])
def test_default_paper_of_the_other_model(tmp_path, monkeypatch, default, size, dots):
    import io
    from PIL import Image
    # No paper set on a 180 dpi printer, LABEL_DEFAULT_SIZE for the other
    # model: assumed, previewed and printed on the printer's size of it
    printed_on = []
    original = escpos.Escpos.render

    def render(self, queue, entries, keep=None):
        job = original(self, queue, entries, keep)
        if printing():
            printed_on.append((queue.label_size, job[0][2]))
        return job
    monkeypatch.setattr(escpos.Escpos, 'render', render)
    client = make_client(tmp_path, empty_repo=True)
    client.application.config.update(PRINTERS=[('Epson/ESC-POS-180', 'simulation', 'Till')], LABEL_DEFAULT_SIZE=default)
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['tape']
    assert (shown['id'], shown['assumed']) == (size, True)
    response = client.post('/labeldesigner/api/preview', data={'printer': 'Till', 'print_type': 'text', 'text': TEXT})
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).width == dots
    response = client.post('/labeldesigner/api/print', data={'printer': 'Till', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    assert printed_on == [(size, dots // 8)]


@pytest.mark.parametrize('device_id, model', [
    # As LPrint lists them in lprint-escpos.h
    ('CMD:ESC/POS;', 'Epson/ESC-POS'),
    ('MFG:EPSON;MDL:TM-m10;CMD:ESC/POS;', 'Epson/ESC-POS'),
    ('MFG:EPSON;MDL:TM-T20III;CMD:ESC/POS;', 'Epson/ESC-POS'),
    ('MFG:EPSON;MDL:TM-T88VI;CMD:ESC/POS;', 'Epson/ESC-POS-180'),
    ('MFG:EPSON;MDL:TM-T70II;CMD:ESC/POS;', 'Epson/ESC-POS-180'),
    # Other spellings of the command set
    ('MFG:Printer;CMD:ESCPOS;MDL:POS-58;', 'Epson/ESC-POS'),
    ('MFG:Printer;CMD:escpos;MDL:POS-58;', 'Epson/ESC-POS'),
    ('MFG:Printer;CMD:ESC/POS-1.0,Raster;MDL:POS-80;', 'Epson/ESC-POS'),
    # Epson's command sets of other printers, and other printers
    ('MFG:EPSON;CMD:ESC/P;MDL:LX-350;', None),
    ('MFG:EPSON;CMD:ESCPL2,BDC,D4,D4PX,ESCPR1,END4,GENEP,URF;MDL:XP-2100 Series;', None),
    ('MFG:EPSON;CMD:ESC/PAGE,PJL;MDL:AL-M200;', None),
    ('MFG:EPSON;CMD:ESC/P-R;MDL:ET-2810;', None),
    ('MFG:Printer;CMD:ESC/POSX;MDL:POS-58;', None),
    # Impact printers, which print no raster images, as LPrint lists them
    ('MFG:EPSON;MDL:TM-U220;CMD:ESC/POS;', None),
    ('MFG:EPSON;MDL:TM-U220II;CMD:ESC/POS;', None),
    ('MFG:EPSON;CMD:ESC/POS;MDL:TM-U295;', None),
    # A TSPL printer LPrint lists
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'TSC/TSPL')])
def test_model_in_device_ids(device_id, model):
    from app.labeldesigner import drivers
    assert escpos.DRIVER.model_in(device_id) == (model if model and model.startswith('Epson/') else None)
    # No other driver takes an ESC/POS printer for one of its printers
    assert drivers.told_model(device_id) == model


class Usblp:
    """A receipt printer in place of /dev/usb/lp<N>: it takes everything
    written to it and answers each status request where it answers."""

    def __init__(self, answering):
        self.ours, self.printer = socket.socketpair()
        self.received, self.answering = b'', answering
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while data := self.printer.recv(65536):
            self.received += data
            if self.answering and data in (protocol.OFFLINE, protocol.PAPER):
                self.printer.sendall(bytes([QUIET]))

    def connect(self, device):
        c = protocol.Connection.__new__(protocol.Connection)
        c.dev = os.dup(self.ours.fileno())
        return c

    def got(self, end):
        """What it received once that ends with end."""
        for _ in range(500):
            if self.received.endswith(end):
                break
            time.sleep(0.01)
        return self.received

    def close(self):
        self.ours.close()


@pytest.mark.parametrize('answering', [False, True], ids=['never answers', 'answers'])
def test_printer_that_never_answered_gets_the_job_without_being_asked(client, monkeypatch, answering):
    # A clone that knows no DLE EOT is told as ready and prints, a printer
    # that answers is asked before each job
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.2)
    printer = Usblp(answering)
    monkeypatch.setattr(protocol, 'connect', printer.connect)
    told = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (told['errors'], told['path']) == ([], DEVICE)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    asked = (protocol.OFFLINE + protocol.PAPER) * 2 if answering else protocol.OFFLINE
    assert printer.got(protocol.CUT).startswith(asked + protocol.INIT)
    assert printer.received.count(b'\x1dv0\x00') == 1
    printer.close()


def test_printer_that_answered_before_is_not_printed_on_without_an_answer(client, fake):
    printer = fake()
    client.get('/labeldesigner/api/printer_status')
    printer.silent = True
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('failed', 'The printer does not answer')
    assert protocol.INIT not in printer.sent


def test_status_names_the_printer_by_its_device_id(client, fake, monkeypatch, tmp_path):
    # As usblp read it when the printer was plugged in, with its serial number
    (tmp_path / 'sys' / 'lp3').mkdir(parents=True)
    (tmp_path / 'sys' / 'lp3' / 'ieee1284_id').write_text('MFG:Xprinter;CMD:ESC/POS;MDL:XP-58IIH;SN:XP0123456789;')
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'sys' / '{name}' / 'ieee1284_id'))
    printer = fake()
    told = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (told['model'], told['device_name']) == ('Epson/ESC-POS', 'Xprinter XP-58IIH')
    # Kept while it does not answer
    printer.silent = True
    told = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert told['status_type'] == devices.FAILED_STATUS_TYPE and told['device_name'] == 'Xprinter XP-58IIH'


def test_status_of_a_printer_without_a_device_id_names_none(client, fake):
    fake()
    told = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert 'device_name' not in told


@pytest.fixture
def clock(monkeypatch):
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(protocol, 'time', clock)
    return clock


def print_one(client):
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    return outcome(client, response)


def test_epson_that_left_a_request_unanswered_is_asked_again(client, fake, clock):
    # Its first job goes out without an answer, a later one is refused once it tells it has no paper
    printer = fake(paper=QUIET | 0x60)
    printer.silent = True
    assert print_one(client) == ('printed', '')
    printer.silent = False
    clock.sleep(protocol.ASK_AGAIN + 1)
    assert print_one(client) == ('failed', 'The printer has no paper')


@pytest.fixture
def told(monkeypatch, tmp_path):
    """Sets the device ID usblp read of DEVICE."""
    (tmp_path / 'sys' / 'lp3').mkdir(parents=True)
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'sys' / '{name}' / 'ieee1284_id'))
    return (tmp_path / 'sys' / 'lp3' / 'ieee1284_id').write_text


CLONE = 'MFG:Printer;CMD:ESC/POS;MDL:POS-58;'
TM_T20II = 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;'


def test_clone_swapped_for_an_epson_at_the_same_address(client, fake, clock, told):
    told(CLONE)
    printer = fake(paper=QUIET | 0x60)
    printer.silent = True
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []
    assert print_one(client) == ('printed', '')
    # The Epson is asked at once
    told(TM_T20II)
    printer.silent = False
    assert print_one(client) == ('failed', 'The printer has no paper')


def test_epson_swapped_for_a_clone_at_the_same_address(client, fake, clock, told):
    told(TM_T20II)
    printer = fake()
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []
    told(CLONE)
    printer.silent = True
    assert print_one(client) == ('printed', '')
    assert printer.sent.count(protocol.INIT) == 1


def test_clone_that_cannot_be_reached_keeps_its_name(client, fake, told, monkeypatch):
    import errno
    told(CLONE)
    printer = fake()
    printer.silent = True
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['device_name'] == 'POS-58'

    def busy(device):
        raise OSError(errno.EBUSY, 'Device or resource busy')
    monkeypatch.setattr(protocol, 'connect', busy)
    state = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert state['errors'] == ['The printer is in use by another program'] and state['device_name'] == 'POS-58'
