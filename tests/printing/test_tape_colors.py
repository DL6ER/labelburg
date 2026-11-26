"""The colors of TZe tapes and of their print as Brother printers report
them, and previews shown in them."""

import re

import pytest
from PIL import Image

from app.labeldesigner import tape
from app.labeldesigner.drivers.base import network_status
from app.labeldesigner.drivers.brother import status
from app.labeldesigner.drivers.brother.status import INK_SHOWN, TAPE_SHOWN, parse_status

BLACK, WHITE, RED, GRAY = (0, 0, 0), (255, 255, 255), (255, 0, 0), (128, 128, 128)

# The names the status gave for the colors before, which it keeps giving
OLD_TAPE_NAMES = {
    0x01: 'White', 0x02: 'Other', 0x03: 'Clear', 0x04: 'Red', 0x05: 'Blue', 0x06: 'Yellow', 0x07: 'Green',
    0x08: 'Black', 0x09: 'Clear(White text)', 0x20: 'Matte White', 0x21: 'Matte Clear', 0x22: 'Matte Silver',
    0x23: 'Satin Gold', 0x24: 'Satin Silver', 0x30: 'Blue(D)', 0x31: 'Red(D)', 0x40: 'Fluorescent Orange',
    0x41: 'Fluorescent Yellow', 0x50: 'Berry Pink(S)', 0x51: 'Light Gray(S)', 0x52: 'Lime Green(S)',
    0x60: 'Yellow(F)', 0x61: 'Pink(F)', 0x62: 'Blue(F)', 0x70: 'White(Heat-shrink Tube)', 0x90: 'White(Flex. ID)',
    0x91: 'Yellow(Flex. ID)', 0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}
OLD_TEXT_NAMES = {
    0x01: 'White', 0x02: 'Other', 0x04: 'Red', 0x05: 'Blue', 0x08: 'Black', 0x0A: 'Gold', 0x62: 'Blue(F)',
    0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}


def ptouch_status(tape_code: int, ink_code: int, width: int = 24, media: int = 0x01, model: int = 0x68) -> bytes:
    """The status of a P-touch printer, a PT-P750W by default, with TZe tape
    of the width, its color (byte 24) and the color of its print (byte 25)."""
    packet = bytearray(32)
    packet[0:5] = b'\x80\x20\x42\x30' + bytes([model])
    packet[10], packet[11], packet[24], packet[25] = width, media, tape_code, ink_code
    return bytes(packet)


def colors(tape_name: str, tape_hex: str, ink_name: str, ink_hex: str) -> dict:
    return {'tape': {'name': tape_name, 'hex': tape_hex}, 'ink': {'name': ink_name, 'hex': ink_hex}}


def test_the_codes_are_those_of_brothers_reference():
    # The tables (8) and (9) of the raster command references of the
    # PT-E550W/P750W/P710BT and of the PT-P900 series, without "Other",
    # "Cleaning", "Stencil" and "Incompatible"
    assert sorted(TAPE_SHOWN) == [0x01, *range(0x03, 0x0A), *range(0x20, 0x25), 0x30, 0x31, 0x40, 0x41,
                                  0x50, 0x51, 0x52, 0x60, 0x61, 0x62, 0x70, 0x90, 0x91]
    assert sorted(INK_SHOWN) == [0x01, 0x04, 0x05, 0x08, 0x0A, 0x62]
    assert set(OLD_TAPE_NAMES) - set(TAPE_SHOWN) == set(OLD_TEXT_NAMES) - set(INK_SHOWN) == {0x02, 0xF0, 0xF1, 0xFF}
    for name, shown in [*TAPE_SHOWN.values(), *INK_SHOWN.values()]:
        # Plain words, no codes or brackets
        assert re.fullmatch(r'[a-z]+( [a-z]+)*', name)
        assert re.fullmatch(r'#[0-9a-f]{6}', shown) or shown == status.CLEAR


@pytest.mark.parametrize('code, name', [
    (0x01, 'white'), (0x03, 'clear'), (0x06, 'yellow'), (0x08, 'black'), (0x09, 'clear'), (0x21, 'matte clear'),
    (0x23, 'satin gold'), (0x30, 'blue'), (0x40, 'fluorescent orange'), (0x51, 'light gray'), (0x70, 'white'),
    (0x91, 'yellow'),
])
def test_tape_colors_by_name(code, name):
    assert TAPE_SHOWN[code][0] == name


def test_clear_tapes_are_see_through():
    assert [name for name, shown in TAPE_SHOWN.values() if shown == status.CLEAR] == ['clear', 'clear', 'matte clear']
    assert status.CLEAR == '#ffffff00'


@pytest.mark.parametrize('tape_code, ink_code, shown', [
    (0x06, 0x08, colors('yellow', '#ffd400', 'black', '#000000')),
    (0x08, 0x01, colors('black', '#000000', 'white', '#ffffff')),
    (0x08, 0x0A, colors('black', '#000000', 'gold', '#d4af37')),
    (0x03, 0x08, colors('clear', '#ffffff00', 'black', '#000000')),
])
def test_status_of_a_ptouch_printer_tells_the_colors(tape_code, ink_code, shown):
    state = parse_status(ptouch_status(tape_code, ink_code))
    assert (state['model'], state['media_width'], state['media_category']) == ('PT-P750W', 24, 'TZe')
    assert state['colors'] == shown


@pytest.mark.parametrize('tape_code, ink_code', [
    # Other, cleaning, stencil, incompatible
    (0x02, 0x08), (0x06, 0x02), (0xF0, 0xF0), (0xF1, 0xF1), (0xFF, 0xFF),
    # Codes the reference does not list are no reason to refuse the status
    (0x0B, 0x08), (0x06, 0x03), (0x00, 0x00),
])
def test_no_colors_where_the_status_names_none(tape_code, ink_code):
    state = parse_status(ptouch_status(tape_code, ink_code))
    assert state['colors'] == {}
    assert state['media_width'] == 24


def test_the_names_of_the_colors_stay_as_they_were():
    for code, name in OLD_TAPE_NAMES.items():
        state = parse_status(ptouch_status(code, 0x08))
        assert (state['tape_color'], state['text_color']) == (name, 'Black')
    for code, name in OLD_TEXT_NAMES.items():
        state = parse_status(ptouch_status(0x01, code))
        assert (state['tape_color'], state['text_color']) == ('White', name)
    # A code not listed is no name, where it made the status unreadable before
    state = parse_status(ptouch_status(0x0B, 0x03))
    assert (state['tape_color'], state['text_color'], state['media_width']) == ('', '', 24)


def test_no_colors_of_rolls_of_ql_printers():
    # Byte 25 tells a black and red roll there
    state = parse_status(bytes.fromhex('802042343830000000003e0a0000230000000000000000000681000000000000'))
    assert (state['tape_color'], state['text_color'], state['colors'], state['red_roll']) == ('', '', {}, True)


def test_printers_that_tell_nothing_tell_no_colors():
    state = network_status('tcp://192.0.2.1', 'QL-1100')
    assert (state['tape_color'], state['text_color'], state['colors']) == ('', '', {})
    # Each status has its own
    assert state['colors'] is not network_status('tcp://192.0.2.1', 'QL-1100')['colors']


def label(*colors) -> Image.Image:
    im = Image.new('RGB', (len(colors), 1))
    im.putdata(list(colors))
    return im


def pixels(im: Image.Image) -> list:
    return [im.getpixel((x, 0)) for x in range(im.width)]


def test_white_on_black_tape():
    shown = tape.in_colors(label(WHITE, BLACK, GRAY), '#000000', '#ffffff')
    assert shown.mode == 'RGB'
    assert pixels(shown) == [BLACK, WHITE, (127, 127, 127)]


def test_black_on_yellow_tape_red_stays_red():
    shown = tape.in_colors(label(WHITE, BLACK, RED, GRAY, (255, 128, 128)), '#ffd400', '#000000')
    # Gray is between the ink and the tape, light red between red and the tape
    assert pixels(shown) == [(255, 212, 0), BLACK, RED, (128, 106, 0), (255, 106, 0)]


def test_clear_tape_is_see_through():
    shown = tape.in_colors(label(WHITE, BLACK, RED, GRAY), '#ffffff00', '#000000')
    assert shown.mode == 'RGBA'
    assert pixels(shown)[1:] == [(0, 0, 0, 255), (255, 0, 0, 255), (0, 0, 0, 127)]
    assert pixels(shown)[0][3] == 0
