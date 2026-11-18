"""The calibration labels of the Niimbot B1, 40 mm (narrow) and 50 mm (wide)
rolls on its 384 dots wide printhead, 8 dots per mm."""

import pytest

from app.labeldesigner.drivers.niimbot import calibration


def test_pages_span_the_printhead():
    assert calibration.narrow(40, 30).size == calibration.wide(50, 30).size == (384, 240)


def test_triangles_cross_the_expected_edges_at_zero():
    img = calibration.narrow(40, 30)
    # Between two ticks, which are white across the triangles
    y = calibration.row_of(0.125, 30)
    # The label from x 32 to 351
    assert img.getpixel((31, y)) < 128 and img.getpixel((34, y)) == 255
    assert img.getpixel((352, y)) < 128 and img.getpixel((349, y)) == 255


def test_long_labels_read_3_mm_each_way_short_ones_2():
    assert (calibration.row_of(-3, 30), calibration.row_of(3, 30)) == (16, 223)
    assert (calibration.row_of(-2, 20), calibration.row_of(2, 20)) == (16, 143)


def test_rounded_ends_are_kept_clear():
    img = calibration.narrow(40, 30)
    for y in list(range(16)) + list(range(224, 240)):
        assert all(img.getpixel((x, y)) == 255 for x in list(range(61)) + list(range(323, 384)))


def test_top_and_bottom_lines_are_1_mm_inside():
    img = calibration.narrow(40, 30)
    assert img.getpixel((150, 8)) < 128 and img.getpixel((150, 231)) < 128
    assert img.getpixel((150, 4)) == img.getpixel((150, 236)) == 255


def test_wide_frame_is_1_5_mm_inside_the_printhead():
    # Moved 1 mm by a calibration and 0.25 mm by the B1 it stays on the paper
    img = calibration.wide(50, 30)
    assert img.getpixel((12, 120)) < 128 and img.getpixel((371, 120)) < 128
    assert all(img.getpixel((x, 120)) == 255 for x in range(12))
    assert img.getpixel((150, 8)) < 128 and img.getpixel((150, 231)) < 128


@pytest.mark.parametrize('width, narrow', [(40, True), (42, True), (43, False), (50, False)])
def test_narrow_rolls(width, narrow):
    assert calibration.is_narrow(width) is narrow


def test_example_images_are_pngs():
    assert calibration.example('narrow').startswith(b'\x89PNG')
    assert calibration.example('wide').startswith(b'\x89PNG')
    with pytest.raises(ValueError):
        calibration.example('other')


def test_example_images_are_made_once():
    assert calibration.example('wide') is calibration.example('wide')
