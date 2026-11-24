"""The bytes printers get for labels of fixed patterns, by their digests: how
labels are turned into dots may change, what each printer gets may not."""

import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner.drivers import bitmap, escpos, niimbot, puqu, zpl
from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.raster import convert
from app.labeldesigner.drivers.escpos import protocol as escpos_protocol
from app.labeldesigner.drivers.niimbot import calibration, protocol as niimbot_protocol
from app.labeldesigner.drivers.puqu import protocol as puqu_protocol
from app.labeldesigner.drivers.zpl import protocol as zpl_protocol
from app.labeldesigner.enums import LabelContent, LabelOrientation

# Red, dark red, black, grays, white, blue, orange, pink and pale red: on
# both sides of what prints black and what red
COLORS = ((255, 0, 0), (150, 20, 20), (60, 10, 10), (0, 0, 0), (128, 128, 128), (255, 255, 255),
          (0, 0, 255), (255, 128, 0), (255, 0, 128), (40, 40, 40), (200, 150, 150))


def _pattern(size, mode='L', seed=0):
    """Diagonal stripes of all gray levels, or of blocks of COLORS."""
    w, h = size
    if mode == 'L':
        row = bytes((x * 3 + seed) % 256 for x in range(w))
        shift = [(y * 5) % w for y in range(h)]
        return Image.frombytes('L', size, b''.join(row[k:] + row[:k] for k in shift))
    row = b''.join(bytes(COLORS[(x // 7 + seed) % len(COLORS)]) for x in range(w))
    shift = [((y // 5) * 7 % w) * 3 for y in range(h)]
    return Image.frombytes('RGB', size, b''.join(row[k:] + row[:k] for k in shift))


def _digest(data) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


class _Label:
    label_orientation = LabelOrientation.STANDARD

    def __init__(self, image, content=LabelContent.TEXT_ONLY):
        self.image, self.label_content = image, content

    def generate(self):
        return self.image.copy()


@pytest.mark.parametrize('model, label_size, options, images, digest', [
    # Several labels in one job, dithered
    ('QL-800', '62', dict(dither=True), [((696, 120), 'L', 0), ((696, 200), 'L', 1), ((696, 80), 'RGB', 2)], '619325ab3cfa7ab5'),
    ('QL-700', '62', dict(rotate=90, hq=False), [((150, 696), 'L', 3), ((90, 696), 'L', 4)], 'fdcc9fa8841d09b4'),
    # Resized to the width of the label
    ('QL-800', '29', dict(dither=True), [((400, 100), 'L', 5)], '8ee6c0b1370b1fcb'),
    # Die-cut labels, one drawn lying
    ('QL-800', '62x29', dict(dither=True), [((696, 271), 'L', 6), ((271, 696), 'L', 7)], '83bfaa11f6fa76f4'),
    ('QL-800', '62x29', dict(dpi_600=True), [((1392, 542), 'L', 8)], '1cb90c2535435658'),
    ('QL-810W', 'd24', dict(compress=True), [((236, 236), 'L', 9)], '9c23d407e76637e0'),
    ('QL-1100', '102', dict(dither=True, compress=True), [((1164, 150), 'L', 10)], '5dfe7ba8f82216d1'),
    ('QL-1100', '103x164', {}, [((1200, 1822), 'L', 11)], '5cb0391ad904e650'),
    ('QL-500', '17x54', {}, [((165, 566), 'L', 12)], '71d7574fc32f0514'),
    ('PT-E550W', 'pt12', dict(rotate=0, compress=True, cut_every=2, half_cut=True, chain=True),
     [((70, 90), 'L', 13), ((70, 60), 'L', 14)], 'd00cef4ce43e7a09'),
    ('PT-P900W', 'pt36', dict(rotate=0, compress=True, dither=True), [((454, 120), 'L', 15)], '04c446dd2f463b4d'),
    # Black and red
    ('QL-800', '62red', dict(red=True), [((696, 150), 'RGB', 16), ((696, 90), 'L', 17)], '46b9527780daca3a'),
    ('QL-800', '62red', dict(red=True, dpi_600=True), [((1392, 200), 'RGB', 18)], '8a7de3ff3ab7e4f4'),
    ('QL-810W', '62red', dict(red=True, compress=True, rotate=90), [((120, 696), 'RGB', 19)], 'c4c803ac5827bf38'),
])
def test_brother_raster(model, label_size, options, images, digest):
    raster = Raster(model)
    convert(raster, [_pattern(*image) for image in images], label_size, **options)
    assert _digest(raster.data) == digest


def test_brother_raster_prints_red():
    # The colors of the pattern print black and red, each line of the
    # QL-800 once in black and once in red
    raster = Raster('QL-800')
    convert(raster, [_pattern((696, 150), 'RGB', 16)], '62red', red=True)
    data = bytes(raster.data)
    start = data.index(b'\x77\x01')
    lines = [data[i:i + 93] for i in range(start, start + 150 * 2 * 93, 93)]
    assert {line[:3] for line in lines} == {b'\x77\x01\x5a', b'\x77\x02\x5a'}
    assert any(any(line[3:]) for line in lines[0::2]) and any(any(line[3:]) for line in lines[1::2])


def test_escpos_job():
    labels = []
    for i, (size, image, content) in enumerate((('escpos58', ((384, 200), 'L', 1), LabelContent.TEXT_ONLY),
                                                ('escpos80-180', ((512, 300), 'RGB', 2), LabelContent.IMAGE_BW),
                                                ('escpos58', ((150, 384), 'L', 3), LabelContent.IMAGE_GRAYSCALE))):
        _, rows, width = escpos.label_rows(_Label(_pattern(*image), content), size)
        labels.append((i % 2 == 0, rows, width, 203))
    assert _digest(escpos_protocol.job(labels)) == 'fc1203713a471d1e'


def test_zpl_labels():
    job = []
    for size, image, content in (('zpl57x32', ((456, 256), 'L', 4), LabelContent.TEXT_ONLY),
                                 ('zpl102x152', ((1216, 816), 'RGB', 5), LabelContent.IMAGE_BW)):
        _, rows, width = zpl.label_rows(_Label(_pattern(*image), content), size)
        job.append(zpl_protocol.label(rows, width))
    assert _digest(b''.join(job)) == '2e83ab9a6fef0312'


def test_niimbot_rows():
    packets = []
    for size, image, offset, content in (('niimbot12x40', ((320, 96), 'L', 6), (0, 0), LabelContent.TEXT_ONLY),
                                         ('niimbot40x30', ((320, 240), 'L', 7), (0.5, -0.25), LabelContent.TEXT_ONLY),
                                         ('niimbot50x30', ((400, 240), 'RGB', 8), (5.0, 1.0), LabelContent.IMAGE_BW),
                                         ('niimbotd50', ((400, 400), 'L', 9), (-1.0, 0.5), LabelContent.TEXT_ONLY)):
        _, rows = niimbot.label_rows(_Label(_pattern(*image), content), size, offset)
        packets += niimbot_protocol.row_packets(rows, niimbot_protocol.HEAD if size == 'niimbot12x40' else niimbot_protocol.B1_HEAD)
    for size in ('niimbot40x30', 'niimbot50x30', 'niimbotd50'):
        for rows in niimbot.DRIVER.calibration_job(SimpleNamespace(label_size=size, offset=(-0.5, 0.25))):
            packets += niimbot_protocol.row_packets(rows, niimbot_protocol.B1_HEAD)
    assert _digest(b''.join(packets)) == 'f79422cb9c6d04e3'


def test_puqu_labels():
    data = []
    for size, image, content in (('puqu40x30', ((320, 240), 'L', 10), LabelContent.TEXT_ONLY),
                                 ('puqu50x80', ((640, 384), 'RGB', 11), LabelContent.IMAGE_BW)):
        _, rows = puqu.label_rows(_Label(_pattern(*image), content), size)
        data.append(puqu_protocol.label_data(rows))
    assert _digest(b''.join(data)) == 'fac8e676418e1d6a'


@pytest.mark.parametrize('kind, digest', [('narrow', '1e49b92f01615e1d'), ('wide', '006aebd5884c28d5'), ('round', '86dc33b41ed4155b')])
def test_calibration_example(kind, digest):
    assert _digest(Image.open(io.BytesIO(calibration.example(kind))).tobytes()) == digest


def test_dots_are_packed_msb_first():
    assert bitmap.pack([1, 0, 0, 0, 0, 0, 0, 1, 1]) == b'\x81\x80'
    # Any dot but 0 is printed
    assert bitmap.pack(bytes([0, 2, 0, 0, 0, 0, 0, 255])) == b'\x41'
    assert bitmap.pack(b'') == b''
