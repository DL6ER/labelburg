import os
import struct
import unittest
from app.fonts import WIDTHS, Fonts, FontNotFound, font_names
import json
import logging

import pytest


def read_testfile(file_path, data):
    file_path = f'tests/fixtures/{file_path}.json'
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)
        return data


class TestFonts(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger('TestFonts')
        logging.basicConfig(level=logging.DEBUG)
        self.fonts = Fonts(logger)

    def test_fonts_available_empty(self):
        # Should be False when no fonts are loaded
        self.fonts.fonts.clear()
        self.assertFalse(self.fonts.fonts_available())

    def test_fonts_available_nonempty(self):
        # Should be True when fonts are loaded
        self.fonts.fonts['TestFont']['Regular'] = '/path/to/font.ttf'
        self.assertTrue(self.fonts.fonts_available())

    def test_fontlist(self):
        font_styles = self.fonts.fontlist()
        expected_font_styles = read_testfile('font_list', font_styles)
        self.assertEqual(font_styles, expected_font_styles)


def test_font_folder_is_scanned(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path='/opt/myfonts')
    assert '/opt/myfonts' in scanned
    assert '/' not in scanned


def test_font_folder_list_is_scanned(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path=['/opt/fonts', '/srv/fonts'])
    assert scanned[-2:] == ['/opt/fonts', '/srv/fonts']


def test_missing_default_style_keeps_family():
    family = Fonts(logging.getLogger('TestFonts')).fontlist()[-1]['family']
    fonts = Fonts(logging.getLogger('TestFonts'), default_family=family, default_style='No Such Style')
    assert fonts.get_default_font()[0] == family


def test_no_fonts_found(monkeypatch):
    monkeypatch.setattr(os.path, 'isdir', lambda path: False)
    fonts = Fonts(logging.getLogger('TestFonts'))
    assert not fonts.fonts_available()


def test_missing_default_font_is_replaced():
    fonts = Fonts(logging.getLogger('TestFonts'), default_family='No Such Font', default_style='Book')
    family, style = fonts.get_default_font()
    assert style in fonts.fonts.get(family, {})
    # Every worker process of a WSGI server has to pick the same font
    assert Fonts(logging.getLogger('TestFonts'), default_family='No Such Font').get_default_font() == (family, style)


def test_only_the_font_folder_without_system_fonts(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path='/opt/myfonts', system_fonts=False)
    assert scanned == ['/opt/myfonts']


def sfnt(records, tables=(b'name',), os2=None):
    """A font file with only a table directory and a name table of the given
    (platform, encoding, name ID, bytes) records, and an OS/2 table where
    given"""
    strings = b''.join(raw for *_, raw in records)
    name = struct.pack('>HHH', 0, len(records), 6 + 12 * len(records))
    start = 0
    for platform, encoding, name_id, raw in records:
        name += struct.pack('>6H', platform, encoding, 0x409 if platform == 3 else 0, name_id, len(raw), start)
        start += len(raw)
    name += strings
    data = {tag: name if tag == b'name' else b'' for tag in tables}
    if os2 is not None:
        data[b'OS/2'] = os2
    offset, directory, body = 12 + 16 * len(data), b'', b''
    for tag, table in data.items():
        directory += struct.pack('>4sIII', tag, 0, offset + len(body), len(table))
        body += table
    return struct.pack('>IHHHH', 0x10000, len(data), 0, 0, 0) + directory + body


def os2(weight, width, selection=0):
    """An OS/2 table of version 0 with the weight and width classes and
    fsSelection given"""
    table = bytearray(78)
    struct.pack_into('>HH', table, 4, weight, width)
    struct.pack_into('>H', table, 62, selection)
    return bytes(table)


def win(name_id, text):
    return 3, 1, name_id, text.encode('utf_16_be')


def test_font_names_of_an_installed_font():
    path = Fonts(logging.getLogger('TestFonts')).get_path('DejaVu Sans,Bold')
    assert font_names(path) == ('DejaVu Sans', 'Bold')


@pytest.mark.parametrize('records, names', [
    ([win(1, 'Family'), win(2, 'Style')], ('Family', 'Style')),
    # The first record of each wins, Macintosh Roman as well
    ([(1, 0, 1, 'Café'.encode('mac_roman')), (1, 0, 2, b'Bold'), win(1, 'Other'), win(2, 'Other')], ('Café', 'Bold')),
    # Empty records and those in other encodings are left out
    ([win(1, ''), (3, 2, 1, b'\x82\xa0'), (1, 1, 2, b'\x82\xa0'), win(1, 'Family'), win(2, 'Style')], ('Family', 'Style')),
    ([(0, 3, 1, 'Unicode'.encode('utf_16_be')), win(4, 'Full name')], ('Unicode', None)),
    ([], (None, None)),
])
def test_font_names(tmp_path, records, names):
    path = tmp_path / 'font.ttf'
    path.write_bytes(sfnt(records))
    assert font_names(path) == names


def test_font_names_without_a_name_table(tmp_path):
    path = tmp_path / 'font.ttf'
    path.write_bytes(sfnt([win(1, 'Family')], tables=(b'cmap',)))
    assert font_names(path) == (None, None)


@pytest.mark.parametrize('data', [b'', b'\x00\x01\x00\x00\x00\x01', sfnt([win(1, 'Family')])[:-4],
                                  sfnt([(3, 1, 1, b'\xd8\x00')])])
def test_broken_fonts_raise(tmp_path, data):
    """The scanner leaves out fonts it cannot read"""
    path = tmp_path / 'font.ttf'
    path.write_bytes(data)
    with pytest.raises((ValueError, struct.error)):
        font_names(path)


def test_broken_fonts_are_left_out(tmp_path):
    (tmp_path / 'broken.ttf').write_bytes(b'not a font')
    (tmp_path / 'good.otf').write_bytes(sfnt([win(1, 'Good'), win(2, 'Regular')]))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert fonts.fonts == {'Good': {'Regular': str(tmp_path / 'good.otf')}}


def families(fonts=None) -> dict:
    """The families of the label designer's font menu by their names"""
    return {family['name']: family for family in (fonts or Fonts(logging.getLogger('TestFonts'))).families()}


def faces(family) -> dict:
    return {style['font']: (style['weight'], style['width'], style['italic']) for style in family['styles']}


def test_the_widths_and_weights_of_inconsolata():
    inconsolata = families()['Inconsolata']
    found = faces(inconsolata)
    assert len(found) == 72
    assert found['Inconsolata,Regular'] == (400, 'normal', False)
    assert found['Inconsolata,Condensed Light'] == (300, 'condensed', False)
    assert found['Inconsolata,Semi Expanded Bold'] == (700, 'semi-expanded', False)
    assert found['Inconsolata,Ultra Condensed Black'] == (900, 'ultra-condensed', False)
    # Its ExtraLight is 275 in the font files, between two classes it is the
    # lighter one
    assert found['Inconsolata,Extra Expanded ExtraLight'] == (200, 'extra-expanded', False)
    assert sorted({weight for weight, _, _ in found.values()}) == [200, 300, 400, 500, 600, 700, 800, 900]
    assert {width for _, width, _ in found.values()} == set(WIDTHS)
    assert not any(italic for _, _, italic in found.values())
    assert (inconsolata['regular'], inconsolata['bold']) == ('Inconsolata,Regular', 700)
    # Narrow to wide, light to heavy
    assert [style['font'] for style in inconsolata['styles']][:3] == [
        'Inconsolata,Ultra Condensed ExtraLight', 'Inconsolata,Ultra Condensed Light', 'Inconsolata,Ultra Condensed']


def test_the_styles_of_dejavu():
    found = families()
    assert faces(found['DejaVu Sans']) == {
        'DejaVu Sans,Condensed / Book': (400, 'semi-condensed', False),
        'DejaVu Sans,Condensed / Oblique': (400, 'semi-condensed', True),
        'DejaVu Sans,Condensed / Bold': (700, 'semi-condensed', False),
        'DejaVu Sans,Condensed / Bold Oblique': (700, 'semi-condensed', True),
        'DejaVu Sans,Light / ExtraLight': (200, 'normal', False),
        'DejaVu Sans,Book': (400, 'normal', False),
        'DejaVu Sans,Oblique': (400, 'normal', True),
        'DejaVu Sans,Bold': (700, 'normal', False),
        'DejaVu Sans,Bold Oblique': (700, 'normal', True),
    }
    assert [style['font'] for style in found['DejaVu Serif']['styles']] == [
        'DejaVu Serif,Condensed / Book', 'DejaVu Serif,Condensed / Italic', 'DejaVu Serif,Condensed / Bold',
        'DejaVu Serif,Condensed / Bold Italic', 'DejaVu Serif,Book', 'DejaVu Serif,Italic', 'DejaVu Serif,Bold',
        'DejaVu Serif,Bold Italic']
    # Its Mono is a family of its own, as the font files name it
    assert faces(found['DejaVu Sans Mono']) == {
        'DejaVu Sans,Mono / Book': (400, 'normal', False), 'DejaVu Sans,Mono / Oblique': (400, 'normal', True),
        'DejaVu Sans,Mono / Bold': (700, 'normal', False), 'DejaVu Sans,Mono / Bold Oblique': (700, 'normal', True)}
    assert [(found[name]['regular'], found[name]['bold']) for name in ('DejaVu Sans', 'DejaVu Sans Mono', 'DejaVu Serif')] == [
        ('DejaVu Sans,Book', 700), ('DejaVu Sans,Mono / Book', 700), ('DejaVu Serif,Book', 700)]


def test_the_font_families_of_the_menu():
    fonts = Fonts(logging.getLogger('TestFonts'))
    listed = fonts.families()
    assert [family['name'] for family in listed] == [
        'DejaVu Math TeX Gyre', 'DejaVu Sans', 'DejaVu Sans Mono', 'DejaVu Serif', 'Droid Sans', 'Droid Sans Mono',
        'Droid Serif', 'FreeMono', 'FreeSans', 'FreeSerif', 'Inconsolata', 'Liberation Mono', 'Liberation Sans',
        'Liberation Serif', 'Ligconsolata', 'Noto Sans', 'Noto Sans Math', 'Noto Sans Mono', 'Noto Sans Symbols',
        'Noto Sans Symbols 2', 'Noto Serif', 'Noto Serif Display']
    for family in listed:
        assert list(family) == ['name', 'regular', 'bold', 'styles']
        for style in family['styles']:
            assert list(style) == ['font', 'weight', 'width', 'italic']
            assert style['weight'] in range(100, 1000, 100) and style['width'] in WIDTHS
    # Each style of the server once, by the name the API takes
    assert sorted(style['font'] for family in listed for style in family['styles']) == sorted(
        f'{family["family"]},{style}' for family in fonts.fontlist() for style in family['styles'])
    found = {family['name']: family for family in listed}
    # Liberation Sans Narrow is Liberation Sans, condensed
    assert faces(found['Liberation Sans'])['Liberation Sans,Narrow / Bold Italic'] == (700, 'condensed', True)
    # The bold of FreeSans is 600 in its files, Noto Sans Math has none
    assert (found['FreeSans']['bold'], found['Noto Sans Math']['bold']) == (600, None)
    assert faces(found['Noto Sans Symbols'])['Noto Sans,Symbols Thin'] == (100, 'normal', False)


def test_a_font_without_an_os2_table_is_described_by_its_names(tmp_path):
    (tmp_path / 'a.ttf').write_bytes(sfnt([win(1, 'Sample Sans'), win(2, 'Regular')]))
    (tmp_path / 'b.ttf').write_bytes(sfnt([win(1, 'Sample Sans Semi Condensed'), win(2, 'Bold Italic')]))
    (tmp_path / 'c.ttf').write_bytes(sfnt([win(1, 'Sample Sans Mono'), win(2, 'Extra Light')]))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert fonts.families() == [
        {'name': 'Sample Sans', 'regular': 'Sample Sans,Regular', 'bold': 700, 'styles': [
            {'font': 'Sample Sans,Semi Condensed / Bold Italic', 'weight': 700, 'width': 'semi-condensed', 'italic': True},
            {'font': 'Sample Sans,Regular', 'weight': 400, 'width': 'normal', 'italic': False}]},
        {'name': 'Sample Sans Mono', 'regular': 'Sample Sans,Mono / Extra Light', 'bold': None, 'styles': [
            {'font': 'Sample Sans,Mono / Extra Light', 'weight': 200, 'width': 'normal', 'italic': False}]}]


@pytest.mark.parametrize('table, face', [
    (os2(400, 5), (400, 'normal', False)),
    (os2(275, 3), (200, 'condensed', False)),
    (os2(950, 9, 0x20), (900, 'ultra-expanded', False)),
    # Italic or oblique
    (os2(400, 5, 0x01), (400, 'normal', True)),
    (os2(400, 5, 0x200), (400, 'normal', True)),
    # 1 to 9 of old fonts for 100 to 900
    (os2(7, 5), (700, 'normal', False)),
    # Classes out of range are left to the names
    (os2(0, 5), (300, 'semi-expanded', False)),
    (os2(400, 12), (300, 'semi-expanded', False)),
    (os2(400, 5)[:40], (300, 'semi-expanded', False)),
])
def test_the_face_of_a_font_is_read_from_its_os2_table(tmp_path, table, face):
    (tmp_path / 'a.ttf').write_bytes(sfnt([win(1, 'Sample Sans Semi Expanded'), win(2, 'Light')], os2=table))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert faces(fonts.families()[0]) == {'Sample Sans Semi Expanded,Light': face}


def test_thin_and_extra_light_of_the_old_numbering_stay_apart(tmp_path):
    # Windows once had 250 for Thin and 275 for ExtraLight, e.g. Inconsolata
    (tmp_path / 'a.ttf').write_bytes(sfnt([win(1, 'Sample Sans'), win(2, 'Thin')], os2=os2(250, 5)))
    (tmp_path / 'b.ttf').write_bytes(sfnt([win(1, 'Sample Sans'), win(2, 'ExtraLight')], os2=os2(275, 5)))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert faces(fonts.families()[0]) == {'Sample Sans,Thin': (100, 'normal', False), 'Sample Sans,ExtraLight': (200, 'normal', False)}


@pytest.mark.parametrize('style, face, family', [
    ('Demi', (600, 'normal', False), 'Sample Sans'),
    ('Demi Oblique', (600, 'normal', True), 'Sample Sans'),
    ('SemiLight', (300, 'normal', False), 'Sample Sans'),
    ('Semi Light Italic', (300, 'normal', True), 'Sample Sans'),
    ('ExtraBlack', (900, 'normal', False), 'Sample Sans'),
    # Two words that are no weight or width together stay apart
    ('Semi Mono', (400, 'normal', False), 'Sample Sans Semi Mono'),
])
def test_the_words_of_a_style_without_os2_table(tmp_path, style, face, family):
    (tmp_path / 'a.ttf').write_bytes(sfnt([win(1, 'Sample Sans'), win(2, style)]))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert [(f['name'], faces(f)) for f in fonts.families()] == [(family, {f'Sample Sans,{style}': face})]


def test_the_families_are_made_once():
    # The sample route and every label ask for them
    fonts = Fonts(logging.getLogger('TestFonts'))
    assert fonts.families() is fonts.families()


def test_the_regular_font_of_a_family():
    fonts = Fonts(logging.getLogger('TestFonts'))
    assert fonts.family_font('DejaVu Sans Mono') == 'DejaVu Sans,Mono / Book'
    assert fonts.family_font('DejaVu Sans') == 'DejaVu Sans,Book'
    assert fonts.family_font('Inconsolata') == 'Inconsolata,Regular'
    with pytest.raises(FontNotFound):
        fonts.family_font('No Such Font')
    # Font of new labels kept by name: a family of the menu or of the server
    assert {'DejaVu Sans', 'DejaVu Sans Mono', 'Noto Sans Symbols 2'} <= fonts.family_names()
    assert 'No Such Font' not in fonts.family_names()


if __name__ == '__main__':
    unittest.main()
