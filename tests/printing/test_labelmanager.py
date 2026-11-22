"""DYMO's LabelManager tape printers: the bytes of a job as DYMO's technical
reference, labelle, LPrint and thermal-label send them, the pins each tape
takes, the status byte, how the printers are recognized apart from the
LabelWriters, and printing through usblp and PyUSB, also on HID."""
import json
import os
import sys
import threading
import time
import tty
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner import drivers, tape
from app.labeldesigner.drivers import backends, devices, dymo, labelmanager
from app.labeldesigner.drivers.labelmanager import protocol
from app.labeldesigner.enums import LabelOrientation
from tests.helpers import make_client, outcome
from tests.printing.test_dymo import _usblp

ESC = b'\x1b'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
PNP, LM280, LM420P, PC2 = labelmanager.MODELS
# A cassette in, no error (the reference, p. 25; thermal-label, protocol.md)
READY = 0x40
# 23 NUL before a job (LPrint, lprint-dymo.c, rstartjob)
SYNC = bytes(23)
ASK = ESC + b'A'
# The real one, which the conftest replaces in each test
FOUND_USB_PRINTERS = devices.found_usb_printers


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    monkeypatch.setattr(protocol, '_asked', {})
    monkeypatch.setattr(protocol, '_answered', {})
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.2)
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0.2)
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(devices, '_usb_device_ids', {})


class _Label:
    """A label as the label designer draws it, across the tape as wide as
    the pins of its tape, rotated ones lying along it, black in the boxes."""
    label_content = None

    def __init__(self, size, black=(), orientation=LabelOrientation.STANDARD):
        self.size, self.black, self.label_orientation = size, black, orientation

    def generate(self):
        img = Image.new('1', self.size, 1)
        for box in self.black:
            img.paste(0, box)
        return img


def _black(across, rows):
    return _Label((across, rows), [(0, 0, across, rows)])


class D1:
    """Reads what a LabelManager gets as the reference describes it: after
    SYN as many bytes as ESC D set are a row, a bare SYN at ESC D 0 feeds
    one, ESC B, C and D take a byte, ESC A is answered with status, None for
    no answer, from the request of each number of after on as it gives,
    bytes outside a row and a command are no command. Each ESC C starts a
    label: [tape type, dot tab, bytes per row, rows, rows fed]."""

    def __init__(self, status=READY):
        self.status, self.after, self.width, self.labels = status, {}, 0, []
        self.asked, self.replies, self.pending = 0, [], b''

    def feed(self, data: bytes):
        self.pending += data
        data, at = self.pending, 0
        while at < len(data):
            if data[at] == 0x16:
                if len(data) - at - 1 < self.width:
                    break
                if self.width:
                    self.labels[-1][3].append(data[at + 1:at + 1 + self.width])
                else:
                    self.labels[-1][4] += 1
                at += 1 + self.width
            elif data[at] == 0x1b:
                command = data[at + 1:at + 2]
                if command == b'A':
                    self.asked += 1
                    self.status = self.after.get(self.asked, self.status)
                    if self.status is not None:
                        self.replies.append(bytes([self.status]))
                    at += 2
                    continue
                if len(data) - at < 3:
                    break
                assert command in (b'B', b'C', b'D'), command
                value = data[at + 2]
                if command == b'C':
                    self.labels.append([value, None, None, [], 0])
                elif command == b'B':
                    self.labels[-1][1] = value
                else:
                    self.width = value
                    if value:
                        self.labels[-1][2] = value
                at += 3
            else:
                at += 1
        self.pending = data[at:]


class _Printer(D1):
    """A printer as D1 reads it that keeps each write."""

    def __init__(self, status=READY):
        super().__init__(status)
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
    monkeypatch.setattr(protocol, 'connect', lambda device, ids: fake)
    return fake


def _send(model, label_size, labels, device='/dev/usb/lp0'):
    queue = SimpleNamespace(device_specifier=device, model=model, label_size=label_size)
    return labelmanager.DRIVER.send(queue, labelmanager.DRIVER.render(queue, [{'label': label, 'cut': True} for label in labels]))


def test_a_12_mm_label_on_a_pnp_as_the_sources_send_it(printer):
    assert _send(PNP, 'd1-12', [_black(64, 10)]) == ''
    assert b''.join(printer.written) == (
        SYNC + ASK
        # Tape type 0, dot tab 0, 8 bytes a row (the reference, p. 23 f.;
        # thermal-label, buildPrinterStream()), each row after SYN
        + b'\x1bC\x00' + b'\x1bB\x00' + b'\x1bD\x08' + (b'\x16' + b'\xff' * 8) * 10
        # 2 x 8.1 mm fed past the cutter at 180 dpi, bare SYN at ESC D 0
        # (labelle, margins.py; the reference, p. 23), 64 rows between
        # two answers at most (labelle, synwait=64)
        + b'\x1bD\x00' + b'\x16' * 54 + ASK + b'\x16' * 61 + ASK)
    assert labelmanager.FEED == 115


@pytest.mark.parametrize('model, label_size, across, dot_tab, row', [
    # Centered on a head of 64 (thermal-label, media.json5 and protocol.ts)
    (PNP, 'd1-9', 48, 1, b'\xff' * 6),
    (PNP, 'd1-6', 32, 2, b'\xff' * 4),
    (LM280, 'd1-9', 48, 1, b'\xff' * 6),
    # Pins 4 to 61 and 18 to 47 on a 280 (labelle issue #69)
    (LM280, 'd1-12-280', 58, 0, b'\x1f' + b'\xff' * 6 + b'\xf8'),
    (LM280, 'd1-6-280', 30, 2, b'\x7f\xff\xff\xfe'),
    # Pins 32 to 105, 35 to 98, 44 to 86 and 3 to 128 on a 420P (issue #62)
    (LM420P, 'd1-12-420p', 74, 3, b'\x01' + b'\xff' * 9 + b'\x80'),
    (LM420P, 'd1-9-420p', 64, 4, b'\x3f' + b'\xff' * 7 + b'\xc0'),
    (LM420P, 'd1-6-420p', 43, 5, b'\x1f' + b'\xff' * 4 + b'\xfc'),
    (LM420P, 'd1-19-420p', 126, 0, b'\x3f' + b'\xff' * 15),
    # Pins 24 to 105, 28 to 91 and 1 to 128 on a PC II (issue #51)
    (PC2, 'd1-12-pc2', 82, 2, b'\x01' + b'\xff' * 10 + b'\x80'),
    (PC2, 'd1-9-pc2', 64, 3, b'\x1f' + b'\xff' * 7 + b'\xe0'),
    (PC2, 'd1-24-pc2', 128, 0, b'\xff' * 16)])
def test_each_tape_is_printed_on_its_pins(printer, model, label_size, across, dot_tab, row):
    assert labelmanager.BY_ID[label_size].dots_printable == (across, 0)
    assert _send(model, label_size, [_black(across, 3)]) == ''
    assert printer.labels == [[0, dot_tab, len(row), [row] * 3, labelmanager.FEED]]
    # No more than the head has (the reference, p. 23)
    assert dot_tab + len(row) <= labelmanager.HEAD_DOTS[model] // 8


def test_standard_labels_come_out_top_first_rotated_ones_with_their_end(printer):
    # The top across the tape, its first pins first
    standard = _Label((64, 20), [(0, 0, 64, 2), (0, 0, 8, 20)])
    # Lying along the tape: its end comes out first, its top on the first pins
    rotated = _Label((30, 64), [(26, 0, 30, 64), (0, 0, 30, 8)], LabelOrientation.ROTATED)
    assert _send(PNP, 'd1-12', [standard, rotated]) == ''
    first, second = (label[3] for label in printer.labels)
    assert first[:2] == [b'\xff' * 8] * 2 and set(first[2:]) == {b'\xff' + bytes(7)}
    assert second[:4] == [b'\xff' * 8] * 4 and set(second[4:]) == {b'\xff' + bytes(7)}
    assert (len(first), len(second)) == (20, 30)


@pytest.mark.parametrize('status, errors', [
    # As thermal-label read them on a PnP (protocol.md, status.ts)
    (0x40, []), (0x00, [protocol.NO_TAPE_TEXT]), (0x50, [protocol.CUTTER_TEXT]), (0x44, [protocol.ERROR_TEXT]),
    # The general error only where no other bit tells why; bits 7, 5, 3, 1
    # and 0 are left alone (the reference, p. 25)
    (0x54, [protocol.CUTTER_TEXT]), (0x04, [protocol.NO_TAPE_TEXT]), (0x10, [protocol.NO_TAPE_TEXT, protocol.CUTTER_TEXT]),
    (0xeb, [])])
def test_status_byte(status, errors):
    assert protocol.errors(status) == errors


def test_a_label_is_not_sent_without_a_cassette(printer):
    printer.status = 0x00
    assert _send(PNP, 'd1-12', [_black(64, 10)]) == protocol.NO_TAPE_TEXT
    assert printer.written == [SYNC, ASK]


def test_a_job_stops_at_an_error_the_printer_tells_while_printing(printer):
    # Its second answer, after the first 64 rows
    printer.after = {2: 0x44}
    assert _send(PNP, 'd1-12', [_black(64, 100)]) == protocol.ERROR_TEXT
    assert printer.asked == 2 and len(printer.labels[0][3]) == 64


def test_a_printer_that_never_answered_is_printed_on_without_waiting(printer):
    printer.status = None
    started = time.monotonic()
    assert _send(PNP, 'd1-12', [_black(64, 10), _black(64, 10)]) == ''
    # The status request and the end of the job only, as LPrint sends a job
    assert printer.asked == 2 and printer.written[:2] == [SYNC, ASK] and printer.written[-1] == ASK
    assert len(printer.written) == 2 + 4 + 1 and time.monotonic() - started < 1
    assert [len(label[3]) for label in printer.labels] == [10, 10]


def test_a_printer_that_stopped_answering_is_not_printed_on(printer):
    key = '/dev/usb/lp0'
    assert protocol.status(printer, key) == READY
    printer.status = None
    assert _send(PNP, 'd1-12', [_black(64, 10)]) == 'Could not send the label to the printer: The printer does not answer'
    assert printer.labels == []


def test_one_status_request_is_outstanding_at_most(printer):
    printer.status = None
    for _ in range(4):
        with pytest.raises(protocol.NoAnswer):
            protocol.status(printer, 'lp0')
    assert printer.asked == 1
    # Its answer came late, it is taken
    printer.replies.append(b'\x00')
    assert protocol.status(printer, 'lp0') == 0x00 and printer.asked == 1


def test_status_of_a_printer(printer):
    state = labelmanager.DRIVER.status('/dev/usb/lp0', PNP)
    assert (state['errors'], state['status_type'], state['media_type'], state['model']) == (
        [], 'Reply to status request', 'Tape', PNP)
    printer.status = 0x00
    state = labelmanager.DRIVER.status('/dev/usb/lp0', PNP)
    assert (state['errors'], state['status_type']) == ([protocol.NO_TAPE_TEXT], 'Error occurred')
    assert printer.written == [ASK, ASK]


# As LPrint lists them in lprint-dymo.h, in other spellings, and devices of
# DYMO it does not print on or of other makers
@pytest.mark.parametrize('device_id, model', [
    ('MFG:DYMO;MDL:LabelMANAGER PNP ;', PNP),
    ('MFG:DYMO;MDL:LabelMANAGER PC II ;', PC2),
    ('MANUFACTURER:dymo;MODEL: LabelManager  PnP;CLASS:PRINTER;', PNP),
    ('MFG:DYMO;MDL:LabelMANAGER PC ;', None),
    ('MFG:DYMO;MDL:LabelMANAGER 400 ;', None),
    ('MFG:DYMO;MDL:LabelPOINT 350 ;', None),
    ('MFG:DYMO;MDL:LabelWriter 450 DUO Tape;', None),
    ('MFG:DYMO;MDL:LabelWriter 450;', None),
    ('MFG:ACME;MDL:LabelManager PnP;', None),
    ('MDL:LabelManager PnP;', None)])
def test_known_by_its_device_id(device_id, model):
    assert labelmanager.DRIVER.model_in(device_id) == model
    # A LabelWriter is no LabelManager and the other way round
    assert dymo.DRIVER.model_in(device_id) is None or model is None
    if model:
        assert drivers.told_model(device_id) == model


# labelle's constants.py: switched to printing and before
@pytest.mark.parametrize('device, model', [
    ('usb://0x0922:0x1001', PNP), ('usb://0x0922:0x1002/12345', PNP),
    ('usb://0x0922:0x1005', LM280), ('usb://0x0922:0x1006', LM280),
    ('usb://0x0922:0x1003', LM420P), ('usb://0x0922:0x1004', LM420P),
    ('usb://0x0922:0x001C', PC2),
    # The LabelManager PC, the Wireless PnP, LabelWriters and others
    ('usb://0x0922:0x0011', None), ('usb://0x0922:0x1008', None), ('usb://0x0922:0x0020', None),
    ('usb://0x0922:0x0023', None), ('usb://0x04f9:0x1002', None), ('usbip://192.0.2.5', None), ('simulation', None)])
def test_a_printer_that_tells_no_device_id_is_known_by_its_usb_ids(monkeypatch, device, model):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: '')
    assert labelmanager.DRIVER.model_of(device) == model
    if model:
        assert dymo.DRIVER.model_of(device) is None
        # Its tape commands only, also where another model is configured
        assert drivers.resolve(device, 'DYMO/LabelWriter-450') == (labelmanager.DRIVER, model)


@pytest.mark.parametrize('device_id, model', [
    # A LabelManager its device ID does not name, by its product ID
    ('MFG:DYMO;MDL:LabelManager 280;', LM280), ('MFG:DYMO;CMD:;', LM280),
    ('MFG:DYMO;MDL:LabelWriter 450;', None), ('MFG:ACME;MDL:Tape 280;', None)])
def test_a_device_id_counts_before_the_usb_ids(device_id, model):
    assert labelmanager.DRIVER.model_of('usb://0x0922:0x1006', device_id) == model


def test_usblp_tells_the_usb_ids(tmp_path, monkeypatch):
    _usblp(tmp_path, monkeypatch, '0922', '1004')
    assert labelmanager.DRIVER.model_of('file:///dev/usb/lp0') == LM420P
    assert drivers.resolve('file:///dev/usb/lp0', 'Unknown') == (labelmanager.DRIVER, LM420P)
    assert dymo.DRIVER.model_of('file:///dev/usb/lp0') is None


@pytest.mark.parametrize('model, sizes', [
    (PNP, ['d1-12', 'd1-6', 'd1-9']), (LM280, ['d1-12-280', 'd1-6-280', 'd1-9']),
    (LM420P, ['d1-12-420p', 'd1-6-420p', 'd1-9-420p', 'd1-19-420p']), (PC2, ['d1-12-pc2', 'd1-9-pc2', 'd1-24-pc2'])])
def test_tapes_of_the_printers(model, sizes):
    from app.labeldesigner.label_names import label_dpi, label_name
    assert tape.takes(model) == sizes
    # 12 mm assumed where none is set
    assert tape.assumed_size(model, '62') == sizes[0]
    assert {label_name(tape.SIZES[size]) for size in sizes} <= {f'{w} mm tape (DYMO D1)' for w in (6, 9, 12, 19, 24)}
    assert all(label_dpi(size, False) == 180 for size in sizes)


@pytest.mark.parametrize('saved, model, printed', [
    ('d1-12', LM280, 'd1-12-280'), ('d1-12-280', PNP, 'd1-12'), ('d1-12-pc2', LM420P, 'd1-12-420p'),
    ('d1-9', PC2, 'd1-9-pc2'), ('d1-9-420p', LM280, 'd1-9'), ('d1-6-420p', LM280, 'd1-6-280'),
    # Tapes the model does not take, and other paper, stay as saved
    ('d1-24-pc2', PNP, 'd1-24-pc2'), ('d1-6', PC2, 'd1-6'), ('pt12', PNP, 'pt12'), ('12', LM420P, '12')])
def test_a_label_prints_on_the_same_tape_of_another_model(saved, model, printed):
    assert labelmanager.DRIVER.label_for(saved, model) == printed
    assert tape.same_paper(saved, model) == printed


def test_a_printer_of_no_known_model_takes_each_tape_once():
    assert [size for size in tape.takes(None) if size.startswith('d1-')] == ['d1-12', 'd1-6', 'd1-9', 'd1-19-420p', 'd1-24-pc2']


def test_capabilities():
    caps = labelmanager.DRIVER.capabilities(LM280)
    # Cut by hand after the tape between head and cutter
    assert (caps['cutter'], caps['manual_cutter'], caps['leaves_turned'], caps['lead_mm']) == (False, True, True, 8.1)
    assert (caps['qualities'], caps['high_res'], caps['vendor'], caps['model_shown']) == (['quality'], False, 'DYMO', 'LabelManager 280')
    # mm/s as DYMO names them, and the tape fed after a label
    assert labelmanager.DRIVER.print_speed(LM280, False, False) == (15, pytest.approx(16.2 / 15))
    assert [labelmanager.DRIVER.print_speed(model, True, False)[0] for model in labelmanager.MODELS] == [12, 15, 12, 10]


@pytest.mark.parametrize('model, name', [(PNP, 'LabelManager PnP'), (LM280, 'LabelManager 280'),
                                         (LM420P, 'LabelManager 420P'), (PC2, 'LabelManager PC II')])
def test_models_are_named_plainly(model, name):
    from app.labeldesigner.drivers.base import shown
    assert shown(model) == f'DYMO {name}'


def test_refusals_name_the_printer_plainly(tmp_path, printer):
    from app.labeldesigner import refusals
    client = make_client(tmp_path, model=PNP, printer='file:///dev/usb/lp_dymo')
    job = {'print_type': 'text', 'text': TEXT}
    r = client.post('/labeldesigner/api/print', data={**job, 'label_size': 'd1-19-420p'})
    message = 'The label size 19 mm tape (DYMO D1) cannot be printed on a DYMO LabelManager PnP'
    assert (r.status_code, r.get_json()['message']) == (400, message)
    r = client.post('/labeldesigner/api/print', data={**job, 'label_size': 'd1-12', 'high_res': '1'})
    assert r.get_json()['message'] == 'The DYMO LabelManager PnP printer cannot print at 600 dpi, turn off 600 dpi printing'
    assert message in refusals.MESSAGES and r.get_json()['message'] in refusals.MESSAGES
    assert set(protocol.TEXTS) <= set(refusals.MESSAGES)
    assert printer.written == []


class FakeLabelManager(D1):
    """A LabelManager as D1 reads it on a pseudo terminal, as usblp's
    device, which keeps what it gets."""

    def __init__(self, status=READY):
        super().__init__(status)
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
def labelmanager_on_usblp():
    fake = FakeLabelManager()
    yield fake
    fake.close()


def test_two_copies_printed_through_usblp(tmp_path, labelmanager_on_usblp):
    fake = labelmanager_on_usblp
    client = make_client(tmp_path, model=LM420P, printer=f'file://{fake.path}')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'd1-12-420p', 'print_type': 'text', 'text': TEXT,
                                                             'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not fake.data.endswith(ASK):
        time.sleep(0.05)
    assert fake.data.startswith(SYNC + ASK)
    assert [(tape_type, dot_tab, width, fed) for tape_type, dot_tab, width, _, fed in fake.labels] == [(0, 3, 11, 115)] * 2
    rows = len(fake.labels[0][3])
    # Asked before the job, then after each 64 rows at most
    assert rows and fake.asked == 1 + -(-(rows + 115) * 2 // 64)


class Endpoint:
    def __init__(self, address, printer=None):
        self.bEndpointAddress, self.printer = address, printer

    def write(self, data, timeout):
        self.printer.write(data)

    def read(self, length, timeout):
        if not self.printer.replies:
            raise USBTimeoutError('Operation timed out')
        return self.printer.replies.pop(0)


class Interface:
    def __init__(self, kind, number, printer):
        self.bInterfaceClass, self.bInterfaceNumber, self.bAlternateSetting = kind, number, 0
        # Bulk on the printer interface, interrupt on HID, both 64 bytes
        self.endpoints = [Endpoint(0x85 if kind == 7 else 0x81, printer), Endpoint(0x05 if kind == 7 else 0x01, printer)]


class Device:
    """A LabelManager on USB with interfaces of the classes given, 7 a
    printer, 8 a USB drive, 3 HID, each held by a driver of the kernel."""

    def __init__(self, product, classes, serial='01234'):
        self.idVendor, self.idProduct, self.bDeviceClass, self.iSerialNumber, self.serial = 0x0922, product, 0, 3, serial
        self.printer = _Printer()
        self.interfaces = [Interface(kind, number, self.printer) for number, kind in enumerate(classes)]
        self.bound, self.calls = set(range(len(classes))), []

    def __iter__(self):
        return iter([self])

    def get_active_configuration(self):
        return self

    def is_kernel_driver_active(self, number):
        return number in self.bound

    def detach_kernel_driver(self, number):
        self.calls.append(('detach', number))
        self.bound.discard(number)

    def attach_kernel_driver(self, number):
        self.calls.append(('attach', number))
        self.bound.add(number)


class USBError(OSError):
    pass


class USBTimeoutError(USBError):
    pass


@pytest.fixture
def pyusb(monkeypatch):
    """A fake PyUSB with the devices of its list."""
    found = []
    libusb = object()

    def find(find_all, custom_match, backend, idVendor=None):
        assert backend is libusb
        return [device for device in found if idVendor in (None, device.idVendor) and custom_match(device)]

    def find_descriptor(parent, bInterfaceClass=None, custom_match=None):
        if bInterfaceClass is not None:
            return next((i for i in parent.interfaces if i.bInterfaceClass == bInterfaceClass), None)
        return next((e for e in parent.endpoints if custom_match(e)), None)
    usb = types.ModuleType('usb')
    usb.core = types.SimpleNamespace(find=find, USBError=USBError, USBTimeoutError=USBTimeoutError, NoBackendError=USBError)
    usb.util = types.SimpleNamespace(
        find_descriptor=find_descriptor, endpoint_direction=lambda address: address & 0x80, ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        dispose_resources=lambda device: device.calls.append('dispose'), get_string=lambda device, index: device.serial)
    monkeypatch.setitem(sys.modules, 'usb', usb)
    monkeypatch.setitem(sys.modules, 'usb.core', usb.core)
    monkeypatch.setitem(sys.modules, 'usb.util', usb.util)
    monkeypatch.setattr(backends, '_libusb', lambda: libusb)
    return found


@pytest.mark.parametrize('classes, used', [
    # Switched to printing: its printer interface; else HID, as labelle
    # prints on it, never the USB drive's interface
    ([7, 8, 3], 0), ([8, 3], 1), ([3], 0)])
def test_printed_on_its_printer_or_hid_interface(pyusb, classes, used):
    device = Device(0x1002 if 7 in classes else 0x1001, classes)
    pyusb.append(device)
    queue = SimpleNamespace(device_specifier='usb://0x0922:0x%04x/01234' % device.idProduct, model=PNP, label_size='d1-12')
    assert labelmanager.DRIVER.status(queue.device_specifier, PNP)['errors'] == []
    assert labelmanager.DRIVER.send(queue, labelmanager.DRIVER.render(queue, [{'label': _black(64, 10), 'cut': True}])) == ''
    assert [len(label[3]) for label in device.printer.labels] == [10]
    # Its driver given back after each
    assert device.calls == [('detach', used), 'dispose', ('attach', used)] * 2
    assert device.bound == set(range(len(classes)))


def test_a_printer_that_shows_a_usb_drive_only_is_refused(pyusb, tmp_path):
    device = Device(0x1005, [8])
    pyusb.append(device)
    address = 'usb://0x0922:0x1005'
    state = labelmanager.DRIVER.status(address, LM280)
    assert (state['errors'], state['status_type']) == ([protocol.USB_DRIVE_TEXT], 'Error occurred')
    queue = SimpleNamespace(device_specifier=address, model=LM280, label_size='d1-12-280')
    assert labelmanager.DRIVER.send(queue, labelmanager.DRIVER.render(queue, [{'label': _black(58, 10), 'cut': True}])) == \
        protocol.USB_DRIVE_TEXT
    # Its USB drive is left alone
    assert device.calls == [] and device.bound == {0}


def test_found_on_usb_also_on_hid(pyusb, tmp_path, monkeypatch):
    from app.labeldesigner import printer as printer_module
    # Not switched to printing, switched with usblp holding it, and one
    # showing a USB drive only
    hid, held, drive = Device(0x1001, [8, 3], serial='A1'), Device(0x1004, [7, 8, 3], serial='B2'), Device(0x1005, [8], serial='C3')
    pyusb.extend([hid, held, drive])
    assert backends.found_printers(labelmanager.DRIVER.usb_ids) == (
        [('usb://0x0922:0x1001/A1', ''), ('usb://0x0922:0x1005/C3', '')], [])
    # Without the IDs only printers
    assert backends.found_printers() == ([], [])
    monkeypatch.setattr(devices, 'found_usb_printers', FOUND_USB_PRINTERS)
    monkeypatch.setattr(printer_module, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(printer_module, '_last_scan_ts', 0)
    monkeypatch.setattr(printer_module, '_cached_printers', [])
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
    assert [(p['path'], p['model'], p['errors']) for p in found] == [
        ('usb://0x0922:0x1001/A1', PNP, []), ('usb://0x0922:0x1005/C3', LM280, [protocol.USB_DRIVE_TEXT]), ('simulation', 'QL-800', [])]
    assert found[0]['tape']['id'] == 'd1-12' and found[0]['tape']['assumed']
    assert held.calls == []


@pytest.mark.parametrize('vendor, product', [
    # A security key, a DYMO scale: HID devices, no LabelManagers
    (0x1050, 0x0407), (0x0922, 0x8003)])
def test_a_request_naming_another_hid_device_is_refused_untouched(pyusb, tmp_path, vendor, product):
    other = Device(product, [3])
    other.idVendor = vendor
    pyusb.append(other)
    address = 'usb://0x%04x:0x%04x' % (vendor, product)
    client = make_client(tmp_path)
    r = client.post('/labeldesigner/api/print', data={'printer': address, 'model': PNP, 'label_size': 'd1-12', 'print_type': 'text',
                                                      'text': TEXT})
    assert outcome(client, r) == ('failed', 'Could not send the label to the printer: The printer is switched off or not connected')
    with pytest.raises(FileNotFoundError):
        labelmanager.DRIVER.status(address, PNP)
    # Neither its driver taken away nor anything written
    assert other.calls == [] and other.printer.written == []


def test_a_printer_of_another_maker_is_reached_on_its_printer_interface_only(pyusb):
    other = Device(0x2042, [7, 3])
    other.idVendor = 0x04f9
    pyusb.append(other)
    with protocol.connect('usb://0x04f9:0x2042', labelmanager.DRIVER.usb_ids) as c:
        assert c.number == 0
    hid = Device(0x2042, [3])
    hid.idVendor = 0x04f9
    pyusb[:] = [hid]
    with pytest.raises(FileNotFoundError):
        protocol.connect('usb://0x04f9:0x2042', labelmanager.DRIVER.usb_ids)
    assert hid.calls == []


def test_a_job_is_printed_also_where_the_kernel_does_not_take_its_interface_back(pyusb, caplog):
    device = Device(0x1001, [8, 3])

    def attach(number):
        raise USBError('No such device (it may have been disconnected)')
    device.attach_kernel_driver = attach
    pyusb.append(device)
    address = 'usb://0x0922:0x1001/01234'
    assert labelmanager.DRIVER.status(address, PNP)['errors'] == []
    queue = SimpleNamespace(device_specifier=address, model=PNP, label_size='d1-12')
    assert labelmanager.DRIVER.send(queue, labelmanager.DRIVER.render(queue, [{'label': _black(64, 10), 'cut': True}])) == ''
    assert [len(label[3]) for label in device.printer.labels] == [10]
    assert 'not given back' in caplog.text
