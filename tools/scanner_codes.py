"""Draw the setup codes of docs/scanners.md as SVGs in docs/scanners:

    python3 tools/scanner_codes.py

Each code holds the command its scanner takes, drawn here as Code 128.
Zebra marks its commands with FNC3, which tells the scanner to set
itself up with the code instead of typing it.
"""
import os

from barcode.charsets import code128

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'scanners')
FNC3 = 96

# File name: (command, FNC3)
CODES = {
    'zebra-factory': ('92', True),
    'zebra-keyboard': ('SXUAH20003', True),
    'zebra-enter': ('N6S06201007S0680100DB0EB01', True),
    'zebra-us': ('N03C0003C10000', True),
    'zebra-uk': ('N03C00F3C10000', True),
    'zebra-german': ('N03C00A3C10000', True),
    'zebra-french': ('N03C0093C10000', True),
    'zebra-italian': ('N03C00D3C10000', True),
    'zebra-spanish': ('N03C00C3C10000', True),
    'zebra-turkish': ('N03C0433C10000', True),
    'netum-factory': ('%#IFSNO$CR', False),
    'netum-keyboard': ('%#IFSNO$2', False),
    'netum-dongle-keyboard': ('$USB#KEY', False),
    'netum-aztec': ('1003281', True),
    'netum-us': ('$LAN#EN', False),
    'netum-uk': ('$LAN#UK', False),
    'netum-german': ('$LAN#GE', False),
    'netum-french': ('$LAN#FR', False),
    'netum-italian': ('$LAN#IT', False),
    'netum-spanish': ('$LAN#ES', False),
    'netum-turkish': ('$LAN#TK', False),
}


def modules(text, fnc3=False):
    """The bars of Code 128 in code set B, '1' for a dark module."""
    values = [code128.START_CODES['B']] + [FNC3] * fnc3 + [code128.B[ch] for ch in text]
    check = (values[0] + sum(i * v for i, v in enumerate(values[1:], 1))) % 103
    return ''.join(code128.CODES[v] for v in values + [check]) + code128.STOP + '11'


def svg(text, fnc3=False, module=2, height=60, quiet=10):
    bars = modules(text, fnc3)
    width = (len(bars) + 2 * quiet) * module
    rects = []
    x = 0
    while x < len(bars):
        if bars[x] == '1':
            end = bars.index('0', x) if '0' in bars[x:] else len(bars)
            rects.append(f'<rect x="{(x + quiet) * module}" width="{(end - x) * module}" height="{height}"/>')
            x = end
        else:
            x += 1
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="#fff"/>{"".join(rects)}</svg>\n')


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, (text, fnc3) in CODES.items():
        with open(os.path.join(OUT, name + '.svg'), 'w', encoding='utf-8') as fh:
            fh.write(svg(text, fnc3))


if __name__ == '__main__':
    main()
