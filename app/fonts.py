import bisect
import io
import os
import re
import logging
import struct
from collections import defaultdict
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

# Name records in these (platform, encoding) pairs can be read: Unicode,
# Windows Unicode and Macintosh Roman
NAME_ENCODINGS = {**{(0, encoding): 'utf_16_be' for encoding in range(7)},
                  (3, 0): 'utf_16_be', (3, 1): 'utf_16_be', (3, 10): 'utf_16_be', (1, 0): 'mac_roman'}

# The widths of usWidthClass 1 to 9 in the OS/2 table, as CSS names them
WIDTHS = ('ultra-condensed', 'extra-condensed', 'condensed', 'semi-condensed', 'normal',
          'semi-expanded', 'expanded', 'extra-expanded', 'ultra-expanded')
# Words of font names for a weight, a width or a slant, e.g. "Extra Condensed"
# as one. What else a style names is a family of its own, e.g. Mono.
WEIGHT_WORDS = {'thin': 100, 'hairline': 100, 'extralight': 200, 'ultralight': 200, 'light': 300, 'semilight': 300,
                'book': 400, 'regular': 400, 'normal': 400, 'roman': 400, 'medium': 500, 'semibold': 600, 'demibold': 600,
                'demi': 600, 'bold': 700, 'extrabold': 800, 'ultrabold': 800, 'black': 900, 'heavy': 900,
                'extrablack': 900, 'ultrablack': 900}
WIDTH_WORDS = {'ultracondensed': 'ultra-condensed', 'extracondensed': 'extra-condensed', 'condensed': 'condensed',
               'narrow': 'condensed', 'semicondensed': 'semi-condensed', 'semiexpanded': 'semi-expanded',
               'expanded': 'expanded', 'wide': 'expanded', 'extraexpanded': 'extra-expanded', 'ultraexpanded': 'ultra-expanded'}
SLANT_WORDS = ('italic', 'oblique')
# Fonts that draw what the font of a line has no glyph for, in this order:
# symbols, arrows and dingbats of the fonts installed, then emoji in black
# and white, see tools/update_fonts.py
FALLBACK_FONTS = (('DejaVu Sans', 'Book'), ('Noto Sans Symbols 2', 'Regular'), ('Noto Sans Symbols', 'Regular'),
                  ('Noto Sans Math', 'Regular'), ('FreeSerif', 'Regular'))
EMOJI_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'emoji', 'NotoEmoji.ttf')


class FontNotFound(LookupError):
    """A font given as "<family>,<style>" that is not installed."""


def _tables(path, tags: tuple) -> dict:
    """The tables of a TrueType or OpenType font named in tags, those it has.
    A broken file raises ValueError or struct.error."""
    with open(path, 'rb') as fh:
        def read(offset: int, length: int) -> bytes:
            fh.seek(offset)
            data = fh.read(length)
            if len(data) < length:
                raise ValueError(f'Font file too short: {path}')
            return data

        count = struct.unpack('>H', read(4, 2))[0]
        directory = read(12, 16 * count)
        found = {}
        for i in range(count):
            tag, _, offset, length = struct.unpack_from('>4sIII', directory, 16 * i)
            if tag in tags and tag not in found:
                found[tag] = (offset, length)
        return {tag: read(offset, length) for tag, (offset, length) in found.items()}


def _names(table: bytes | None, path) -> tuple[str | None, str | None]:
    if table is None:
        return None, None
    _, count, strings = struct.unpack_from('>HHH', table)
    names = {}
    for i in range(count):
        platform, encoding, _, name_id, size, start = struct.unpack_from('>6H', table, 6 + 12 * i)
        codec = NAME_ENCODINGS.get((platform, encoding))
        if name_id in (1, 2) and name_id not in names and codec:
            raw = table[strings + start:strings + start + size]
            if len(raw) < size:
                raise ValueError(f'Font name table too short: {path}')
            if text := raw.decode(codec):
                names[name_id] = text
    return names.get(1), names.get(2)


def _os2_face(table: bytes | None) -> dict | None:
    """Weight, width and slant of a font's OS/2 table, None without one or
    with classes out of range."""
    if table is None or len(table) < 64:
        return None
    weight, width = struct.unpack_from('>HH', table, 4)
    selection = struct.unpack_from('>H', table, 62)[0]
    # Some old fonts have 1 to 9 for 100 to 900, Windows once had 250 and
    # 275 for Thin and ExtraLight, e.g. Inconsolata
    if 1 <= weight <= 9:
        weight *= 100
    weight = {250: 100, 275: 200}.get(weight, weight)
    if not 1 <= weight <= 1000 or not 1 <= width <= 9:
        return None
    # A weight between two classes is the lighter one; ITALIC or OBLIQUE in
    # fsSelection
    return {'weight': max(100, min(900, weight // 100 * 100)), 'width': WIDTHS[width - 1], 'italic': bool(selection & 0x201)}


def _words(name: str) -> list[tuple[str, str]]:
    """The words of a font name with their keys, e.g. "Extra Condensed" as
    one word with the key "extracondensed". Two words are one only where
    they name a weight or width together, "Demi Oblique" stays two."""
    words = []
    for word in filter(None, re.split(r'[\s/_-]+', name)):
        joined = words[-1][1] + word.lower() if words else ''
        if words and words[-1][1] in ('extra', 'ultra', 'semi', 'demi') and (joined in WEIGHT_WORDS or joined in WIDTH_WORDS):
            words[-1] = (f'{words[-1][0]} {word}', joined)
        else:
            words.append((word, word.lower()))
    return words


def _named_face(name: str) -> dict:
    """Weight, width and slant as the words of a font name tell them."""
    keys = [key for _, key in _words(name)]
    weights = [WEIGHT_WORDS[key] for key in keys if key in WEIGHT_WORDS]
    widths = [WIDTH_WORDS[key] for key in keys if key in WIDTH_WORDS]
    return {'weight': weights[-1] if weights else 400, 'width': widths[-1] if widths else 'normal',
            'italic': any(key in SLANT_WORDS for key in keys)}


def _variant(style: str) -> str:
    """What a style names besides weight, width and slant, e.g. Mono of
    "Mono / Bold Oblique"."""
    return ' '.join(word for word, key in _words(style)
                    if key not in WEIGHT_WORDS and key not in WIDTH_WORDS and key not in SLANT_WORDS)


def font_info(path) -> tuple[str | None, str | None, dict | None]:
    """Family, style and face (weight 100 to 900, width as in WIDTHS and
    italic) of a TrueType or OpenType font, the face as its OS/2 table tells
    it. A broken file raises ValueError or struct.error."""
    tables = _tables(path, (b'name', b'OS/2'))
    family, style = _names(tables.get(b'name'), path)
    return family, style, _os2_face(tables.get(b'OS/2'))


def font_names(path) -> tuple[str | None, str | None]:
    """Family and style of a TrueType or OpenType font: the first readable,
    non-empty records with name IDs 1 and 2 in its name table. A broken file
    raises ValueError or struct.error."""
    return font_info(path)[:2]


def _cmap_ranges(table: bytes) -> list[tuple[int, int]] | None:
    """The characters a cmap table maps to a glyph, as (first, last) ranges,
    of the Unicode subtable FreeType takes: one for all of Unicode before
    one for the BMP. None without either."""
    count = struct.unpack_from('>H', table, 2)[0]
    subtables = {}
    for i in range(count):
        platform, encoding, offset = struct.unpack_from('>HHI', table, 4 + 8 * i)
        if platform == 0 or (platform, encoding) in ((3, 1), (3, 10)):
            subtables.setdefault(struct.unpack_from('>H', table, offset)[0], offset)
    ranges = []
    if 12 in subtables:
        offset = subtables[12]
        groups = struct.unpack_from('>I', table, offset + 12)[0]
        for i in range(groups):
            first, last, glyph = struct.unpack_from('>III', table, offset + 16 + 12 * i)
            # Glyph 0 is the missing glyph
            ranges.append((first + (glyph == 0), min(last, 0x10FFFF)))
    elif 4 in subtables:
        offset = subtables[4]
        segments = struct.unpack_from('>H', table, offset + 6)[0] // 2
        ends = struct.unpack_from(f'>{segments}H', table, offset + 14)
        starts = struct.unpack_from(f'>{segments}H', table, offset + 16 + 2 * segments)
        deltas = struct.unpack_from(f'>{segments}H', table, offset + 16 + 4 * segments)
        range_offsets = offset + 16 + 6 * segments
        for i, (first, last, delta) in enumerate(zip(starts, ends, deltas)):
            shift = struct.unpack_from('>H', table, range_offsets + 2 * i)[0]
            if not shift:
                # Only the one character the delta takes to glyph 0 is missing
                missing = (0x10000 - delta) % 0x10000
                ranges += [(first, min(last, missing - 1)), (max(first, missing + 1), last)]
                continue
            for code in range(first, last + 1):
                glyph = struct.unpack_from('>H', table, range_offsets + 2 * i + shift + 2 * (code - first))[0]
                if glyph and (glyph + delta) % 0x10000:
                    ranges.append((code, code))
    else:
        return None
    return [(first, last) for first, last in ranges if first <= last]


@lru_cache(maxsize=256)
def _glyph_ranges(path: str) -> tuple[list[int], list[int]] | None:
    """The first and last characters of the ranges of characters the font
    at path has glyphs for, merged and in order. None where its cmap cannot
    be read."""
    try:
        table = _tables(path, (b'cmap',)).get(b'cmap')
        ranges = _cmap_ranges(table) if table is not None else None
    except (OSError, ValueError, struct.error):
        return None
    if ranges is None:
        return None
    firsts, lasts = [], []
    for first, last in sorted(ranges):
        if lasts and first <= lasts[-1] + 1:
            lasts[-1] = max(lasts[-1], last)
        else:
            firsts.append(first)
            lasts.append(last)
    return firsts, lasts


def has_glyphs(path: str, text: str) -> bool:
    """Whether the font at path has a glyph for each character of text. A
    font whose characters cannot be read is taken to have them all, so that
    it draws as without fallback fonts."""
    ranges = _glyph_ranges(path)
    if ranges is None:
        return True
    firsts, lasts = ranges
    for char in set(text):
        i = bisect.bisect_right(firsts, ord(char)) - 1
        if i < 0 or ord(char) > lasts[i]:
            return False
    return True


SAMPLE_SIZE = 40


@lru_cache(maxsize=256)
def sample(path: str, text: str) -> bytes | None:
    """text drawn in the font, black on transparent as a PNG image, None
    where the font has no glyph for one of its characters. Every sample is
    as high and has its baseline at the same height. A file that cannot
    draw, e.g. a broken one, has none either; kept, it is told once."""
    try:
        return _sample(path, text)
    except OSError as e:
        logging.getLogger(__name__).warning('The font %s cannot draw a sample: %s', path, e)
        return None


def _sample(path: str, text: str) -> bytes | None:
    font = ImageFont.truetype(path, SAMPLE_SIZE)
    missing = font.getmask('\U0010fffd')

    def drawn(char):
        mask = font.getmask(char)
        return mask.size != missing.size or bytes(mask) != bytes(missing)
    if not all(drawn(char) for char in set(text) if not char.isspace()):
        return None
    image = Image.new('LA', (round(font.getlength(text)) + 4, round(SAMPLE_SIZE * 1.4)), (0, 0))
    ImageDraw.Draw(image).text((2, round(SAMPLE_SIZE * 1.1)), text, font=font, fill=(0, 255), anchor='ls')
    out = io.BytesIO()
    image.save(out, 'PNG', optimize=True)
    return out.getvalue()


class Fonts:
    def __init__(self,
                 logger: logging.Logger,
                 default_family: str = 'DejaVu Serif',
                 default_style: str = 'Book',
                 additional_path: str | list[str] = '',
                 system_fonts: bool = True):
        self.fonts = defaultdict(dict)
        # Weight, width and slant of each font file, see font_info()
        self.faces = {}
        # Each font file by the family and style its names tell
        named = {}
        self.default_family = default_family
        self.default_style = default_style

        # Scan for TTF/OTF fonts, see font_names().
        # :param additional_path: Directory (or list of directories) to search
        #     in addition to common system font paths.
        # :param system_fonts: Whether to search the system font paths at all.
        search_paths = [
            '/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
            os.path.expanduser('~/.local/share/fonts'), '/Library/Fonts', '/System/Library/Fonts',
            'C:\\Windows\\Fonts'
        ] if system_fonts else []
        # FONT_FOLDER may be a single folder or a list of folders
        if isinstance(additional_path, str):
            additional_path = [additional_path] if additional_path else []
        search_paths.extend(additional_path or [])

        font_exts = ('.ttf', '.otf')
        for base_path in search_paths:
            if not os.path.isdir(base_path):
                continue
            for root, _, files in os.walk(base_path):
                for file in files:
                    if file.lower().endswith(font_exts):
                        font_path = os.path.join(root, file)
                        try:
                            family, style, face = font_info(font_path)
                            if family and style:
                                self.fonts[family][style] = font_path
                                named.setdefault((family, style), font_path)
                                # Without an OS/2 table as its names tell it
                                self.faces[font_path] = face or _named_face(f'{family} {style}')
                        except Exception:
                            continue

        # Sort fonts alphabetically by family name
        self.fonts = defaultdict(dict, {k: self.fonts[k] for k in sorted(self.fonts.keys(), key=str.lower)})

        # Consolidate fonts: Search for fonts that have children, e.g.
        # "DejaVu Sans" and "DejaVu Sans Condensed" and move children
        # under the parent font, adding them as a style instead
        for family in list(self.fonts):
            for other_family in list(self.fonts.keys()):
                if family != other_family and family in other_family:
                    extra_style = other_family.replace(family + ' ', '')
                    for style in self.fonts[other_family].keys():
                        new_style = extra_style + ((' / ' + style) if style != 'Regular' else '')
                        self.fonts[family][new_style] = self.fonts[other_family][style]

                    # Remove the child
                    del self.fonts[other_family]

        # Check if the default family/style is available, if not, pick the
        # first available one. The choice must not be random, otherwise every
        # worker process of a WSGI server could end up with another default.
        if default_family in self.fonts and default_style in self.fonts[default_family]:
            logger.debug(f"Selected the following default font: {default_family}")
        elif self.fonts:
            logger.warning('Could not find any of the default fonts. Choosing the first available one.')
            fontlist = self.fontlist()
            # Prefer another style of the configured family
            first = next((f for f in fontlist if f['family'] == default_family), fontlist[0])
            self.default_family = first['family']
            self.default_style = first['styles'][0]
            logger.warning(f'The default font is now set to: {self.default_family} ({self.default_style})')

        # Those whose characters can be read
        fallbacks = [named[name] for name in FALLBACK_FONTS if name in named] + [EMOJI_FONT_PATH]
        self._fallbacks = tuple(path for path in fallbacks if _glyph_ranges(path) is not None)

        # The families of the font menu and the regular font of each name
        # family_font() takes, made once: every label and sample asks
        self._families = self._grouped()
        self._regular = {family['name']: family['regular'] for family in self._families}
        for family in self._families:
            for style in family['styles']:
                self._regular.setdefault(style['font'].split(',', 1)[0], family['regular'])

    def get_default_font(self):
        """Return the default font family and style."""
        return (self.default_family, self.default_style)

    def fontlist(self):
        """Return a sorted list of font styles for each family."""
        fontlist = []
        for family, variants in self.fonts.items():
            style_keys = list(variants.keys())
            # Prioritize 'Book' or 'Regular' in sorting if present
            prioritized = [s for s in style_keys if s.lower() in ('book', 'regular')]
            others = sorted([s for s in style_keys if s.lower() not in ('book', 'regular')], key=str.lower)
            sorted_styles = prioritized + others
            fontlist.append({
                'family': family,
                'styles': sorted_styles
            })
        return fontlist

    def families(self) -> list[dict]:
        """The font families as the font menu of the label designer lists
        them: the families of the server, what a style names besides weight,
        width and slant a family of its own, e.g. DejaVu Sans Mono. Each with
        its regular font, the weight of its bold (None without one) and its
        styles, narrow to wide and light to heavy: the font as the API takes
        it, its weight, width and whether it is italic. Shared, not to be
        changed."""
        return self._families

    def _grouped(self) -> list[dict]:
        grouped = defaultdict(list)
        for family, styles in self.fonts.items():
            for style, path in styles.items():
                name = ' '.join(filter(None, (family, _variant(style))))
                grouped[name].append({'font': f'{family},{style}', **(self.faces.get(path) or _named_face(style))})
        listed = []
        for name in sorted(grouped, key=str.casefold):
            styles = sorted(grouped[name], key=lambda s: (WIDTHS.index(s['width']), s['weight'], s['italic']))
            regular = min(styles, key=lambda s: (s['italic'], abs(WIDTHS.index(s['width']) - 4), abs(s['weight'] - 400), s['weight']))
            # Bold in the label designer: of the weights from 600 the one nearest 700
            bold = min((s['weight'] for s in styles if s['weight'] >= 600), key=lambda w: (abs(w - 700), -w), default=None)
            listed.append({'name': name, 'regular': regular['font'], 'bold': bold, 'styles': styles})
        return listed

    def family_font(self, name: str) -> str:
        """The regular font of a family as families() names it, or of a
        family of the server, e.g. a font of new labels kept before."""
        if name not in self._regular:
            raise FontNotFound(f'Unknown font family: {name}')
        return self._regular[name]

    def family_names(self) -> set[str]:
        """The names family_font() takes."""
        return set(self._regular)

    def fallbacks(self) -> tuple[str, ...]:
        """The files of the fonts that draw what the font of a line has no
        glyph for, the first that has it, see FALLBACK_FONTS."""
        return self._fallbacks

    def fonts_available(self):
        """Return True if any fonts are available, else False."""
        return bool(self.fonts)

    def get_path(self, font: str):
        if ',' not in font:
            raise LookupError(f'Font must be given as "<family>,<style>": {font}')
        family_name, style_name = font.split(",", 1)
        if family_name not in self.fonts:
            raise FontNotFound(f"Unknown font family: {family_name}")
        if style_name not in self.fonts[family_name]:
            raise FontNotFound(f"Unknown font style: {style_name} for font {family_name}")
        return self.fonts[family_name][style_name]
