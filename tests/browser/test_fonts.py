"""The font menu of the label designer: families with a search field and the
recent ones first, weight and width of the family below, bold and italic in
the toolbar of a line, the same menu for all lines and in the Settings."""
import json
import logging
import re
import shutil
import struct

import pytest

from app.fonts import Fonts
from tests.browser.test_editor import changed_preview, editor, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_gallery import search_and_open
from tests.browser.test_settings import back_to_the_editor, open_settings

# Copies of DejaVu fonts under other names, weights and widths: the test
# image has DejaVu Sans, its Mono and DejaVu Serif, none with widths
SAMPLES = [
    ('DejaVu Sans,Book', 'Sample Sans', 'Regular', 400, 5),
    ('DejaVu Sans,Bold', 'Sample Sans', 'Bold', 700, 5),
    ('DejaVu Sans,Book', 'Sample Sans Light', 'Regular', 300, 5),
    ('DejaVu Sans,Book', 'Sample Sans Condensed', 'Regular', 400, 3),
    ('DejaVu Sans,Book', 'Sample Sans Condensed Light', 'Regular', 300, 3),
    ('DejaVu Sans,Bold', 'Sample Sans Condensed Bold', 'Regular', 700, 3),
    ('DejaVu Sans,Book', 'Sample Sans Expanded', 'Regular', 400, 7),
    ('DejaVu Serif,Book', 'Sample Serif', 'Regular', 400, 5),
    ('DejaVu Serif,Bold', 'Sample Serif', 'Bold', 700, 5),
    ('DejaVu Serif,Book', 'Sample Serif Semi Condensed', 'Regular', 400, 4),
    ('DejaVu Sans,Mono / Book', 'Sample Mono', 'Regular', 400, 5),
    # More widths than a row of buttons takes
    ('DejaVu Sans,Book', 'Sample Wide Extra Condensed', 'Regular', 400, 2),
    ('DejaVu Sans,Book', 'Sample Wide Condensed', 'Regular', 400, 3),
    ('DejaVu Sans,Book', 'Sample Wide', 'Regular', 400, 5),
    ('DejaVu Sans,Book', 'Sample Wide Expanded', 'Regular', 400, 7),
    ('DejaVu Sans,Book', 'Sample Wide Extra Expanded', 'Regular', 400, 8),
]
FAMILIES = ['DejaVu Sans', 'DejaVu Sans Mono', 'DejaVu Serif', 'Sample Mono', 'Sample Sans', 'Sample Serif', 'Sample Wide']
# The DejaVu fonts of the test image and of fonts-dejavu-core in CI, the only
# fonts of the server besides the samples: hosts have others, e.g. DejaVu
# Sans Condensed
DEJAVU = ['DejaVu Sans,Book', 'DejaVu Sans,Bold', 'DejaVu Sans,Mono / Book', 'DejaVu Sans,Mono / Bold', 'DejaVu Sans,Mono / Oblique',
          'DejaVu Sans,Mono / Bold Oblique', 'DejaVu Serif,Book', 'DejaVu Serif,Bold']


def font_copy(source: str, family: str, style: str, weight: int, width: int) -> bytes:
    """The font file at source with another family and style in its name
    table and the weight and width classes given in its OS/2 table."""
    with open(source, 'rb') as fh:
        data = fh.read()
    tables = {}
    for i in range(struct.unpack_from('>H', data, 4)[0]):
        tag, _, offset, length = struct.unpack_from('>4sIII', data, 12 + 16 * i)
        tables[tag] = data[offset:offset + length]
    records = [(1, family.encode('utf_16_be')), (2, style.encode('utf_16_be'))]
    name = struct.pack('>HHH', 0, len(records), 6 + 12 * len(records))
    start = 0
    for name_id, raw in records:
        name += struct.pack('>6H', 3, 1, 0x409, name_id, len(raw), start)
        start += len(raw)
    tables[b'name'] = name + b''.join(raw for _, raw in records)
    os2 = bytearray(tables[b'OS/2'])
    struct.pack_into('>HH', os2, 4, weight, width)
    tables[b'OS/2'] = bytes(os2)
    offset, directory, body = 12 + 16 * len(tables), b'', b''
    for tag in sorted(tables):
        directory += struct.pack('>4sIII', tag, 0, offset + len(body), len(tables[tag]))
        body += tables[tag] + b'\0' * (-len(tables[tag]) % 4)
    return data[:12] + directory + body


@pytest.fixture(scope='session')
def sample_fonts(tmp_path_factory):
    folder = tmp_path_factory.mktemp('fonts')
    system = Fonts(logging.getLogger('test'))
    for i, (source, family, style, weight, width) in enumerate(SAMPLES):
        (folder / f'sample{i}.ttf').write_bytes(font_copy(system.get_path(source), family, style, weight, width))
    for name in DEJAVU:
        shutil.copy(system.get_path(name), folder)
    return str(folder)


@pytest.fixture
def fonts(app, sample_fonts):
    """The server with the sample fonts and DEJAVU only"""
    before = app.extensions['fonts']
    app.extensions['fonts'] = Fonts(app.logger, before.default_family, before.default_style, sample_fonts, system_fonts=False)
    yield
    app.extensions['fonts'] = before


@pytest.fixture
def designer(fonts, editor):  # noqa: F811
    return editor


def font(page) -> str:
    return page.evaluate('() => labelEditor.S.edit.design.text[0].font')


def sent_fonts(request) -> list:
    return [line['font'] for line in json.loads(request['text'])]


def open_menu(page, button='#ltb [data-font]'):
    page.click(button)
    page.wait_for_selector('.font-menu')


def listed(page, section=None) -> list:
    """The families of the menu, or of its section Recent or All"""
    rows = page.eval_on_selector_all('.font-menu .fm-list > *', 'els => els.map(e => [e.className, e.textContent.trim()])')
    found, inside = [], section is None
    for kind, text in rows:
        if 'fm-h' in kind.split():
            inside = section is None or text == section
        elif inside and 'fm-o' in kind.split():
            found.append(text)
    return found


def pick(page, family) -> list:
    """The fonts of the lines sent after the family is picked in the menu"""
    return sent_fonts(changed_preview(page, lambda: choose(page, family)))


def choose(page, family):
    """The family picked in the menu, its row in All"""
    page.click(f'.font-menu .fm-o[data-family="{family}"] >> nth=-1')


def set_font(page, value):
    """The first line in the font, as a label opened with it, picked"""
    page.evaluate('f => { labelEditor.S.edit.design.text[0].font = f; }', value)
    changed_preview(page, lambda: (page.click('#field .ln >> nth=0'), page.keyboard.press('End'), page.keyboard.type('!')))
    page.click('#picks .pk[data-i="0"]')


def test_the_menu_lists_families_found_by_typing(designer):
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    # Families only, each in its own font, the search field ready to type
    assert listed(designer) == FAMILIES
    assert designer.locator('.font-menu .fm-h').count() == 0
    designer.wait_for_function("""() => [...document.querySelectorAll('.font-menu .fm-o img')].length === 7
        && [...document.querySelectorAll('.font-menu .fm-o img')].every(i => i.complete && i.naturalWidth > 0)""")
    assert designer.evaluate('() => document.activeElement.matches(".font-menu input[type=search]")')
    designer.keyboard.type('mono')
    assert listed(designer) == ['DejaVu Sans Mono', 'Sample Mono']
    # Enter picks the first, the line keeps its bold
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('Enter'))) == ['DejaVu Sans,Mono / Bold']
    assert designer.inner_text('#ltb [data-font] span') == 'DejaVu Sans Mono'
    designer.keyboard.type('x')
    assert listed(designer) == [] and designer.inner_text('.font-menu .fm-none') == 'No font is named like that'
    # Arrow keys go through the list, Enter picks
    designer.fill('.font-menu input[type=search]', 'sa')
    assert listed(designer) == ['DejaVu Sans', 'DejaVu Sans Mono', 'Sample Mono', 'Sample Sans', 'Sample Serif', 'Sample Wide']
    for key in ('ArrowDown', 'ArrowDown', 'ArrowUp', 'ArrowDown'):
        designer.keyboard.press(key)
    assert designer.get_attribute('.font-menu .fm-o.act', 'data-family') == 'Sample Mono'
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('Enter'))) == ['Sample Mono,Regular']
    # Escape closes it, the font button has the focus again
    designer.keyboard.press('Escape')
    assert designer.locator('.font-menu').count() == 0
    assert designer.evaluate('() => document.activeElement.matches("#ltb [data-font]")')


def test_the_families_used_last_come_first(designer):
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    for family in ('Sample Serif', 'DejaVu Sans Mono', 'Sample Serif'):
        choose(designer, family)
    wait_for_preview(designer)
    # Kept in the browser
    designer.reload()
    designer.wait_for_selector('#field .ln')
    wait_for_preview(designer)
    open_menu(designer)
    assert listed(designer, 'Recent') == ['Sample Serif', 'DejaVu Sans Mono']
    assert listed(designer, 'All') == FAMILIES
    # Searching shows each family once
    designer.keyboard.type('serif')
    assert listed(designer) == ['DejaVu Serif', 'Sample Serif']


def test_a_family_picked_keeps_the_nearest_weight_and_width(designer):
    type_label(designer, 'Shelf 3')
    set_font(designer, 'Sample Sans,Condensed Light')
    open_menu(designer)
    assert pick(designer, 'Sample Serif') == ['Sample Serif,Semi Condensed']
    assert pick(designer, 'DejaVu Sans') == ['DejaVu Sans,Book']
    assert pick(designer, 'Sample Sans') == ['Sample Sans,Regular']
    designer.keyboard.press('Escape')
    set_font(designer, 'Sample Sans,Condensed Bold')
    open_menu(designer)
    assert pick(designer, 'DejaVu Serif') == ['DejaVu Serif,Bold']


def test_weight_and_width_change_the_style(designer):
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    assert pick(designer, 'Sample Sans') == ['Sample Sans,Bold']
    face = '.font-menu .fm-face'
    assert designer.inner_text(f'{face} .fm-name') == 'Sample Sans'
    assert designer.inner_text(f'{face} [data-wname]') == 'Bold'
    assert [designer.inner_text(f'{face} .fm-ends small >> nth={i}') for i in (0, 1)] == ['Light', 'Bold']
    assert [b.inner_text() for b in designer.locator(f'{face} [data-fwidth]').all()] == ['Condensed', 'Normal', 'Expanded']
    assert designer.get_attribute(f'{face} [data-fwidth].on', 'data-fwidth') == 'normal'
    # The slider by its keys, it stops at the weights the family has
    designer.focus(f'{face} [data-fweight]')
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('ArrowLeft'))) == ['Sample Sans,Regular']
    assert designer.inner_text(f'{face} [data-wname]') == 'Regular'
    assert designer.get_attribute(f'{face} [data-fweight]', 'aria-valuetext') == 'Regular'
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('Home'))) == ['Sample Sans,Light']
    assert sent_fonts(changed_preview(designer, lambda: designer.click(f'{face} [data-fwidth="condensed"]'))) == ['Sample Sans,Condensed Light']
    # Expanded has only Regular
    assert sent_fonts(changed_preview(designer, lambda: designer.click(f'{face} [data-fwidth="expanded"]'))) == ['Sample Sans,Expanded']
    assert designer.inner_text(f'{face} [data-wname]') == 'Regular'
    # Plain words, no numbers
    assert not re.search(r'\d', designer.inner_text(face))
    designer.keyboard.press('Escape')
    designer.click('#undo')
    designer.wait_for_function("() => labelEditor.S.edit.design.text[0].font === 'Sample Sans,Condensed Light'")


def test_many_widths_are_a_slider_as_the_weights(designer):
    # Up to four are buttons, more would be rows of them
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    assert pick(designer, 'Sample Wide') == ['Sample Wide,Regular']
    face = '.font-menu .fm-face'
    assert designer.locator(f'{face} [data-fwidth]').count() == 0
    assert designer.inner_text(f'{face} [data-dname]') == 'Normal'
    assert [designer.inner_text(f'{face} .fm-ends:has([data-fwidths]) small >> nth={i}') for i in (0, 1)] == ['Extra condensed', 'Extra expanded']
    designer.focus(f'{face} [data-fwidths]')
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('ArrowRight'))) == ['Sample Wide,Expanded']
    assert designer.inner_text(f'{face} [data-dname]') == 'Expanded'
    assert designer.get_attribute(f'{face} [data-fwidths]', 'aria-valuetext') == 'Expanded'
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('Home'))) == ['Sample Wide,Extra Condensed']


def overlap(a, b):
    return a['x'] < b['x'] + b['width'] and b['x'] < a['x'] + a['width'] and a['y'] < b['y'] + b['height'] and b['y'] < a['y'] + a['height']


@pytest.mark.parametrize('viewport, large', [
    pytest.param({'width': 1326, 'height': 924}, False, id='wide'),
    pytest.param({'width': 1024, 'height': 700}, False, id='narrow'),
    pytest.param({'width': 900, 'height': 560}, False, id='low'),
    pytest.param({'width': 1100, 'height': 640}, True, id='low, large preview'),
])
def test_the_menu_leaves_the_label_in_sight(designer, viewport, large):
    designer.set_viewport_size(viewport)
    type_label(designer, 'Shelf 3', 'Second line')
    if large:
        designer.click('#zoom')
    for button in ('#ltb [data-font]', '#adv [data-afont]'):
        if button.startswith('#adv'):
            designer.click('#advBtn')
            designer.wait_for_function("() => document.querySelector('#labelbox').dataset.laid === '1'")
        open_menu(designer, button)
        choose(designer, 'Sample Sans')
        designer.wait_for_selector('.font-menu [data-fwidth]')
        label, menu = designer.locator('#paper').bounding_box(), designer.locator('.font-menu').bounding_box()
        assert not overlap(label, menu), (button, label, menu)
        assert menu['y'] >= 0 and menu['y'] + menu['height'] <= viewport['height'], (button, menu)
        # All of it in reach: what does not fit scrolls inside the menu
        designer.locator('.font-menu [data-fwidth] >> nth=0').scroll_into_view_if_needed()
        width = designer.locator('.font-menu [data-fwidth] >> nth=0').bounding_box()
        assert menu['y'] <= width['y'] and width['y'] + width['height'] <= menu['y'] + menu['height'], (button, menu, width)
        designer.keyboard.press('Escape')


def test_bold_and_italic_toggle_the_style(designer):
    type_label(designer, 'Shelf 3')
    set_font(designer, 'Sample Sans,Light')
    assert designer.get_attribute('#ltb [data-bold]', 'aria-pressed') == 'false'
    # Bold and back to the weight before
    assert sent_fonts(changed_preview(designer, lambda: designer.click('#ltb [data-bold]'))) == ['Sample Sans,Bold']
    assert designer.get_attribute('#ltb [data-bold]', 'aria-pressed') == 'true'
    # Shown before, the preview comes from the cache
    designer.click('#ltb [data-bold]')
    designer.wait_for_function("() => labelEditor.S.edit.design.text[0].font === 'Sample Sans,Light'")
    # A family without italics says so
    assert designer.is_disabled('#ltb [data-italic]')
    assert designer.get_attribute('#ltb [data-italic]', 'title') == 'Sample Sans has no italic style'
    open_menu(designer)
    assert pick(designer, 'DejaVu Sans Mono') == ['DejaVu Sans,Mono / Book']
    designer.keyboard.press('Escape')
    assert sent_fonts(changed_preview(designer, lambda: designer.click('#ltb [data-italic]'))) == ['DejaVu Sans,Mono / Oblique']
    assert designer.get_attribute('#ltb [data-italic]', 'aria-pressed') == 'true'
    assert sent_fonts(changed_preview(designer, lambda: designer.click('#ltb [data-bold]'))) == ['DejaVu Sans,Mono / Bold Oblique']
    # Without the weight before, bold goes back to the regular weight
    set_font(designer, 'Sample Sans,Condensed Bold')
    assert sent_fonts(changed_preview(designer, lambda: designer.click('#ltb [data-bold]'))) == ['Sample Sans,Condensed']


def test_a_saved_label_shows_its_style_and_keeps_it(designer, server):
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                      data=json.dumps({'name': 'Narrow.json', 'title': 'Narrow', 'label_size': '62', 'orientation': 'standard',
                                       'print_type': 'text', 'text': [{'text': 'Jam', 'size': '40', 'font': 'Sample Sans,Condensed Light', 'align': 'center'}]}))
    search_and_open(designer, 'Narrow')
    designer.wait_for_function('() => labelEditor.S.edit.title === "Narrow"')
    wait_for_preview(designer)
    designer.click('#picks .pk[data-i="0"]')
    assert designer.inner_text('#ltb [data-font] span') == 'Sample Sans'
    assert designer.get_attribute('#ltb [data-bold]', 'aria-pressed') == 'false'
    open_menu(designer)
    assert designer.inner_text('.font-menu [data-wname]') == 'Light'
    assert designer.get_attribute('.font-menu [data-fwidth].on', 'data-fwidth') == 'condensed'
    designer.keyboard.press('Escape')
    # Other changes keep it
    request = changed_preview(designer, lambda: designer.click('#ltb [data-size="1"]'))
    assert sent_fonts(request) == ['Sample Sans,Condensed Light']
    assert font(designer) == 'Sample Sans,Condensed Light'


def test_families_without_weights_or_widths_show_none(designer):
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    # The line is in DejaVu Serif
    assert designer.get_attribute('.font-menu .fm-o[aria-selected="true"]', 'data-family') == 'DejaVu Serif'
    assert designer.locator('.font-menu [data-fweight]').count() == 1
    assert designer.locator('.font-menu [data-fwidth]').count() == 0
    pick(designer, 'Sample Mono')
    assert designer.locator('.font-menu .fm-face [data-fweight], .font-menu .fm-face [data-fwidth]').count() == 0
    # Nor bold where the family has none
    designer.keyboard.press('Escape')
    assert designer.is_disabled('#ltb [data-bold]')
    assert designer.get_attribute('#ltb [data-bold]', 'title') == 'Sample Mono has no bold style'


def test_advanced_sets_the_font_of_all_lines(designer):
    type_label(designer, 'Shelf 3', 'spices')
    designer.click('#advBtn')
    designer.wait_for_selector('#adv [data-afont]')
    assert designer.inner_text('#adv [data-afont] span') == 'DejaVu Serif'
    open_menu(designer, '#adv [data-afont]')
    # Each line keeps its weight
    assert pick(designer, 'Sample Sans') == ['Sample Sans,Bold', 'Sample Sans,Regular']
    face = '.font-menu .fm-face'
    assert designer.inner_text(f'{face} [data-wname]') == 'Several'
    assert sent_fonts(changed_preview(designer, lambda: designer.click(f'{face} [data-fwidth="condensed"]'))) == [
        'Sample Sans,Condensed Bold', 'Sample Sans,Condensed']
    designer.focus(f'{face} [data-fweight]')
    assert sent_fonts(changed_preview(designer, lambda: designer.keyboard.press('Home'))) == ['Sample Sans,Condensed Light'] * 2
    designer.keyboard.press('Escape')
    assert designer.inner_text('#adv [data-afont] span') == 'Sample Sans'
    # Lines in other families
    set_font(designer, 'DejaVu Sans,Book')
    assert designer.inner_text('#adv [data-afont] span') == 'Several fonts'


def test_the_menu_is_a_sheet_on_the_phone(designer):
    designer.set_viewport_size({'width': 390, 'height': 844})
    type_label(designer, 'Shelf 3')
    open_menu(designer)
    box = designer.locator('.font-menu').bounding_box()
    assert box['x'] == 0 and box['width'] == 390 and round(box['y'] + box['height']) == 844
    # The keyboard of the phone waits for a tap on the search field
    assert not designer.evaluate('() => document.activeElement.matches(".font-menu input")')
    assert pick(designer, 'Sample Sans') == ['Sample Sans,Bold']


def test_new_labels_start_with_a_family_of_the_menu(designer):
    open_settings(designer, 'labels')
    designer.click('#setFont')
    designer.wait_for_selector('.font-menu')
    # The server's default first, then the families; a family only
    assert listed(designer) == ["The server's default: DejaVu Serif"] + FAMILIES
    assert designer.locator('.font-menu [data-fweight], .font-menu [data-fwidth]').count() == 0
    with designer.expect_response('**/api/settings/labels') as answer:
        designer.click('.font-menu .fm-o[data-family="DejaVu Sans Mono"]')
    assert answer.value.json()['settings']['font_family'] == 'DejaVu Sans Mono'
    assert designer.locator('.font-menu').count() == 0
    designer.wait_for_function('() => document.querySelector("#setFont").textContent === "DejaVu Sans Mono"')
    back_to_the_editor(designer)
    type_label(designer, 'Flour', 'sugar')
    assert designer.evaluate('() => labelEditor.S.edit.design.text.map(l => l.font)') == ['DejaVu Sans,Mono / Bold', 'DejaVu Sans,Mono / Book']
