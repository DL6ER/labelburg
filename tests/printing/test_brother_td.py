"""Brother's TD-2 and TD-4 printers, after the raster command references of
the TD-2000 series (TD2000), the TD-2300D series (TD2300) and the TD-4D
series (TD4000), see drivers/brother/models.py."""

import pytest
from PIL import Image, ImageDraw

from app.labeldesigner import drivers, tape
from app.labeldesigner.drivers import backends, devices
from app.labeldesigner.drivers.base import Told, shown
from app.labeldesigner.drivers.brother import DRIVER, labels_td, network, usb
from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.raster import convert
from app.labeldesigner.label_names import label_printers, length_limits
from tests.helpers import EXAMPLE_FORMDATA, make_client, outcome, without_status
from app.labeldesigner import ipp
from tests.printing.test_ipp import attribute, response, state
from tests.printing.test_snmp import agent, fake_printer, query  # noqa: F401
from tests.printing.test_usb_jobs import COMPLETED, ERROR, PHASE_CHANGE, PRINTING, REPLY, WAITING, Clock, FakePrinter, send  # noqa: F401

ESC_I = b'\x1b\x69'
CONTINUOUS, DIE_CUT = 0x4A, 0x4B


def td_status(model_code, width, media_type=CONTINUOUS, length=0, kind=REPLY, phase=WAITING, errors=(0, 0)):
    """A status of a TD printer, as the references lay it out: series code
    35h, the media in mm, media sensor value 3Fh, mode 01h."""
    packet = bytearray(32)
    packet[0:6] = b'\x80\x20\x42\x35' + bytes([model_code]) + b'\x30'
    packet[8], packet[9] = errors
    packet[10], packet[11], packet[14], packet[15], packet[17] = width, media_type, 0x3F, 0x01, length
    packet[18], packet[19] = kind, phase
    return bytes(packet)


def job(model, label_size, image, **options):
    raster = Raster(model)
    convert(raster, [image], label_size, **options)
    return bytes(raster.data)


def test_continuous_roll_of_the_td_2000_series_as_in_its_reference():
    # TD2000 2.1: 200 bytes of invalidate, ESC @, ESC i a 01h, the print
    # information of its example of 58 mm at 300 dpi (C6h 0Ah 3Ah 00h, 266
    # lines), its 3 mm of margins (23h 00h) and no compression. 2.3.5: 12 pins
    # right and left of 648, 84 bytes a row (g 00h 54h).
    row = b'\x00\x0f' + b'\xff' * 80 + b'\xf0\x00'
    assert job('TD-2130N', 'td2-300-58', Image.new('1', (648, 266), 0)) == (
        b'\x00' * 200 + b'\x1b\x40' + ESC_I + b'\x61\x01'
        + ESC_I + b'\x7a\xc6\x0a\x3a\x00\x0a\x01\x00\x00\x00\x00'
        + ESC_I + b'\x64\x23\x00' + b'\x4d\x00'
        + (b'\x67\x00\x54' + row) * 266 + b'\x1a')


def test_td_2000_series_prints_fast_without_cut_commands():
    # No ESC i M, A or K in its command list (TD2000 3.), the quality flag
    # 40h only for quality; 203 dpi: 24 dots of margins, 56 bytes a row
    data = job('TD-2120N', 'td2-58', Image.new('1', (440, 100), 1), hq=False, cut=True)
    assert data.startswith(b'\x00' * 200 + b'\x1b\x40' + ESC_I + b'\x61\x01' + ESC_I + b'\x7a\x86\x0a\x3a\x00\x64\x00')
    assert ESC_I + b'\x64\x18\x00\x4d\x00\x67\x00\x38' in data
    assert all(ESC_I + command not in data for command in (b'\x4d', b'\x41', b'\x4b', b'\x53', b'\x21'))
    assert data.endswith(b'\x1a')


def test_die_cut_label_of_the_td_2300d_series_as_in_its_reference():
    # TD2300 4.1: 661 bytes of invalidate, ESC @, ESC i a 01h, ESC i ! 00h
    # for the status while printing, the print information of its example of
    # 51 x 26 mm (8Eh 0Bh 33h 1Ah, 230 lines at 300 dpi), auto cut (ESC i M
    # 40h), each label (ESC i A 01h), at the end (ESC i K 08h), no margins on
    # die-cut labels (4.3.3), and ESC i a FFh after the job. 4.3.5: 67 pins
    # left of 563, 66 right, 87 bytes a row: the first column of the label
    # is the pin next to the left margin.
    image = Image.new('1', (563, 230), 1)
    ImageDraw.Draw(image).line((0, 0, 0, 229), fill=0)
    row = b'\x00' * 78 + b'\x08' + b'\x00' * 8
    assert job('TD-2310D-300', 'td23-300-51x26', image) == (
        b'\x00' * 661 + b'\x1b\x40' + ESC_I + b'\x61\x01' + ESC_I + b'\x21\x00'
        + ESC_I + b'\x7a\x8e\x0b\x33\x1a\xe6\x00\x00\x00\x00\x00'
        + ESC_I + b'\x4d\x40' + ESC_I + b'\x41\x01' + ESC_I + b'\x4b\x08' + ESC_I + b'\x64\x00\x00' + b'\x4d\x00'
        + (b'\x67\x00\x57' + row) * 230 + b'\x1a' + ESC_I + b'\x61\xff')


@pytest.mark.parametrize('model, invalidate', [('TD-4420DN', 350), ('TD-4425DN', 661)])
def test_continuous_roll_of_the_td_4d_series_not_cut(model, invalidate):
    # TD4000 NULL: 350 or 661 bytes of invalidate by the model; 4.1: 3 mm of
    # margins at 203 dpi (18h 00h); 4.3.5: 22 pins right and left of 788 on
    # 102 mm, 104 bytes a row; ESC i K: not cut at the end
    row = b'\x00\x00\x03' + b'\xff' * 98 + b'\xc0\x00\x00'
    assert job(model, 'td4-102', Image.new('1', (788, 100), 0), cut=False) == (
        b'\x00' * invalidate + b'\x1b\x40' + ESC_I + b'\x61\x01' + ESC_I + b'\x21\x00'
        + ESC_I + b'\x7a\x86\x0a\x66\x00\x64\x00\x00\x00\x00\x00'
        + ESC_I + b'\x4b\x00' + ESC_I + b'\x64\x18\x00' + b'\x4d\x00'
        + (b'\x67\x00\x68' + row) * 100 + b'\x1a' + ESC_I + b'\x61\xff')


@pytest.mark.parametrize('model, label_size, margins, row_bytes', [
    # TD4000 and TD2300 4.1: 3 mm are 24h 00h at 300 dpi, 18h 00h at 203 dpi
    ('TD-4520DN', 'td4-300-102', b'\x24\x00', 160), ('TD-2350D-300', 'td23-300-58', b'\x24\x00', 87),
    ('TD-2350D', 'td23-58', b'\x18\x00', 59), ('TD-2135NWB', 'td2-300-57', b'\x23\x00', 84)])
def test_margins_and_rows_of_a_continuous_roll(model, label_size, margins, row_bytes):
    label = labels_td.BY_ID[label_size]
    data = job(model, label_size, Image.new('1', (label.dots_printable[0], 150), 1))
    assert ESC_I + b'\x64' + margins + b'\x4d\x00\x67\x00' + bytes([row_bytes]) in data


@pytest.mark.parametrize('label_size, pins', [
    # TD2000 2.3.5, TD2300 and TD4000 4.3.5: pins right of, in and left of the print area
    ('td2-51x26', (33, 382, 33)), ('td2-60x60', (0, 448, 0)), ('td2-300-30x30', (177, 318, 177)),
    ('td23-300-57', (29, 637, 30)), ('td4-76x26', (123, 585, 124)), ('td4-90', (68, 695, 69)),
    ('td4-300-58', (315, 649, 316)), ('td4-300-102x152', (58, 1164, 58))])
def test_print_area_on_the_print_head(label_size, pins):
    label = labels_td.BY_ID[label_size]
    head = Raster(label.models[0]).pixel_width
    assert (label.offset_r, label.dots_printable[0], head - label.offset_r - label.dots_printable[0]) == pins


@pytest.mark.parametrize('model, label_size, lines, flags', [
    # TD2000 2.3.2, TD2300 and TD4000 4.3.2: print area lengths of die-cut
    # labels; the TD-2000 series with the quality flag
    ('TD-2120N', 'td2-51x26', 157, 0xCE), ('TD-2310D', 'td23-51x26', 156, 0x8E), ('TD-4410D', 'td4-102x152', 1170, 0x8E),
    ('TD-4555DNWB', 'td4-300-102x152', 1728, 0x8E), ('TD-2135N', 'td2-300-40x60', 638, 0xCE)])
def test_die_cut_labels_are_printed_in_their_print_area(model, label_size, lines, flags):
    label = labels_td.BY_ID[label_size]
    assert label.dots_printable[1] == lines
    data = job(model, label_size, Image.new('1', label.dots_printable, 1))
    information = data.index(ESC_I + b'\x7a')
    assert data[information + 3:information + 9] == bytes([flags, 0x0B, *label.tape_size]) + lines.to_bytes(2, 'little')


def test_a_label_drawn_lying_is_turned():
    assert job('TD-4420DN', 'td4-102x152', Image.new('1', (1170, 788), 1)) == job(
        'TD-4420DN', 'td4-102x152', Image.new('1', (788, 1170), 1))


@pytest.mark.parametrize('model_code, model', [
    # TD2000 4. (status, p. 22), TD2300 5.2.1, TD4000 5.2.1
    (0x33, 'TD-2020'), (0x35, 'TD-2120N'), (0x36, 'TD-2130N'), (0x48, 'TD-2135NWB'),
    (0x54, 'TD-2310D'), (0x55, 'TD-2310D-300'), (0x5A, 'TD-2320DSA'), (0x67, 'TD-2350DSA-300'),
    (0x37, 'TD-4410D'), (0x41, 'TD-4520DN'), (0x42, 'TD-4550DNWB'), (0x43, 'TD-4210D'), (0x6A, 'TD-4215D'),
    (0x70, 'TD-4555DNWB'),
    # Left out: linerless models, the TD-2030A and the TD-4510D
    (0x58, 'Unknown'), (0x71, 'Unknown'), (0x44, 'Unknown'), (0x39, 'Unknown')])
def test_model_of_the_status(model_code, model):
    assert usb.parse_status(td_status(model_code, 102))['model'] == model


@pytest.mark.parametrize('model_code, width, media_type, length, model, size', [
    (0x38, 102, DIE_CUT, 152, 'TD-4420DN', 'td4-102x152'), (0x41, 102, CONTINUOUS, 0, 'TD-4520DN', 'td4-300-102'),
    (0x35, 51, DIE_CUT, 26, 'TD-2120N', 'td2-51x26'), (0x36, 40, DIE_CUT, 60, 'TD-2130N', 'td2-300-40x60'),
    (0x57, 57, CONTINUOUS, 0, 'TD-2320D-300', 'td23-300-57'), (0x6F, 76, DIE_CUT, 26, 'TD-4455DNWB', 'td4-76x26')])
def test_roll_of_the_status(model_code, width, media_type, length, model, size):
    # Media types 4Ah and 4Bh: TD2000 4. (4), TD2300 5.2.6, TD4000 5.2.5
    state = usb.parse_status(td_status(model_code, width, media_type, length))
    assert (state['model'], state['media_category'], state['errors']) == (model, 'RD', [])
    found = tape.in_printer(state, model, '', '62')
    assert (found.id, found.known) == (size, True)


@pytest.mark.parametrize('errors, expected', [
    # TD2300 5.2.3 and 5.2.4, TD4000 5.2.2 and 5.2.3
    ((0x02, 0), ['End of media (die-cut size only)']), ((0x04, 0), ['Tape cutter jam']),
    ((0, 0x10), ['Cover opened while printing (Except QL-500)']),
    ((0, 0x40), ['Media cannot be fed (also when the media end is detected)'])])
def test_errors_of_the_status(errors, expected):
    assert usb.parse_status(td_status(0x38, 102, errors=errors))['errors'] == expected


def test_status_over_snmp(agent):
    # The model and roll of its status, as of the other Brother network printers
    state = query(agent, td_status(0x42, 102, DIE_CUT, 50), model='QL-800')
    assert (state['model'], state['media_width'], state['media_length'], state['media_type']) == (
        'TD-4550DNWB', 102, 50, 'Die-cut labels')
    assert query(agent, td_status(0x38, 102, errors=(0, 0x10)))['errors'] == ['The cover is open']
    assert query(agent, td_status(0x38, 102, errors=(0x02, 0)))['errors'] == ['The labels are used up']


def test_announced_on_the_network():
    service = {'service': '_pdl-datastream._tcp.local', 'address': '192.0.2.5', 'port': 9100,
               'txt': {'ty': 'Brother TD-4420DN', 'usb_mdl': 'TD-4420DN'}}
    assert DRIVER.announced(service) == ('tcp://192.0.2.5', 'TD-4420DN')
    assert network.label_printer_model('Brother TD-2135NWB') == 'TD-2135NWB'


@pytest.mark.parametrize('name, configured, model', [
    # As Brother names the TD-2300D models of each resolution (TD2300_GUIDE,
    # LabelCLT; TD2300 4.2.1 and 5.2.1)
    ('Brother TD-2350D (300 dpi)', None, 'TD-2350D-300'), ('Brother TD-2310D (300 DPI)', 'TD-2310D', 'TD-2310D-300'),
    ('Brother TD-2350D (203 dpi)', 'TD-2350D-300', 'TD-2350D'),
    # Named without it, a configured model of the other resolution wins
    ('Brother TD-2350D', 'TD-2350D-300', 'TD-2350D-300'), ('Brother TD-2350D', None, 'TD-2350D'),
    ('Brother TD-2350D', 'QL-800', 'TD-2350D'), ('Brother TD-2320D', 'TD-2350D-300', 'TD-2320D'),
    # Other printers as before
    ('Brother QL-810W', 'QL-800', 'QL-810W'), ('Brother TD-4520DN', 'TD-4420DN', 'TD-4520DN'),
    ('Brother MFC-L3760CDW series', 'TD-2350D-300', None)])
def test_model_of_a_name_on_the_network(name, configured, model):
    assert network.label_printer_model(name, configured) == model


@pytest.mark.parametrize('name, configured, model', [
    ('Brother TD-2350D (300 dpi)', 'QL-800', 'TD-2350D-300'), ('Brother TD-2350D', 'TD-2350D-300', 'TD-2350D-300'),
    ('Brother TD-2350D', 'TD-2350D', 'TD-2350D')])
def test_printer_of_300_dpi_on_ipp_only(fake_printer, name, configured, model):
    fake_printer['body'] = response(attribute(0x41, 'printer-make-and-model', name.encode()), state(ipp.IDLE))
    assert network.query_network_status('tcp://127.0.0.1', configured)['model'] == model


def test_printer_of_300_dpi_over_snmp(agent):
    # The model code tells the resolution, whatever is configured
    assert query(agent, td_status(0x55, 58), model='TD-2310D')['model'] == 'TD-2310D-300'
    assert query(agent, td_status(0x54, 58), model='TD-2310D-300')['model'] == 'TD-2310D'


@pytest.mark.parametrize('txt, model', [
    ({'ty': 'Brother TD-2350D (300 dpi)', 'usb_mdl': 'TD-2350D'}, 'TD-2350D-300'),
    ({'ty': 'Brother TD-2350D', 'usb_mdl': 'TD-2350D'}, 'TD-2350D')])
def test_announced_printer_of_300_dpi(txt, model):
    service = {'service': '_pdl-datastream._tcp.local', 'address': '192.0.2.5', 'port': 9100, 'txt': txt}
    assert DRIVER.announced(service) == ('tcp://192.0.2.5', model)


@pytest.mark.parametrize('name, configured', [('Brother TD-2350D', 'TD-2350D-300'), ('Brother TD-2350D (300 dpi)', 'QL-800')])
def test_label_of_a_printer_of_300_dpi_on_ipp_only_is_made_for_it(fake_printer, monkeypatch, tmp_path, name, configured):
    # Not as for 203 dpi, two thirds as large
    fake_printer['body'] = response(attribute(0x41, 'printer-make-and-model', name.encode()), state(ipp.IDLE))
    sent = []
    monkeypatch.setattr(network, 'send', lambda data, device: sent.append(data))
    monkeypatch.setattr(network, 'NETWORK_JOB_POLL', 0.01)
    client = make_client(tmp_path, model=configured, printer='tcp://127.0.0.1')
    model = client.get('/labeldesigner/api/printer_status').get_json()['model']
    assert model == 'TD-2350D-300'
    # As the label designer prints, with the model of the status
    r = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size='td23-300-58', model=model))
    assert outcome(client, r) == ('printed', '')
    assert ESC_I + b'\x64\x24\x00\x4d\x00\x67\x00\x57' in sent[0]


@pytest.mark.parametrize('product, model', [
    # TD2000, Appendix A
    (0x2055, 'TD-2020'), (0x2057, 'TD-2120N'), (0x2058, 'TD-2130N'), (0x20F6, None), (0x20F7, 'TD-2125N'),
    (0x20F8, 'TD-2125NWB'), (0x20F9, 'TD-2135N'), (0x20FA, 'TD-2135NWB'),
    # A QL-800 tells its model in its status
    (0x209B, None)])
@pytest.mark.parametrize('device', ['file:///dev/usb/lp0', 'usb://0x04f9:0x2057'])
def test_td_2000_series_known_by_its_product_id(monkeypatch, device, product, model):
    monkeypatch.setattr(devices, 'usb_ids', lambda device: (0x04F9, product))
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: '')
    assert DRIVER.model_of(device) == model
    assert drivers.resolve(device, 'QL-800') == (drivers.BROTHER, model or 'QL-800')


def test_product_ids_of_other_makers_are_none(monkeypatch):
    monkeypatch.setattr(devices, 'usb_ids', lambda device: (0x0922, 0x2057))
    assert DRIVER.model_of('file:///dev/usb/lp0') is None
    assert DRIVER.model_of('tcp://192.0.2.5') is None and DRIVER.model_of(None) is None


def test_name_of_a_td_2300d_model_is_that_of_either_resolution(monkeypatch):
    assert DRIVER.told('MFG:Brother;MDL:TD-2310D;') == Told('TD-2310D', resolution=False)
    assert DRIVER.told('MFG:Brother;MDL:TD-2310D (300 dpi);') == Told('TD-2310D-300')
    assert DRIVER.told('MFG:Brother;MDL:TD-4420DN;') == Told('TD-4420DN')
    # Shared over USB/IP, a 300 dpi one keeps the model configured
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: 'MFG:Brother;MDL:TD-2310D;')
    assert drivers.resolve('usbip://192.0.2.1', 'TD-2310D-300') == (drivers.BROTHER, 'TD-2310D-300')
    assert drivers.resolve('usbip://192.0.2.1', 'QL-800') == (drivers.BROTHER, 'TD-2310D')


def test_td_2300d_models_of_300_dpi_are_shown_as_those_of_203_dpi():
    assert (shown('TD-2310D-300'), shown('TD-2350DSA-300'), shown('TD-4420DN')) == ('TD-2310D', 'TD-2350DSA', 'TD-4420DN')
    assert DRIVER.capabilities('TD-2310D-300')['model_shown'] == 'TD-2310D'
    assert DRIVER.resolution_problem('TD-2310D-300', True) == 'The TD-2310D cannot print at 600 dpi, turn off 600 dpi printing'


def test_capabilities():
    caps = {model: DRIVER.capabilities(model) for model in labels_td.MODELS}
    # The TD-2000 series takes no cut commands, its labels are torn off
    assert {model for model, c in caps.items() if not c['cutter']} == {
        'TD-2020', 'TD-2120N', 'TD-2125N', 'TD-2125NWB', 'TD-2130N', 'TD-2135N', 'TD-2135NWB'}
    assert not any(c['manual_cutter'] or c['high_res'] or c['sensing'] or c['half_cut'] for c in caps.values())
    # Only the TD-2000 series takes a quality flag
    assert caps['TD-2120N']['qualities'] == ['fast', 'quality'] and caps['TD-4420DN']['qualities'] == ['quality']
    assert caps['TD-4420DN']['leaves_turned'] and caps['TD-4420DN']['colors'] == 'black'


def test_td_printers_take_only_their_rolls():
    assert tape.takes('TD-4420DN') == ['td4-58', 'td4-76', 'td4-90', 'td4-102', 'td4-51x26', 'td4-76x26', 'td4-102x50',
                                       'td4-102x152']
    assert tape.takes('TD-2350D-300') == ['td23-300-57', 'td23-300-58', 'td23-300-51x26']
    assert len(tape.takes('TD-2120N')) == 9 and all(size.startswith('td2-') for size in tape.takes('TD-2125NWB'))
    assert '62' not in tape.takes('TD-4520DN') and not any(size.startswith('td') for size in tape.takes('QL-1100'))
    assert label_printers(labels_td.BY_ID['td4-300-102x152']) == ['TD-4520DN', 'TD-4525DN', 'TD-4550DNWB', 'TD-4555DNWB']


@pytest.mark.parametrize('model, speed', [
    # mm/s as Brother's user's guides give them, see models.py
    ('TD-2120N', 152.4), ('TD-2135NWB', 152.4), ('TD-2350D-300', 152.4), ('TD-4210D', 127), ('TD-4420DN', 203.2),
    ('TD-4425DN', 203), ('TD-4550DNWB', 152.4)])
def test_print_speed(model, speed):
    assert DRIVER.print_speed(model, True, False)[0] == speed


def test_quality_is_slower_only_where_the_printer_takes_it():
    from app.labeldesigner.drivers.brother.models import QL_BESIDES, QUALITY_SLOWER
    # Only the TD-2000 series takes the flag of print quality
    assert DRIVER.print_speed('TD-4420DN', False, False) == DRIVER.print_speed('TD-4420DN', True, False) == (203.2, QL_BESIDES)
    assert DRIVER.print_speed('TD-2120N', False, False) == (152.4 / QUALITY_SLOWER, QL_BESIDES * QUALITY_SLOWER)


def test_names_of_the_rolls():
    assert DRIVER.label_names['td4-102x152'] == '102 x 152 mm die-cut (RD)'
    assert DRIVER.label_names['td2-300-58'] == '58 mm endless (RD)'


@pytest.mark.parametrize('label_size, limits', [
    # Margins of 3 mm, the shortest print of 12 mm (TD2000 2.3.4) or 20 mm with
    # the cutter (TD2300 and TD4000 4.3.4), the longest of 1000 or 3000 mm
    ('td2-58', (18.1, 1000, 3.0)), ('td2-300-57', (18, 1000, 3.0)), ('td23-58', (26.1, 3000, 3.0)),
    ('td4-300-102', (26.1, 3000, 3.0))])
def test_lengths_of_a_continuous_roll(label_size, limits):
    shortest, longest, margin = length_limits(labels_td.BY_ID[label_size])
    assert (shortest, longest, round(margin, 1)) == limits


def test_label_too_long_is_refused():
    from app.labeldesigner import refusals
    assert DRIVER.length_problem('TD-2120N', 7993, False) == 'Labels on the TD-2120N can be at most 1000 mm long'
    assert DRIVER.length_problem('TD-4420DN', 23977, False) == ''
    assert DRIVER.length_problem('TD-2310D-300', 35434, False) == 'Labels on the TD-2310D can be at most 3000 mm long'
    assert 'Labels on the TD-2120N can be at most 1000 mm long' in refusals.MESSAGES
    assert 'The TD-4420DN cannot print at 600 dpi, turn off 600 dpi printing' in refusals.MESSAGES
    assert 'The label size 102 x 152 mm die-cut (RD) cannot be printed on a QL-1100' in refusals.MESSAGES


@pytest.mark.parametrize('saved, model, printed', [
    # A 4 x 6 in label of any driver, also Brother's DK-11241, and the 102 mm DK-22243
    ('zpl102x152', 'TD-4420DN', 'td4-102x152'), ('tspl300-102x152', 'TD-4520DN', 'td4-300-102x152'),
    ('dymo102x152', 'TD-4425DN', 'td4-102x152'), ('102x152', 'TD-4550DNWB', 'td4-300-102x152'),
    ('102', 'TD-4410D', 'td4-102'),
    # The same roll of another TD series or resolution
    ('td2-51x26', 'TD-4420DN', 'td4-51x26'), ('td4-300-51x26', 'TD-2310D', 'td23-51x26'),
    ('td23-58', 'TD-2130N', 'td2-300-58'), ('td2-300-40x60', 'TD-2125N', 'td2-40x60'),
    # No TD roll of that paper
    ('62', 'TD-4420DN', '62'), ('zpl102x51', 'TD-4420DN', 'zpl102x51'), ('td2-60x60', 'TD-4420DN', 'td2-60x60')])
def test_label_for_the_same_paper(saved, model, printed):
    assert DRIVER.label_for(saved, model) == printed
    assert tape.same_paper(saved, model) == printed


def test_a_td_label_prints_on_the_same_paper_of_other_drivers():
    assert drivers.for_model('Zebra/ZPL').label_for('td4-102x152', 'Zebra/ZPL') == 'zpl102x152'
    assert drivers.for_model('DYMO/LabelWriter-4XL').label_for('td4-300-102x152', 'DYMO/LabelWriter-4XL') == 'dymo102x152'
    # QL printers keep the label size
    assert DRIVER.label_for('td4-102x152', 'QL-1100') == 'td4-102x152'


def page(model_code=0x38, printing_seconds=0.0):
    """The messages of a page of a TD printer with the status notification
    on: printing, completed, waiting to receive (TD4000 8.1)."""
    return [(0, td_status(model_code, 102, kind=PHASE_CHANGE, phase=PRINTING)),
            (printing_seconds, td_status(model_code, 102, kind=COMPLETED)),
            (0, td_status(model_code, 102, kind=PHASE_CHANGE, phase=WAITING))]


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(usb, 'time', clock)
    return clock


def test_job_confirmed_by_the_status_messages(clock, monkeypatch):
    info = send(monkeypatch, FakePrinter(clock, page(printing_seconds=2) * 2), [152, 152])
    assert (info['did_print'], info['ready_for_next_job'], info['confirmed']) == (True, True, 2)


def test_error_while_printing(clock, monkeypatch):
    fake = FakePrinter(clock, [page()[0], (1, td_status(0x38, 102, kind=ERROR, errors=(0, 0x10)))])
    info = send(monkeypatch, fake, [152])
    assert not info['did_print']
    assert usb.print_failure(info) == 'The printer reports: Cover opened while printing (Except QL-500)'


def test_labels_printed_through_the_api(clock, monkeypatch, tmp_path):
    jobs = []

    class Backend(FakePrinter):
        def __init__(self, device_specifier):
            super().__init__(clock, [])
            jobs.append(self)

        def write(self, data):
            super().write(data)
            if self.written.endswith(b'\x1a' + ESC_I + b'\x61\xff'):
                self.messages = page() * self.written.count(ESC_I + b'\x7a')
    monkeypatch.setattr(backends, 'KernelPrinter', Backend)
    without_status(monkeypatch)
    client = make_client(tmp_path, model='TD-4420DN', printer='file:///dev/usb/lp0')
    data = dict(EXAMPLE_FORMDATA, label_size='td4-102x152', print_count='2')
    assert outcome(client, client.post('/labeldesigner/api/print', data=data)) == ('printed', '')
    written = jobs[-1].written
    # Each label a job of its own, as on the QL printers
    assert written.count(b'\x00' * 350 + b'\x1b\x40' + ESC_I + b'\x61\x01' + ESC_I + b'\x21\x00') == 2
    assert written.count(ESC_I + b'\x7a\x8e\x0b\x66\x98\x92\x04\x00\x00\x00\x00') == 2
    assert written.count(b'\x1a' + ESC_I + b'\x61\xff') == 2 and written.endswith(b'\x1a' + ESC_I + b'\x61\xff')
