"""Build the font of the symbols of {{fastener:<name>}} and
{{electronic:<name>}} in label texts, app/static/symbols/symbols.ttf, and
their names, app/static/symbols/symbols.json, from the SVGs in
tools/symbols/<set>:

    pip install -r requirements-symbols.txt
    python3 tools/symbols_font.py

The same SVGs and tool versions give the same font: .github/workflows/fonts.yml
builds it again and fails when the committed one differs, and builds it on
Dependabot's pull requests that raise the tools.
"""
import json
import os
import re
import sys

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.timeTools import timestampFromString
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib.removeOverlaps import removeOverlaps
from picosvg.svg import SVG

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SOURCES = os.path.join(ROOT, 'tools', 'symbols')
OUT = os.path.join(ROOT, 'app', 'static', 'symbols')

# The sets, their folders and first code points, in Supplementary Private
# Use Area-B, since Material Design Icons are in A
SETS = {'fastener': ('fasteners', 0x100000), 'electronic': ('electronics', 0x101000)}
# Lines made thicker than drawn, so that they look as heavy as the icons
# next to them: the electronic symbols are drawn 5 wide in 150
STROKES = {'electronic': 2}
UPEM = 512
# As in Material Design Icons: the em from 64 below the baseline to 448
# above, a symbol 20/24 of it high in the middle
ASCENT, DESCENT = 448, 64
PAD = UPEM * 2 / 24
# Fixed timestamps, so that the same SVGs give the same font
TIMESTAMP = timestampFromString('Thu Jan  1 00:00:00 2026')


def name_of(file: str) -> str:
    """The name of a symbol, its file name without the standard "COM" of
    the electronic symbols, e.g. Diode-COM-LED.svg is diode-led."""
    parts = file.removesuffix('.svg').lower().replace('_', '-').split('-')
    return '-'.join(part for part in parts if part != 'com')


def glyph(path: str, stroke: float = 1):
    """The glyph and its advance of an SVG, its viewBox the symbol's box,
    its lines stroke times as wide."""
    with open(path, encoding='utf-8') as f:
        text = re.sub(r'stroke-width="([0-9.]+)"', lambda m: f'stroke-width="{float(m.group(1)) * stroke:g}"', f.read())
    svg = SVG.fromstring(text).topicosvg()
    x, y, w, h = svg.view_box()
    scale = (UPEM - 2 * PAD) / h
    tt = TTGlyphPen(None)
    # SVG y grows down, the font's up
    pen = TransformPen(Cu2QuPen(tt, max_err=1, reverse_direction=True),
                       (scale, 0, 0, -scale, PAD - x * scale, ASCENT - PAD + y * scale))
    for shape in svg.shapes():
        parse_path(shape.d, pen)
    return tt.glyph(), round(w * scale + 2 * PAD)


def build():
    glyphs, advances, cmap, names = {'.notdef': TTGlyphPen(None).glyph()}, {'.notdef': (UPEM, 0)}, {}, {}
    for set_name, (folder, first) in SETS.items():
        files = sorted((f for f in os.listdir(os.path.join(SOURCES, folder)) if f.endswith('.svg')), key=name_of)
        names[set_name] = {}
        for code, file in enumerate(files, first):
            name = name_of(file)
            if name in names[set_name]:
                sys.exit(f'{file} is named {name} as another {set_name}')
            glyph_name = f'{set_name}.{name}'
            glyphs[glyph_name], width = glyph(os.path.join(SOURCES, folder, file), STROKES.get(set_name, 1))
            # The left side bearing as drawn, overlaps are removed keeping it
            glyphs[glyph_name].recalcBounds(None)
            advances[glyph_name] = (width, glyphs[glyph_name].xMin)
            cmap[code] = glyph_name
            names[set_name][name] = f'{code:X}'
    fb = FontBuilder(UPEM, isTTF=True)
    fb.setupGlyphOrder(list(glyphs))
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(advances)
    fb.setupHorizontalHeader(ascent=ASCENT, descent=-DESCENT)
    fb.setupNameTable({'familyName': 'Labelburg Symbols', 'styleName': 'Regular',
                       'copyright': 'Fasteners drawn as in gflabel by Nicholas Devenish (BSD-3-Clause), '
                                    'electronic symbols by Chris Pikul (MIT), see tools/symbols'})
    fb.setupOS2(sTypoAscender=ASCENT, sTypoDescender=-DESCENT, usWinAscent=ASCENT, usWinDescent=DESCENT)
    fb.setupPost()
    fb.setupHead(unitsPerEm=UPEM, created=TIMESTAMP, modified=TIMESTAMP)
    removeOverlaps(fb.font)
    fb.font.recalcTimestamp = False
    os.makedirs(OUT, exist_ok=True)
    fb.font.save(os.path.join(OUT, 'symbols.ttf'))
    with open(os.path.join(OUT, 'symbols.json'), 'w', encoding='utf-8') as f:
        json.dump(names, f, separators=(',', ':'))
        f.write('\n')
    return {set_name: len(symbols) for set_name, symbols in names.items()}


if __name__ == '__main__':
    print(build(), 'written to', OUT)
