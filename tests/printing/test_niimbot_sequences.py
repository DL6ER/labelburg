"""The print tasks of niimbluelib besides B1 and D110M_V4: the packets of a
small label written by hand from niimbluelib's source at commit 35110a71
(https://github.com/MultiMote/niimbluelib), files under src/, how each tells
that the label was printed, and the task of each model."""
import socket
import time
from types import SimpleNamespace

import pytest

from app.labeldesigner import refusals
from app.labeldesigner.drivers import devices, niimbot
from app.labeldesigner.drivers.niimbot import models, protocol
from tests.printing.test_niimbot import B1_TAG

DEVICE = 'bt://00:00:5E:00:53:21'


def p(command, data):
    """A packet as packets/packet.ts:80-124 toBytes() makes it: 55 55,
    command, length, data, the XOR of command, length and data (:72-78),
    aa aa."""
    data = bytes(data)
    check = command ^ len(data)
    for byte in data:
        check ^= byte
    return bytes([0x55, 0x55, command, len(data), *data, check, 0xaa, 0xaa])


class Printer:
    """A Niimbot that answers a packet by its command, or by its command and
    first byte: with the packets of an answer, of a list one after the other
    and the last one again, nothing for None."""

    def __init__(self, answers):
        self.answers = {key: list(value) if isinstance(value, list) else [value] for key, value in answers.items()}
        self.sent, self.pending = [], b''

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def sendall(self, data):
        while data:
            # The packet to connect leads with 03
            lead = 1 if data[0] == 0x03 else 0
            size = lead + data[lead + 3] + 7
            packet, data = data[:size], data[size:]
            self.sent.append(packet)
            command = packet[lead + 2]
            key = (command, packet[lead + 4]) if (command, packet[lead + 4]) in self.answers else command
            answers = self.answers.get(key)
            if answers:
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                self.pending += answer or b''

    def recv(self, size):
        data, self.pending = self.pending, b''
        return data


def connection(printer):
    c = protocol.Connection.__new__(protocol.Connection)
    c.s, c.received = printer, b''
    return c


# The heartbeats of a D110 and a B21S with the lid closed, src/test/dumps.ts:113
# and :339, as test/virtual-client.test.ts:200 and :309 read them
D110_BEAT = p(0xdd, bytes.fromhex('0fd700640064324d0103'))
B21S_BEAT = p(0xdd, bytes.fromhex('1f340095009500005400030001'))
# Packets taken, a roll without an RFID tag
TAKEN = {0x21: p(0x31, [1]), 0x23: p(0x33, [1]), 0x01: p(0x02, [1]), 0x20: p(0x30, [1]), 0x03: p(0x04, [1]),
         0x13: p(0x14, [1]), 0x15: p(0x16, [1]), 0x86: p(0xd3, [1]), 0xe3: p(0xe4, [1]),
         0xa3: p(0xb3, [0, 1, 100, 100]), 0xf3: p(0xf4, [1]), 0x1a: p(0x1b, [0])}


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
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.001)


def printed(monkeypatch, model, rows, answers):
    """What the driver tells after printing rows on a printer of the model
    that answers so, and the packets it sent."""
    known = models.BY_NAME[f'Niimbot/{model}']
    beat = D110_BEAT if known.dots == 96 else B21S_BEAT
    printer = Printer({0x40: p(0x48, known.ids[0].to_bytes(2)), 0xdc: beat, **TAKEN, **answers})
    monkeypatch.setattr(protocol, 'connect', lambda device, le=False: connection(printer))
    queue = SimpleNamespace(device_specifier=DEVICE, model=known.name, label_size='niimbot12x40')
    return niimbot.DRIVER.send(queue, [rows]), printer.sent


# Labelburg asks every printer for its model ID, heartbeat and roll first
ASKED = [p(0x40, [8]), p(0xdc, [1]), p(0x1a, [1])]
# The packets of protocol.ts:309-312 sendConnect() in :319-337
# connectNegotiate(), leading with 03 (packet.ts:116-121, dumps.ts:5)
CONNECT = b'\x03' + p(0xc1, [1])

# 12 x 40 mm on 96 dots: a row of 2 dots, 319 black
ROWS_96 = [bytes([1] + [0] * 94 + [1])] + [b'\x01' * 96] * 319
# As packet_generator.ts:437-477 writeImageDataSingleColor() sends them:
# rows of up to 6 dots by their places (:442-451, :276-291,
# image_encoder.ts:247-261), others as bitmap (:453-461, :254-263), equal
# rows counted up to every 200th (image_encoder.ts:202-236). Their dots
# counted in each third of 4 bytes of printheadPixels 96 (utils.ts:98-153).
IMAGE_96 = [p(0x83, [0, 0, 1, 0, 1, 1, 0, 0, 0, 95]),
            p(0x85, [0, 1, 32, 32, 32, 199] + [0xff] * 12),
            p(0x85, [0, 200, 32, 32, 32, 120] + [0xff] * 12)]

# 40 x 30 mm on 384 dots: 320 dots across, 2 rows empty, one of 3 dots, 237
# black
_THREE = bytearray(320)
for _x in (10, 300, 319):
    _THREE[_x] = 1
ROWS_320 = [bytes(320)] * 2 + [bytes(_THREE)] + [b'\x01' * 320] * 237
# Empty rows (packet_generator.ts:471-473, :250-252), the others as above.
# Their dots counted in thirds of 16 bytes of printheadPixels 384.
IMAGE_320 = [p(0x84, [0, 0, 2]),
             p(0x83, [0, 2, 1, 0, 2, 1, 0, 10, 1, 44, 1, 63]),
             p(0x85, [0, 3, 128, 128, 64, 197] + [0xff] * 40),
             p(0x85, [0, 200, 128, 128, 64, 40] + [0xff] * 40)]
# With countsMode "total" all dots counted, little-endian after a zero
# (utils.ts:108-109, :151-152), with enableCheckLine checkLine() after row
# 199 (packet_generator.ts:466-469, :328-330)
IMAGE_320_B21 = [p(0x84, [0, 0, 2]),
                 p(0x83, [0, 2, 0, 3, 0, 1, 0, 10, 1, 44, 1, 63]),
                 p(0x85, [0, 3, 0, 0x40, 0x01, 197] + [0xff] * 40),
                 p(0x86, [0, 199, 1]),
                 p(0x85, [0, 200, 0, 0x40, 0x01, 40] + [0xff] * 40)]


def test_b21_gets_the_packets_of_niimbluelibs_b21_v1_task(monkeypatch):
    # src/print_tasks/B21V1PrintTask.ts, the packets of
    # src/packets/packet_generator.ts, data [1] where mapped() (:41) sends it
    message, sent = printed(monkeypatch, 'B21', ROWS_320, {})
    assert message == ''
    assert sent == ASKED + [
        # :11 setDensity (:109-111), densityDefault printer_models.ts:292
        p(0x21, [3]),
        # :12 setLabelType (:113-115), WithGaps (enumerations.ts:65) of a roll without a tag
        p(0x23, [1]),
        # :13 printStart1b (:213-215)
        p(0x01, [1]),
        # :24 pageStart (:242-244)
        p(0x03, [1]),
        # :25 setPageSize4b (:129-131): 240 rows, 320 columns
        p(0x13, [0, 240, 1, 64]),
        # :26-30 writeImageData, countsMode total, enableCheckLine
        *IMAGE_320_B21,
        # :31 pageEnd (:246-248)
        p(0xe3, [1]),
        # :39-45 waitUntilPrintFinishedByPrintEndPoll (protocol.ts:189-210):
        # printEnd (:449-453, packet_generator.ts:238-240) taken, none after
        # it (protocol.ts:184)
        p(0xf3, [1])]


def test_b21_asks_again_to_end_a_print_it_refused_to_end(monkeypatch):
    # niimbluelib's printEnd() stops the poll of protocol.ts:193-208 at its
    # first packet (:450) and would wait on
    message, sent = printed(monkeypatch, 'B21', ROWS_320, {0xf3: [p(0xf4, [0]), p(0xf4, [1])]})
    assert message == ''
    assert sent[-3:] == [p(0xe3, [1]), p(0xf3, [1]), p(0xf3, [1])]


def test_b21_l2b_gets_the_packets_of_niimbluelibs_b21_l2b_task(monkeypatch):
    # src/print_tasks/B21L2BPrintTask.ts. The start and the end of the page
    # are refused at first.
    message, sent = printed(monkeypatch, 'B21_L2B', ROWS_320, {
        0x03: [p(0x04, [0]), p(0x04, [1])], 0xe3: [p(0xe4, [0]), p(0xe4, [0]), p(0xe4, [1])]})
    assert message == ''
    assert sent == ASKED + [
        # :11-13 setDensity (printer_models.ts:325), setLabelType, printStart1b
        p(0x21, [3]), p(0x23, [1]), p(0x01, [1]),
        # :21 Utils.doUntilTrue (utils.ts:290-307) of pageStart (protocol.ts:437-440)
        p(0x03, [1]), p(0x03, [1]),
        # :25 setPageSize4b
        p(0x13, [0, 240, 1, 64]),
        # :26-30 as the B21's
        *IMAGE_320_B21,
        # :31 pageEnd, its answer not judged
        p(0xe3, [1]),
        # :39-42 waitForPageFinished, doUntilTrue of pageEnd (protocol.ts:443-446)
        p(0xe3, [1]), p(0xe3, [1]),
        # :44-46 waitForFinished sends nothing, AbstractPrintTask.ts:157-161 printEnd
        p(0xf3, [1])]


def test_d110_gets_the_packets_of_niimbluelibs_d110_task(monkeypatch):
    # src/print_tasks/D110PrintTask.ts, its first status tells no page
    message, sent = printed(monkeypatch, 'D110', ROWS_96, {0xa3: [p(0xb3, [0, 0, 0, 0]), p(0xb3, [0, 1, 100, 100])]})
    assert message == ''
    assert sent == ASKED + [
        # :11-13 setDensity (printer_models.ts:545), setLabelType, printStart1b
        p(0x21, [2]), p(0x23, [1]), p(0x01, [1]),
        # :22 printClear (packet_generator.ts:320-322), :23 pageStart
        p(0x20, [1]), p(0x03, [1]),
        # :24 setPageSize4b: 320 rows, 96 columns
        p(0x13, [1, 64, 0, 96]),
        # :25 setPrintQuantity (:195-197)
        p(0x15, [0, 1]),
        # :26 writeImageData
        *IMAGE_96,
        # :27 pageEnd
        p(0xe3, [1]),
        # :34-40 waitForPageFinished, waitUntilPrintFinishedByStatusPoll
        # (protocol.ts:154-177): printStatus (:199-201) until 1 page
        p(0xa3, [1]), p(0xa3, [1]),
        # :42-45 waitForFinished, the same again
        p(0xa3, [1]),
        # AbstractPrintTask.ts:157-161 printEnd
        p(0xf3, [1])]


def test_b21s_gets_the_packets_of_niimbluelibs_d110_task(monkeypatch):
    # print_tasks/index.ts:81, the packets of D110PrintTask.ts as the D110's,
    # its rows counted in thirds of 16 bytes
    message, sent = printed(monkeypatch, 'B21S', ROWS_320, {})
    assert message == ''
    assert sent == ASKED + [p(0x21, [3]), p(0x23, [1]), p(0x01, [1]), p(0x20, [1]), p(0x03, [1]),
                            p(0x13, [0, 240, 1, 64]), p(0x15, [0, 1]), *IMAGE_320, p(0xe3, [1]),
                            p(0xa3, [1]), p(0xa3, [1]), p(0xf3, [1])]


def test_d11_gets_the_packets_of_niimbluelibs_d11_v1_task(monkeypatch):
    # src/print_tasks/OldD11PrintTask.ts at protocol version 0, the page
    # index told along with the end of the page
    message, sent = printed(monkeypatch, 'D11', ROWS_96, {0xc1: p(0xc2, [1]), 0xe3: p(0xe4, [1]) + p(0xe0, [0, 1])})
    assert message == ''
    assert sent == [
        p(0x40, [8]),
        # print_tasks/index.ts:78 and :81: the version picks the task,
        # Connected is version 0 (protocol.ts:328-334, enumerations.ts:101)
        CONNECT,
        p(0xdc, [1]), p(0x1a, [1]),
        # :11-13 setDensity (printer_models.ts:512), setLabelType, printStart1b
        p(0x21, [2]), p(0x23, [1]), p(0x01, [1]),
        # :22 printClear, :23 pageStart
        p(0x20, [1]), p(0x03, [1]),
        # :24 setPageSize2b (packet_generator.ts:117-119): 320 rows
        p(0x13, [1, 64]),
        # :25 setPrintQuantity
        p(0x15, [0, 1]),
        # :26 writeImageData
        *IMAGE_96,
        # :27 pageEnd
        p(0xe3, [1]),
        # :34-39 waitUntilPrintFinishedByPageIndex (protocol.ts:112-144)
        # sends nothing, it waits for In_PrinterPageIndex 1 (commands.ts:138)
        p(0xf3, [1])]


def test_d11_of_protocol_version_1_gets_the_packets_of_niimbluelibs_d110_task(monkeypatch):
    # print_tasks/index.ts:81 { m: M.D11, v: 1 } comes before :78 (:94-100),
    # ConnectedNew is version 1 (protocol.ts:328-329). The packets of
    # D110PrintTask.ts as the D110's.
    message, sent = printed(monkeypatch, 'D11', ROWS_96, {0xc1: p(0xc2, [2])})
    assert message == ''
    assert sent == [p(0x40, [8]), CONNECT, p(0xdc, [1]), p(0x1a, [1]),
                    p(0x21, [2]), p(0x23, [1]), p(0x01, [1]), p(0x20, [1]), p(0x03, [1]),
                    p(0x13, [1, 64, 0, 96]), p(0x15, [0, 1]), *IMAGE_96, p(0xe3, [1]),
                    p(0xa3, [1]), p(0xa3, [1]), p(0xf3, [1])]


def test_h1s_gets_the_packets_of_niimbluelibs_h1s_task(monkeypatch):
    # src/print_tasks/H1SPrintTask.ts
    message, sent = printed(monkeypatch, 'H1S', ROWS_96, {})
    assert message == ''
    assert sent == ASKED + [
        # :11-12 setDensity (printer_models.ts:644), setLabelType
        p(0x21, [2]), p(0x23, [1]),
        # :13 printStart2b (packet_generator.ts:217-219): 1 page
        p(0x01, [0, 1]),
        # :22 pageStart
        p(0x03, [1]),
        # :23 setPageSize9b (:147-161): 320 rows, 96 columns, 1 copy, cut height 0, cut type 0
        p(0x13, [1, 64, 0, 96, 0, 1, 0, 0, 0]),
        # :24 writeImageData
        *IMAGE_96,
        # :25 pageEnd
        p(0xe3, [1]),
        # :32-38 waitUntilPrintFinishedByStatusPoll
        p(0xa3, [1]),
        # AbstractPrintTask.ts:157-161 printEnd
        p(0xf3, [1])]


# The status data of a D110_M, src/test/dumps.ts:199, version 4 as
# test/virtual-client.test.ts:226 reads it
VERSION_4 = p(0xb5, bytes.fromhex('3030232800c80000000a00030112ee00'))


def test_b18_of_protocol_version_4_gets_the_packets_of_niimbluelibs_d110m_v4_task(monkeypatch):
    # print_tasks/index.ts:102-104, no task of its own
    message, sent = printed(monkeypatch, 'B18', ROWS_96, {0xc1: p(0xc2, [3]), 0xa5: VERSION_4})
    assert message == ''
    assert sent == [
        p(0x40, [8]),
        # ConnectedV3 (enumerations.ts:103), its version by
        # getPrinterStatusData (protocol.ts:314-317, packet_parser.ts:141-162)
        CONNECT, p(0xa5, [1]),
        p(0xdc, [1]), p(0x1a, [1]),
        # D110MV4PrintTask.ts:20-21 setLabelType, setDensity (printer_models.ts:237)
        p(0x23, [1]), p(0x21, [2]),
        # :33 printStart9b at speed 1
        p(0x01, [0, 1, 0, 0, 0, 0, 0, 1, 0]),
        # :44-46 printStatus not waited for
        p(0xa3, [1]),
        # :50 setPageSize13b: 320 rows, 96 columns, 1 copy
        p(0x13, [1, 64, 0, 96, 0, 1, 0, 0, 0, 0, 0, 0, 0]),
        # :51 writeImageData, :52 pageEnd
        *IMAGE_96, p(0xe3, [1]),
        # :59-64 waitForFinished, :73 printEnd, :70-75 heartbeat
        p(0xa3, [1]), p(0xf3, [1]), p(0xdc, [1])]


@pytest.mark.parametrize('model, connected', [('B18', p(0xc2, [1])), ('B3S', p(0xc2, [3]))])
def test_model_without_a_task_before_protocol_version_4_is_not_printed_on(monkeypatch, model, connected):
    # Version 0 and that of a B1, 3 (dumps.ts:8, virtual-client.test.ts:50)
    message, sent = printed(monkeypatch, model, ROWS_96, {
        0xc1: connected, 0xa5: p(0xb5, bytes.fromhex('3030032000c80000000f010204019800'))})
    shown = models.BY_NAME[f'Niimbot/{model}'].shown
    assert message == f'This printer is a Niimbot {shown}, Labelburg cannot print on it yet'
    assert message in refusals.MESSAGES
    assert not {0x21, 0x23, 0x01} & {packet[2] for packet in sent if packet[0] == 0x55}


@pytest.mark.parametrize('connected, status, version', [
    # dumps.ts:93, a D110 (virtual-client.test.ts:193)
    (2, None, 1),
    # dumps.ts:323 a B21S, :8 a B1, :199 a D110_M, :37 a B21_PRO (virtual-client.test.ts:301, :50, :226, :86)
    (3, '3030032000c800000014000203', 0), (3, '3030032000c80000000f010204019800', 3),
    (3, '3030232800c80000000a00030112ee00', 4), (3, '303007d000640000000f01030203f500', 5),
    # Connected, not of version 3 (enumerations.ts:99-105)
    (1, None, 0)])
def test_protocol_version_is_asked_as_niimbluelib_asks_it(connected, status, version):
    # protocol.ts:319-337, packet_parser.ts:141-162
    printer = Printer({0xc1: p(0xc2, [connected]), 0xa5: p(0xb5, bytes.fromhex(status)) if status else None})
    assert protocol.protocol_version(connection(printer)) == version
    assert printer.sent == [CONNECT] + ([p(0xa5, [1])] if connected == 3 else [])


# The tasks of print_tasks/index.ts:77-85 by findPrintTask() (:91-107) at
# protocol versions 0 to 5
@pytest.mark.parametrize('model, tasks', [
    ('B21', ['b21_v1'] * 6), ('B21_L2B', ['b21_l2b'] * 6),  # :79, :80
    ('B21S', ['d110'] * 6), ('B21S_C2B', ['d110'] * 6), ('D110', ['d110'] * 6),  # :81
    # :78, at versions 1 and 2 :81 found first (:94-98)
    ('D11', ['d11_v1', 'd110', 'd110', 'd11_v1', 'd11_v1', 'd11_v1']),
    ('D11S', ['d11_v1'] * 6), ('H1S', ['h1s'] * 6),  # :78, :84
    ('B1', ['b1'] * 6), ('B21_C2B', ['b1'] * 6), ('M2_H', ['b1'] * 6), ('N1', ['b1'] * 6), ('D101', ['b1'] * 6),  # :82
    ('B1_PRO', ['d110m_v4'] * 6), ('B21_PRO', ['d110m_v4'] * 6), ('D11_H', ['d110m_v4'] * 6),  # :83
    # None, D110M_V4 from version 4 on (:102-104)
    ('B18', ['', '', '', '', 'd110m_v4', 'd110m_v4']), ('B3S', ['', '', '', '', 'd110m_v4', 'd110m_v4'])])
def test_task_of_each_model_is_niimbluelibs(model, tasks):
    known = models.BY_NAME[f'Niimbot/{model}']
    assert [models.sequence(known, version) for version in range(6)] == tasks
    # Only where it depends on the version the printer is asked
    assert known.asks_version is (len(set(tasks)) > 1)


def test_every_model_but_the_d110_m_prints_with_a_task_of_niimbluelib():
    # The D110_M keeps the sequence of hass-niimbot it printed with
    assert {model.sequence for model in models.MODELS} == {'v4', 'b1', 'd110m_v4', 'b21_v1', 'b21_l2b', 'd110',
                                                            'd11_v1', 'h1s', ''}
    assert [model.name for model in models.MODELS if model.sequence == 'v4'] == ['Niimbot/D110_M']


@pytest.mark.parametrize('model, answers', [
    # packet_parser.ts:128-131: an error in the print status of 10 bytes
    ('D110', {0xa3: p(0xb3, [0, 0, 0, 0, 0, 0, 1, 0, 0, 0])}),
    ('H1S', {0xa3: p(0xb3, [0, 0, 0, 0, 0, 0, 1, 0, 0, 0])}),
    # abstract_client.ts:177-180, an error answer
    ('D11', {0xc1: p(0xc2, [1]), 0x13: p(0xdb, [1])}),
    ('B21', {0xe3: p(0xdb, [1])})])
def test_error_the_printer_tells_ends_the_print(monkeypatch, model, answers):
    # As AbstractPrintTask.ts:67-88 ends every print task
    message, sent = printed(monkeypatch, model, ROWS_96 if model in ('D110', 'H1S', 'D11') else ROWS_320, answers)
    assert message == 'The cover of the printer is open'
    assert sent[-1] == p(0xf3, [1]) and sent.count(p(0xf3, [1])) == 1


@pytest.mark.parametrize('model, answers', [
    # The status never tells a page printed
    ('D110', {0xa3: p(0xb3, [0, 0, 0, 0])}),
    # No page index told
    ('D11S', {}),
    # printEnd not taken
    ('B21', {0xf3: p(0xf4, [0])}),
    # pageEnd not taken, 1 and 20 times (B21L2BPrintTask.ts:31, :40)
    ('B21_L2B', {0xe3: p(0xe4, [0])})])
def test_label_not_told_as_printed_is_an_error(monkeypatch, model, answers):
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0.05)
    message, sent = printed(monkeypatch, model, ROWS_96 if model in ('D110', 'D11S') else ROWS_320, answers)
    assert message == 'The printer did not tell that it printed the label'
    assert sent[-1] == p(0xf3, [1])
    if model == 'B21_L2B':
        assert sent.count(p(0xe3, [1])) == 21


class Counting(Printer):
    """A printer that tells page index 0 at each read once the page ended,
    forever."""

    def recv(self, size):
        if p(0xe3, [1]) not in self.sent:
            return super().recv(size)
        self.reads = getattr(self, 'reads', 0) + 1
        if self.reads > 1000:
            raise RuntimeError('still waiting')
        time.sleep(0.001)
        return super().recv(size) or p(0xe0, [0, 0])


def test_page_index_that_stays_0_is_not_waited_for_longer_than_a_label_takes(monkeypatch):
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0.05)
    printer = Counting({0x40: p(0x48, [2, 2]), 0xdc: D110_BEAT, **TAKEN})
    monkeypatch.setattr(protocol, 'connect', lambda device, le=False: connection(printer))
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/D11S', label_size='niimbot12x40')
    assert niimbot.DRIVER.send(queue, [ROWS_96]) == 'The printer did not tell that it printed the label'
    assert printer.sent[-1] == p(0xf3, [1])


def test_page_start_refused_five_times_is_an_error(monkeypatch):
    # B21L2BPrintTask.ts:21
    message, sent = printed(monkeypatch, 'B21_L2B', ROWS_320, {0x03: p(0x04, [0])})
    assert message == 'The printer refused the label, the log of the server names the reason'
    assert sent[len(ASKED) + 3:] == [p(0x03, [1])] * 5 + [p(0xf3, [1])]


def test_print_status_unanswered_is_asked_once_more(monkeypatch):
    # protocol.ts:73-90 sendRepeatUntilSuccess() of getPrintStatus(2)
    message, sent = printed(monkeypatch, 'H1S', ROWS_96, {0xa3: [None, p(0xb3, [0, 1, 100, 100])]})
    assert message == ''
    assert sent[-3:] == [p(0xa3, [1]), p(0xa3, [1]), p(0xf3, [1])]


class Silent(Printer):
    """A printer whose read of no answer waits as long as it may."""
    timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        if not self.pending:
            time.sleep(self.timeout)
            raise socket.timeout
        return super().recv(size)


def test_start_of_a_page_unanswered_is_waited_for_as_niimbluelib_waits(monkeypatch):
    # B21L2BPrintTask.ts:21 pageStart() (protocol.ts:437-440) within the
    # DEFAULT_PACKET_TIMEOUT of 1 s (protocol.ts:37), not ANSWER_WAIT
    assert protocol.PACKET_WAIT == 1
    monkeypatch.setattr(protocol, 'PACKET_WAIT', 0.05)
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 1)
    printer = Silent({0x40: p(0x48, [3, 1]), 0xdc: B21S_BEAT, **TAKEN, 0x03: None})
    monkeypatch.setattr(protocol, 'connect', lambda device, le=False: connection(printer))
    queue = SimpleNamespace(device_specifier=DEVICE, model='Niimbot/B21_L2B', label_size='niimbot40x30')
    started = time.monotonic()
    assert niimbot.DRIVER.send(queue, [ROWS_320]) == 'The printer does not answer'
    assert time.monotonic() - started < 1
    assert [packet[2] for packet in printer.sent].count(0x03) == 5


@pytest.mark.parametrize('model, answers, sent_once', [
    # A refusal of the label type after the density taken, then the answer
    # to the label type sent
    ('B1', {0x21: p(0x31, [1]) + p(0x33, [0]), 0x23: p(0x33, [1]), 0x1a: p(0x1b, B1_TAG)}, 0x23),
    # A refused start of the page after the start of the print taken
    ('B21_L2B', {0x01: p(0x02, [1]) + p(0x04, [0])}, 0x03)])
def test_packets_after_an_answer_answer_no_packet_sent_later(monkeypatch, model, answers, sent_once):
    message, sent = printed(monkeypatch, model, ROWS_320, answers)
    assert message == ''
    assert [packet[2] for packet in sent].count(sent_once) == 1


def test_roll_query_not_supported_is_no_roll(monkeypatch):
    # abstract_client.ts:181-182 In_NotSupported, the labels then with gaps
    message, sent = printed(monkeypatch, 'D110', ROWS_96, {0x1a: p(0x00, [1])})
    assert message == '' and p(0x23, [1]) in sent


@pytest.mark.parametrize('model, beat, cover_open', [
    # 10 bytes, closed as dumps.ts:113
    ('D110', '0fd700640064324d0103', False), ('D110', '0fd700640064324d0003', True),
    # 13 bytes, closed as dumps.ts:339
    ('B21S', '1f340095009500005400030001', False), ('B21S', '1f340095009500005401030001', True),
    # 19 bytes, the lid at 15 (packet_parser.ts:253-258)
    ('B21', '000000000000000000000000000000' + '01030001', True),
    # The models of packet_parser.ts:268 tell 1 with the lid closed (:270-272)
    ('D101', '1f340095009500005401030001', False), ('D101', '1f340095009500005400030001', True),
    ('D11', '000000000000000000000000000000' + '01030001', False)])
def test_lid_is_read_as_niimbluelib_reads_it(monkeypatch, model, beat, cover_open):
    printer = Printer({0xdc: p(0xdd, bytes.fromhex(beat)), 0x1a: p(0x1b, [0])})
    monkeypatch.setattr(protocol, 'connect', lambda device, le=False: connection(printer))
    errors = niimbot.DRIVER.status(DEVICE, f'Niimbot/{model}')['errors']
    assert errors == (['The cover of the printer is open'] if cover_open else [])
