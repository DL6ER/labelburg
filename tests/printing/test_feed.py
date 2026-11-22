"""Feeding the paper of a printer on request, e.g. to cut off the last label
of a job printed without a cut: what each driver sends for it, also through
the label designer to fake printers."""
import struct
from types import SimpleNamespace

import packbits
import pytest

from app.labeldesigner import printer
from app.labeldesigner.drivers import BROTHER, ESCPOS, ZPL, for_model
from app.labeldesigner.drivers.brother import usb
from app.labeldesigner.drivers.brother.models import MODELS
from app.labeldesigner.drivers.escpos import protocol as escpos
from app.labeldesigner.drivers.zpl import protocol as zpl
from tests.helpers import make_client, outcome, without_status
from tests.printing.test_escpos import FakePrinter

DEVICE = 'file:///dev/usb/lp0'
FEED = '/labeldesigner/api/feed'


def _told(data: bytes, letter: bytes, length: int = 1) -> bytes:
    """The parameters of the raster command ESC i <letter>."""
    at = data.index(b'\x1bi' + letter)
    return data[at + 3:at + 3 + length]


def _blank_rows(data: bytes, model: str) -> int:
    """The raster lines of the data that print nothing."""
    width = MODELS[model].number_bytes_per_row
    if model.startswith('PT-'):
        row = packbits.encode(bytes(width))
        return data.count(b'\x47' + bytes([len(row), 0]) + row)
    return data.count(b'\x67\x00' + bytes([width]) + bytes(width))


@pytest.mark.parametrize('model, label_size, lines, kind', [
    # The shortest label the printer takes: 12.7 mm on QL printers, 4.4 mm
    # on TZe tape
    ('QL-800', '62', 150, 'cut'),
    ('QL-1100', '102', 150, 'cut'),
    ('PT-E550W', 'pt12', 32, 'cut'),
    ('PT-P900W', 'pt36', 63, 'cut'),
    # One whole die-cut label
    ('QL-700', '62x29', 271, 'cut_label'),
    ('QL-810W', 'd24', 236, 'cut_label'),
])
def test_brother_printers_feed_an_empty_label_and_cut_it(model, label_size, lines, kind):
    assert BROTHER.feed_kind(model, label_size) == kind
    job = BROTHER.feed_job(printer.PrinterQueue(model, DEVICE, label_size))
    assert struct.unpack('<L', _told(job.data, b'z', 8)[4:]) == (lines,)
    assert _blank_rows(job.data, model) == lines
    # Cut after it: on QL printers at the end of the job, on P-touch
    # printers without chain printing
    assert (_told(job.data, b'M'), _told(job.data, b'A')) == (b'\x40', b'\x01')
    assert _told(job.data, b'K')[0] & 0x08
    assert job.data.endswith(b'\x1a') and job.data.count(b'\x1biz') == 1
    assert len(job.page_lengths) == 1 and job.page_lengths[0] == pytest.approx(lines / MODELS[model].dpi * 25.4)


def test_a_ptouch_printer_cuts_also_after_chain_printing():
    # The last label of a chain stays in the printer, this cuts it off
    queue = printer.PrinterQueue('PT-E550W', DEVICE, 'pt12')
    queue.cut, queue.half_cut, queue.chain = 'none', True, True
    data = BROTHER.feed_job(queue).data
    assert (_told(data, b'M'), _told(data, b'A'), _told(data, b'K')) == (b'\x40', b'\x01', b'\x08')


def test_a_black_and_red_roll_gets_its_job_as_labels_on_it_do():
    data = BROTHER.feed_job(printer.PrinterQueue('QL-800', DEVICE, '62red')).data
    assert _told(data, b'K')[0] & 0x01
    assert data.count(b'\x77\x01\x5a' + bytes(90)) == data.count(b'\x77\x02\x5a' + bytes(90)) == 150


@pytest.mark.parametrize('model, label_size', [
    # Cut with a lever
    ('QL-500', '62'),
    # Without a cutter, or nothing known to feed them
    ('Niimbot/B1', 'niimbot40x30'), ('Niimbot/D110_M', 'niimbot12x40'), ('PUQU/Q00', 'puqu40x30'), ('VC-500W', 'vc25'),
])
def test_printers_without_it(model, label_size):
    driver = for_model(model)
    assert driver.feed_kind(model, label_size) == ''
    assert driver.feed_job(SimpleNamespace(model=model, label_size=label_size, device_specifier=DEVICE)) is None


@pytest.mark.parametrize('model, label_size', [('Epson/ESC-POS', 'escpos58'), ('Epson/ESC-POS-180', 'escpos80-180')])
def test_a_receipt_printer_feeds_to_its_cutter_and_cuts(model, label_size):
    assert ESCPOS.feed_kind(model, label_size) == 'cut_paper'
    job = ESCPOS.feed_job(SimpleNamespace(model=model, label_size=label_size))
    assert escpos.job(job) == escpos.INIT + escpos.CUT == b'\x1b@\x1dVA\x00'
    assert ESCPOS.label_lengths(job) == [0.0]


def test_a_zpl_printer_feeds_one_label_and_changes_no_setting():
    assert ZPL.feed_kind('Zebra/ZPL', 'zpl57x32') == 'label'
    job = ZPL.feed_job(SimpleNamespace(model='Zebra/ZPL', label_size='zpl57x32'))
    # As its feed button does: no format, so neither the width nor the length
    # of its labels is set
    assert job == [(b'~PH\n', None)]
    assert ZPL.label_lengths(job) == []


def test_a_ql_printer_gets_it_through_the_label_designer(tmp_path, monkeypatch):
    without_status(monkeypatch)
    sent = []
    monkeypatch.setattr(usb, 'send_job', lambda data, device, page_lengths: sent.append((data, device, page_lengths))
                        or {'did_print': True, 'ready_for_next_job': True})
    client = make_client(tmp_path, empty_repo=True, model='QL-700', printer=DEVICE)
    assert outcome(client, client.post(FEED)) == ('printed', '')
    (data, device, lengths), = sent
    assert device == DEVICE and lengths == [pytest.approx(12.7)]
    assert struct.unpack('<L', _told(data, b'z', 8)[4:]) == (150,) and _told(data, b'K')[0] & 0x08


def test_a_ptouch_printer_set_to_chain_printing_gets_the_cut(tmp_path, monkeypatch):
    without_status(monkeypatch)
    sent = []
    monkeypatch.setattr(usb, 'send_job', lambda data, device, page_lengths: sent.append(data)
                        or {'did_print': True, 'ready_for_next_job': True})
    client = make_client(tmp_path, empty_repo=True, model='PT-P750W', printer=DEVICE)
    client.post('/labeldesigner/api/settings', data={'label_size': 'pt12', 'cut': 'none', 'chain': '1'})
    assert outcome(client, client.post(FEED)) == ('printed', '')
    assert (_told(sent[0], b'M'), _told(sent[0], b'K')) == (b'\x40', b'\x08')


def test_a_receipt_printer_gets_it_through_the_label_designer(tmp_path, monkeypatch):
    fake = FakePrinter()
    monkeypatch.setattr(escpos, 'connect', fake)
    client = make_client(tmp_path, empty_repo=True, model='Epson/ESC-POS', printer='file:///dev/usb/lp3')
    assert outcome(client, client.post(FEED)) == ('printed', '')
    # Asked for its errors first, as for labels
    assert fake.sent == escpos.OFFLINE + escpos.PAPER + escpos.INIT + escpos.CUT


def test_a_zpl_printer_gets_it_through_the_label_designer(tmp_path, monkeypatch):
    without_status(monkeypatch)
    sent = []
    monkeypatch.setattr(zpl, 'write', lambda device, data: sent.append((device, data)))
    client = make_client(tmp_path, empty_repo=True, model='Zebra/ZPL', printer='file:///dev/usb/lp_zpl')
    assert outcome(client, client.post(FEED)) == ('printed', '')
    assert sent == [('file:///dev/usb/lp_zpl', b'~PH\n')]
