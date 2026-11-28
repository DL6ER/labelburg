"""The P-touch printers on Bluetooth that Brother's raster command references
document, the PT-P710BT and the PT-P910BT: their jobs, their status, and
printing on them over RFCOMM against a fake printer.

PT-E550W/P750W/P710BT reference:
https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
PT-P900/P900W/P950NW/P910BT reference:
https://download.brother.com/welcome/docp100407/cv_ptp900_eng_raster_102.pdf"""
import _socket
import errno
import json
import socket
import threading
import time
import types

import packbits
import pytest
from PIL import Image

from app.labeldesigner import bluetooth, drivers, printer, refusals
from app.labeldesigner.drivers import brother, devices, transports
from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.raster import _add_job, convert
from app.labeldesigner.drivers.brother.status import parse_status
from tests.helpers import EXAMPLE_FORMDATA, make_client, outcome
from tests.printing.test_bluetooth import Management, found
from tests.printing.test_transports import PRINTER_CLASS, class_of_device

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'
STATUS = '/labeldesigner/api/printer_status'
PRINT = '/labeldesigner/api/print'
# The PT-P710BT reference names no model code of the PT-P710BT, this one is
# that of no Brother model
UNNAMED = 0x00
# Text that fits on 12 mm tape
SMALL_TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Test', 'size': '8', 'align': 'center'}])
# Socket pairs are made of real sockets also while the tests replace them
SOCKET = socket.socket


def socket_pair() -> tuple[socket.socket, socket.socket]:
    return tuple(SOCKET(socket.AF_UNIX, socket.SOCK_STREAM, 0, end.detach()) for end in _socket.socketpair())


def told(model_code: int, status_type: int = 0x00, phase: int = 0x00, width: int = 12, media: int = 0x01) -> bytes:
    """A status as the references lay it out: series code 30h, laminated tape
    unless media says otherwise, white with black text."""
    packet = bytearray(32)
    packet[0:6] = b'\x80\x20\x42\x30' + bytes([model_code]) + b'\x30'
    packet[10], packet[11], packet[18], packet[19] = width, media, status_type, phase
    packet[24], packet[25] = 0x01, 0x08
    return bytes(packet)


def rows(image_rows: list[bytes], width: int) -> Image.Image:
    """An image of white (0) and black (1) bits, one bytes object per line."""
    return Image.frombytes('1', (width, len(image_rows)), b''.join(bytes(~b & 0xff for b in row) for row in image_rows))


def _found(data: bytes, command: bytes) -> list[int]:
    at, found_at = data.find(command), []
    while at >= 0:
        found_at.append(at)
        at = data.find(command, at + 1)
    return found_at


def test_pt_p710bt_job_follows_the_reference():
    # Two pages of a white and a black line on 24 mm tape, its 128 pins
    raster = Raster('PT-P710BT')
    page = rows([bytes(16), b'\xff' * 16], 128)
    convert(raster, [page, page], 'pt24', rotate=0, compress=True, half_cut=True)

    def control(page_number):
        return (b'\x1b\x69\x53'
                # Print information: width 24 mm, 2 lines, starting page 0, other pages 1 (p. 32)
                + b'\x1b\x69\x7a\xcc\x00\x18\x00\x02\x00\x00\x00' + bytes([page_number, 0])
                # Auto cut (p. 33), no ESC i A, which it does not take (p. 22)
                + b'\x1b\x69\x4d\x40'
                # No chain printing, no half cut, which it does not use (p. 33), as in the example (p. 11)
                + b'\x1b\x69\x4b\x08'
                # Margins of 14 dots (pp. 17, 34) and TIFF compression (p. 35)
                + b'\x1b\x69\x64\x0e\x00' + b'\x4d\x02'
                # 16 bytes a line, packed (pp. 20, 35, 37)
                + b'\x47\x02\x00\xf1\x00' + b'\x47\x02\x00\xf1\xff')
    assert raster.data == (b'\x1b\x69\x61\x01'
                           # Invalidate with 100 bytes, initialize, raster mode (pp. 5, 23, 31)
                           + bytes(100) + b'\x1b\x40' + b'\x1b\x69\x61\x01'
                           # Status while printing (p. 31), which Brother's driver turns off (p. 11)
                           + b'\x1b\x69\x21\x00'
                           + control(0) + b'\x0c' + control(1) + b'\x1a')


@pytest.mark.parametrize('cut_every, cuts', [(1, [0x40, 0x40, 0x40]), (3, [0x00, 0x00, 0x40])])
def test_pt_p710bt_cuts_after_each_page_told_to_cut(cut_every, cuts):
    # Without ESC i A, cutting after the last label only is auto cut on the last page
    raster = Raster('PT-P710BT')
    convert(raster, [Image.new('1', (70, 2), 1)] * 3, 'pt12', rotate=0, cut_every=cut_every)
    assert [raster.data[at + 3] for at in _found(raster.data, b'\x1b\x69\x4d')] == cuts
    assert b'\x1b\x69\x41' not in raster.data


def test_pt_p710bt_cuts_after_the_last_of_more_than_255_labels():
    # Cut once at the end, also after more labels than ESC i A counts
    raster = Raster('PT-P710BT')
    job = {'settings': (0, False, False, False), 'images': [Image.new('1', (70, 2), 1)] * 300, 'cuts': [False] * 300}
    _add_job(printer.PrinterQueue('PT-P710BT', DEVICE, 'pt12'), raster, job)
    cuts = [raster.data[at + 3] for at in _found(raster.data, b'\x1b\x69\x4d')]
    assert len(cuts) == 300 and [i for i, flag in enumerate(cuts) if flag == 0x40] == [299]
    assert b'\x1b\x69\x41' not in raster.data


def test_pt_p750w_still_counts_the_labels_it_cuts_after():
    raster = Raster('PT-P750W')
    convert(raster, [Image.new('1', (70, 2), 1)] * 3, 'pt12', rotate=0, cut_every=3)
    assert [raster.data[at + 3] for at in _found(raster.data, b'\x1b\x69\x4d')] == [0x40] * 3
    assert len(_found(raster.data, b'\x1b\x69\x41\x03')) == 3
    assert b'\x1b\x69\x21' not in raster.data


def test_pt_p910bt_job_follows_the_reference():
    # A white and a black line on 36 mm tape, 454 pins between 61 and 45 (p. 20)
    raster = Raster('PT-P910BT')
    image = Image.new('1', (454, 2), 1)
    image.paste(0, (0, 1, 454, 2))
    convert(raster, [image], 'pt36', rotate=0, compress=True, half_cut=True)
    head = (b'\x1b\x69\x61\x01'
            # Invalidate with 200 bytes, initialize, raster mode (pp. 5, 23, 32)
            + bytes(200) + b'\x1b\x40' + b'\x1b\x69\x61\x01'
            # Its status while printing only when told so (pp. 5, 33, 41)
            + b'\x1b\x69\x21\x00'
            + b'\x1b\x69\x53'
            # Print information: width 36 mm, 2 lines (p. 33)
            + b'\x1b\x69\x7a\xcc\x00\x24\x00\x02\x00\x00\x00\x00\x00'
            # Auto cut, each label, half cut and no chain printing, 1 mm
            # margins at 360 dpi and TIFF compression as in the table (p. 5)
            + b'\x1b\x69\x4d\x40' + b'\x1b\x69\x41\x01' + b'\x1b\x69\x4b\x0c' + b'\x1b\x69\x64\x0e\x00' + b'\x4d\x02'
            # A white line of 70 bytes as in the example (p. 10)
            + b'\x47\x02\x00\xbb\x00')
    assert raster.data.startswith(head) and raster.data.endswith(b'\x1a')
    black = raster.data[len(head):-1]
    assert black[0] == 0x47 and int.from_bytes(black[1:3], 'little') == len(black) - 3
    assert packbits.decode(black[3:]) == bytes(7) + b'\x07' + b'\xff' * 56 + b'\xe0' + bytes(5)


def test_pt_p910bt_packs_lines_as_the_reference_example():
    # 20 bytes of 00h, 2 of 22h and 6 different ones (p. 38), the rest 00h
    line = bytes(20) + bytes.fromhex('2222 23babfa2222b') + bytes(42)
    raster = Raster('PT-P910BT')
    raster.add_compression(True)
    raster.add_raster_data(Image.frombytes('1', (560, 1), line).transpose(Image.Transpose.FLIP_LEFT_RIGHT))
    assert raster.data == b'\x4d\x02' + b'\x47\x0d\x00' + bytes.fromhex('ed00 ff22 0523babfa2222b d700')


@pytest.mark.parametrize('model, half_cut', [('PT-P710BT', False), ('PT-P910BT', True)])
def test_what_the_label_designer_offers(model, half_cut):
    capabilities = drivers.BROTHER.capabilities(model)
    assert (capabilities['half_cut'], capabilities['high_res'], capabilities['chain'], capabilities['cutter']) == (half_cut, False, True, True)
    problem = f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    assert drivers.resolution_problem(model, True) == problem and problem in refusals.MESSAGES


def test_tapes_of_each_print_head():
    assert [label.identifier for label in drivers.BROTHER.labels if drivers.BROTHER.fits(label, 'PT-P710BT')] == \
        ['pt6', 'pt9', 'pt12', 'pt18', 'pt24']
    assert [label.identifier for label in drivers.BROTHER.labels if drivers.BROTHER.fits(label, 'PT-P910BT')] == ['18', 'pt36']


def test_status_names_the_pt_p910bt_by_its_model_code():
    # PT-P910BT: "x" (78h) (p. 24), with its battery level, on AC adapter and full (p. 30)
    reply = bytearray(told(0x78))
    reply[6] = 0x30
    state = parse_status(bytes(reply))
    assert (state['model'], state['media_width'], state['errors']) == ('PT-P910BT', 12, [])


class UsbBrother:
    """A Brother printer on USB in place of the connection to it, which
    answers a status request with its status."""

    def __init__(self, model_code: int):
        self.model_code, self.unread = model_code, b''

    def write(self, data: bytes):
        if data == b'\x1b\x69\x53':
            self.unread += told(self.model_code)

    def read(self, length: int = 32) -> bytes:
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def dispose(self):
        pass


@pytest.mark.parametrize('model, named', [('PT-P710BT', 'PT-P710BT'), ('PT-P750W', 'Unknown'), ('Unknown', 'Unknown')])
def test_status_of_a_pt_p710bt_names_the_model_given(monkeypatch, model, named):
    # Its reference names no model code of it, it tells one of no Brother
    # model; another model with a model code of its own stays unknown
    monkeypatch.setattr(brother.usb, 'get_printer', lambda device: UsbBrother(UNNAMED))
    state = drivers.BROTHER.status('/dev/usb/lp0', model)
    assert (state['model'], state['media_width'], state['errors']) == (named, 12, [])


def test_pt_p710bt_on_usb_is_found_by_its_device_id(tmp_path, monkeypatch):
    lp = tmp_path / 'lp0'
    lp.touch()
    device_id = 'MFG:Brother;CMD:PT-CBP;MDL:PT-P710BT;CLS:PRINTER;'
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [str(lp)])
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: device_id if str(lp) in device else '')
    monkeypatch.setattr(brother.usb, 'get_printer', lambda device: UsbBrother(UNNAMED))
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    found_printer = client.get(STATUS).get_json()['printers'][0]
    assert (found_printer['path'], found_printer['model'], found_printer['follow'], found_printer['errors']) == \
        (f'file://{lp}', 'PT-P710BT', True, [])
    assert drivers.resolve(f'file://{lp}', 'QL-800') == (drivers.BROTHER, 'PT-P710BT')
    # A model with a model code of its own is the one its status names
    device_id = 'MFG:Brother;CMD:PT-CBP;MDL:PT-P750W;CLS:PRINTER;'
    assert drivers.resolve(f'file://{lp}', 'QL-800') == (drivers.BROTHER, 'QL-800')


def test_pt_p710bt_on_usb_is_known_by_its_product_id(tmp_path, monkeypatch):
    # 0x20af in Appendix A of its reference, also where its device ID is not read
    assert drivers.resolve('usb://0x04f9:0x20af/A1', 'QL-800') == (drivers.BROTHER, 'PT-P710BT')
    monkeypatch.setattr(devices, 'USB_IDS', str(tmp_path / '{name}-{field}'))
    (tmp_path / 'lp1-idVendor').write_text('04f9\n')
    (tmp_path / 'lp1-idProduct').write_text('20af\n')
    assert drivers.resolve('/dev/usb/lp1', 'QL-800') == (drivers.BROTHER, 'PT-P710BT')


class FakeBrother:
    """A Brother printer on classic Bluetooth in place of the RFCOMM socket:
    a socket pair for each connection, whose far end answers a status request
    with its status and a job with the status messages of printing it."""

    def __init__(self, model_code: int, width: int = 12, refused: int | None = None, media: int = 0x01):
        self.model_code, self.width, self.refused, self.media = model_code, width, refused, media
        self.addresses, self.jobs, self.requests = [], [], 0

    def __call__(self, family, kind, proto):
        assert (family, kind, proto) == (socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        ours, theirs = socket_pair()
        return Connection(self, ours, theirs)

    def serve(self, end: socket.socket):
        received = b''
        with end:
            while True:
                end.settimeout(None if not received.endswith(b'\x1a') else 0.2)
                try:
                    data = end.recv(65536)
                except TimeoutError:
                    # The end of a job
                    self.jobs.append(received)
                    received = b''
                    for status_type, phase in ((0x06, 0x01), (0x01, 0x00), (0x06, 0x00)):
                        end.sendall(told(self.model_code, status_type, phase, self.width, self.media))
                    continue
                except ConnectionResetError:
                    # Closed with status messages left unread
                    data = b''
                if not data:
                    # A job not waited for ends with the connection
                    if received:
                        self.jobs.append(received)
                    return
                received += data
                if received == b'\x1b\x69\x53':
                    self.requests += 1
                    received = b''
                    end.sendall(told(self.model_code, width=self.width, media=self.media))


class Connection:
    def __init__(self, printer_: FakeBrother, ours: socket.socket, theirs: socket.socket):
        self.printer, self.end, self.theirs = printer_, ours, theirs

    def connect(self, address):
        self.printer.addresses.append(address)
        if self.printer.refused is not None:
            self.theirs.close()
            raise OSError(self.printer.refused, 'Refused')
        threading.Thread(target=self.printer.serve, args=(self.theirs,), daemon=True).start()

    def close(self):
        self.end.close()

    def __getattr__(self, name):
        return getattr(self.end, name)


@pytest.fixture
def brother_on_bluetooth(monkeypatch):
    def install(model_code=UNNAMED, width=12, refused=None, media=0x01):
        fake = FakeBrother(model_code, width, refused, media)
        monkeypatch.setattr(transports.socket, 'socket', fake)
        monkeypatch.setattr(devices, '_unanswered', {})
        return fake
    return install


@pytest.fixture
def clock(monkeypatch):
    """The clock of the status queries on Bluetooth, moved on by hand from
    now, before the label designer looks for printers."""
    now = [time.monotonic()]
    monkeypatch.setattr(brother, 'time', types.SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(devices, 'looked', 0.0)
    return now


@pytest.mark.parametrize('model, code, width, label_size', [('PT-P710BT', UNNAMED, 12, 'pt12'), ('PT-P910BT', 0x78, 18, '18')])
def test_printing_on_bluetooth(tmp_path, brother_on_bluetooth, model, code, width, label_size):
    fake = brother_on_bluetooth(code, width)
    client = make_client(tmp_path, model=model, printer=DEVICE)
    state = client.get(STATUS).get_json()['printers'][0]
    assert (state['model'], state['errors'], state['follow'], state['label_size']) == (model, [], False, label_size)
    response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size=label_size))
    assert outcome(client, response) == ('printed', '')
    [job] = fake.jobs
    assert b'\x1b\x69\x21\x00' in job and (b'\x1b\x69\x41' in job) == (model == 'PT-P910BT')
    # On channel 1, which neither reference names otherwise
    assert set(fake.addresses) == {(MAC, 1)}


@pytest.mark.parametrize('media', [0x11, 0x17])
def test_labels_too_long_for_heat_shrink_tube_are_refused(tmp_path, brother_on_bluetooth, media):
    # HS 2:1 and 3:1 in the status, at most 500 mm long
    fake = brother_on_bluetooth(media=media)
    client = make_client(tmp_path, model='PT-P710BT', printer=DEVICE)
    assert client.get(STATUS).get_json()['printers'][0]['media_type'].startswith('Heat-Shrink Tube')
    response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size='pt12', label_length='600'))
    assert (response.status_code, response.get_json()['message']) == (400, 'Labels on heat-shrink tube can be at most 500 mm long')
    response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size='pt12', label_length='400'))
    assert outcome(client, response) == ('printed', '') and len(fake.jobs) == 1


@pytest.mark.parametrize('label_length, warning', [('600', 'Labels on heat-shrink tube can be at most 500 mm long.'), ('400', '')])
def test_preview_says_that_heat_shrink_tube_takes_no_longer_label(tmp_path, brother_on_bluetooth, label_length, warning):
    brother_on_bluetooth(media=0x11)
    client = make_client(tmp_path, model='PT-P710BT', printer=DEVICE)
    client.get(STATUS)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, label_size='pt12', label_length=label_length, text=SMALL_TEXT, margin_top='0', margin_bottom='0'))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning', '') == warning


def test_preview_on_tze_tape_takes_a_label_longer_than_500_mm(tmp_path, brother_on_bluetooth):
    brother_on_bluetooth()
    client = make_client(tmp_path, model='PT-P710BT', printer=DEVICE)
    client.get(STATUS)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, label_size='pt12', label_length='600', text=SMALL_TEXT, margin_top='0', margin_bottom='0'))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning', '') == ''


def test_status_on_bluetooth_is_asked_only_when_looking_or_after_printing(tmp_path, brother_on_bluetooth, clock):
    fake = brother_on_bluetooth()
    client = make_client(tmp_path, model='PT-P710BT', printer=DEVICE)
    start = clock[0]

    def asked(at, look=False):
        clock[0] = start + at
        assert client.get(STATUS + ('?look=1' if look else '')).get_json()['printers'][0]['model'] == 'PT-P710BT'
        return fake.requests
    assert [asked(0), asked(14), asked(31), asked(3600)] == [1, 1, 1, 1]
    # Looking for printers asks it at once
    assert [asked(3600, look=True), asked(3700), asked(7200)] == [2, 2, 2]
    # As does the first status query after printing
    client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size='pt12'))
    assert asked(7200) == 3 and len(fake.jobs) == 1


def test_status_while_a_job_holds_the_printer_is_not_an_answer(brother_on_bluetooth, clock, monkeypatch):
    # The first status query after the job asks the printer
    fake = brother_on_bluetooth()
    monkeypatch.setattr(devices, 'STATUS_LOCK_TIMEOUT', 0.05)
    drivers.BROTHER.status(DEVICE, 'PT-P710BT')
    # Asked again once the label designer looked for printers
    monkeypatch.setattr(devices, 'looked', clock[0] + 1)
    clock[0] += 2
    with devices.device_lock(DEVICE):
        assert drivers.BROTHER.status(DEVICE, 'PT-P710BT')['status_type'] == devices.BUSY_STATUS_TYPE
    assert drivers.BROTHER.status(DEVICE, 'PT-P710BT')['status_type'] != devices.BUSY_STATUS_TYPE
    assert fake.requests == 2


def test_pt_p710bt_is_not_followed_on_bluetooth():
    assert drivers.BROTHER.followed(DEVICE, 'PT-P710BT') is False
    assert drivers.BROTHER.followed('usb://0x04f9:0x20af', 'PT-P710BT') is True


@pytest.mark.parametrize('error', [errno.EACCES, errno.EBADE])
def test_printer_that_is_not_paired_says_so(tmp_path, brother_on_bluetooth, clock, error):
    # Authentication Failure, PIN or Key Missing: the kernel's EACCES and EBADE
    fake = brother_on_bluetooth(refused=error)
    client = make_client(tmp_path, model='PT-P710BT', printer=DEVICE)
    # Paged once, until the label designer looks for printers
    for at in (0, 1, 31, 300):
        clock[0] += at
        state = client.get(STATUS).get_json()['printers'][0]
        assert (state['model'], state['errors']) == ('PT-P710BT', ['Pair the printer with this computer first'])
    assert len(fake.addresses) == 1
    client.get(STATUS + '?look=1')
    assert len(fake.addresses) == 2
    response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size='pt12'))
    message = 'Could not send the label to the printer: Pair the printer with this computer first'
    assert outcome(client, response) == ('failed', message) and message in refusals.MESSAGES


def test_failed_printer_keeps_a_model_its_answer_did_not_name(monkeypatch):
    monkeypatch.setitem(devices.status_cache, devices.lock_key(DEVICE), {'model': 'Unknown', 'media_width': 12})
    state = devices.failed_printer_state(DEVICE, OSError(errno.EHOSTDOWN, 'Host is down'), 'PT-P710BT')
    assert (state['model'], state['media_width']) == ('PT-P710BT', 12)
    monkeypatch.setitem(devices.status_cache, devices.lock_key(DEVICE), {'model': 'PT-P910BT'})
    assert devices.failed_printer_state(DEVICE, OSError(errno.EHOSTDOWN, 'Host is down'), 'Unknown')['model'] == 'PT-P910BT'


@pytest.mark.parametrize('name, model', [('PT-P710BT1234', 'PT-P710BT'), ('PT-P910BT', 'PT-P910BT'), ('PT-P750W', None), ('QL-820NWB1234', None)])
def test_p_touch_printers_are_known_by_their_names_on_classic_bluetooth(name, model):
    device = {'address': MAC, 'name': name, 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}
    assert drivers.BROTHER.found_on_bluetooth(device) == ((DEVICE, model) if model else None)
    assert drivers.BROTHER.found_on_bluetooth(dict(device, le=True, classic=False)) is None


class Radio:
    """Both sockets of Bluetooth: the kernel's management channel to search
    and RFCOMM to the printer."""

    def __init__(self, management, printer_):
        self.management, self.printer = management, printer_

    def __call__(self, family, kind, proto):
        return (self.management if proto == socket.BTPROTO_HCI else self.printer)(family, kind, proto)


def test_looking_for_printers_finds_a_pt_p710bt(tmp_path, monkeypatch):
    management = Management([found(MAC, kind=0, name='PT-P710BT1234', raw=class_of_device(PRINTER_CLASS))])
    fake = FakeBrother(UNNAMED)
    monkeypatch.setattr(bluetooth.socket, 'socket', Radio(management, fake))
    monkeypatch.setattr(bluetooth.select, 'select', lambda r, w, x, t=None: (r if management.incoming else [], [], []))
    monkeypatch.setattr(printer, 'BLUETOOTH_DISCOVERY', True)
    monkeypatch.setattr(printer, 'BLUETOOTH_INTERVAL', 0)
    monkeypatch.setattr(printer, '_bluetooth', {})
    monkeypatch.setattr(printer, '_bluetooth_searched', None)
    monkeypatch.setattr(printer, '_bluetooth_thread', None)
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'looked', 0.0)
    monkeypatch.setattr(devices, '_unanswered', {})
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'

    def listed(look=False):
        client.get(STATUS + ('?look=1' if look else ''))
        printer._bluetooth_thread.join(5)
        printer._last_scan_ts = 0
        return [(p['path'], p['model'], p['follow']) for p in client.get(STATUS).get_json()['printers'] if p['path'] != 'simulation']
    # Classic Bluetooth only when looking for printers
    assert listed() == []
    assert listed(look=True) == [(DEVICE, 'PT-P710BT', False)]
    assert [params for op, _, params in management.sent if op == 0x0023] == [b'\x06', b'\x07']
    assert fake.addresses == [(MAC, 1)] and fake.requests == 1
