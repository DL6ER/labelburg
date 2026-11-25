"""The setup codes of docs/scanners.md."""
import os
import re

import barcode
import pytest

from tools import scanner_codes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize('text', ['Ab%#$x', '$LAN#GE', '%#IFSNO$CR'])
def test_the_bars_are_those_of_code_128(text):
    assert scanner_codes.modules(text) == barcode.Code128(text).build()[0]


def test_fnc3_comes_first_and_counts_in_the_check():
    # Start B, FNC3, '9', '2', check (104 + 96 + 2 * 25 + 3 * 18) % 103 = 98, stop
    assert scanner_codes.modules('92', fnc3=True) == (
        '11010010000' '10111100010' '11100101100' '11001110010' '11110100010' '1100011101011')


def test_the_pictures_in_the_docs_are_drawn_from_the_codes():
    folder = os.path.join(ROOT, 'docs', 'scanners')
    assert sorted(os.listdir(folder)) == sorted(name + '.svg' for name in scanner_codes.CODES)
    for name, (text, fnc3) in scanner_codes.CODES.items():
        with open(os.path.join(folder, name + '.svg'), encoding='utf-8') as fh:
            assert fh.read() == scanner_codes.svg(text, fnc3), name


def test_the_guide_shows_every_code_once():
    with open(os.path.join(ROOT, 'docs', 'scanners.md'), encoding='utf-8') as fh:
        shown = re.findall(r'\]\(scanners/([\w-]+)\.svg\)', fh.read())
    assert sorted(shown) == sorted(scanner_codes.CODES)
