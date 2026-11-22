"""Printers on IPP that no other driver prints on, "Other printer": a page
of PWG raster as PWG 5102.4 lays it out, the format a printer takes, its
loaded roll, how a job ends, and printing on a fake IPP printer through
the label designer."""
import io
import json
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app.labeldesigner import adding, drivers, ipp, refusals
from app.labeldesigner.drivers import devices, ipp as other, reach
from app.labeldesigner.drivers.ipp import documents
from app.labeldesigner.label_names import label_of
from tests.helpers import make_client, outcome
from tests.printing.test_ipp import attribute, fixture, job, keywords, media, response, state

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
ZEBRA = b'Zebra ZD421'


def values(tag: int, name: str, items) -> bytes:
    """An attribute of several values, the further ones without a name."""
    return b''.join(attribute(tag, name if i == 0 else '', item) for i, item in enumerate(items))


def dpi(value: int, units: int = ipp.DOTS_PER_INCH) -> bytes:
    return struct.pack('>iib', value, value, units)


def printer_attributes(formats=('application/octet-stream', 'image/png', 'image/pwg-raster', 'image/urf'),
                       types=('black_1', 'sgray_8'), resolutions=(203,), default=203, loaded=(10160, 15240),
                       reasons=('none',), printer_state=ipp.IDLE, name=ZEBRA, media_type='labels', kinds=(),
                       ready=()) -> bytes:
    """What a printer answers, by default as LPrint for a 203 dpi Zebra with
    4 x 6 in labels."""
    return response(attribute(0x41, 'printer-make-and-model', name), state(printer_state),
                    keywords('printer-state-reasons', *reasons),
                    *([media(*loaded, media_type)] if loaded else []),
                    *([keywords('printer-kind', *kinds)] if kinds else []),
                    *([keywords('media-ready', *ready)] if ready else []),
                    values(ipp.TAG_MIME_TYPE, 'document-format-supported', [kind.encode() for kind in formats]),
                    keywords('pwg-raster-document-type-supported', *types),
                    values(ipp.TAG_RESOLUTION, 'pwg-raster-document-resolution-supported', [dpi(each) for each in resolutions]),
                    *([attribute(ipp.TAG_RESOLUTION, 'printer-resolution-default', dpi(default))] if default else []))


def split(body: bytes) -> tuple[bytes, bytes]:
    """An IPP request and the document after it."""
    i = 8
    while body[i] != ipp.TAG_END:
        if body[i] < 0x10:
            i += 1
            continue
        name = struct.unpack('>H', body[i + 1:i + 3])[0]
        value = struct.unpack('>H', body[i + 3 + name:i + 5 + name])[0]
        i += 5 + name + value
    return body[:i + 1], body[i + 1:]


class FakePrinter:
    """An IPP printer on localhost. It answers Get-Printer-Attributes with
    the answers in printer, takes a job as job 42 unless refusal is a
    status code, after job_delay seconds, or answers none for it with
    silent, and tells the states of its job in states, one after the
    other, the last one again and again; 'gone' for a job it forgot, a
    tuple for a state with its reason. An operation in http answers with
    that HTTP status. Each request is kept as (operation, attributes,
    document)."""

    def __init__(self, *printer: bytes):
        self.printer = list(printer) or [printer_attributes()]
        self.states = [ipp.COMPLETED]
        self.refusal = 0
        self.job_delay = 0.0
        self.silent = False
        self.http = {}
        self.requests = []
        self.paths = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                header, document = split(self.rfile.read(int(self.headers['Content-Length'])))
                operation = struct.unpack('>H', header[2:4])[0]
                fake.requests.append((operation, ipp.decode_response(header), document))
                fake.paths.append(self.path)
                if operation == ipp.PRINT_JOB:
                    if fake.silent:
                        self.close_connection = True
                        return
                    time.sleep(fake.job_delay)
                if operation in fake.http:
                    self.send_response(fake.http[operation])
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                answer = fake.answer(operation)
                self.send_response(200)
                self.send_header('Content-Type', 'application/ipp')
                self.send_header('Content-Length', str(len(answer)))
                self.end_headers()
                self.wfile.write(answer)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.address = f'ipp://127.0.0.1:{self.port}/ipp/print'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def _next(answers: list):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def answer(self, operation: int) -> bytes:
        if operation == ipp.GET_PRINTER_ATTRIBUTES:
            return self._next(self.printer)
        if operation == ipp.PRINT_JOB:
            return response(status=self.refusal) if self.refusal else job(42, ipp.PENDING)
        told = self._next(self.states)
        if told == 'gone':
            return response(status=ipp.NOT_FOUND)
        state, reason = told if isinstance(told, tuple) else (told, 'none')
        return job(42, state)[:-1] + keywords('job-state-reasons', reason) + b'\x03'

    def operations(self) -> list[int]:
        return [operation for operation, _, _ in self.requests]

    def jobs(self) -> list[tuple[dict, bytes]]:
        return [(attributes, document) for operation, attributes, document in self.requests if operation == ipp.PRINT_JOB]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    printer = FakePrinter()
    yield printer
    printer.close()


def client_of(tmp_path, printer: FakePrinter, model: str = 'Other/IPP-203'):
    return make_client(tmp_path, model=model, printer=printer.address)


def print_label(client, label_size: str = 'ipp203-102x152', **data):
    return client.post('/labeldesigner/api/print', data={'label_size': label_size, 'print_type': 'text', 'text': TEXT, **data})


def pages(document: bytes) -> list[tuple[bytes, list[bytes]]]:
    """The pages of PWG raster: header and rows, decompressed as PWG 5102.4
    4.4 tells."""
    assert document[:4] == b'RaS2'
    found, i = [], 4
    while i < len(document):
        header = document[i:i + 1796]
        height, = struct.unpack('>I', header[376:380])
        per_line, = struct.unpack('>I', header[392:396])
        i += 1796
        rows = []
        while len(rows) < height:
            repeat = document[i] + 1
            i += 1
            row = b''
            while len(row) < per_line:
                count = document[i]
                if count < 128:
                    row += document[i + 1:i + 2] * (count + 1)
                    i += 2
                else:
                    row += document[i + 1:i + 1 + 257 - count]
                    i += 1 + 257 - count
            rows += [row] * repeat
        found.append((header, rows))
    return found


# --- PWG raster ------------------------------------------------------------------


def numbers(*values: int) -> bytes:
    """Integers and unsigned integers of 32 bits, most significant octet first
    (4.1, 4.3.1.6, 4.3.1.12)."""
    return b''.join(value.to_bytes(4, 'big', signed=True) for value in values)


def cstring(text: bytes = b'') -> bytes:
    """A CString: up to 63 characters, then NUL to 64 octets (4.3.1.2)."""
    return text.ljust(64, b'\0')


def table_1(bits: int, per_line: int, color_space: int) -> list[tuple[int, int, str, bytes]]:
    """A page header as PWG 5102.4 Table 1 lists its fields, row by row: the
    first and last byte, the name and the value of a page of 816 x 1216
    dots at 203 dpi on 4 x 6 in labels, of a document of two pages."""
    return [
        (0, 63, 'PwgRaster', cstring(b'PwgRaster')),  # 4.3.2.1
        (64, 127, 'MediaColor', cstring()),
        (128, 191, 'MediaType', cstring()),
        (192, 255, 'PrintContentOptimize', cstring()),
        (256, 267, 'Reserved', bytes(12)),  # 4.3.1.10: 0
        (268, 271, 'CutMedia', numbers(0)),
        (272, 275, 'Duplex', numbers(0)),
        (276, 283, 'HWResolution', numbers(203, 203)),  # 4.3.2.2: cross-feed, feed
        (284, 299, 'Reserved', bytes(16)),
        (300, 303, 'InsertSheet', numbers(0)),
        (304, 307, 'Jog', numbers(0)),
        (308, 311, 'LeadingEdge', numbers(0)),
        (312, 323, 'Reserved', bytes(12)),
        (324, 327, 'MediaPosition', numbers(0)),
        (328, 331, 'MediaWeightMetric', numbers(0)),
        (332, 339, 'Reserved', bytes(8)),
        (340, 343, 'NumCopies', numbers(0)),  # 4.3.3.9: 0 for the default
        (344, 347, 'Orientation', numbers(0)),
        (348, 351, 'Reserved', bytes(4)),
        (352, 359, 'PageSize', numbers(288, 432)),  # 4.3.3.11: in points
        (360, 367, 'Reserved', bytes(8)),
        (368, 371, 'Tumble', numbers(0)),
        (372, 375, 'Width', numbers(816)),  # 4.3.2.4
        (376, 379, 'Height', numbers(1216)),
        (380, 383, 'Reserved', bytes(4)),
        (384, 387, 'BitsPerColor', numbers(bits)),  # 4.3.2.5, Table 12
        (388, 391, 'BitsPerPixel', numbers(bits)),
        (392, 395, 'BytesPerLine', numbers(per_line)),  # 4.3.2.6
        (396, 399, 'ColorOrder', numbers(0)),  # Table 2: chunky
        (400, 403, 'ColorSpace', numbers(color_space)),  # Table 3
        (404, 419, 'Reserved', bytes(16)),
        (420, 423, 'NumColors', numbers(1)),
        (424, 451, 'Reserved', bytes(28)),
        (452, 455, 'TotalPageCount', numbers(2)),  # 4.3.2.7
        (456, 459, 'CrossFeedTransform', numbers(1)),  # 4.3.2.8, Table 9: a front side
        (460, 463, 'FeedTransform', numbers(1)),
        (464, 467, 'ImageBoxLeft', numbers(0)),  # 4.3.2.9: all of the page
        (468, 471, 'ImageBoxTop', numbers(0)),
        (472, 475, 'ImageBoxRight', numbers(816)),
        (476, 479, 'ImageBoxBottom', numbers(1216)),
        (480, 483, 'AlternatePrimary', numbers(0)),  # 4.3.2.10: black
        (484, 487, 'PrintQuality', numbers(0)),  # Table 7: the default
        (488, 507, 'Reserved', bytes(20)),
        (508, 511, 'VendorIdentifier', numbers(0)),  # 4.3.2.11: not used
        (512, 515, 'VendorLength', numbers(0)),
        (516, 1603, 'VendorData', bytes(1088)),
        (1604, 1667, 'Reserved', bytes(64)),
        (1668, 1731, 'RenderingIntent', cstring()),
        (1732, 1795, 'PageSizeName', cstring(b'custom_101.6x152.4mm_101.6x152.4mm')),  # 4.3.3.11, PWG 5101.1
    ]


@pytest.mark.parametrize('raster_type, bits, per_line, color_space', [
    # Table 12: sgray_8 of 8 bits, ColorSpace 18; black_1 of 1 bit, ColorSpace 3
    ('sgray_8', 8, 816, 18), ('black_1', 1, 102, 3),
])
def test_page_header_as_pwg_5102_4_lays_it_out(raster_type, bits, per_line, color_space):
    rows = table_1(bits, per_line, color_space)
    # The rows of the table follow each other over all 1796 octets (4.3)
    assert [first for first, _, _, _ in rows] == [0] + [last + 1 for _, last, _, _ in rows[:-1]] and rows[-1][1] == 1795
    assert all(len(value) == last - first + 1 for first, last, _, value in rows)
    assert documents.page_header(816, 1216, 203, (10160, 15240), 2, raster_type) == b''.join(value for *_, value in rows)


def test_page_of_one_bit_as_pwg_5102_4_compresses_it():
    # 12 dots across: two octets a line, 8 dots to an octet, the first in its
    # highest bit (4.1, 4.4), 1 a printed dot in the Black color space (4.3.2.10)
    img = Image.new('1', (12, 4), 1)
    for x in range(4):
        img.putpixel((x, 0), 0)
        img.putpixel((x, 1), 0)
    for x in (0, 2, 4, 6, 9):
        img.putpixel((x, 2), 0)
    document = documents.pwg_raster([img], 203, (150, 50), 'black_1')
    # 4.2: the synchronization word "RaS2", then the page header (4.3)
    assert document[:4] == b'RaS2' and document[4:1800] == documents.page_header(12, 4, 203, (150, 50), 1, 'black_1')
    assert document[1800:] == bytes([
        # Two equal lines: the repetition count 2 - 1 (4.4), then their two
        # octets, which differ: 257 - 2 and the octets
        0x01, 0xFF, 0xF0, 0x00,
        # One line of two other octets
        0x00, 0xFF, 0xAA, 0x40,
        # One line, of one octet twice: 2 - 1 and the octet
        0x00, 0x01, 0x00,
    ])


def test_page_of_8_bit_gray_as_pwg_5102_4_compresses_it():
    # A byte for each dot, 0 black and 255 white in sGray (Table 3), each
    # byte a color value of the runs (4.4)
    img = Image.new('1', (12, 4), 1)
    for x in range(4):
        img.putpixel((x, 0), 0)
        img.putpixel((x, 1), 0)
    for x in (0, 2, 4, 6, 9):
        img.putpixel((x, 2), 0)
    document = documents.pwg_raster([img], 203, (150, 50), 'sgray_8')
    assert document[4:1800] == documents.page_header(12, 4, 203, (150, 50), 1, 'sgray_8')
    assert document[1800:] == bytes([
        # Two equal lines: 4 black dots repeated (4 - 1, 0), 8 white ones (8 - 1, 255)
        0x01, 0x03, 0x00, 0x07, 0xFF,
        # 7 differing dots (257 - 7), then two white, one black, two white
        0x00, 0xFA, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0xFF, 0x00, 0x01, 0xFF, 0x00, 0x00, 0x01, 0xFF,
        # 12 white dots
        0x00, 0x0B, 0xFF,
    ])


def test_long_runs_and_many_equal_lines_are_split():
    # At most 128 octets a run and 256 lines a repetition count (4.4)
    line = bytes([127, 0, 127, 0, 43, 0])
    assert documents.page_bitmap([bytes(300)] * 300) == bytes([255]) + line + bytes([43]) + line
    others = bytes(range(200))
    assert documents.page_bitmap([others]) == bytes([0, 129]) + others[:128] + bytes([257 - 72]) + others[128:]
    # A single octet before a run is one repeated once
    assert documents.page_bitmap([b'\x01\x02\x02']) == b'\x00\x00\x01\x01\x02'
    assert documents.page_bitmap([b'\x01\x02\x03\x03']) == b'\x00\xff\x01\x02\x01\x03'


# --- The printer ------------------------------------------------------------------


@pytest.mark.parametrize('formats, types, resolutions, made, expected', [
    # As LPrint: PWG raster of 8 bit gray at the resolution of the label
    (('image/png', 'image/pwg-raster'), ('black_1', 'black_8', 'sgray_8'), (203,), 203, ('image/pwg-raster', 203, 'sgray_8')),
    # Black dots of 1 bit where it takes no gray ones; 200 dpi are the 8
    # dots per mm of 203 dpi
    (('image/pwg-raster',), ('black_1',), (200, 300), 203, ('image/pwg-raster', 200, 'black_1')),
    (('image/pwg-raster', 'image/png'), ('srgb_8', 'sgray_8'), (203, 300), 300, ('image/pwg-raster', 300, 'sgray_8')),
    # Neither of them, or not at the resolution: PNG, which the printer scales
    (('image/pwg-raster', 'image/png'), ('srgb_8',), (203,), 203, ('image/png', 203, '')),
    (('image/pwg-raster', 'image/png'), ('sgray_8',), (300,), 203, ('image/png', 203, '')),
    # As a queue of CUPS
    (('application/pdf', 'application/postscript', 'image/png'), (), (), 300, ('image/png', 300, '')),
    (('application/pdf', 'application/postscript'), (), (), 300, ('application/pdf', 300, '')),
    # None of them
    (('application/octet-stream', 'image/urf'), (), (), 300, None),
    (('image/pwg-raster',), ('sgray_8',), (600,), 300, None),
])
def test_format_chosen_by_what_the_printer_takes(formats, types, resolutions, made, expected):
    attributes = ipp.decode_response(printer_attributes(formats, types, resolutions))
    assert documents.chosen(attributes, made) == expected


@pytest.mark.parametrize('default, units, model', [
    (203, ipp.DOTS_PER_INCH, 'Other/IPP-203'), (200, ipp.DOTS_PER_INCH, 'Other/IPP-203'),
    (300, ipp.DOTS_PER_INCH, 'Other/IPP'), (118, ipp.DOTS_PER_CM, 'Other/IPP'),
    # The nearest of both
    (600, ipp.DOTS_PER_INCH, 'Other/IPP'), (180, ipp.DOTS_PER_INCH, 'Other/IPP-203'),
])
def test_model_by_the_resolution_of_the_printer(default, units, model):
    attributes = dict(ipp.decode_response(printer_attributes(formats=('image/png',), default=0)), **{'printer-resolution-default': (default, default, units)})
    assert other.offered(attributes) == model


def test_lprint_with_a_zebra_of_203_dpi():
    # LPrint 1.3.1 with a "Zebra ZPL 4-inch/203dpi/Direct-Thermal" and 4 x 6 in
    # labels, asked for OFFERED
    attributes = ipp.decode_response(fixture('lprint-zpl-203dpi.bin'))
    assert other.offered(attributes) == 'Other/IPP-203'
    # 8 bit gray: it hands black dots of 1 bit as they are to its driver, which takes gray ones
    assert documents.chosen(attributes, 203) == ('image/pwg-raster', 203, 'sgray_8')
    state = other.told_status('ipp://192.0.2.9/ipp/print/Zebra', 'Other/IPP-203', attributes)
    assert (state['media_width'], state['media_length'], state['errors']) == (102, 152, [])
    assert state['device_name'] == 'Zebra ZPL 4-inch/203dpi/Direct-Thermal'
    assert other.media_size(label_of('ipp203-102x152'), attributes['media-col-ready']) == (10160, 15240)


def test_printer_that_tells_no_resolution_is_one_of_300_dpi():
    assert other.offered(ipp.decode_response(printer_attributes(formats=('image/png',), default=0))) == 'Other/IPP'


def test_printer_that_takes_none_of_the_formats_is_offered_as_none():
    assert other.offered(ipp.decode_response(printer_attributes(formats=('image/urf',)))) is None


def test_label_sizes_in_the_dots_of_each_resolution():
    assert label_of('ipp102x152').dots_printable == (1205, 1795)
    assert label_of('ipp203-102x152').dots_printable == (816, 1216)
    assert other.DRIVER.label_for('ipp102x152', 'Other/IPP-203') == 'ipp203-102x152'
    assert other.DRIVER.label_for('ipp203-105x148', 'Other/IPP') == 'ipp105x148'
    # The same paper of another driver
    assert other.DRIVER.label_for('zpl102x152', 'Other/IPP-203') == 'ipp203-102x152'
    assert other.DRIVER.label_for('tspl300-50x30', 'Other/IPP') == 'ipp50x30'
    assert other.NAMES['ipp203-102x152'] == '102 x 152 mm die-cut (Other printer)'


def test_one_printer_by_any_of_its_addresses():
    keys = {devices.lock_key(device) for device in ('ipp://Printer.local/ipp/print', 'ipps://printer.local:631/ipp/print')}
    assert keys == {'ipp://printer.local:631/ipp/print'}
    assert devices.lock_key('cups://Dymo') == devices.lock_key('ipp://localhost/printers/dymo')


def test_printers_on_ipp_have_a_host():
    assert adding.host_of('ipp://192.168.1.5/ipp/print') == '192.168.1.5'
    assert adding.host_of('ipps://[fd00::5]:8631/ipp/print') == 'fd00::5'
    assert adding.host_of('cups://dymo') == 'localhost'


def test_status_tells_the_loaded_roll(tmp_path, fake):
    status = client_of(tmp_path, fake).get('/labeldesigner/api/printer_status').get_json()
    found = status['printers'][0]
    assert (found['path'], found['model'], found['device_name']) == (fake.address, 'Other/IPP-203', 'Zebra ZD421')
    # 4 x 6 in labels as LPrint tells them
    assert (found['media_width'], found['media_length'], found['media_type']) == (102, 152, 'Die-cut labels')
    assert (found['errors'], found['status_type']) == ([], 'Reply to status request')
    assert found['tape']['id'] == 'ipp203-102x152'
    assert fake.operations() == [ipp.GET_PRINTER_ATTRIBUTES]


@pytest.mark.parametrize('loaded, media_type', [
    (None, 'labels'),
    # A queue of CUPS tells A4 without a type, ippeveprinter letter of an office printer
    ((20990, 29704), ''), ((21590, 27940), 'stationery'),
])
def test_printer_that_does_not_tell_its_roll(tmp_path, fake, loaded, media_type):
    fake.printer = [printer_attributes(loaded=loaded, media_type=media_type)]
    found = client_of(tmp_path, fake).get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (found['media_width'], found['media_length']) == (None, 0)
    assert found['tape']['assumed']


@pytest.mark.parametrize('printer_state, reasons, status_type, errors', [
    (ipp.PROCESSING, ('none',), devices.BUSY_STATUS_TYPE, []),
    (ipp.STOPPED, ('media-empty-error', 'media-low-warning'), 'Error occurred', ['There are no labels in the printer']),
    (ipp.STOPPED, ('paused',), 'Error occurred', ['The printer is paused']),
    # Other reasons count only as errors, named by their keyword
    (ipp.STOPPED, ('toner-gone-error', 'marker-supply-low-report'), 'Error occurred', ['toner-gone']),
    # A queue of CUPS while it prints
    (ipp.PROCESSING, ('cups-waiting-for-job-completed', 'connecting-to-device'), devices.BUSY_STATUS_TYPE, []),
    (ipp.IDLE, ('offline-report', 'cups-insecure-filter-warning'), 'Reply to status request', []),
])
def test_status_of_the_printer(fake, printer_state, reasons, status_type, errors):
    fake.printer = [printer_attributes(printer_state=printer_state, reasons=reasons)]
    found = other.DRIVER.status(fake.address, 'Other/IPP-203')
    assert (found['status_type'], found['errors']) == (status_type, errors)


def test_printer_that_does_not_answer(tmp_path):
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        address = f'ipp://127.0.0.1:{unused.getsockname()[1]}/ipp/print'
    status = make_client(tmp_path, model='Other/IPP', printer=address).get('/labeldesigner/api/printer_status').get_json()
    assert status['printers'][0]['errors'] == ['The printer refuses the connection']


# --- Printing ---------------------------------------------------------------------


def test_label_is_printed_as_pwg_raster(tmp_path, fake):
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('printed', '')
    # Asked for its roll by the request, for its state and formats by the
    # job, sent the job, then asked how it ended
    assert fake.operations() == [ipp.GET_PRINTER_ATTRIBUTES, ipp.GET_PRINTER_ATTRIBUTES, ipp.PRINT_JOB,
                                 ipp.GET_PRINTER_ATTRIBUTES, ipp.GET_JOB_ATTRIBUTES]
    (attributes, document), = fake.jobs()
    assert attributes['printer-uri'] == fake.address
    assert (attributes['document-format'], attributes['print-scaling']) == ('image/pwg-raster', 'none')
    # The loaded labels as the printer names them, without margins
    assert attributes['media-col'] == {'media-size': {'x-dimension': 10160, 'y-dimension': 15240}, 'media-bottom-margin': 0,
                                       'media-left-margin': 0, 'media-right-margin': 0, 'media-top-margin': 0}
    (header, rows), = pages(document)
    assert struct.unpack('>II', header[276:284]) == (203, 203)
    assert struct.unpack('>II', header[372:380]) == (816, 1216)
    # 8 bit gray, the text is printed black
    assert struct.unpack('>II', header[384:392]) == (8, 8) and struct.unpack('>I', header[400:404]) == (18,)
    assert set(b''.join(rows)) == {0, 255}
    asked = fake.requests[-1][1]
    assert (asked['job-id'], asked['requested-attributes']) == (42, ['job-state', 'job-state-reasons'])


def test_copies_are_pages_of_one_document(tmp_path, fake):
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client, print_count='3')) == ('printed', '')
    (_, document), = fake.jobs()
    found = pages(document)
    assert len(found) == 3 and {struct.unpack('>I', header[452:456])[0] for header, _ in found} == {3}


def test_labels_of_300_dpi(tmp_path, fake):
    fake.printer = [printer_attributes(resolutions=(300,), default=300, loaded=None)]
    client = client_of(tmp_path, fake, 'Other/IPP')
    assert outcome(client, print_label(client, 'ipp50x30')) == ('printed', '')
    (attributes, document), = fake.jobs()
    # Of no loaded labels, the size of the label
    assert attributes['media-col']['media-size'] == {'x-dimension': 5000, 'y-dimension': 3000}
    (header, rows), = pages(document)
    assert struct.unpack('>II', header[372:380]) == (591, 354)


def test_printer_that_takes_black_dots_of_one_bit_only(tmp_path, fake):
    fake.printer = [printer_attributes(types=('black_1',))]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('printed', '')
    (header, rows), = pages(fake.jobs()[0][1])
    assert struct.unpack('>II', header[384:392]) == (1, 1) and struct.unpack('>I', header[400:404]) == (3,)
    assert len(rows[0]) == 102 and any(any(row) for row in rows)


def test_label_is_printed_as_png_one_job_each(tmp_path, fake):
    fake.printer = [printer_attributes(formats=('image/png', 'image/jpeg'))]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client, print_count='2')) == ('printed', '')
    jobs = fake.jobs()
    assert [attributes['document-format'] for attributes, _ in jobs] == ['image/png', 'image/png']
    with Image.open(io.BytesIO(jobs[0][1])) as img:
        # Its resolution tells the printer how large it is
        assert (img.size, img.mode, round(img.info['dpi'][0])) == ((816, 1216), 'L', 203)
        assert set(img.tobytes()) == {0, 255}


def test_label_is_printed_as_pdf(tmp_path, fake):
    fake.printer = [printer_attributes(formats=('application/pdf', 'application/postscript'), loaded=None)]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client, 'ipp203-50x30', print_count='2')) == ('printed', '')
    (attributes, document), = fake.jobs()
    assert attributes['document-format'] == 'application/pdf'
    assert document.startswith(b'%PDF') and b'/Count 2' in document
    # Each page 400 x 240 dots at 203 dpi, in points
    boxes = re.findall(rb'/MediaBox \[ 0 0 ([0-9.]+) ([0-9.]+) \]', document)
    assert [(round(float(width), 2), round(float(length), 2)) for width, length in boxes] == [(141.87, 85.12)] * 2


def test_printer_that_takes_none_of_the_formats(tmp_path, fake):
    fake.printer = [printer_attributes(formats=('image/urf',))]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', other.NO_FORMAT)
    assert fake.jobs() == []


@pytest.mark.parametrize('reasons, message', [
    (('cover-open-error',), 'The printer reports: The cover is open'),
    (('toner-gone-error',), 'The printer reports: toner-gone'),
])
def test_job_is_not_sent_to_a_printer_with_an_error(tmp_path, fake, reasons, message):
    fake.printer = [printer_attributes(printer_state=ipp.STOPPED, reasons=reasons)]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', message)
    assert fake.jobs() == []


def test_queue_of_cups_prints_while_it_waits_for_its_jobs(tmp_path, fake):
    # CUPS tells these while a job prints and for a while after it
    fake.printer = [printer_attributes(printer_state=ipp.PROCESSING, reasons=('cups-waiting-for-job-completed', 'connecting-to-device'))]
    fake.states = [ipp.JOB_PROCESSING, ipp.COMPLETED]
    client = client_of(tmp_path, fake)
    for _ in range(2):
        assert outcome(client, print_label(client)) == ('printed', '')
    assert len(fake.jobs()) == 2


@pytest.mark.parametrize('states, expected', [
    ([ipp.PENDING, ipp.JOB_PROCESSING, ipp.JOB_PROCESSING, ipp.COMPLETED], ('printed', '')),
    # A printer that forgot the job it printed
    ([ipp.JOB_PROCESSING, 'gone'], ('printed', '')),
    ([ipp.CANCELED], ('failed', other.CANCELED)),
    ([ipp.JOB_PROCESSING, ipp.ABORTED], ('failed', other.ABORTED)),
    ([ipp.HELD], ('failed', other.HELD)),
])
def test_job_states_to_the_outcome(tmp_path, fake, states, expected):
    fake.states = states
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == expected


def test_printer_that_stops_while_the_job_waits(tmp_path, fake):
    # Ready for the request and the job, stopped once it has the job
    fake.printer = [printer_attributes(), printer_attributes(),
                    printer_attributes(printer_state=ipp.STOPPED, reasons=('media-empty-error',))]
    fake.states = [ipp.JOB_STOPPED]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == (
        'failed', 'The printer reports: There are no labels in the printer, it prints the label once this is fixed')


def test_job_that_never_ends(tmp_path, fake, monkeypatch):
    # Time enough to take it, not to print it
    monkeypatch.setattr(other, 'JOB_WAIT', 0.5)
    monkeypatch.setattr(other, 'SLOWEST', 1e6)
    fake.states = [ipp.JOB_PROCESSING]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', other.NOT_FINISHED)


@pytest.mark.parametrize('status, message', [
    (0x0401, other.LOGIN), (0x0403, other.LOGIN), (0x0506, other.NOT_TAKING), (0x0507, other.NOT_TAKING),
    (0x040A, other.REFUSED), (0x0500, other.REFUSED),
])
def test_refused_jobs(tmp_path, fake, status, message):
    fake.refusal = status
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', message)


@pytest.mark.parametrize('http_status, message', [(401, other.LOGIN), (403, other.LOGIN), (426, other.TLS)])
@pytest.mark.parametrize('operation', [ipp.GET_PRINTER_ATTRIBUTES, ipp.PRINT_JOB])
def test_login_or_tls_asked_for_over_http(tmp_path, fake, http_status, message, operation):
    # As CUPS asks for them, before it reads the request
    fake.http[operation] = http_status
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', message)
    if operation == ipp.GET_PRINTER_ATTRIBUTES:
        # Asked for before the job, which is not sent
        assert fake.jobs() == []
        found = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
        assert (found['errors'], found['status_type']) == ([message], 'Error occurred')
        with pytest.raises(ValueError) as refused:
            adding.identify(fake.address, 'local')
        assert str(refused.value) == message


def test_job_cups_holds_for_a_login(tmp_path, fake):
    fake.states = [(ipp.HELD, 'cups-held-for-authentication')]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', other.LOGIN)


def test_job_answered_late_is_printed(tmp_path, fake, monkeypatch):
    # Later than it takes to connect, as a printer that reads the job while it prints
    monkeypatch.setattr(other, 'JOB_TIMEOUT', 0.2)
    fake.job_delay = 0.6
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('printed', '')


@pytest.mark.parametrize('silent', [True, False])
def test_job_the_printer_took_without_answering(tmp_path, fake, monkeypatch, silent):
    # Closed without an answer, or no answer within the time the job may take
    monkeypatch.setattr(other, 'JOB_WAIT', 0.3)
    monkeypatch.setattr(other, 'SLOWEST', 1e6)
    fake.silent, fake.job_delay = silent, 1.5
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', other.NO_ANSWER)
    assert len(fake.jobs()) == 1


def test_a_job_may_take_as_long_as_its_labels(tmp_path, fake, monkeypatch):
    waits = []
    real = ipp.print_job

    def kept(printer, kind, media, document, wait, timeout):
        waits.append(wait)
        return real(printer, kind, media, document, wait, timeout)
    monkeypatch.setattr(ipp, 'print_job', kept)
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client, print_count='2')) == ('printed', '')
    # Two labels of 152 mm, at SLOWEST mm/s
    assert waits == [other.JOB_WAIT + 2 * 152 / other.SLOWEST]


def test_printer_that_does_not_answer_after_the_job(tmp_path, fake, monkeypatch):
    def gone(*args, **kwargs):
        raise ConnectionRefusedError(111, 'Connection refused')
    monkeypatch.setattr(ipp, 'get_job_attributes', gone)
    fake.states = [ipp.PENDING]
    client = client_of(tmp_path, fake)
    assert outcome(client, print_label(client)) == ('failed', other.NO_ANSWER)


def test_request_naming_a_printer_outside_the_local_network_is_refused(tmp_path, fake):
    client = client_of(tmp_path, fake)
    answer = print_label(client, printer='ipp://8.8.8.8/ipp/print')
    assert (answer.status_code, answer.get_json()['message']) == (400, adding.NAMED_OUTSIDE)


@pytest.fixture
def tried(monkeypatch):
    """The ports the server connects to."""
    ports = []
    real = reach.create_connection

    def recorded(address, timeout, source_address=None, deadline=None):
        ports.append(address[1])
        return real(address, timeout, source_address, deadline)
    monkeypatch.setattr(reach, 'create_connection', recorded)
    return ports


@pytest.mark.parametrize('named, message', [
    ('ipp://127.0.0.1:{port}/containers/labelburg/kill', 'Unsupported printer'),
    ('ipp://127.0.0.1:{port}/ipp/../containers/labelburg/kill', 'Unsupported printer'),
    ('ipp://127.0.0.1:9100/ipp/print', ipp.RAW_PORT),
])
def test_requests_ask_printers_on_ipp_at_their_paths_and_ports_only(tmp_path, fake, tried, named, message):
    answer = print_label(client_of(tmp_path, fake), printer=named.format(port=fake.port))
    assert (answer.status_code, answer.get_json()['message']) == (400, message)
    assert tried == [] and fake.requests == []


def test_printer_on_ipp_configured_at_a_raw_port_is_never_asked(tmp_path, tried):
    client = make_client(tmp_path, model='Other/IPP-203', printer='ipp://127.0.0.1:9100/ipp/print')
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == [ipp.RAW_PORT]
    assert outcome(client, print_label(client)) == ('failed', f'{devices.NOT_SENT}: {ipp.RAW_PORT}')
    assert tried == []


def test_what_the_label_designer_offers():
    # Cut as the printer is set up, the top of a page leaves it first
    caps = other.DRIVER.capabilities('Other/IPP-203')
    assert (caps['cutter'], caps['cut_on_printer'], caps['leaves_turned']) == (False, True, True)
    # Shown as "Other printer"
    assert (caps['vendor'], caps['model_shown'], caps['qualities']) == ('Other', 'printer', ['quality'])


def test_printer_on_ipp_is_added_and_printed_on(tmp_path, fake):
    client = make_client(tmp_path, empty_repo=True)
    found = client.post('/labeldesigner/api/printers/identify', json={'address': fake.address}).get_json()['printer']
    assert (found['address'], found['model'], found['device_name']) == (fake.address, 'Other/IPP-203', 'Zebra ZD421')
    assert (found['tape']['id'], found['errors'], found['listed']) == ('ipp203-102x152', [], None)
    added = client.post('/labeldesigner/api/printers/add', json={'address': fake.address, 'name': 'Shipping'}).get_json()
    assert added['printer'] == {'address': fake.address, 'model': 'Other/IPP-203', 'name': 'Shipping'}
    shown = next(p for p in client.get('/labeldesigner/api/printer_status').get_json()['printers'] if p.get('added'))
    assert (shown['name'], shown['model'], shown['device_name'], shown['media_width']) == ('Shipping', 'Other/IPP-203', 'Zebra ZD421', 102)
    assert outcome(client, print_label(client, printer='Shipping')) == ('printed', '')
    assert len(fake.jobs()) == 1


@pytest.mark.parametrize('model, resolved', [('Other/IPP-203', 'Other/IPP-203'), ('Other/IPP', 'Other/IPP'), ('Unknown', 'Other/IPP')])
def test_printers_on_ipp_are_other_printers(model, resolved):
    for device in ('ipp://192.0.2.9/ipp/print', 'ipps://192.0.2.9/ipp/print', 'cups://DYMO'):
        assert drivers.resolve(device, model) == (other.DRIVER, resolved)


@pytest.mark.parametrize('model', ['QL-800', 'Zebra/ZPL', 'Epson/ESC-POS'])
def test_printer_on_ipp_of_another_model_is_refused(model):
    with pytest.raises(devices.OtherPrinterOnly) as refused:
        drivers.resolve('ipp://192.0.2.9/ipp/print', model)
    assert str(refused.value) == devices.OTHER_PRINTER_ONLY
    assert refusals.message(refused.value, '-') == devices.OTHER_PRINTER_ONLY


@pytest.mark.parametrize('configured', ['printers', 'printer'])
def test_printer_on_ipp_configured_as_another_model_gets_nothing(tmp_path, fake, configured):
    if configured == 'printers':
        client = make_client(tmp_path)
        client.application.config['PRINTERS'] = [('QL-800', fake.address, 'Labels')]
    else:
        client = make_client(tmp_path, model='QL-800', printer=fake.address)
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (found['path'], found['errors']) == (fake.address, [devices.OTHER_PRINTER_ONLY])
    answer = print_label(client, '62')
    assert (answer.status_code, answer.get_json()['message']) == (400, devices.OTHER_PRINTER_ONLY)
    # Neither asked nor sent anything, by no driver
    assert fake.requests == []


def test_printer_on_ipp_of_an_assumed_model_is_an_other_printer():
    # E.g. PRINTER_MODEL left at its default, or a printer a request names
    assert drivers.resolve('ipp://192.0.2.9/ipp/print', 'QL-800', assumed=True) == (other.DRIVER, 'Other/IPP')


def test_printer_on_ipp_as_printer_printer_left_without_a_model(tmp_path, fake):
    client = make_client(tmp_path, printer=fake.address)
    found = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (found['model'], found['errors']) == ('Other/IPP', [])
    assert outcome(client, print_label(client, 'ipp102x152')) == ('printed', '')


def test_request_naming_a_printer_on_ipp_without_a_model(tmp_path, fake):
    # Neither configured nor added: the address tells its driver, not the configured model
    client = make_client(tmp_path, model='QL-800')
    assert outcome(client, print_label(client, 'ipp102x152', printer=fake.address)) == ('printed', '')
    (attributes, _), = fake.jobs()
    assert attributes['media-col']['media-size'] == {'x-dimension': 10160, 'y-dimension': 15240}
