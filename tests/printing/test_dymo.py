"""DYMO's LabelWriter 450 series: the bytes of a job as DYMO's technical
reference lays them out, the status byte, how the printers are recognized,
and printing and status through usblp and PyUSB."""
import json
import os
import threading
import time
import tty
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner import drivers
from app.labeldesigner.drivers import devices, dymo, labelmanager, zpl
from app.labeldesigner.drivers.dymo import protocol
from tests.helpers import make_client, outcome

ESC = b'\x1b'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
# A printer ready, at the top of a label (the reference, p. 17)
READY = 0x03


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    monkeypatch.setattr(protocol, '_asked', {})
    monkeypatch.setattr(protocol, '_answered', {})
    monkeypatch.setattr(dymo, '_sides', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, '_usb_device_ids', {})


class _Label:
    """A label drawn as the label designer draws it: one longer than wide
    lying, with a black stripe along its top and its end black."""
    label_content = None

    def __init__(self, along, across, end=10):
        self.size, self.end = (along, across), end

    def generate(self):
        img = Image.new('1', self.size, 1)
        img.paste(0, (0, 0, self.size[0], 8))
        img.paste(0, (self.size[0] - self.end, 0, self.size[0], self.size[1]))
        return img


# A status request of a printer of 84 bytes a row: 85 ESC, then ESC A
ASK = ESC * 85 + b'\x1bA'


class Parser:
    """Reads what a LabelWriter gets as the reference describes it: after SYN
    the next bytes as many as ESC D set are a row, whatever they are, ESC A
    read as a command is answered with status, of the roll ESC q selected
    where by_roll names it, None for no answer."""
    ARGUMENTS = {b'B': 1, b'D': 1, b'L': 2, b'q': 1}

    def __init__(self, head_bytes=84, status=READY):
        self.head_bytes, self.width, self.status, self.by_roll = head_bytes, head_bytes, status, {}
        self.roll, self.rows, self.asked, self.replies, self.pending = None, 0, 0, [], b''

    def feed(self, data: bytes):
        self.pending += data
        data, at = self.pending, 0
        while at < len(data):
            if data[at] == 0x16:
                if len(data) - at - 1 < self.width:
                    break
                at, self.rows = at + 1 + self.width, self.rows + 1
            elif data[at] == 0x1b:
                command = data[at + 1:at + 2]
                if command == ESC:
                    at += 1
                    continue
                arguments = data[at + 2:at + 2 + self.ARGUMENTS.get(command, 0)]
                if not command or len(arguments) < self.ARGUMENTS.get(command, 0):
                    break
                if command == b'D':
                    self.width = arguments[0]
                elif command == b'@':
                    self.width = self.head_bytes
                elif command == b'q':
                    self.roll = arguments
                elif command == b'A':
                    self.asked += 1
                    status = self.by_roll.get(self.roll, self.status)
                    if status is not None:
                        self.replies.append(bytes([status]))
                at += 2 + len(arguments)
            else:
                at += 1
        self.pending = data[at:]


class _Printer(Parser):
    """A printer as Parser reads it that keeps what it gets."""

    def __init__(self, status=READY):
        super().__init__(status=status)
        self.written = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.written.append(data)
        self.feed(data)

    def read(self, wait):
        return self.replies.pop(0) if self.replies else b''


@pytest.fixture
def printer(monkeypatch):
    fake = _Printer()
    monkeypatch.setattr(protocol, 'connect', lambda device: fake)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.2)
    return fake


def _printed(printer, model, label_size, labels):
    """The job sent for the labels, each _Label, on a printer of the model."""
    queue = SimpleNamespace(device_specifier='/dev/usb/lp0', model=model, label_size=label_size)
    job = dymo.DRIVER.render(queue, [{'label': label, 'cut': True} for label in labels])
    assert dymo.DRIVER.send(queue, job) == ''
    status, data = printer.written
    assert status == ESC * (dymo.HEAD_DOTS[model] // 8 + 1) + b'\x1bA'
    return job, data


def _rows(width, across, stripe_rows, rows):
    """SYN and the bytes of each row: its first stripe_rows black, the
    stripe along the top of the label as drawn at the left of the others."""
    last = (0xff << (8 - across % 8)) & 0xff if across % 8 else 0xff
    black = protocol.SYN + b'\xff' * (width - 1) + bytes([last])
    striped = protocol.SYN + b'\xff' + bytes(width - 1)
    return black * stripe_rows + striped * (rows - stripe_rows)


def test_an_address_label_as_the_reference_lays_it_out(printer):
    _, data = _printed(printer, 'DYMO/LabelWriter-450', 'dymo28x89', [_Label(1051, 331)])
    # 85 ESC to leave any row unfinished (p. 9), reset (p. 17), text speed
    # and normal density (p. 18 f.); 42 bytes a row (ESC D, p. 15), 1051 rows
    # long (ESC L, p. 16); each row after SYN, its end comes out first; a
    # form feed to the tear bar after the last label (p. 16)
    assert data == (ESC * 85 + b'\x1b@' + b'\x1bh' + b'\x1be'
                    + b'\x1bD\x2a' + b'\x1bL\x04\x1b' + _rows(42, 331, 10, 1051) + b'\x1bE')


def test_two_shipping_labels_have_a_short_form_feed_between_them(printer):
    job, data = _printed(printer, 'DYMO/LabelWriter-450', 'dymo54x101', [_Label(1193, 638)] * 2)
    label = b'\x1bD\x50' + b'\x1bL\x04\xa9' + _rows(80, 638, 10, 1193)
    # ESC G between the labels, ESC E after the last (p. 16)
    assert data == ESC * 85 + b'\x1b@\x1bh\x1be' + label + b'\x1bG' + label + b'\x1bE'
    assert dymo.DRIVER.label_lengths(job) == [pytest.approx(101, abs=0.1)] * 2


def test_the_4xl_prints_as_wide_as_its_labels(printer):
    # 1248 dots across: 157 ESC end any row of 156 bytes
    _, data = _printed(printer, 'DYMO/LabelWriter-4XL', 'dymo102x152', [_Label(1795, 1205)])
    assert data == (ESC * 157 + b'\x1b@\x1bh\x1be'
                    + b'\x1bD\x97' + b'\x1bL\x07\x03' + _rows(151, 1205, 10, 1795) + b'\x1bE')
    printer.written.clear()
    # Labels of the 450 start at its first dot as well
    _, data = _printed(printer, 'DYMO/LabelWriter-4XL', 'dymo28x89', [_Label(1051, 331)])
    assert data.startswith(ESC * 157 + b'\x1b@\x1bh\x1be\x1bD\x2a\x1bL\x04\x1b' + protocol.SYN)


def test_the_se450_prints_203_dpi_and_feeds_each_label_to_the_tear_bar(printer):
    job, data = _printed(printer, 'DYMO/LabelWriter-SE450', 'dymo203-28x89', [_Label(712, 224)] * 2)
    # 448 dots, 57 ESC (SE450 reference, SYN); it takes no ESC G
    label = b'\x1bD\x1c' + b'\x1bL\x02\xc8' + _rows(28, 224, 10, 712)
    assert data == ESC * 57 + b'\x1b@\x1bh\x1be' + label + b'\x1bE' + label + b'\x1bE'
    assert dymo.DRIVER.label_lengths(job) == [89, 89]


def test_a_label_is_not_sent_while_the_printer_has_no_labels(printer):
    printer.status = 0xa1
    queue = SimpleNamespace(device_specifier='/dev/usb/lp0', model='DYMO/LabelWriter-450', label_size='dymo28x89')
    job = dymo.DRIVER.render(queue, [{'label': _Label(1051, 331), 'cut': True}])
    assert dymo.DRIVER.send(queue, job) == 'No labels are in the printer'
    assert printer.written == [ASK]


@pytest.mark.parametrize('model, label_size, label', [
    ('DYMO/LabelWriter-450', 'dymo28x89', _Label(1051, 331)), ('DYMO/LabelWriter-4XL', 'dymo102x152', _Label(1795, 1205)),
    ('DYMO/LabelWriter-SE450', 'dymo203-54x101', _Label(808, 432))])
def test_a_job_broken_off_in_a_row_leaves_the_next_one_printed(printer, model, label_size, label):
    head_bytes = dymo.HEAD_DOTS[model] // 8
    printer.head_bytes = printer.width = head_bytes
    queue = SimpleNamespace(device_specifier='/dev/usb/lp0', model=model, label_size=label_size)
    job = dymo.DRIVER.render(queue, [{'label': label, 'cut': True}])
    assert dymo.DRIVER.status('/dev/usb/lp0', model)['errors'] == []
    # A job broke off in the middle of a row, e.g. a write that timed out
    printer.feed(protocol.job([(rows, width) for rows, width, _ in job], head_bytes)[:16384])
    assert printer.pending.startswith(protocol.SYN)
    # The ESC before ESC A fill the row, the printer answers and prints the next job whole
    assert dymo.DRIVER.send(queue, job) == ''
    assert printer.asked == 2 and printer.pending == b''


def test_a_twin_turbo_is_asked_for_the_roll_it_prints_on(printer):
    # The right roll is empty and was selected last, the left one is loaded
    printer.by_roll, printer.roll = {b'1': READY, b'2': 0xa1}, b'2'
    queue = SimpleNamespace(device_specifier='/dev/usb/lp0', model='DYMO/LabelWriter-450-Twin-Turbo', label_size='dymo28x89', side='left')
    job = dymo.DRIVER.render(queue, [{'label': _Label(1051, 331), 'cut': True}])
    assert dymo.DRIVER.send(queue, job) == ''
    assert printer.written[0] == ESC * 85 + b'\x1bq1\x1bA' and printer.roll == b'1'
    queue.side = 'right'
    assert dymo.DRIVER.send(queue, job) == 'No labels are in the printer'
    assert printer.written[2:] == [ESC * 85 + b'\x1bq2\x1bA']
    # The status is that of the roll selected last
    assert dymo.DRIVER.status('/dev/usb/lp0', queue.model)['errors'] == ['No labels are in the printer']
    assert printer.written[3] == ASK


@pytest.mark.parametrize('status, errors', [
    (0x03, []), (0x01, []),
    # No labels sets the error bit too
    (0xa1, [protocol.NO_LABELS_TEXT]), (0x21, [protocol.NO_LABELS_TEXT]),
    (0xc1, [protocol.JAM_TEXT]),
    (0xe1, [protocol.NO_LABELS_TEXT, protocol.JAM_TEXT]),
    # An error of no other bit, e.g. a sequence it did not take
    (0x81, [protocol.ERROR_TEXT]),
    # Reserved bits
    (0x1f, [])])
def test_status_byte(status, errors):
    assert protocol.errors(status) == errors


@pytest.mark.parametrize('status, errors', [
    # Bit 0 set is not ready, bit 6 reserved (SE450 reference, GS S)
    (0x00, []), (0x01, []), (0x40, []), (0xc0, [protocol.ERROR_TEXT]), (0xa0, [protocol.NO_LABELS_TEXT])])
def test_status_byte_of_the_se450(status, errors):
    assert protocol.errors(status, jam=False) == errors


# As LPrint lists them in lprint-dymo.h, in other spellings, and devices of
# DYMO of another family or of other makers
@pytest.mark.parametrize('device_id, model', [
    ('MFG:DYMO;MDL:LabelWriter 450;', 'DYMO/LabelWriter-450'),
    ('MFG:DYMO;MDL:LabelWriter 450 Turbo;', 'DYMO/LabelWriter-450'),
    ('MFG:DYMO;MDL:LabelWriter 450 DUO Label;', 'DYMO/LabelWriter-450'),
    ('MFG:DYMO;MDL:LabelWriter 450 Twin Turbo;', 'DYMO/LabelWriter-450-Twin-Turbo'),
    ('MFG:DYMO;MDL:LabelWriter 4XL;', 'DYMO/LabelWriter-4XL'),
    ('MFG:DYMO;MDL:LabelWriter SE450;', 'DYMO/LabelWriter-SE450'),
    ('MFG:DYMO;CMD: ;MDL:LabelWriter 450 Turbo;CLASS:PRINTER;DESCRIPTION:DYMO LabelWriter 450 Turbo;SERN:01010112345;',
     'DYMO/LabelWriter-450'),
    ('MANUFACTURER:dymo ;MODEL: labelwriter  450  twin turbo ;', 'DYMO/LabelWriter-450-Twin-Turbo'),
    ('MFG:DYMO;MDL:LabelWriter 450 DUO Tape;', None),
    ('MFG:DYMO;MDL:LabelMANAGER PNP ;', None),
    ('MFG:DYMO;MDL:LabelManager 420P;', None),
    ('MFG:DYMO;MDL:LabelWriter 550;', None),
    ('MFG:DYMO;MDL:LabelWriter 550 Turbo;', None),
    ('MFG:DYMO;MDL:LabelWriter 5XL;', None),
    ('MFG:DYMO;MDL:LabelWriter 400 Turbo;', None),
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;CLS:PRINTER;', None),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;CLS:PRINTER;', None),
    ('MDL:LabelWriter 450;', None)])
def test_known_by_its_device_id(device_id, model):
    assert dymo.DRIVER.model_in(device_id) == model
    # No other driver takes it for one of its printers, a LabelManager its own
    if 'dymo' in device_id.lower():
        assert drivers.told_model(device_id) == (model or labelmanager.DRIVER.model_in(device_id))


@pytest.mark.parametrize('device, model', [
    ('usb://0x0922:0x0020', 'DYMO/LabelWriter-450'),
    ('usb://0x0922:0x0021/12345', 'DYMO/LabelWriter-450'),
    ('usb://0x0922:0x0022', 'DYMO/LabelWriter-450-Twin-Turbo'),
    ('usb://0x0922:0x001F', 'DYMO/LabelWriter-4XL'),
    ('usb://0x0922:0x0400', 'DYMO/LabelWriter-SE450'),
    # The Duo has its tape side on the same IDs, a 550, a LabelManager PnP
    ('usb://0x0922:0x0023', None), ('usb://0x0922:0x0028', None), ('usb://0x0922:0x1001', None),
    ('usb://0x04f9:0x0020', None), ('usbip://192.0.2.5', None), ('tcp://192.0.2.5', None)])
def test_a_printer_that_tells_no_device_id_is_known_by_its_usb_ids(monkeypatch, device, model):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: '')
    assert dymo.DRIVER.model_of(device) == model
    assert drivers.resolve(device, 'QL-800')[1] == (model or labelmanager.DRIVER.model_of(device) or 'QL-800')


@pytest.mark.parametrize('device, device_id, configured, assumed, model', [
    # PRINTER_MODEL left unset: the device ID decides, else the USB IDs
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter 450 Turbo;', 'QL-800', True, 'DYMO/LabelWriter-450'),
    ('usb://0x0922:0x0022', '', 'QL-800', True, 'DYMO/LabelWriter-450-Twin-Turbo'),
    # A configured LabelWriter keeps its driver, the device ID tells which one
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter 450;', 'DYMO/LabelWriter-450', False, 'DYMO/LabelWriter-450'),
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter 450 Twin Turbo;', 'DYMO/LabelWriter-450', False,
     'DYMO/LabelWriter-450-Twin-Turbo'),
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter SE450;', 'DYMO/LabelWriter-450', False, 'DYMO/LabelWriter-SE450'),
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter 450;', 'DYMO/LabelWriter-SE450', False, 'DYMO/LabelWriter-450'),
    ('file:///dev/usb/lp_dymo', '', 'DYMO/LabelWriter-4XL', False, 'DYMO/LabelWriter-4XL'),
    # It takes nothing but its raster commands, also where another model is configured
    ('file:///dev/usb/lp_dymo', 'MFG:DYMO;MDL:LabelWriter 450;', 'QL-800', False, 'DYMO/LabelWriter-450'),
    ('usb://0x0922:0x0400', '', 'Zebra/ZPL', False, 'DYMO/LabelWriter-SE450'),
    # A configured LabelWriter that is another printer
    ('file:///dev/usb/lp_dymo', 'MFG:Zebra Technologies;CMD:ZPL;MDL:ZD420;', 'DYMO/LabelWriter-450', False, 'Zebra/ZPL')])
def test_driver_and_model_of_a_printer(monkeypatch, device, device_id, configured, assumed, model):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: device_id)
    assert drivers.resolve(device, configured, assumed) == (drivers.for_model(model), model)


@pytest.mark.parametrize('configured, device_id, model', [
    (None, 'MFG:DYMO;CMD: ;MDL:LabelWriter 450 Twin Turbo;CLS:PRINTER;', 'DYMO/LabelWriter-450-Twin-Turbo'),
    ('DYMO/LabelWriter-4XL', '', 'DYMO/LabelWriter-4XL'),
    ('DYMO/LabelWriter-450', 'MFG:DYMO;MDL:LabelWriter 4XL;', 'DYMO/LabelWriter-4XL')])
def test_the_printer_of_printer_printer(tmp_path, monkeypatch, printer, configured, device_id, model):
    from config import Default
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: device_id)
    monkeypatch.delenv('PRINTER_MODEL', raising=False)
    client = make_client(tmp_path, empty_repo=True, model=configured, printer='file:///dev/usb/lp_dymo')
    assert isinstance(client.application.config['PRINTER_MODEL'], Default) is (configured is None)
    shown = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (shown['path'], shown['model'], shown['errors']) == ('file:///dev/usb/lp_dymo', model, [])
    response = client.post('/labeldesigner/api/print', data={'label_size': 'dymo28x89', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')


def test_a_device_id_counts_before_the_usb_ids():
    # The tape side of a Duo, and a printer of another maker on DYMO's IDs
    assert dymo.DRIVER.model_of('usb://0x0922:0x0020', 'MFG:DYMO;MDL:LabelWriter 450 DUO Tape;') is None
    assert dymo.DRIVER.model_of('usb://0x0922:0x0020', 'MFG:ACME;MDL:Label 450;') is None


def _usblp(tmp_path, monkeypatch, vendor, product):
    """lp0 of usblp in a sysfs as Linux has it: its interface in the folder
    of its USB device."""
    interface = tmp_path / 'devices' / '1-1' / '1-1:1.0'
    interface.mkdir(parents=True)
    (interface.parent / 'idVendor').write_text(f'{vendor}\n')
    (interface.parent / 'idProduct').write_text(f'{product}\n')
    (tmp_path / 'usbmisc' / 'lp0').mkdir(parents=True)
    os.symlink(interface, tmp_path / 'usbmisc' / 'lp0' / 'device')
    monkeypatch.setattr(devices, 'USB_DESCRIPTOR', str(tmp_path / 'usbmisc' / '{name}' / 'device' / '..' / '{field}'))
    monkeypatch.setattr(devices, 'USB_DEVICE_ID', str(tmp_path / 'usbmisc' / '{name}' / 'device' / 'ieee1284_id'))


@pytest.mark.parametrize('vendor, product, model', [
    ('0922', '0020', 'DYMO/LabelWriter-450'), ('0922', '0022', 'DYMO/LabelWriter-450-Twin-Turbo'),
    ('0922', '0023', None), ('04f9', '2042', None)])
def test_usblp_tells_the_usb_ids(tmp_path, monkeypatch, vendor, product, model):
    _usblp(tmp_path, monkeypatch, vendor, product)
    assert devices.usb_ids('file:///dev/usb/lp0') == (int(vendor, 16), int(product, 16))
    assert dymo.DRIVER.model_of('/dev/usb/lp0') == model
    assert devices.usb_ids('/dev/usb/lp1') is None and devices.usb_ids('/dev/pts/0') is None


def test_usblp_tells_the_device_id_first(tmp_path, monkeypatch):
    _usblp(tmp_path, monkeypatch, '0922', '0023')
    device_id = tmp_path / 'usbmisc' / 'lp0' / 'device' / 'ieee1284_id'
    device_id.write_text('MFG:DYMO;CMD: ;MDL:LabelWriter 450 DUO Label;CLS:PRINTER;')
    assert drivers.resolve('file:///dev/usb/lp0', 'Unknown') == (dymo.DRIVER, 'DYMO/LabelWriter-450')
    device_id.write_text('MFG:DYMO;CMD: ;MDL:LabelWriter 450 DUO Tape;CLS:PRINTER;')
    assert dymo.DRIVER.model_of('file:///dev/usb/lp0') is None


# The 4 x 6 in labels of each model that has them
FOUR_BY_SIX = {'Zebra/ZPL': 'zpl102x152', 'Zebra/ZPL-300': 'zpl300-102x152', 'TSC/TSPL': 'tspl102x152',
               'TSC/TSPL-300': 'tspl300-102x152', 'DYMO/LabelWriter-4XL': 'dymo102x152'}
DIE_CUT_MODELS = (*FOUR_BY_SIX, 'DYMO/LabelWriter-450', 'DYMO/LabelWriter-450-Twin-Turbo', 'DYMO/LabelWriter-SE450')


@pytest.mark.parametrize('saved', FOUR_BY_SIX.values())
@pytest.mark.parametrize('model', DIE_CUT_MODELS)
def test_a_4_x_6_in_label_prints_on_the_same_paper_of_any_driver(saved, model):
    from app.labeldesigner import tape
    # The 450 and the SE450 have no labels this wide, it stays as saved
    printed = FOUR_BY_SIX.get(model, saved)
    assert drivers.for_model(model).label_for(saved, model) == printed
    assert tape.same_paper(saved, model) == printed


@pytest.mark.parametrize('saved', ['dymo54x101', 'dymo203-54x101'])
@pytest.mark.parametrize('model, printed', [
    ('DYMO/LabelWriter-450', 'dymo54x101'), ('DYMO/LabelWriter-450-Twin-Turbo', 'dymo54x101'),
    ('DYMO/LabelWriter-4XL', 'dymo54x101'), ('DYMO/LabelWriter-SE450', 'dymo203-54x101'),
    # ZPL and TSPL printers have no labels of that size
    ('Zebra/ZPL', None), ('Zebra/ZPL-300', None), ('TSC/TSPL', None), ('TSC/TSPL-300', None)])
def test_a_shipping_label_of_54_x_101_mm_prints_on_the_same_paper(saved, model, printed):
    from app.labeldesigner import tape
    assert drivers.for_model(model).label_for(saved, model) == (printed or saved)
    assert tape.same_paper(saved, model) == (printed or saved)


@pytest.mark.parametrize('saved, model', [('62', 'DYMO/LabelWriter-450'), ('102x152', 'DYMO/LabelWriter-4XL'),
                                          ('dymo28x89', 'Zebra/ZPL'), ('dymo28x89', 'TSC/TSPL')])
def test_other_paper_stays_as_saved(saved, model):
    # Endless tape, Brother's die-cut labels and sizes the printer has not
    assert drivers.for_model(model).label_for(saved, model) == saved


def test_labels_of_the_printers():
    from app.labeldesigner import tape
    from app.labeldesigner.label_names import label_dpi, label_name
    by_id = {label.identifier: label for label in dymo.LABELS}
    # 59 mm wide labels are wider than the 672 dots of the 450, also on the 4XL
    assert (by_id['dymo59x102'].dots_total, by_id['dymo59x102'].dots_printable) == ((697, 1205), (672, 1205))
    assert by_id['dymo102x152'].dots_printable == (1205, 1795)
    assert by_id['dymo203-28x89'].dots_printable == (224, 712)
    assert (label_dpi('dymo28x89', False), label_dpi('dymo203-28x89', False)) == (300, 203)
    assert label_name(by_id['dymo28x89']) == label_name(by_id['dymo203-28x89']) == '28 x 89 mm address (DYMO)'
    assert tape.takes('DYMO/LabelWriter-450')[:2] == ['dymo28x89', 'dymo36x89'] and 'dymo102x152' not in tape.takes('DYMO/LabelWriter-450')
    assert 'dymo102x152' in tape.takes('DYMO/LabelWriter-4XL')
    assert all(size.startswith('dymo203-') for size in tape.takes('DYMO/LabelWriter-SE450'))
    # A printer of no known model takes each paper once
    assert not [size for size in tape.takes(None) if size.startswith('dymo203-')]


def test_capabilities():
    caps = dymo.DRIVER.capabilities('DYMO/LabelWriter-450')
    assert (caps['cutter'], caps['leaves_turned'], caps['qualities'], caps['vendor'], caps['sensing']) == (False, True, ['quality'], 'DYMO', False)
    # Listed as the 450, named as itself
    assert (dymo.DRIVER.capabilities('DYMO/LabelWriter-SE450')['shown_as'], caps['shown_as']) == ('DYMO/LabelWriter-450', '')
    # Labels per minute of address labels, 89 mm long
    assert dymo.DRIVER.print_speed('DYMO/LabelWriter-450-Twin-Turbo', False, False) == (pytest.approx(71 * 89 / 60), 0)


class FakeLabelWriter(Parser):
    """A LabelWriter as Parser reads it on a pseudo terminal, as usblp's
    device, which keeps what it gets."""

    def __init__(self, status=READY):
        super().__init__(status=status)
        self.data = b''
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            self.data += data
            self.feed(data)
            while self.replies:
                os.write(self.master, self.replies.pop(0))

    def close(self):
        os.close(self.slave)
        os.close(self.master)


@pytest.fixture
def labelwriter(monkeypatch):
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.3)
    fake = FakeLabelWriter()
    yield fake
    fake.close()


def _received(labelwriter):
    """What the printer got after the status request once a whole job came,
    nothing more for a while."""
    deadline, seen = time.monotonic() + 5, None
    while time.monotonic() < deadline and not (labelwriter.data == seen and seen.endswith(b'\x1bE')):
        seen = labelwriter.data
        time.sleep(0.1)
    assert labelwriter.data.startswith(ASK)
    return labelwriter.data.removeprefix(ASK)


def _labels(data, head_bytes):
    """The labels of a job as the reference lays them out: (bytes per row,
    rows, feed after it) each."""
    head = ESC * (head_bytes + 1) + b'\x1b@\x1bh\x1be'
    assert data.startswith(head)
    at, labels = len(head), []
    while at < len(data):
        assert data[at:at + 2] == b'\x1bD' and data[at + 3:at + 5] == b'\x1bL'
        width, rows = data[at + 2], int.from_bytes(data[at + 5:at + 7], 'big')
        at += 7
        assert all(data[at + i * (width + 1)] == 0x16 for i in range(rows))
        at += rows * (width + 1)
        labels.append((width, rows, data[at:at + 2]))
        at += 2
    return labels


def test_two_copies_printed_through_usblp(tmp_path, labelwriter):
    client = make_client(tmp_path, model='DYMO/LabelWriter-450', printer=f'file://{labelwriter.path}')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'dymo54x101', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    assert _labels(_received(labelwriter), 84) == [(80, 1193, b'\x1bG'), (80, 1193, b'\x1bE')]
    # Asked once, before the job
    assert labelwriter.asked == 1


def test_status_through_usblp(labelwriter):
    device = f'file://{labelwriter.path}'
    state = dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')
    assert (state['errors'], state['status_type'], state['media_type']) == ([], 'Reply to status request', 'Die-cut labels')
    labelwriter.status = 0xa1
    state = dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')
    assert (state['errors'], state['status_type']) == (['No labels are in the printer'], 'Error occurred')
    assert labelwriter.asked == 2


def test_a_printer_that_never_answered_is_printed_on(tmp_path, labelwriter):
    labelwriter.status = None
    device = f'file://{labelwriter.path}'
    assert dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')['errors'] == []
    client = make_client(tmp_path, model='DYMO/LabelWriter-450', printer=device)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'dymo28x89', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert _labels(_received(labelwriter), 84) == [(42, 1051, b'\x1bE')]
    # Its request is outstanding, the job sent none
    assert labelwriter.asked == 1


def test_one_status_request_is_outstanding_at_most(monkeypatch, labelwriter):
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(protocol, 'time', SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(protocol, '_answer', lambda c, deadline: c.read(0.2))
    device = f'file://{labelwriter.path}'
    dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')
    labelwriter.status = None
    # It answered once, silence is an error now
    with pytest.raises(protocol.NoAnswer):
        dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')
    for _ in range(9):
        clock.sleep(2)
        with pytest.raises(protocol.NoAnswer):
            dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')
    assert labelwriter.asked == 2
    # Lost after 20 s, asked again
    clock.sleep(2)
    labelwriter.status = READY
    assert dymo.DRIVER.status(device, 'DYMO/LabelWriter-450')['errors'] == []
    assert labelwriter.asked == 3


def test_a_late_answer_is_taken(monkeypatch):
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    printer = _Printer(status=None)
    with pytest.raises(protocol.NoAnswer):
        protocol.status(printer, 'lp0', 84)
    printer.replies.append(b'\xa1')
    assert protocol.status(printer, 'lp0', 84) == 0xa1
    assert printer.written == [ASK]


def test_printer_through_pyusb(monkeypatch):
    printers = []

    class Printer:
        def __init__(self, device):
            assert device == 'usb://0x0922:0x0020/01010112345'
            self.reads, self.written, self.disposed = [b'\x02'], [], False
            printers.append(self)

        def read(self, length):
            return self.reads.pop(0) if self.reads else b''

        def write(self, data):
            self.written.append(data)
            if data == ASK:
                self.reads.append(b'\x03')

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4096)
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: 'MFG:DYMO;MDL:LabelWriter 450 Turbo;SERN:01010112345;')
    device = 'usb://0x0922:0x0020/01010112345'
    driver, model = drivers.resolve(device, 'QL-800')
    state = driver.status(device, model)
    # An answer from before is not taken for this one
    assert (state['errors'], printers[0].written) == ([], [ASK])
    queue = SimpleNamespace(device_specifier=device, model=model, label_size='dymo28x89')
    assert driver.send(queue, driver.render(queue, [{'label': _Label(1051, 331), 'cut': True}])) == ''
    status, *job = printers[1].written
    assert status == ASK and len(job) > 1 and {len(part) for part in job[:-1]} == {4096}
    assert _labels(b''.join(job), 84) == [(42, 1051, b'\x1bE')]
    assert all(printer.disposed for printer in printers)


def test_status_names_the_printer_by_its_device_id(monkeypatch, printer):
    device = 'usb://0x0922:0x0021/01010112345'
    monkeypatch.setattr(protocol.backends, 'device_id', lambda device: 'MFG:DYMO;CMD: ;MDL:LabelWriter 450 Turbo;CLS:PRINTER;SERN:01010112345;')
    driver, model = drivers.resolve(device, 'QL-800')
    state = driver.status(device, model)
    assert (model, state['device_name'], state['model']) == ('DYMO/LabelWriter-450', 'DYMO LabelWriter 450 Turbo', model)


def test_found_on_usb(tmp_path, monkeypatch, printer):
    from app.labeldesigner import printer as printer_module
    told = {'usb://0x0922:0x0022/01010112345': 'MFG:DYMO;MDL:LabelWriter 450 Twin Turbo;',
            'usb://0x0922:0x0028/02020212345': 'MFG:DYMO;MDL:LabelWriter 550;'}
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: told.get(device, ''))
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: (list(told), []))
    monkeypatch.setattr(printer_module, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(printer_module, '_last_scan_ts', 0)
    monkeypatch.setattr(printer_module, '_cached_printers', [])
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    # Not the 550, its labels have a chip
    assert [(p['path'], p['model']) for p in found] == [
        ('usb://0x0922:0x0022/01010112345', 'DYMO/LabelWriter-450-Twin-Turbo'), ('simulation', 'QL-800')]
    assert found[0]['tape']['id'] == 'dymo28x89' and found[0]['tape']['assumed']


@pytest.mark.parametrize('side, roll', [(None, b'1'), ('left', b'1'), ('right', b'2')])
def test_a_twin_turbo_prints_on_the_roll_chosen(printer, side, roll):
    # ESC q with ASCII 1 for the left roll, 2 for the right one (p. 16)
    queue = SimpleNamespace(device_specifier='/dev/usb/lp0', model='DYMO/LabelWriter-450-Twin-Turbo', label_size='dymo28x89', side=side)
    job = dymo.DRIVER.render(queue, [{'label': _Label(1051, 331), 'cut': True}])
    assert dymo.DRIVER.send(queue, job) == ''
    assert printer.written[1] == (ESC * 85 + b'\x1b@\x1bq' + roll + b'\x1bh\x1be'
                                  + b'\x1bD\x2a' + b'\x1bL\x04\x1b' + _rows(42, 331, 10, 1051) + b'\x1bE')


def test_the_roll_set_for_a_twin_turbo_goes_to_its_jobs(tmp_path, printer):
    device = 'file:///dev/usb/lp_dymo'
    client = make_client(tmp_path, empty_repo=True, model='DYMO/LabelWriter-450-Twin-Turbo', printer=device)
    job = {'label_size': 'dymo28x89', 'print_type': 'text', 'text': TEXT}
    assert outcome(client, client.post('/labeldesigner/api/print', data=job)) == ('printed', '')
    assert client.post('/labeldesigner/api/settings', data={'side': 'right'}).get_json()['settings'] == {'side': 'right'}
    assert outcome(client, client.post('/labeldesigner/api/print', data=job)) == ('printed', '')
    jobs = printer.written[1::2]
    assert [data[85:90] for data in jobs] == [b'\x1b@\x1bq1', b'\x1b@\x1bq2']
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['settings'] == {'side': 'right'}


def test_only_a_twin_turbo_has_two_rolls():
    assert {model for model in drivers.MODELS if drivers.for_model(model).capabilities(model)['two_rolls']} == {
        'DYMO/LabelWriter-450-Twin-Turbo'}


@pytest.mark.parametrize('model, name', [
    ('DYMO/LabelWriter-450', 'LabelWriter 450'), ('DYMO/LabelWriter-450-Twin-Turbo', 'LabelWriter 450 Twin Turbo'),
    ('DYMO/LabelWriter-4XL', 'LabelWriter 4XL'), ('DYMO/LabelWriter-SE450', 'LabelWriter SE450')])
def test_models_are_named_plainly(model, name):
    from app.labeldesigner.drivers.base import shown
    assert shown(model) == f'DYMO {name}'
    assert dymo.DRIVER.capabilities(model)['model_shown'] == name
    # As before for the models of other drivers
    assert (shown('Zebra/ZPL-300'), shown('QL-800'), shown('PUQU/Q00')) == ('Zebra ZPL', 'QL-800', 'PUQU Q00')


def test_refusals_name_the_printer_plainly(tmp_path, printer):
    from app.labeldesigner import refusals
    client = make_client(tmp_path, model='DYMO/LabelWriter-SE450', printer='file:///dev/usb/lp_dymo')
    job = {'print_type': 'text', 'text': TEXT}
    r = client.post('/labeldesigner/api/print', data={**job, 'label_size': 'dymo102x152'})
    message = 'The label size 102 x 152 mm shipping (DYMO) cannot be printed on a DYMO LabelWriter SE450'
    assert (r.status_code, r.get_json()['message']) == (400, message)
    r = client.post('/labeldesigner/api/print', data={**job, 'label_size': 'dymo203-28x89', 'high_res': '1'})
    assert r.get_json()['message'] == 'The DYMO LabelWriter SE450 printer cannot print at 600 dpi, turn off 600 dpi printing'
    assert message in refusals.MESSAGES and r.get_json()['message'] in refusals.MESSAGES
    assert printer.written == []


@pytest.mark.parametrize('followed', [False, True], ids=['list', 'followed'])
def test_the_status_of_a_twin_turbo_is_that_of_the_roll_chosen(tmp_path, printer, followed):
    # The right roll is empty, the left one loaded and selected
    printer.by_roll, printer.roll = {b'1': READY, b'2': 0xa1}, b'1'
    device = 'file:///dev/usb/lp_dymo'
    client = make_client(tmp_path, empty_repo=True, model='DYMO/LabelWriter-450-Twin-Turbo', printer=device)

    def errors():
        found = client.get('/labeldesigner/api/printer_status', query_string={'printer': device} if followed else {})
        return next(p for p in found.get_json()['printers'] if p['path'] == device)['errors']
    # Not set, the left roll
    assert errors() == []
    assert printer.written[-1] == ESC * 85 + b'\x1bq1\x1bA'
    client.post('/labeldesigner/api/settings', data={'side': 'right'})
    assert errors() == ['No labels are in the printer']
    assert printer.written[-1] == ESC * 85 + b'\x1bq2\x1bA'
    client.post('/labeldesigner/api/settings', data={'side': ''})
    assert errors() == []
    assert printer.written[-1] == ESC * 85 + b'\x1bq1\x1bA'


@pytest.mark.parametrize('configured', ['printer', 'model unset', 'printers'])
@pytest.mark.parametrize('followed', [False, True], ids=['list', 'followed'])
def test_the_first_status_after_a_restart_is_that_of_the_roll_chosen(tmp_path, monkeypatch, printer, configured, followed):
    printer.by_roll, printer.roll = {b'1': READY, b'2': 0xa1}, b'1'
    device = 'file:///dev/usb/lp_dymo'
    # Kept by its serial number, as the device ID tells it
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: 'MFG:DYMO;MDL:LabelWriter 450 Twin Turbo;SERN:01010112345;')
    model = None if configured == 'model unset' else 'DYMO/LabelWriter-450-Twin-Turbo'
    client = make_client(tmp_path, empty_repo=True, model=model, printer=device)
    if configured == 'printers':
        client.application.config['PRINTERS'] = [('DYMO/LabelWriter-450-Twin-Turbo', device, 'Shipping')]
    assert client.post('/labeldesigner/api/settings', data={'printer': device, 'side': 'right'}).status_code == 200
    # The server starts again
    monkeypatch.setattr(dymo, '_sides', {})
    found = client.get('/labeldesigner/api/printer_status', query_string={'printer': device} if followed else {})
    shown = next(p for p in found.get_json()['printers'] if p['path'] == device)
    assert (shown['model'], shown['errors']) == ('DYMO/LabelWriter-450-Twin-Turbo', ['No labels are in the printer'])
    assert printer.written == [ESC * 85 + b'\x1bq2\x1bA']


@pytest.mark.parametrize('serial', [True, False], ids=['kept by serial number', 'kept by device'])
def test_the_first_status_after_a_restart_of_a_twin_turbo_the_search_finds(tmp_path, monkeypatch, printer, serial):
    from app.labeldesigner import printer as printer_module, routes
    printer.by_roll, printer.roll = {b'1': READY, b'2': 0xa1}, b'1'
    device_id = 'MFG:DYMO;MDL:LabelWriter 450 Twin Turbo;' + ('SERN:01010112345;' if serial else '')
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: device_id)
    if serial:
        device = 'usb://0x0922:0x0022/01010112345'
        monkeypatch.setattr(devices, 'found_usb_printers', lambda: ([device], []))
    else:
        (tmp_path / 'lp0').write_bytes(b'')
        device = f'file://{tmp_path}/lp0'
        monkeypatch.setattr(printer_module, 'USB_PRINTER_DEVICES', [str(tmp_path / 'lp0')])
    client = make_client(tmp_path, empty_repo=True, printer='?')
    if serial:
        assert client.post('/labeldesigner/api/settings', data={'printer': device, 'side': 'right'}).status_code == 200
    else:
        from app.labeldesigner import printer_settings
        (tmp_path / 'label_repo' / printer_settings.FILE).write_text(json.dumps({'printers': {str(tmp_path / 'lp0'): {'side': 'right'}}}))
    # The server starts again
    monkeypatch.setattr(dymo, '_sides', {})
    monkeypatch.setattr(routes, '_NOTED', set())
    monkeypatch.setattr(printer_module, '_cached_printers', [])
    monkeypatch.setattr(printer_module, '_last_scan_ts', 0)
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    shown = next(p for p in found if p['path'] == device)
    assert (shown['model'], shown['errors']) == ('DYMO/LabelWriter-450-Twin-Turbo', ['No labels are in the printer'])
    assert printer.written == [ESC * 85 + b'\x1bq2\x1bA']
