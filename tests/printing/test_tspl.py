"""Label printers on TSPL: the bytes of a label as TSC's TSPL/TSPL2
programming manual (version 3.0) gives its commands, the status byte of
<ESC>!?, detection by device ID, and printers behind a device file, PyUSB
and the network."""
import json
import os
import select
import socket
import threading
import time
import tty
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner.drivers import backends, devices, diecut, progress, tspl, zpl
from app.labeldesigner.drivers.tspl import protocol
from tests.helpers import make_client, outcome, without_status

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    monkeypatch.setattr(tspl, '_ask_after', {})
    monkeypatch.setattr(tspl, '_names', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.5)
    # The speed of a job here is not that of a later printer on the same terminal
    monkeypatch.setattr(progress, '_factors', {})


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(protocol, 'write', lambda device, data: out.append((device, data)))
    return out


def requests(data: bytes) -> tuple[list[bytes], bytes]:
    """The requests in data in their order, and what follows the last one."""
    found = []
    while at := [(data.find(asked), asked) for asked in (protocol.STATUS, protocol.MODEL_NAME) if asked in data]:
        index, asked = min(at)
        found.append(asked)
        data = data[index + len(asked):]
    return found, data


class FakeTspl:
    """What a TSPL printer answers: its status byte to <ESC>!?, its model
    name to ~!T while it is ready, None for a printer that does not answer
    it. 'received' is all that reached it."""

    def __init__(self, status=0, name=b'TE200\r\n'):
        self.status, self.name = status, name
        self.received = b''
        self._unanswered = b''

    def heard(self, data: bytes) -> bytes:
        self.received += data
        asked, self._unanswered = requests(self._unanswered + data)
        answers = {protocol.STATUS: b'' if self.status is None else bytes([self.status]),
                   protocol.MODEL_NAME: self.name if self.name is not None and self.status == 0 else b''}
        return b''.join(answers[request] for request in asked)

    def wait_for(self, part: bytes) -> bytes:
        """All that reached the printer once part did, read aside after the
        job was sent."""
        deadline = time.monotonic() + 5
        while part not in self.received and time.monotonic() < deadline:
            time.sleep(0.01)
        return self.received

    @property
    def asked(self) -> list[bytes]:
        return requests(self.received)[0]


class FakeUsblp(FakeTspl):
    """The printer behind a device file as usblp gives it, a terminal in raw
    mode here."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.master, self._slave = os.openpty()
        tty.setraw(self._slave)
        self.path = os.ttyname(self._slave)
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self._closed:
            if not select.select([self.master], [], [], 0.02)[0]:
                continue
            try:
                data = os.read(self.master, 65536)
            except OSError:
                return
            if answer := self.heard(data):
                os.write(self.master, answer)

    def close(self):
        self._closed = True
        os.close(self._slave)
        os.close(self.master)


class FakeNetworkTspl(FakeTspl):
    """The printer on the network, on its own port."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port = self.server.getsockname()[1]
        self.connections = 0
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.connections += 1
            with conn:
                while data := conn.recv(65536):
                    if answer := self.heard(data):
                        conn.sendall(answer)

    def close(self):
        self.server.close()


@pytest.fixture
def usblp():
    printer = FakeUsblp()
    yield printer
    printer.close()


@pytest.fixture
def on_network(monkeypatch):
    printer = FakeNetworkTspl()
    monkeypatch.setattr(protocol, 'PORT', printer.port)
    yield printer
    printer.close()


def test_label_as_a_bitmap():
    # BITMAP takes the width in bytes and the height in dots, a 0 bit is
    # printed (manual pp. 67-69), each command line ends with CR LF (p. X)
    assert protocol.label([b'\x80\x00', b'\x00\x01'], 2, (50, 30), 3, 'gap') == (
        b'SIZE 50 mm,30 mm\r\nGAP 3 mm,0 mm\r\nDIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\n'
        b'BITMAP 0,0,2,2,0,\x7f\xff\xff\xfe\r\nPRINT 1,1\r\n')


@pytest.mark.parametrize('sensing, command', [
    ('gap', b'GAP 3 mm,0 mm\r\n'), ('mark', b'BLINE 3 mm,0 mm\r\n'), ('endless', b'GAP 0,0\r\n'), (None, b'')])
def test_how_the_printer_finds_the_labels(sensing, command):
    # GAP (p. 3, 5), BLINE (p. 9) and a continuous roll as GAP 0,0 (p. 5).
    # Not set, the printer finds them as it is set up.
    assert protocol.label([b'\x80'], 1, (50, 30), 3, sensing) == (
        b'SIZE 50 mm,30 mm\r\n' + command + b'DIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\nBITMAP 0,0,1,1,0,\x7f\r\nPRINT 1,1\r\n')


def _label(width: int, length: int):
    """A label as the printer gets it, its first eight dots and its last dot
    across black in its first row."""
    img = Image.new('1', (width, length), 1)
    for x in (*range(8), width - 1):
        img.putpixel((x, 0), 0)
    return SimpleNamespace(label_content=None, generate=lambda: img)


@pytest.mark.parametrize('sensing, command', [
    ('gap', b'GAP 3 mm,0 mm\r\n'), ('mark', b'BLINE 3 mm,0 mm\r\n'), ('endless', b'GAP 0,0\r\n'), (None, b'')])
@pytest.mark.parametrize('label_size, size, dots, rows, last', [
    # 4 x 6 in: 102 mm are 816 dots at 203 dpi, 1205 at 300 dpi sent as 151
    # bytes, the 3 dots after the label white
    ('tspl102x152', b'102 mm,152 mm', 816, 1216, 0xfe),
    ('tspl300-102x152', b'102 mm,152 mm', 1205, 1795, 0xf7),
    ('tspl50x30', b'50 mm,30 mm', 400, 240, 0xfe),
    ('tspl300-50x30', b'50 mm,30 mm', 591, 354, 0xfd)])
def test_job_of_a_label(sensing, command, label_size, size, dots, rows, last):
    queue = SimpleNamespace(label_size=label_size, sensing=sensing)
    job = tspl.DRIVER.render(queue, [{'label': _label(dots, rows), 'cut': True}])
    width = (dots + 7) // 8
    bitmap = b'\x00' + b'\xff' * (width - 2) + bytes([last]) + b'\xff' * (width * (rows - 1))
    assert [data for data, _ in job] == [
        b'SIZE ' + size + b'\r\n' + command + b'DIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\n'
        + f'BITMAP 0,0,{width},{rows},0,'.encode() + bitmap + b'\r\nPRINT 1,1\r\n']
    assert tspl.DRIVER.label_lengths(job) == [pytest.approx(int(label_size.rpartition('x')[2]), abs=0.1)]


def test_end_of_a_lying_label_leaves_first():
    class _Label:
        label_content = None

        def generate(self):
            # A 102 x 152 mm label drawn lying, its start black
            img = Image.new('RGB', (1216, 816), 'white')
            img.paste((0, 0, 0), (0, 0, 10, 816))
            return img
    _, rows, width = tspl.label_rows(_Label(), 'tspl102x152')
    assert (len(rows), width) == (1216, 102)
    # The first row leaves the printer first with DIRECTION 0 (p. XI, 17):
    # the end of the label, as on the other printers
    assert not any(rows[0]) and all(byte == 0xff for byte in rows[-1])


def test_top_of_a_label_wider_than_long_leaves_first():
    class _Label:
        label_content = None

        def generate(self):
            img = Image.new('RGB', (816, 408), 'white')
            img.paste((0, 0, 0), (0, 0, 816, 10))
            return img
    _, rows, width = tspl.label_rows(_Label(), 'tspl102x51')
    assert (len(rows), width) == (408, 102)
    assert all(byte == 0xff for byte in rows[0]) and not any(rows[-1])


# Every value of the table of <ESC>!? (p. 122)
HEAD = 'The printhead is open'
JAM = 'The labels are stuck or the printer cannot find where each one starts'
EMPTY = 'No labels are in the printer'
RIBBON = 'The ribbon has run out'
PAUSED = 'The printer is paused'
OTHER = 'The printer reports an error, switch it off and on'


@pytest.mark.parametrize('answer, errors, status_type', [
    (0x00, [], 'Reply to status request'),
    (0x01, [HEAD], 'Error occurred'),
    (0x02, [JAM], 'Error occurred'),
    (0x03, [HEAD, JAM], 'Error occurred'),
    (0x04, [EMPTY], 'Error occurred'),
    (0x05, [HEAD, EMPTY], 'Error occurred'),
    (0x08, [RIBBON], 'Error occurred'),
    (0x09, [HEAD, RIBBON], 'Error occurred'),
    (0x0a, [JAM, RIBBON], 'Error occurred'),
    (0x0b, [HEAD, JAM, RIBBON], 'Error occurred'),
    (0x0c, [EMPTY, RIBBON], 'Error occurred'),
    (0x0d, [HEAD, EMPTY, RIBBON], 'Error occurred'),
    (0x10, [PAUSED], 'Error occurred'),
    (0x20, [], devices.BUSY_STATUS_TYPE),
    (0x80, [OTHER], 'Error occurred')])
def test_every_status_the_printer_tells(monkeypatch, answer, errors, status_type):
    assert protocol.errors(answer) == errors
    monkeypatch.setattr(protocol, 'ask', lambda device, name=False: (answer, None))
    state = tspl.DRIVER.status('tcp://192.0.2.1', 'TSC/TSPL')
    assert (state['errors'], state['status_type']) == (errors, status_type)
    assert state['phase_type'] == ('Printing state' if answer == 0x20 else 'Waiting to receive')
    # The roll is not told, it is named as set for the printer
    assert (state['media_width'], state['media_length'], state['media_type']) == (None, 0, 'Die-cut labels')


@pytest.mark.parametrize('device_id, model', [
    ('MFG:TSC;CMD:TSPL2;MDL:TE200;CLS:PRINTER;', 'TSC/TSPL'),
    ('MANUFACTURER:TSC;COMMAND SET:TSPL;MODEL:TTP-244 Pro;', 'TSC/TSPL'),
    ('MFG:TSC;CMD:EPL2,TSPL-EZ,ZPL2;MDL:TE210;', 'TSC/TSPL'),
    ('MFG:TSC;CMD:tspl2;MDL:TE300 300 dpi;', 'TSC/TSPL-300'),
    ('MFG:TSC;CMD:TSPL2;MDL:TE200-203dpi;', 'TSC/TSPL'),
    # A printer naming both is taken for TSPL
    ('MFG:Printer;CMD:ZPL,TSPL;MDL:LP-420;', 'TSC/TSPL'),
    # As LPrint lists them in lprint-tspl.h, also spelled otherwise
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP,XL;', 'TSC/TSPL'),
    ('MFG:Marklife ;MDL:X4;CMD:XPP,XL;', 'TSC/TSPL'),
    ('MFG:;MDL:PM-241-BT;CMD:XPP,XL;', 'TSC/TSPL'),
    ('MFG:VEVOR ;CMD: ;MODEL:Y428BT;DES:LabelPrinter;', 'TSC/TSPL-300'),
    ('MFG:printer;MDL:thermal  printer;CMD:XPP, XL;', 'TSC/TSPL'),
    ('MFG:Vevor;MODEL:y428bt;', 'TSC/TSPL-300'),
    # Of a resolution without a model, its labels would come out a third as large
    ('MFG:TSC;CMD:TSPL2;MDL:TX600 600 dpi;', None),
    # Others: another command set, a command set that only starts alike, the
    # devices LPrint lists with something else
    ('MFG:TSC;CMD:TSP;MDL:TE200;', None),
    ('MFG:Printer;CMD:XTSPL;MDL:TE200;', None),
    ('MFG:Printer;MDL:Thermal Printer;', None),
    ('MFG:Printer;MDL:Thermal Printer;CMD:XPP;', None),
    ('MFG:Marklife;MDL:X5;CMD:XPP,XL;', None),
    ('MFG:VEVOR;CMD:ESC/POS;MODEL:Y428BT;', 'Epson/ESC-POS'),
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;', 'Zebra/ZPL'),
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;', 'QL-700'),
    ('', None)])
def test_known_by_its_device_id(device_id, model):
    from app.labeldesigner import drivers
    assert drivers.told_model(device_id) == model
    assert tspl.DRIVER.model_in(device_id) == (model if model and model.startswith('TSC/') else None)


def test_known_by_the_device_id_of_its_usb_device(monkeypatch):
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: 'MFG:TSC;CMD:TSPL2;MDL:TE200;')
    assert tspl.DRIVER.model_of('file:///dev/usb/lp0') == 'TSC/TSPL'
    assert tspl.DRIVER.model_of('file:///dev/usb/lp0', 'MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;') is None
    assert tspl.DRIVER.model_of(None) is None


@pytest.mark.parametrize('saved, model, printed', [
    ('zpl102x152', 'TSC/TSPL', 'tspl102x152'),
    ('zpl102x152', 'TSC/TSPL-300', 'tspl300-102x152'),
    ('zpl300-102x152', 'TSC/TSPL', 'tspl102x152'),
    ('tspl102x152', 'Zebra/ZPL', 'zpl102x152'),
    ('tspl300-102x152', 'Zebra/ZPL-300', 'zpl300-102x152'),
    ('tspl102x152', 'TSC/TSPL-300', 'tspl300-102x152'),
    ('tspl300-50x30', 'TSC/TSPL', 'tspl50x30'),
    ('tspl50x30', 'TSC/TSPL', 'tspl50x30'),
    # Other paper stays
    ('62', 'TSC/TSPL', '62'),
    ('escpos80', 'TSC/TSPL', 'escpos80')])
def test_label_for_the_same_paper_of_another_driver(saved, model, printed):
    from app.labeldesigner import drivers, tape
    assert drivers.for_model(model).label_for(saved, model) == printed
    assert tape.same_paper(saved, model) == printed


def test_a_saved_4x6_label_prints_on_either(tmp_path, sent, monkeypatch):
    monkeypatch.setattr(zpl.protocol, 'write', protocol.write)
    for model, printed in (('TSC/TSPL', b'SIZE 102 mm,152 mm\r\n'), ('Zebra/ZPL', b'^XA\n^PW816\n^LL1216\n')):
        client = make_client(tmp_path, model=model, printer='file:///dev/usb/lp_label')
        response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl102x152', 'print_type': 'text', 'text': TEXT})
        assert outcome(client, response) == ('printed', '')
        assert sent.pop()[1].startswith(printed)


def test_label_sizes_of_both_models():
    from app.labeldesigner.label_names import label_dpi, label_fits, label_name
    low = [label for label in tspl.LABELS if label.models == ('TSC/TSPL',)]
    high = [label for label in tspl.LABELS if label.models == ('TSC/TSPL-300',)]
    assert [label.identifier for label in low] == [f'tspl{w}x{l}' for w, l in tspl.SIZES]
    assert [label.identifier for label in high] == [f'tspl300-{w}x{l}' for w, l in tspl.SIZES]
    assert [label.tape_size for label in low] == [label.tape_size for label in high] == [label.tape_size for label in zpl.LABELS][:len(low)]
    assert label_name(low[1]) == label_name(high[1]) == '102 x 152 mm die-cut (TSPL)'
    by_id = {label.identifier: label for label in tspl.LABELS}
    # A printhead of 104 mm as for ZPL printers
    assert by_id['tspl105x148'].dots_printable == (832, 1184) and by_id['tspl300-105x148'].dots_printable == (1228, 1748)
    assert all(label.gap == 3 for label in tspl.LABELS)
    assert (label_dpi('tspl300-57x32', False), label_dpi('tspl57x32', False)) == (300, 203)
    assert label_fits(by_id['tspl300-57x32'], 'TSC/TSPL-300') and not label_fits(by_id['tspl300-57x32'], 'TSC/TSPL')


def test_300_dpi_model_is_shown_as_the_other_one():
    from app.labeldesigner.drivers.base import shown
    assert shown('TSC/TSPL-300') == shown('TSC/TSPL') == 'TSC TSPL'
    caps = tspl.DRIVER.capabilities('TSC/TSPL-300')
    assert (caps['model_shown'], caps['sensing'], caps['cutter'], caps['leaves_turned'], caps['qualities']) == (
        'TSPL', True, False, True, ['quality'])
    assert tspl.DRIVER.resolution_problem('TSC/TSPL', True) == 'The TSC TSPL printer cannot print at 600 dpi, turn off 600 dpi printing'
    assert tspl.DRIVER.reports_tape is False


def test_status_and_name_of_a_printer_behind_a_device_file(usblp):
    device = f'file://{usblp.path}'
    state = tspl.DRIVER.status(device, 'TSC/TSPL')
    assert (state['errors'], state['status_type'], state['device_name']) == ([], 'Reply to status request', 'TE200')
    usblp.status = 0x04
    state = tspl.DRIVER.status(device, 'TSC/TSPL')
    assert state['errors'] == [EMPTY] and state['device_name'] == 'TE200'
    # ~!T once
    assert usblp.asked == [protocol.STATUS, protocol.MODEL_NAME, protocol.STATUS]


def test_name_is_asked_once_the_printer_is_ready(usblp):
    # ~ commands are answered only while the printer is ready (p. X)
    device = f'file://{usblp.path}'
    usblp.status = 0x01
    assert 'device_name' not in tspl.DRIVER.status(device, 'TSC/TSPL')
    usblp.status = 0
    assert tspl.DRIVER.status(device, 'TSC/TSPL')['device_name'] == 'TE200'
    assert usblp.asked == [protocol.STATUS, protocol.STATUS, protocol.MODEL_NAME]


def test_printer_without_a_name_is_asked_for_it_once(usblp):
    usblp.name = None
    device = f'file://{usblp.path}'
    for _ in range(3):
        assert 'device_name' not in tspl.DRIVER.status(device, 'TSC/TSPL')
    assert usblp.asked == [protocol.STATUS, protocol.MODEL_NAME, protocol.STATUS, protocol.STATUS]


def test_printer_named_by_its_device_id_is_not_asked_for_its_name(usblp, monkeypatch):
    monkeypatch.setattr(devices, 'known_device_id', lambda device: 'MFG:TSC;CMD:TSPL2;MDL:TTP-244 Pro;')
    state = tspl.DRIVER.status(f'file://{usblp.path}', 'TSC/TSPL')
    assert state['device_name'] == 'TSC TTP-244 Pro' and usblp.asked == [protocol.STATUS]


def test_an_answer_from_before_is_not_taken(usblp):
    os.write(usblp.master, b'\x04')
    assert tspl.DRIVER.status(f'file://{usblp.path}', 'TSC/TSPL')['errors'] == []


def test_label_printed_on_a_printer_behind_a_device_file(tmp_path, usblp):
    client = make_client(tmp_path, model='TSC/TSPL', printer=f'file://{usblp.path}')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'tspl102x76', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    received = usblp.wait_for(b'PRINT 1,1\r\n')
    job = received[received.index(b'SIZE'):]
    assert job.startswith(b'SIZE 102 mm,76 mm\r\nDIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\nBITMAP 0,0,102,608,0,')
    assert len(job) == len(b'SIZE 102 mm,76 mm\r\nDIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\nBITMAP 0,0,102,608,0,') + 102 * 608 + len(b'\r\nPRINT 1,1\r\n')
    assert job.endswith(b'\r\nPRINT 1,1\r\n')


def test_printer_that_never_answers_is_printed_on(tmp_path):
    # A file has nothing to read, as a printer that never answers <ESC>!?
    device = tmp_path / 'lp0'
    device.write_bytes(b'')
    client = make_client(tmp_path, model='TSC/TSPL', printer=f'file://{device}')
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (found['errors'], found['model']) == ([], 'TSC/TSPL')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'tspl57x32', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    assert b'SIZE 57 mm,32 mm\r\n' in device.read_bytes()


def test_printer_on_usb_that_is_not_there(tmp_path, sent):
    client = make_client(tmp_path, model='TSC/TSPL', printer='file:///dev/usb/lp_tspl')
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer is switched off or not connected']


def test_a_printer_that_stops_answering(monkeypatch):
    # Asked again only 20 s after a request left unanswered, meanwhile
    # told as it was
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(tspl, 'time', clock)
    answers = [(4, None), (None, None), (None, None), (0, None)]
    asked = []
    monkeypatch.setattr(protocol, 'ask', lambda device, name=False: asked.append(clock.now) or answers.pop(0))
    device = 'tcp://192.0.2.1'
    assert tspl.DRIVER.status(device, 'TSC/TSPL')['errors'] == [EMPTY]
    clock.sleep(2)
    assert tspl.DRIVER.status(device, 'TSC/TSPL')['errors'] == [tspl.NO_ANSWER]
    for _ in range(9):
        clock.sleep(2)
        assert tspl.DRIVER.status(device, 'TSC/TSPL')['errors'] == [tspl.NO_ANSWER]
    clock.sleep(2)
    assert tspl.DRIVER.status(device, 'TSC/TSPL')['errors'] == [tspl.NO_ANSWER]
    clock.sleep(20)
    assert tspl.DRIVER.status(device, 'TSC/TSPL')['errors'] == []
    assert asked == [1000, 1002, 1022, 1042]


def test_status_and_label_over_the_network(tmp_path, on_network, monkeypatch):
    state = tspl.DRIVER.status('tcp://127.0.0.1', 'TSC/TSPL')
    assert (state['errors'], state['device_name']) == ([], 'TE200')
    assert on_network.asked == [protocol.STATUS, protocol.MODEL_NAME]
    without_status(monkeypatch)
    client = make_client(tmp_path, model='TSC/TSPL-300', printer='tcp://127.0.0.1')
    response = client.post('/labeldesigner/api/print', data={'label_size': 'tspl300-50x30', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    received = on_network.wait_for(b'PRINT 1,1\r\n')
    assert b'SIZE 50 mm,30 mm\r\nDIRECTION 0,0\r\nREFERENCE 0,0\r\nCLS\r\nBITMAP 0,0,74,354,0,' in received


def test_printer_that_does_not_answer_over_the_network(on_network):
    on_network.status = None
    state = tspl.DRIVER.status('tcp://127.0.0.1', 'TSC/TSPL')
    assert state['errors'] == [] and 'device_name' not in state


def test_printer_through_pyusb(monkeypatch):
    printer = FakeTspl()
    opened = []

    class Usb:
        def __init__(self, device):
            assert device == 'usb://0x1203:0x0230/A1B2'
            self.unread, self.written, self.disposed = b'\x10', [], False
            opened.append(self)

        def read(self, length):
            data, self.unread = self.unread[:length], self.unread[length:]
            return data

        def write(self, data):
            self.written.append(data)
            self.unread += printer.heard(data)

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Usb)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4)
    monkeypatch.setattr(devices, 'known_device_id', lambda device: '')
    device = 'usb://0x1203:0x0230/A1B2'
    # The answer from before, paused, is not taken for this one
    state = tspl.DRIVER.status(device, 'TSC/TSPL')
    assert (state['errors'], state['device_name']) == ([], 'TE200')
    protocol.write(device, b'CLS\r\nPRINT 1,1\r\n')
    assert opened[0].written == [protocol.STATUS, protocol.MODEL_NAME]
    assert opened[1].written == [b'CLS\r', b'\nPRI', b'NT 1', b',1\r\n']
    assert all(usb.disposed for usb in opened)


def test_printer_that_keeps_sending_through_pyusb(monkeypatch):
    class Usb:
        def __init__(self, device):
            self.reads = 0

        def read(self, length):
            self.reads += 1
            if self.reads > 10 * backends.DISCARD_READS:
                raise OSError('read on and on')
            return b'\x04'

        def write(self, data):
            pass

        def dispose(self):
            pass
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Usb)
    assert protocol.ask('usb://0x1203:0x0230', name=True) == (4, None)


@pytest.mark.parametrize('sensing, command', [('mark', b'BLINE 3 mm,0 mm\r\n'), ('endless', b'GAP 0,0\r\n'), ('gap', b'GAP 3 mm,0 mm\r\n')])
def test_each_label_tells_how_the_printer_finds_them_as_set_for_it(tmp_path, sent, sensing, command):
    client = make_client(tmp_path, empty_repo=True, model='TSC/TSPL', printer='file:///dev/usb/lp_tspl')
    assert client.post('/labeldesigner/api/settings', data={'sensing': sensing}).status_code == 200
    response = client.post('/labeldesigner/api/print', data={'label_size': 'tspl57x32', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert outcome(client, response) == ('printed', '')
    (_, data), = sent
    assert data.count(b'SIZE 57 mm,32 mm\r\n' + command + b'DIRECTION 0,0\r\n') == 2


def test_600_dpi_is_refused(tmp_path, sent):
    client = make_client(tmp_path, model='TSC/TSPL', printer='file:///dev/usb/lp_tspl')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'tspl57x32', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    assert response.get_json() == {'success': False, 'message': 'The TSC TSPL printer cannot print at 600 dpi, turn off 600 dpi printing'}
    assert sent == []


def test_found_on_usb_by_its_device_id(monkeypatch, tmp_path):
    from app.labeldesigner import drivers
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(backends, 'device_id', lambda device: 'MFG:Marklife ;MDL:X4;CMD:XPP,XL;')
    monkeypatch.setattr(protocol, 'ask', lambda device, name=False: (0, None))
    driver, model = drivers.resolve('usb://0x0483:0x5720', 'Unknown')
    assert (driver, model) == (tspl.DRIVER, 'TSC/TSPL')
    assert driver.status('usb://0x0483:0x5720', model)['device_name'] == 'Marklife X4'



def test_the_status_names_the_roll_as_set(tmp_path, monkeypatch):
    monkeypatch.setattr(protocol, 'ask', lambda device, name=False: (0, None))
    client = make_client(tmp_path, empty_repo=True, model='TSC/TSPL', printer='tcp://127.0.0.1')

    def shown():
        found = client.get('/labeldesigner/api/printer_status').get_json()['printers']
        return next(p for p in found if p['path'] == 'tcp://127.0.0.1')
    # No roll named while none is set
    assert (shown()['media_type'], shown()['tape']['roll']) == ('Die-cut labels', '')
    for sensing, media_type in (('endless', 'Continuous roll'), ('mark', 'Labels with black marks on the back'),
                                ('gap', 'Die-cut labels')):
        assert client.post('/labeldesigner/api/settings', data={'sensing': sensing}).status_code == 200
        told = shown()
        assert (told['media_type'], told['tape']['roll'], told['media_length']) == (media_type, diecut.ROLLS[sensing], 0)
    # The state kept stays as the printer told it
    assert devices.status_cache['tcp://127.0.0.1:9100']['media_type'] == 'Die-cut labels'
    assert tspl.DRIVER.status('tcp://127.0.0.1', 'TSC/TSPL') is not devices.status_cache['tcp://127.0.0.1:9100']
