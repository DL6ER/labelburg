"""Material Design Icons (pictogrammers.com) for {{mdi:<name>}} in label
texts, the symbols of fasteners and electronic components for
{{fastener:<name>}} and {{electronic:<name>}}. The fonts, their licenses and
the names are in app/static/mdi and app/static/symbols, see
tools/update_fonts.py and tools/symbols_font.py."""
import functools
import json
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')
MDI_DIR = os.path.join(STATIC_DIR, 'mdi')
FONT_PATH = os.path.join(MDI_DIR, 'materialdesignicons-webfont.ttf')
SYMBOLS_DIR = os.path.join(STATIC_DIR, 'symbols')
SYMBOLS_FONT_PATH = os.path.join(SYMBOLS_DIR, 'symbols.ttf')


@functools.cache
def _icons() -> dict:
    with open(os.path.join(MDI_DIR, 'icons.json'), encoding='utf-8') as f:
        return json.load(f)['icons']


@functools.cache
def _symbols() -> dict:
    with open(os.path.join(SYMBOLS_DIR, 'symbols.json'), encoding='utf-8') as f:
        return json.load(f)


def icon(name: str, symbol_set: str = 'mdi') -> str | None:
    """The character of the icon or symbol, None for an unknown name."""
    code = (_icons() if symbol_set == 'mdi' else _symbols().get(symbol_set, {})).get(name)
    return chr(int(code, 16)) if code else None


def font_of(char: str) -> str | None:
    """The font a character is drawn with, None for the text font: the
    icons are in Supplementary Private Use Area-A, the symbols in B, which
    no text font uses."""
    if 0xF0000 <= ord(char) <= 0xFFFFD:
        return FONT_PATH
    if 0x100000 <= ord(char) <= 0x10FFFD:
        return SYMBOLS_FONT_PATH
    return None


def is_icon(char: str) -> bool:
    """Whether the character is drawn with an icon or symbol font."""
    return font_of(char) is not None


def has_icon(text: str) -> bool:
    """Whether text has a character drawn with an icon or symbol font."""
    # Text without characters as high as the icons is left at once
    return bool(text) and max(text) >= '\U000F0000' and any(map(font_of, text))
