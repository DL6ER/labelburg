"""Details of the label designer at /labeldesigner/: copies belong to the
label, a line grows step by step, thumbnails of saved labels, Advanced next
to the label in a narrow window and after a visit to Settings, chips and the
keyboard, and switching printers."""
import contextlib
import os

import pytest

from app.labeldesigner import printer_settings
from tests.browser.helpers import with_tape
from tests.browser.test_editor import changed_preview, editor, sent, type_label, wait_for_preview  # noqa: F401


def text_of(page, line=0):
    return page.evaluate('i => labelEditor.S.edit.design.text[i].text', line)


def test_each_label_has_its_own_copies(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('.printbtn [data-step="1"]')
    editor.click('.printbtn [data-step="1"]')
    with editor.expect_request('**/api/print') as request:
        editor.click('#print')
    assert sent(request.value)['print_count'] == '3'
    editor.click('#newLabel')
    editor.click('#newDiscard')
    assert editor.input_value('#copies') == '1'
    type_label(editor, 'Shelf 4')
    with editor.expect_request('**/api/print') as request:
        editor.click('#print')
    assert sent(request.value)['print_count'] == '1'
    # A label opened from the templates starts with one copy
    editor.fill('#copies', '5')
    editor.click('#templates')
    editor.click('.tpl-nav [data-cat="saved"]')
    editor.click('.thumb:has-text("TODO List")')
    editor.click('#tplOpen')
    editor.click('#newDiscard')
    editor.wait_for_function('() => labelEditor.S.edit.title === "TODO List"')
    assert editor.input_value('#copies') == '1'


def test_a_line_grows_step_by_step(editor):  # noqa: F811
    type_label(editor, 'Hello')
    for _ in range(3):
        changed_preview(editor, lambda: editor.click('#ltb [data-size="1"]'))
        assert editor.is_enabled('#ltb [data-size="1"]')
    # Filled, the label decides: a larger size cannot make the line larger,
    # told once the steps should show
    editor.click('#advBtn')
    changed_preview(editor, lambda: editor.click('#adv [data-auto]'))
    for _ in range(6):
        if editor.is_disabled('#ltb [data-size="1"]'):
            break
        changed_preview(editor, lambda: editor.click('#ltb [data-size="1"]'))
    assert editor.is_disabled('#ltb [data-size="1"]')
    assert editor.get_attribute('#ltb [data-size="1"]', 'title') == 'Fills the label'


def test_a_line_drawn_small_grows_every_few_steps(editor):  # noqa: F811
    # The first line is shrunk to fit, the second is drawn at about half its
    # size and changes only every second step
    type_label(editor, 'Use by 2026-12-01, keep refrigerated', 'Milk')
    editor.wait_for_function('() => (labelEditor.editorUI.last.parts || []).some(p => p.index === 1)')
    first = editor.evaluate('() => labelEditor.editorUI.last.parts.find(p => p.index === 1).box')
    for _ in range(8):
        changed_preview(editor, lambda: editor.click('#ltb [data-size="1"]'))
        assert editor.is_enabled('#ltb [data-size="1"]')
    last = editor.evaluate('() => labelEditor.editorUI.last.parts.find(p => p.index === 1).box')
    assert last[2] - last[0] > first[2] - first[0]


def test_margins_none(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#advBtn')
    assert [b.inner_text() for b in editor.locator('#adv [data-margins]').all()] == ['None', 'Tight', 'Normal', 'Large']
    request = changed_preview(editor, lambda: editor.click('#adv [data-margins="none"]'))
    assert [request['margin_' + side] for side in ('top', 'bottom', 'left', 'right')] == ['0', '0', '0', '0']
    assert 'on' in editor.get_attribute('#adv [data-margins="none"]', 'class').split()


def test_margins_are_in_proportion_to_the_tape(editor):  # noqa: F811
    printers = [with_tape({'errors': [], 'path': 'simulation', 'model': model, 'name': name, 'media_width': None, 'media_length': 0,
                           'settings': {'label_size': size}}) for model, name, size in (('QL-1100', 'Kitchen', '62'), ('QL-800', 'Office', '29'))]
    editor.route('**/api/printer_status*', lambda route: route.fulfill(json={**printers[0], 'printers': printers, 'selected': 'simulation'}))
    editor.reload()
    editor.wait_for_selector('#field .ln')
    wait_for_preview(editor)
    type_label(editor, 'Pantry')
    margins = lambda request: [request['margin_' + side] for side in ('top', 'bottom', 'left', 'right')]  # noqa: E731
    editor.click('#advBtn')
    assert margins(changed_preview(editor, lambda: editor.click('#adv [data-margins="tight"]'))) == ['12', '12', '18', '18']
    # On the 29 mm tape of the other printer the label keeps tight margins, those of its width

    def choose():
        editor.click('#printerChip')
        editor.click('.pp [data-p="1"]')
        editor.keyboard.press('Escape')
    assert margins(changed_preview(editor, choose)) == ['5', '5', '8', '8']
    if not editor.is_visible('#adv [data-margins]'):
        editor.click('#advBtn')
    assert 'on' in editor.get_attribute('#adv [data-margins="tight"]', 'class').split()
    assert margins(changed_preview(editor, lambda: editor.click('#adv [data-margins="large"]'))) == ['21', '21', '31', '31']


def test_thumbnails_of_saved_labels_do_not_ask_the_printer(editor):  # noqa: F811
    asked = []
    editor.on('request', lambda r: asked.append(r.url) if '/api/repository/preview' in r.url else None)
    editor.click('#templates')
    editor.click('.tpl-nav [data-cat="saved"]')
    editor.wait_for_function("() => [...document.querySelectorAll('.tpl-grid .thumb img')].every(i => i.src.startsWith('blob:') && i.complete)")
    editor.keyboard.press('Escape')
    editor.keyboard.press('Control+k')
    editor.keyboard.type('todo')
    editor.wait_for_function("() => { const i = document.querySelector('#palRes .res img'); return i && i.src.startsWith('blob:'); }")
    assert asked == []


def test_advanced_is_next_to_the_label_in_a_narrow_window(editor):  # noqa: F811
    editor.set_viewport_size({'width': 1024, 'height': 700})
    type_label(editor, 'Shelf 3', 'spices')
    editor.click('#advBtn')
    editor.wait_for_selector('#adv [data-auto]')
    # The label moves aside, then takes the room left
    editor.wait_for_function("() => getComputedStyle(document.querySelector('#center')).marginRight === '360px'")
    editor.wait_for_timeout(400)
    rect = lambda sel: editor.eval_on_selector(sel, 'e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }')  # noqa: E731
    card = rect('#adv')
    for sel in ('#paper', '.composer', '#ltb', '#dimW'):
        assert rect(sel)[2] <= card[0], sel


def test_advanced_stays_open_after_a_visit_to_settings(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#advBtn')
    editor.wait_for_selector('#adv [data-auto]')
    editor.click('#tabs [data-tab="settings"]')
    editor.wait_for_selector('#setPart')
    editor.click('#tabs [data-tab="edit"]')
    editor.wait_for_selector('#adv [data-auto]')
    editor.click('#advBtn')
    editor.wait_for_selector('#adv', state='hidden')


@pytest.mark.parametrize('typed, presses, left', [
    pytest.param('Use by [today]', 1, 'Use by ', id='at the end'),
    pytest.param('A [Name] B', 3, 'A ', id='in the middle'),
])
def test_backspace_removes_the_chip_before_the_caret(editor, typed, presses, left):  # noqa: F811
    type_label(editor, typed)
    editor.wait_for_selector('#field .chip')
    editor.keyboard.press('End')
    for _ in range(presses):
        editor.keyboard.press('Backspace')
    editor.wait_for_function('left => labelEditor.S.edit.design.text[0].text === left', arg=left)
    assert editor.locator('#field .chip').count() == 0


def test_typing_goes_on_after_a_chip_from_the_bracket_menu(editor):  # noqa: F811
    type_label(editor, 'Packed ')
    editor.keyboard.type('[')
    editor.click('.pop-menu button:has-text("Date")')
    editor.wait_for_selector('#field .chip.date')
    assert editor.locator('.chip-pop').count() == 0
    editor.keyboard.type(' and more')
    editor.wait_for_function('() => /^Packed \\{\\{datetime:[^}]+\\}\\} and more$/.test(labelEditor.S.edit.design.text[0].text)')
    # A blank without a name typed asks for one at once
    editor.keyboard.type(' [')
    editor.click('.pop-menu button:has-text("Blank")')
    editor.wait_for_selector('.chip-pop #cpName')
    assert editor.evaluate('() => document.activeElement.id') == 'cpName'


def test_chips_open_from_the_keyboard(editor):  # noqa: F811
    type_label(editor, 'Box [What]')
    editor.wait_for_selector('#field .chip.blank')
    assert editor.get_attribute('#field .chip.blank', 'role') == 'button'
    editor.keyboard.press('Tab')
    assert editor.evaluate('() => document.activeElement.classList.contains("chip")')
    editor.keyboard.press('Enter')
    editor.wait_for_selector('.chip-pop #cpName')
    assert editor.input_value('.chip-pop #cpName') == 'What'
    # Its options take the focus, Esc gives it back to the chip
    assert editor.evaluate('() => document.activeElement.id') == 'cpName'
    editor.keyboard.press('Escape')
    editor.wait_for_selector('.chip-pop', state='detached')
    assert editor.evaluate('() => document.activeElement.classList.contains("chip")')


def test_a_printer_chosen_places_the_label_also_when_closed_at_once(editor, app):  # noqa: F811
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    printers = [with_tape({'errors': [], 'path': 'simulation', 'model': model, 'name': name, 'media_width': None, 'media_length': 0,
                           'settings': {'label_size': size}}) for model, name, size in (('QL-1100', 'Kitchen', '62'), ('QL-800', 'Office', '29'))]
    editor.route('**/api/printer_status*', lambda route: route.fulfill(json={**printers[0], 'printers': printers, 'selected': 'simulation'}))
    editor.reload()
    editor.wait_for_selector('#field .ln')
    wait_for_preview(editor)
    type_label(editor, 'Pantry')
    editor.click('#printerChip')
    editor.wait_for_selector('.pp [data-p="1"]')
    # Chosen and closed in one go, before anything else happens
    editor.evaluate("() => { document.querySelector('.pp [data-p=\"1\"]').click(); document.querySelector('#layer').click(); }")
    editor.wait_for_function('() => labelEditor.S.edit.design.label_size === "29"')
    assert editor.inner_text('#printerChip b') == 'Office'


def test_the_printer_chosen_stays_chosen_when_it_does_not_answer(editor):  # noqa: F811
    office = with_tape({'errors': [], 'path': 'simulation', 'model': 'QL-800', 'name': 'Office', 'media_width': None, 'media_length': 0, 'settings': {}})
    color = with_tape({'errors': [], 'path': 'tcp://192.0.2.20', 'model': 'VC-500W', 'name': 'Color', 'media_width': 25, 'media_length': 0, 'settings': {}})
    silent = with_tape({**color, 'errors': ['The printer does not answer'], 'status_type': 'Error', 'media_width': None})
    answers = {'next': [office, color]}

    def answer(route):
        printers = answers['next']
        route.fulfill(json={**printers[0], 'printers': printers, 'selected': 'simulation'})
    editor.route('**/api/printer_status*', answer)
    editor.reload()
    editor.wait_for_selector('#field .ln')
    assert editor.inner_text('#printerChip b') == 'Office'
    editor.click('#printerChip')
    editor.click('.pp [data-p="1"]')
    editor.keyboard.press('Escape')
    editor.click('#newLabel')
    type_label(editor, 'Jam')
    assert editor.evaluate('() => labelEditor.S.edit.design.label_size') == 'vc25'

    def poll(printers):
        answers['next'] = printers
        with editor.expect_response('**/api/printer_status*'):
            editor.evaluate("() => dispatchEvent(new Event('visibilitychange'))")
        editor.wait_for_timeout(200)
        wait_for_preview(editor)
    # Left out of an answer, then not answering: it stays chosen, with its tape
    for printers in ([office], [office, silent]):
        poll(printers)
        assert editor.inner_text('#printerChip b') == 'Color'
        assert 'bad' in editor.get_attribute('#printerChip .d', 'class').split()
        assert editor.evaluate('() => [labelEditor.S.tape, labelEditor.S.edit.design.label_size]') == ['vc25', 'vc25']
        assert editor.locator('#toast:has-text("labels print on")').count() == 0
    # Printing tries the printer chosen and says when it does not answer
    editor.route('**/api/print', lambda route: route.fulfill(status=500, json={'success': False, 'message': 'The printer does not answer'}))
    with editor.expect_request('**/api/print') as request:
        editor.click('#print')
    assert sent(request.value)['printer'] == 'tcp://192.0.2.20'
    editor.wait_for_selector('.dialog:has-text("Not printed")')
    editor.keyboard.press('Escape')
    # After a reload too, also while it is still left out
    answers['next'] = [office]
    editor.reload()
    editor.wait_for_selector('#field .ln')
    editor.wait_for_function('() => labelEditor.S.printer && labelEditor.S.printer.name === "Color"')
    poll([office, color])
    assert editor.inner_text('#printerChip b') == 'Color'
    assert 'bad' not in editor.get_attribute('#printerChip .d', 'class').split()
