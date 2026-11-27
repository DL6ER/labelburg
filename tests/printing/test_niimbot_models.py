"""The Niimbot models besides the D110_M and the B1: the table they come
from, their labels, their packets against the fake printer of
test_niimbot.py, and how a printer is known by its model ID."""
import struct
from types import SimpleNamespace

import pytest

from app.labeldesigner import drivers, refusals
from app.labeldesigner.drivers import backends, devices, niimbot
from app.labeldesigner.drivers.niimbot import models, protocol
from app.labeldesigner.label_names import label_dpi
from app.labeldesigner.tape import same_paper
from tests.helpers import make_client, outcome, without_status
from tests.printing.test_niimbot import REPLIES, TEXT, FakePrinter, data_of

DEVICE = 'bt://00:00:5E:00:53:21'

# niimbluelib at 0c9d33d447bc7ba612f04fa41b2f7bbd0ddd8117: the id, dpi,
# printheadPixels, densityMin, densityMax and densityDefault of
# src/printer_models.ts, the print task of src/print_tasks/index.ts
NIIMBLUELIB = {
    'D110_M': ((2320,), 203, 96, 1, 5, 3, 'd110m_v4'),  # printer_models.ts:548, index.ts:83 (protocol 4)
    'B1': ((4096,), 203, 384, 1, 5, 3, 'b1'),  # printer_models.ts:174, index.ts:82
    'B21_C2B': ((771, 775), 203, 384, 1, 5, 3, 'b1'),  # printer_models.ts:306, index.ts:82
    'D101': ((2560,), 203, 192, 1, 3, 2, 'b1'),  # printer_models.ts:493, index.ts:82
    'N1': ((3586,), 203, 96, 1, 3, 2, 'b1'),  # printer_models.ts:790, index.ts:82
    'M2_H': ((4608,), 300, 567, 1, 5, 3, 'b1'),  # printer_models.ts:746, index.ts:82
    'B1_PRO': ((4097,), 300, 567, 1, 5, 3, 'd110m_v4'),  # printer_models.ts:185, index.ts:83
    'B21_PRO': ((785,), 300, 591, 1, 5, 3, 'd110m_v4'),  # printer_models.ts:295, index.ts:83
    'D11_H': ((528,), 300, 142, 1, 5, 3, 'd110m_v4'),  # printer_models.ts:515, index.ts:83
    'B21': ((768,), 203, 384, 1, 5, 3, 'b21_v1'),  # printer_models.ts:284, index.ts:79
    'B21_L2B': ((769,), 203, 384, 1, 5, 3, 'b21_l2b'),  # printer_models.ts:317, index.ts:80
    'B21S': ((777,), 203, 384, 1, 5, 3, 'd110'),  # printer_models.ts:328, index.ts:81
    'B21S_C2B': ((776,), 203, 384, 1, 5, 3, 'd110'),  # printer_models.ts:339, index.ts:81
    'D110': ((2304, 2305), 203, 96, 1, 3, 2, 'd110'),  # printer_models.ts:537, index.ts:81
    'D11': ((512,), 203, 96, 1, 3, 2, 'd11_v1'),  # printer_models.ts:504, index.ts:78
    'D11S': ((514,), 203, 96, 1, 3, 2, 'd11_v1'),  # printer_models.ts:559, index.ts:78
    'H1S': ((4352,), 203, 96, 1, 3, 2, 'h1s'),  # printer_models.ts:636, index.ts:84
    'B18': ((3584,), 203, 96, 1, 3, 2, ''),  # printer_models.ts:229, no task
    'B3S': ((256, 260, 262), 203, 576, 1, 5, 3, ''),  # printer_models.ts:394, no task
}
# The printheads https://printers.niim.blue/hardware/ marks verified where
# they differ from printheadPixels: NIIMBOT-B1_Pro/, niimbot-b21_pro/,
# niimbot-m2h/ and niimbot-d11_h/. niimbluelib counts the dots of a row by
# the width the printer tells (AbstractPrintTask.ts:153-156), 576 by a B21
# Pro and a B1 Pro (src/test/virtual-client.test.ts:85, 132, 338). The
# D110_M printed on 120.
HEADS = {'B1_PRO': 576, 'B21_PRO': 576, 'M2_H': 576, 'D11_H': 144, 'D110_M': 120}


@pytest.fixture(autouse=True)
def answered(monkeypatch):
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(niimbot, '_asked', {})
    monkeypatch.setattr(niimbot, '_no_serial', set())
    monkeypatch.setattr(niimbot, '_owed', {})
    monkeypatch.setattr(niimbot, '_told_models', {})
    monkeypatch.setattr(niimbot, '_to_ask', set())
    monkeypatch.setattr(devices, 'looked', 0.0)


@pytest.fixture
def fake(monkeypatch):
    printer = FakePrinter()

    def connect(device, le=False):
        c = protocol.Connection.__new__(protocol.Connection)
        c.s, c.received = printer, b''
        return c
    monkeypatch.setattr(protocol, 'connect', connect)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    return printer


def told(monkeypatch, model_id):
    monkeypatch.setitem(REPLIES, 0x40, (0x48, struct.pack('>H', model_id)))


def niimbluelib_packet(command, data):
    # src/packets/packet.ts:80-110 toBytes(): 55 55, command, length, data,
    # the checksum of :72-77, aa aa
    check = command ^ len(data)
    for byte in data:
        check ^= byte
    return bytes([0x55, 0x55, command, len(data), *data, check, 0xaa, 0xaa])


def test_models_are_those_of_niimbluelib():
    table = {model.short: (model.ids, model.dpi, model.dots, *model.density, model.sequence) for model in models.MODELS}
    listed = {short: (ids, dpi, HEADS.get(short, pixels), *rest) for short, (ids, dpi, pixels, *rest) in NIIMBLUELIB.items()}
    # The D110_M keeps the sequence it printed with
    assert table == dict(listed, D110_M=((2320,), 203, 120, 1, 5, 3, 'v4'))


def test_models_printed_are_those_of_the_sequences_labelburg_has():
    assert niimbot.MODELS == ('Niimbot/D110_M', 'Niimbot/B1', 'Niimbot/B21_C2B', 'Niimbot/D101', 'Niimbot/N1',
                              'Niimbot/M2_H', 'Niimbot/B1_PRO', 'Niimbot/B21_PRO', 'Niimbot/D11_H')
    # Only the D110_M is paired, only these two need the RFID tag of their
    # roll and keep the packets they printed with
    assert [model.name for model in models.PRINTED if 'classic' in model.connections] == ['Niimbot/D110_M']
    assert [model.name for model in models.PRINTED if model.needs_tag] == ['Niimbot/D110_M', 'Niimbot/B1']
    assert [model.name for model in models.PRINTED if not model.exact] == ['Niimbot/D110_M', 'Niimbot/B1']


def test_models_are_shown_by_their_plain_names():
    assert {model: drivers.base.shown(model) for model in niimbot.MODELS} == {
        'Niimbot/D110_M': 'Niimbot D110_M', 'Niimbot/B1': 'Niimbot B1', 'Niimbot/B21_C2B': 'Niimbot B21 C2B',
        'Niimbot/D101': 'Niimbot D101', 'Niimbot/N1': 'Niimbot N1', 'Niimbot/M2_H': 'Niimbot M2-H',
        'Niimbot/B1_PRO': 'Niimbot B1 Pro', 'Niimbot/B21_PRO': 'Niimbot B21 Pro', 'Niimbot/D11_H': 'Niimbot D11-H'}
    assert niimbot.DRIVER.capabilities('Niimbot/B1_PRO')['model_shown'] == 'B1 Pro'


@pytest.mark.parametrize('label_size, model, shared', [
    # The same dots on the same rolls
    ('niimbot50x30', 'Niimbot/B21_C2B', True), ('niimbotd50', 'Niimbot/B21_C2B', True),
    ('niimbot15x8-2x3', 'Niimbot/B21_C2B', True), ('niimbot12x40', 'Niimbot/D101', True),
    ('niimbot15x50', 'Niimbot/D101', True), ('niimbot12x40', 'Niimbot/N1', True),
    # 15 mm on a printhead of 12, or at 300 dpi
    ('niimbot15x50', 'Niimbot/N1', False), ('niimbot50x30', 'Niimbot/B1_PRO', False),
    ('niimbot12x40', 'Niimbot/D11_H', False)])
def test_labels_of_a_model_are_those_of_the_b1_or_d110_m_where_they_are_the_same(label_size, model, shared):
    sizes = {label.identifier: label for label in niimbot.LABELS}
    assert (model in sizes[label_size].models) is shared


def test_labels_that_differ_are_named_by_their_model():
    sizes = {label.identifier: label for label in niimbot.LABELS}
    # 50 mm at 300 dpi, on 48 mm of the printhead
    assert (sizes['niimbot50x30-b1pro'].dots_total, sizes['niimbot50x30-b1pro'].dots_printable) == ((591, 354), (576, 354))
    assert sizes['niimbot50x30-b21pro'].dots_printable == (576, 354)
    assert sizes['niimbot50x30-b1pro'].models == ('Niimbot/B1_PRO',)
    assert sizes['niimbot15x50-n1'].dots_printable == (96, 400)
    assert sizes['niimbot12x40-d11h'].dots_printable == (142, 472)
    assert sizes['niimbot14x40-d11h'].dots_printable == (144, 472)
    assert sizes['niimbotd50-m2h'].dots_printable == (576, 591)
    # Pages of several labels as on the B1 only
    assert not any(label.pieces != (1, 1, 0) and label.models[0] != 'Niimbot/B1' for label in niimbot.LABELS)
    assert niimbot.NAMES['niimbot50x30-b1pro'] == niimbot.NAMES['niimbot50x30'] == '50 x 30 mm die-cut (Niimbot)'
    # Made at the dpi of their printer
    assert (label_dpi('niimbot50x30-b1pro', False), label_dpi('niimbot50x30', False)) == (300, 203)


@pytest.mark.parametrize('label_size, model, on', [
    ('niimbot50x30', 'Niimbot/B1_PRO', 'niimbot50x30-b1pro'), ('niimbot50x30-b1pro', 'Niimbot/B1', 'niimbot50x30'),
    ('niimbot50x30-b1pro', 'Niimbot/M2_H', 'niimbot50x30-m2h'), ('niimbotd50', 'Niimbot/B21_PRO', 'niimbotd50-b21pro'),
    ('niimbot15x8-2x3', 'Niimbot/D11_H', 'niimbot15x8-2x3'), ('niimbot14x40', 'Niimbot/D11_H', 'niimbot14x40-d11h'),
    ('niimbot50x30', 'Niimbot/B21_C2B', 'niimbot50x30')])
def test_label_made_for_another_model_goes_on_the_same_roll(label_size, model, on):
    assert same_paper(label_size, model) == on


@pytest.mark.parametrize('model_id, model', [(771, 'B21 C2B'), (775, 'B21 C2B'), (4097, 'B1 Pro'), (2560, 'D101')])
def test_printer_is_known_by_its_model_id(fake, monkeypatch, model_id, model):
    told(monkeypatch, model_id)
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/B1', label_size='niimbot50x30')
    message = niimbot.DRIVER.send(queue, [[bytes(384)]])
    assert message == f'This printer is a Niimbot {model}, choose that model for it'
    assert message in refusals.MESSAGES and 0x01 not in fake.commands


@pytest.mark.parametrize('model_id, model', [(768, 'B21'), (777, 'B21S'), (776, 'B21S C2B'), (512, 'D11'), (514, 'D11S'),
                                             (3584, 'B18'), (262, 'B3S'), (2305, 'D110'), (769, 'B21 L2B')])
def test_printer_of_a_sequence_labelburg_lacks_is_not_printed_on(fake, monkeypatch, model_id, model):
    told(monkeypatch, model_id)
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/D110_M', label_size='niimbot12x40')
    message = niimbot.DRIVER.send(queue, [[bytes(96)]])
    assert message == f'This printer is a Niimbot {model}, Labelburg cannot print on it yet'
    assert message in refusals.MESSAGES and 0x21 not in fake.commands


@pytest.mark.parametrize('answer, model', [(b'\x03', 'B21'), (b'\x02', 'D11'), (b'\x0e', 'B18'), (b'\x01', 'B3S')])
def test_model_id_of_one_byte_is_its_high_byte(fake, monkeypatch, answer, model):
    # niimbluelib, src/packets/packet_parser.ts:164-173 parsePrinterInfoModelIdResponse
    monkeypatch.setitem(REPLIES, 0x40, (0x48, answer))
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/B1', label_size='niimbot50x30')
    assert niimbot.DRIVER.send(queue, [[bytes(384)]]) == f'This printer is a Niimbot {model}, Labelburg cannot print on it yet'
    assert 0x21 not in fake.commands


def test_b1_pro_gets_the_packets_of_niimbluelibs_d110m_v4_task(fake, monkeypatch):
    # niimbluelib at 0c9d33d4, src/print_tasks/D110MV4PrintTask.ts, the
    # packets of src/packets/packet_generator.ts, the commands of
    # src/packets/commands.ts. Two empty rows, one of 3 dots, 351 black.
    told(monkeypatch, 4097)
    row = bytearray(576)
    for x in (10, 300, 570):
        row[x] = 1
    rows = [bytes(576)] * 2 + [bytes(row)] + [b'\x01' * 576] * 351
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/B1_PRO', label_size='niimbot50x30-b1pro')
    assert niimbot.DRIVER.send(queue, [rows]) == ''
    p = niimbluelib_packet
    # The rows of 576 dots are 72 bytes, the thirds of 24 bytes of the
    # printhead of 576 dots it tells (utils.ts:98-99), their dots counted
    # in each
    black = bytes([192, 192, 192])
    assert fake.packets[3:] == [
        # D110MV4PrintTask.ts:20 setLabelType, packet_generator.ts:113, 0x23 commands.ts:36, WithGaps
        p(0x23, [1]),
        # :21 setDensity, packet_generator.ts:109, 0x21 commands.ts:34, densityDefault printer_models.ts:185
        p(0x21, [3]),
        # :33 printStart9b, packet_generator.ts:234-235: 1 page, 4 zeros,
        # SingleColor 0 (enumerations.ts:158), speed 1 (AbstractPrintTask.ts:57), 0
        p(0x01, [0, 1, 0, 0, 0, 0, 0, 1, 0]),
        # :44-46 printStatus not waited for, packet_generator.ts:199-200, data [1] of mapped() :41
        p(0xa3, [1]),
        # :50 setPageSize13b, packet_generator.ts:168-193: 354 rows, 576
        # columns (image_encoder.ts:130), 1 copy, cut height 0, cut type 0, 0, sendAll 0, part height 0
        p(0x13, [0x01, 0x62, 0x02, 0x40, 0, 1, 0, 0, 0, 0, 0, 0, 0]),
        # :51 writeImageData, packet_generator.ts:471-472 printEmptySpace :250-251 for the empty rows
        p(0x84, [0, 0, 2]),
        # packet_generator.ts:442-450 printBitmapRowIndexed :276-290 for 6
        # dots or less, their places (image_encoder.ts:247-255)
        p(0x83, [0, 2, 1, 1, 1, 1, 0, 10, 1, 44, 2, 58]),
        # packet_generator.ts:454-460 printBitmapRow :254-263; equal rows end
        # after row 199 (image_encoder.ts:225-229)
        p(0x85, [0, 3, *black, 197] + [0xff] * 72),
        p(0x85, [0, 200, *black, 154] + [0xff] * 72),
        # :52 pageEnd, packet_generator.ts:246
        p(0xe3, [1]),
        # :59-64 waitForFinished, protocol.ts:154-159 getPrintStatus :298-299
        p(0xa3, [1]),
        # :73 printEnd, protocol.ts:449-451, packet_generator.ts:238
        p(0xf3, [1]),
        # :70-75 heartbeat Advanced1 (enumerations.ts:77) not waited for, packet_generator.ts:105
        p(0xdc, [1])]


def test_d101_gets_the_packets_of_niimbluelibs_b1_task(fake, monkeypatch):
    # niimbluelib at 0c9d33d4, src/print_tasks/B1PrintTask.ts. One row of 2
    # dots, 319 black, 12 mm across.
    told(monkeypatch, 2560)
    monkeypatch.setitem(REPLIES, 0x03, (0x04, b'\x01'))
    rows = [b'\x01' + bytes(94) + b'\x01'] + [b'\x01' * 96] * 319
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/D101', label_size='niimbot12x40')
    assert niimbot.DRIVER.send(queue, [rows]) == ''
    p = niimbluelib_packet
    # 12 bytes fit into thirds of 8 of printheadPixels 192 (utils.ts:98-99)
    assert fake.packets[3:] == [
        # B1PrintTask.ts:11 setDensity, densityDefault printer_models.ts:493
        p(0x21, [2]),
        # :12 setLabelType
        p(0x23, [1]),
        # :13 printStart7b, packet_generator.ts:229-230
        p(0x01, [0, 1, 0, 0, 0, 0, 0]),
        # :22 pageStart, packet_generator.ts:242
        p(0x03, [1]),
        # :23 setPageSize6b, packet_generator.ts:138-143: 320 rows, 96 columns, 1 copy
        p(0x13, [0x01, 0x40, 0, 96, 0, 1]),
        # :24 writeImageData as above: one dot in the first third, one in the second
        p(0x83, [0, 0, 1, 1, 0, 1, 0, 0, 0, 95]),
        p(0x85, [0, 1, 64, 32, 0, 199] + [0xff] * 12),
        p(0x85, [0, 200, 64, 32, 0, 120] + [0xff] * 12),
        # :25 pageEnd
        p(0xe3, [1]),
        # :32-37 waitForFinished, protocol.ts:154-159
        p(0xa3, [1]),
        # AbstractPrintTask.ts:158 printEnd, protocol.ts:449-451
        p(0xf3, [1])]


def test_d110_m_and_b1_keep_their_packets(fake, monkeypatch):
    # The D110_M with the sequence of hass-niimbot, its rows counted for 120
    # dots, without a heartbeat after the end
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/D110_M', label_size='niimbot12x40')
    assert niimbot.DRIVER.send(queue, [[b'\x01' * 96] * 8]) == ''
    packets = {packet[2]: data_of(packet) for packet in fake.packets}
    assert [c for c in fake.commands if c not in (0x84, 0x85)][3:] == [0x21, 0x23, 0x01, 0xdc, 0x13, 0xe3, 0xa3, 0xf3]
    assert (packets[0x01], packets[0x13]) == (bytes([0, 1] + [0] * 7), struct.pack('>HHHHB', 8, 96, 1, 0, 0))
    assert packets[0x85][:6] == struct.pack('>H3BB', 0, 40, 40, 16, 8)


def test_rows_fit_into_three_thirds_or_are_counted_together():
    # As niimbluelib counts them: thirds of 5 bytes of 142 dots hold 15
    third = [1] * 40 + [0] * 80
    assert data_of(protocol.row_packets([third], 142)[0])[:6] == struct.pack('>H3BB', 0, 40, 0, 0, 1)
    whole = [1] * 144
    assert data_of(protocol.row_packets([whole], 142)[0])[:6] == bytes([0, 0, 0]) + struct.pack('<H', 144) + bytes([1])


class _Black:
    label_content = None

    def __init__(self, w, h):
        self.size = (w, h)

    def generate(self):
        from PIL import Image
        return Image.new('RGB', self.size, 'black')


def test_label_rows_are_as_wide_as_the_label():
    # In whole bytes, as niimbluelib sends them
    _, rows = niimbot.label_rows(_Black(142, 472), 'niimbot12x40-d11h', model='Niimbot/D11_H')
    assert (len(rows), rows[0]) == (472, b'\x01' * 142 + bytes(2))
    # On a B21_C2B a B1 roll, without the shift of the B1
    _, rows = niimbot.label_rows(_Black(320, 240), 'niimbot40x30', (1.0, 1.0), 'Niimbot/B21_C2B')
    assert (len(rows), rows[0]) == (240, b'\x01' * 320)


@pytest.mark.parametrize('model, le', [('Niimbot/B21_PRO', True), ('Niimbot/D11_H', True), ('Niimbot/D110_M', False)])
def test_models_on_bluetooth_le(model, le):
    assert niimbot.DRIVER.followed(DEVICE, model) is le


@pytest.mark.parametrize('name, model', [('B21_PRO-L123456789', 'Niimbot/B21_PRO'), ('D11_H-H123456789', 'Niimbot/D11_H'),
                                         ('M2-H-A123', 'Niimbot/M2_H'), ('D101-G123', 'Niimbot/D101'),
                                         ('B1-G123', 'Niimbot/B1'), ('D110-G123', None), ('B21S-C123', None)])
def test_name_on_bluetooth_le_is_a_first_guess(name, model):
    found = niimbot.DRIVER.found_on_bluetooth({'address': '00:00:5E:00:53:21', 'name': name, 'le': True,
                                               'services': [protocol.NIIMBOT_SERVICE]})
    assert found == ((DEVICE, model) if model else None)


B1_PRO_HEARTBEAT = (0xdd, bytes.fromhex('1ff7006100610000' '4f00040001'))


def found(name):
    """The printer at DEVICE as the search on Bluetooth LE found it."""
    return niimbot.DRIVER.found_on_bluetooth({'address': '00:00:5E:00:53:21', 'name': name, 'le': True,
                                              'services': [protocol.NIIMBOT_SERVICE]})


def test_b1_found_by_its_name_is_asked_as_before(fake, monkeypatch):
    # Its first status sends what it sent on 7f15c4c4: heartbeat, roll and
    # serial number, no model ID
    told(monkeypatch, 4096)
    monkeypatch.setitem(REPLIES, 0xdc, B1_PRO_HEARTBEAT)
    monkeypatch.setitem(REPLIES, (0x40, 0x0b), (0x4b, b'TEST000001'))
    assert found('B1-TEST0001') == (DEVICE, 'Niimbot/B1')
    assert niimbot.DRIVER.status(DEVICE, 'Niimbot/B1')['model'] == 'Niimbot/B1'
    p = niimbluelib_packet
    assert fake.packets == [p(0xdc, [1]), p(0x1a, [1]), p(0x40, [0x0b])]


@pytest.mark.parametrize('name, guess', [('', 'Niimbot/B1'), ('B1-PRO-L123', 'Niimbot/B1'),
                                         ('B21_PRO-L123', 'Niimbot/B21_PRO')])
def test_printer_whose_name_is_a_guess_is_the_model_it_tells(fake, monkeypatch, name, guess):
    # Without a name, one like a B1's and a B1 Pro's, or one of another
    # model than the B1: asked once
    told(monkeypatch, 4097)
    monkeypatch.setitem(REPLIES, 0xdc, B1_PRO_HEARTBEAT)
    assert found(name) == (DEVICE, guess)
    for _ in range(2):
        assert niimbot.DRIVER.status(DEVICE, guess, fresh=True)['model'] == 'Niimbot/B1_PRO'
    assert fake.commands.count(0x40) == 1 and fake.commands.count(0xdc) == 2


def test_b21_pro_found_by_its_name_is_asked_once(fake, monkeypatch):
    told(monkeypatch, 785)
    monkeypatch.setitem(REPLIES, 0xdc, B1_PRO_HEARTBEAT)
    assert found('B21_PRO-L123') == (DEVICE, 'Niimbot/B21_PRO')
    for _ in range(2):
        assert niimbot.DRIVER.status(DEVICE, 'Niimbot/B21_PRO', fresh=True)['model'] == 'Niimbot/B21_PRO'
    p = niimbluelib_packet
    assert fake.packets == [p(0x40, [0x08]), p(0xdc, [1]), p(0x1a, [1]), p(0xdc, [1]), p(0x1a, [1])]


def test_printer_found_by_niimbots_service_alone_is_the_model_found_while_it_tells_none(fake, monkeypatch):
    # A B1 that leaves its model ID unanswered, asked for it again
    # STATUS_LOST seconds later
    assert found('') == (DEVICE, 'Niimbot/B1')
    monkeypatch.delitem(REPLIES, 0x40)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    monkeypatch.setitem(REPLIES, 0xdc, B1_PRO_HEARTBEAT)
    for _ in range(2):
        state = niimbot.DRIVER.status(DEVICE, 'Niimbot/B1', fresh=True)
        assert (state['model'], state['errors']) == ('Niimbot/B1', [])
    model_id = niimbluelib_packet(0x40, [0x08])
    assert fake.packets.count(model_id) == 1 and fake.commands.count(0xdc) == 2
    told(monkeypatch, 4097)
    monkeypatch.setattr(niimbot, 'STATUS_LOST', 0)
    assert niimbot.DRIVER.status(DEVICE, 'Niimbot/B1', fresh=True)['model'] == 'Niimbot/B1_PRO'
    assert fake.packets.count(model_id) == 2


def test_printer_on_bluetooth_le_of_a_model_labelburg_cannot_print_on(fake, monkeypatch):
    # A D110 without a name, asked no more
    told(monkeypatch, 2304)
    found('')
    for _ in range(2):
        state = niimbot.DRIVER.status(DEVICE, 'Niimbot/B1', fresh=True)
        assert state['errors'] == ['This printer is a Niimbot D110, Labelburg cannot print on it yet']
    assert fake.commands == [0x40]


@pytest.mark.parametrize('model', ['Niimbot/D110_M', 'Niimbot/B21_PRO'])
def test_printer_set_up_by_its_address_is_not_asked_for_its_model(fake, model):
    # A paired D110_M, a printer of PRINTERS
    niimbot.DRIVER.status(DEVICE, model)
    assert fake.commands == [0xdc, 0x1a]


def test_device_id_names_a_model_as_niimbluelib_writes_it():
    assert niimbot.DRIVER.model_in('MFG:NIIMBOT;CMD:ESC;MDL:B1 Pro;') == 'Niimbot/B1_PRO'
    assert niimbot.DRIVER.model_in('MFG:NIIMBOT;CMD:ESC;MDL:D11-H;') == 'Niimbot/D11_H'
    # Not one it prints on
    assert niimbot.DRIVER.model_in('MFG:NIIMBOT;CMD:ESC;MDL:B21S;') is None


@pytest.fixture
def serial_port(monkeypatch):
    """Devices on USB are serial ports a driver reaches."""
    ports = []
    monkeypatch.setattr(backends, 'is_serial_port', lambda device: ports.append(device) or True)
    return ports


def test_serial_port_on_usb_tells_its_model_id(fake, monkeypatch, serial_port):
    told(monkeypatch, 785)
    assert drivers.resolve('usb://0x3513:0x0003', 'Unknown') == (niimbot.DRIVER, 'Niimbot/B21_PRO')
    # Asked once
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0003', '') == 'Niimbot/B21_PRO'
    assert fake.commands.count(0x40) == 1


def test_serial_port_on_usb_of_a_model_labelburg_cannot_print_on_is_none(fake, monkeypatch, serial_port):
    told(monkeypatch, 768)
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0003', '') is None


def test_serial_port_on_usb_that_did_not_tell_its_model_is_asked_when_looking(fake, monkeypatch, serial_port):
    monkeypatch.delitem(REPLIES, 0x40)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.05)
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0003', '') is None
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0003', '') is None
    assert fake.commands.count(0x40) == 1
    told(monkeypatch, 785)
    monkeypatch.setattr(devices, 'looked', 1e12)
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0003', '') == 'Niimbot/B21_PRO'


@pytest.mark.parametrize('device, device_id', [
    # Another maker; a device ID that names no model it prints on
    ('usb://0x04f9:0x209b', ''), ('usb://0x3513:0x0002', 'MFG:NIIMBOT;CMD:ESC;MDL:K9;')])
def test_printers_on_usb_that_tell_a_device_id_or_are_of_another_maker_are_not_asked(fake, serial_port, device, device_id):
    assert niimbot.DRIVER.model_of(device, device_id) is None
    assert fake.commands == [] and serial_port == []


def test_printer_on_its_printer_interface_is_not_asked(fake, monkeypatch):
    # e.g. a B1 that usblp holds, which tells no device ID through PyUSB
    monkeypatch.setattr(backends, 'is_serial_port', lambda device: False)
    assert niimbot.DRIVER.model_of('usb://0x3513:0x0002', '') is None
    assert fake.commands == []


B21_PRO_REPLIES = {0x40: (0x48, struct.pack('>H', 785)), 0xdc: B1_PRO_HEARTBEAT, 0x03: (0x04, b'\x01'),
                   0x1a: (0x1b, bytes(8) + b'\x0810262260' + b'\x10PZ1G221322004205' + struct.pack('>HHB', 276, 153, 1))}


def test_b21_pro_prints_through_the_label_designer(tmp_path, fake, monkeypatch):
    without_status(monkeypatch)
    for command, reply in B21_PRO_REPLIES.items():
        monkeypatch.setitem(REPLIES, command, reply)
    client = make_client(tmp_path, model='Niimbot/B21_PRO', printer=DEVICE)
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    # Its roll of 50 x 30 mm, at 300 dpi
    assert (printer_['model'], printer_['errors'], printer_['label_size']) == ('Niimbot/B21_PRO', [], 'niimbot50x30-b21pro')
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot50x30-b21pro', 'print_type': 'text', 'text': TEXT})
    assert outcome(client, response) == ('printed', '')
    size = struct.unpack('>HH', data_of(next(p for p in fake.packets if p[2] == 0x13))[:4])
    assert size == (354, 576)


def test_new_model_without_a_tagged_roll_is_not_an_error(tmp_path, fake, monkeypatch):
    # It tells itself where it needs one
    for command, reply in B21_PRO_REPLIES.items():
        monkeypatch.setitem(REPLIES, command, reply)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, b'\x00'))
    client = make_client(tmp_path, model='Niimbot/B21_PRO', printer=DEVICE)
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []
