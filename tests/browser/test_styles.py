"""The label designer at /labeldesigner/: italic and underline, colors for
the tape in the printer, the details of a frame, long lines that keep their
size and the variables of the bracket menu. The font menu, bold and the
styles of a family are in test_fonts."""
import json

import pytest

from tests.browser.helpers import with_tape
from tests.browser.test_editor import changed_preview, editor, type_label, wait_for_preview  # noqa: F401


def printer_is(page, model, tape, name='Test'):
    """Answer the status queries with a simulator of the model, its tape
    set by hand, and load the page again."""
    printer = with_tape({'errors': [], 'path': 'simulation', 'model': model, 'name': name, 'media_width': None, 'media_length': 0,
                         'settings': {'label_size': tape}})
    page.route('**/api/printer_status*', lambda route: route.fulfill(json={**printer, 'printers': [printer], 'selected': 'simulation'}))
    page.reload()
    page.wait_for_selector('#field .ln')
    page.wait_for_function('tape => labelEditor.S.tape === tape', arg=tape)
    wait_for_preview(page)


def lines(request) -> list:
    return json.loads(request['text'])


def first_line(page, action) -> dict:
    """The first line after an action, once its preview is shown. A state
    shown before comes from the cache of the previews, without a request."""
    action()
    page.wait_for_function("() => !document.querySelector('#labelbox').classList.contains('busy')")
    return page.evaluate('() => labelEditor.S.edit.design.text[0]')


# The fonts of the test image: DejaVu Sans with Book, Bold and its Mono
# styles, which have Oblique, and DejaVu Serif with Book and Bold

def test_italic_and_underline_reach_the_preview(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#ltb [data-font]')
    changed_preview(editor, lambda: editor.click('.font-menu .fm-o[data-family="DejaVu Sans Mono"]'))
    editor.keyboard.press('Escape')
    shown = editor.get_attribute('#labelImg', 'src')
    line = lines(changed_preview(editor, lambda: editor.click('#ltb [data-italic]')))[0]
    assert line['font'] == 'DejaVu Sans,Mono / Bold Oblique'
    assert 'underline' not in line
    line = lines(changed_preview(editor, lambda: editor.click('#ltb [data-underline]')))[0]
    assert line['underline'] is True
    assert editor.get_attribute('#ltb [data-underline]', 'aria-pressed') == 'true'
    editor.wait_for_function('src => document.querySelector("#labelImg").src !== src', arg=shown)
    # Off again, the request leaves it out, as labels saved before have it
    assert 'underline' not in first_line(editor, lambda: editor.click('#ltb [data-underline]'))


@pytest.mark.parametrize('model, tape, colors', [
    pytest.param('VC-500W', 'vc25', 'palette', id='color tape'),
    pytest.param('QL-800', '62red', 'two', id='black and red'),
    pytest.param('QL-800', '62', 'none', id='black'),
])
def test_the_color_swatch_follows_the_tape(editor, model, tape, colors):  # noqa: F811
    printer_is(editor, model, tape)
    type_label(editor, 'Shelf 3')
    swatch = editor.locator('#ltb [data-lcolor]')
    if colors == 'none':
        assert swatch.count() == 0
        # A color the tape cannot print shows, grayed, with the reason the
        # preview tells
        editor.evaluate('() => { labelEditor.S.edit.design.text[0].color = "red"; }')
        changed_preview(editor, lambda: (editor.click('#field .ln >> nth=0'), editor.keyboard.press('End'), editor.keyboard.type('!')))
        editor.wait_for_selector('#ltb [data-lcolor].off')
        assert 'prints black' in swatch.get_attribute('title')
        return
    swatch.click()
    editor.wait_for_selector('.color-menu')
    if colors == 'palette':
        assert editor.locator('.color-menu [data-c]').count() > 6
        assert editor.locator('.color-menu input[type=color]').count() == 1
        line = lines(changed_preview(editor, lambda: editor.click('.color-menu [data-c="#2563eb"]')))[0]
        assert line['color'] == '#2563eb'
    else:
        assert editor.eval_on_selector_all('.color-menu [data-c]', 'bs => bs.map(b => b.dataset.c)') == ['black', 'red']
        line = lines(changed_preview(editor, lambda: editor.click('.color-menu [data-c="red"]')))[0]
        assert line['color'] == 'red'
    # The code and the frame have their colors there too
    editor.click('#addCode')
    editor.click('#ltb [data-ccolor]')
    request = changed_preview(editor, lambda: editor.click('.color-menu [data-c="red"]'))
    assert request['print_color'] == 'red'
    editor.click('#advBtn')
    changed_preview(editor, lambda: editor.click('#adv [data-frame="square"]'))
    editor.click('#adv [data-fcolor]')
    request = changed_preview(editor, lambda: editor.click('.color-menu [data-c="red"]'))
    assert request['border_color'] == 'red'


# Previews wait until window.letGo() is called
HOLD = """() => {
    const original = window.fetch;
    window.fetch = (url, options) => String(url).endsWith('/api/preview')
        ? new Promise(resolve => { window.letGo = () => resolve(original(url, options)); }) : original(url, options);
}"""


def code_with_preview_held(page):
    """The code added on red tape, its preview not yet shown."""
    printer_is(page, 'QL-800', '62red')
    type_label(page, 'Shelf 3')
    page.evaluate(HOLD)
    page.click('#addCode')
    page.wait_for_function('() => !!window.letGo')


def toolbar_top(page) -> float:
    return page.evaluate("() => document.querySelector('#ltb').getBoundingClientRect().top")


def test_a_press_on_the_toolbar_is_not_lost_when_the_preview_moves_it(editor):  # noqa: F811
    code_with_preview_held(editor)
    top = toolbar_top(editor)
    box = editor.locator('#ltb [data-ccolor]').bounding_box()
    editor.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
    editor.mouse.down()
    editor.evaluate('() => letGo()')
    wait_for_preview(editor)
    editor.mouse.up()
    editor.wait_for_selector('.color-menu')
    # Let go, the toolbar moves where the new preview needs it
    editor.wait_for_function("top => document.querySelector('#ltb').getBoundingClientRect().top !== top", arg=top)


def test_a_menu_of_the_toolbar_moves_along_with_it(editor):  # noqa: F811
    code_with_preview_held(editor)
    top = toolbar_top(editor)
    editor.click('#ltb [data-ccolor]')
    editor.evaluate('() => letGo()')
    wait_for_preview(editor)
    assert toolbar_top(editor) != top
    gap = editor.evaluate("""() => document.querySelector('.color-menu').getBoundingClientRect().top
        - document.querySelector('#ltb [data-ccolor]').getBoundingClientRect().bottom""")
    assert gap == pytest.approx(8, abs=1)


def test_how_a_color_prints_is_what_the_server_tells(editor):  # noqa: F811
    # The rule is the server's, the designer says what the preview told
    printer_is(editor, 'QL-800', '62red')

    def told_red(route):
        response = route.fetch()
        told = json.loads(response.headers.get('x-label-colors', '{}'))
        route.fulfill(response=response, headers={**response.headers, 'x-label-colors': json.dumps({c: 'red' for c in told})})
    editor.route('**/api/preview', told_red)
    type_label(editor, 'Shelf 3')
    editor.evaluate('() => { labelEditor.S.edit.design.text[0].color = "#2563eb"; }')
    changed_preview(editor, lambda: (editor.click('#field .ln >> nth=0'), editor.keyboard.press('End'), editor.keyboard.type('!')))
    editor.wait_for_selector('#ltb [data-lcolor].off')
    assert editor.get_attribute('#ltb [data-lcolor]', 'title') == 'This tape prints black and red only, the line prints red.'


def test_the_details_of_a_frame(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#advBtn')
    assert [b.inner_text() for b in editor.locator('#adv [data-frame]').all()] == ['None', 'Square', 'Rounded']
    # Custom without a frame starts a square one with its details
    request = changed_preview(editor, lambda: editor.click('#adv [data-fcustom]'))
    assert request['border_thickness'] == '5' and request['border_roundness'] == '0'
    assert editor.is_disabled('#adv [data-fk="border_roundness"]')
    request = changed_preview(editor, lambda: (editor.fill('#adv [data-fk="border_thickness"]', '1'), editor.press('#adv [data-fk="border_thickness"]', 'Tab')))
    assert request['border_thickness'] == '12'
    # The margins grow so that the text stays inside the frame
    assert int(request['margin_left']) >= 12 + 20 + 25
    changed_preview(editor, lambda: editor.click('#adv [data-frame="rounded"]'))
    assert editor.is_enabled('#adv [data-fk="border_roundness"]')
    assert editor.input_value('#adv [data-fk="border_thickness"]') == '1'


def test_long_lines_keep_their_size(editor):  # noqa: F811
    type_label(editor, 'A rather long line of text for the label, longer than the tape is wide')
    editor.click('#advBtn')
    request = changed_preview(editor, lambda: editor.click('#adv [data-long="keep"]'))
    assert request['text_fit'] == 'none' and request['text_wrap'] == '0'
    assert 'on' in editor.get_attribute('#adv [data-long="keep"]', 'class')
    request = changed_preview(editor, lambda: editor.click('#adv [data-long="wrap"]'))
    assert request['text_wrap'] == '1'
    # Shrink again is the label as it started, its preview is known
    editor.click('#adv [data-long="shrink"]')
    assert editor.evaluate('() => [labelEditor.S.edit.design.text_fit, labelEditor.S.edit.design.text_wrap]') == ['shrink', '0']


def test_variables_dates_and_symbols_of_the_bracket_menu(editor):  # noqa: F811
    type_label(editor, 'From ')
    editor.keyboard.type('[')
    editor.click('.pop-menu button:has-text("Variable")')
    editor.click('.chip-pop details.help summary')
    assert 'LABEL_TEMPLATE_ENV_VARS' in editor.inner_text('.chip-pop .help-body')
    editor.click('.chip-pop [data-var="5"]')
    editor.fill('#cpEnv', 'SITE')
    editor.press('#cpEnv', 'Enter')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "From {{env:SITE}}"')
    editor.keyboard.type(' ')
    editor.keyboard.type('[')
    editor.click('.pop-menu button:has-text("Date")')
    editor.click('#field .chip.date')
    editor.fill('#cpFormat', '%A')
    request = changed_preview(editor, lambda: editor.press('#cpFormat', 'Enter'))
    assert lines(request)[0]['text'] == 'From {{env:SITE}} {{datetime:%A}}'
    editor.click('.chip-pop [data-codes]')
    editor.wait_for_selector('.codes td:has-text("%A")')
    editor.keyboard.press('Escape')
    # A symbol set shows its first ones, all of them on request
    editor.click('#field', position={'x': 640, 'y': 24})
    editor.keyboard.type('[')
    editor.click('.pop-menu button:has-text("Symbol")')
    editor.wait_for_selector('.chip-pop [data-all="mdi"]')
    first = editor.locator('.chip-pop .sym.mdi').count()
    editor.click('.chip-pop [data-all="mdi"]')
    editor.wait_for_function('n => document.querySelectorAll(".chip-pop .sym.mdi").length > n', arg=first)
