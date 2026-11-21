"""The label designer at /labeldesigner/: typing a label and printing
it, the toolbar of a line picked on the label, the Advanced card and
opening a saved label."""
import contextlib
import json
import os
from urllib.parse import parse_qs

import pytest

from app.labeldesigner import printer_settings


def wait_for_preview(page):
    """Wait until the stage shows the newest preview and stands still."""
    page.wait_for_function("() => { const b = document.querySelector('#labelbox'); return b && !b.classList.contains('busy') && b.dataset.laid === '1'; }")


def sent(request) -> dict:
    """The fields of a preview request, each with its last value."""
    return {k: v[-1] for k, v in parse_qs(request.post_data or '', keep_blank_values=True).items()}


@pytest.fixture
def editor(browser, server, app):
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    context = browser.new_context(viewport={'width': 1326, 'height': 924}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    assert errors == []


def type_label(page, *lines):
    page.click('#field')
    for i, line in enumerate(lines):
        if i:
            page.keyboard.press('Enter')
        page.keyboard.type(line)
    page.wait_for_function('n => labelEditor.S.edit.design.text.length === n', arg=len(lines))
    wait_for_preview(page)


def changed_preview(page, action):
    """The preview request an action leads to."""
    with page.expect_request(lambda r: r.url.endswith('/api/preview') and r.method == 'POST') as request:
        action()
    wait_for_preview(page)
    return sent(request.value)


def test_type_a_label_and_print_it(editor):
    assert editor.is_disabled('#print')
    type_label(editor, 'Shelf 3', 'spices and herbs')
    assert 'Everything fits' in editor.inner_text('#fits')
    with editor.expect_response('**/api/print') as answer:
        editor.click('#print')
    assert answer.value.ok
    lines = json.loads(sent(answer.value.request)['text'])
    assert [line['text'] for line in lines] == ['Shelf 3', 'spices and herbs']
    # Enter at the end of a line starts a smaller, regular one
    assert int(lines[1]['size']) < int(lines[0]['size'])
    assert 'Bold' in lines[0]['font'] and 'Bold' not in lines[1]['font']
    editor.wait_for_function("() => labelEditor.S.history.length === 1 && labelEditor.S.history[0].state === 'printed'")
    editor.wait_for_selector('#toast:has-text("Printed 1 label")')


def test_a_date_and_a_blank_from_the_bracket_menu(editor):
    type_label(editor, 'Filled ')
    editor.keyboard.type('[')
    editor.wait_for_selector('.pop-menu')
    assert editor.inner_text('.pop-menu').split('\n')[0] == 'Blank'
    editor.keyboard.type('dat')
    editor.keyboard.press('ArrowDown')
    editor.keyboard.press('Enter')
    editor.wait_for_selector('#field .chip.date')
    editor.click('#field .chip.date')
    editor.wait_for_selector('.chip-pop')
    editor.click('.chip-pop [data-step="1"]')
    assert editor.inner_text('#field .chip.date') == 'today + 1 day'
    editor.click('#field')
    editor.keyboard.press('End')
    request = changed_preview(editor, lambda: editor.keyboard.type(' by [Name]'))
    assert json.loads(request['text'])[0]['text'].startswith('Filled {{datetime+1d:')
    editor.wait_for_function("() => labelEditor.S.edit.design.text[0].text.endsWith('by {{blank:Name}}')")
    assert editor.inner_text('#field .chip.blank') == 'Name'


def test_a_line_picked_on_the_label_changes_its_size_and_is_inverted(editor):
    type_label(editor, 'Shelf 3', 'spices')
    editor.wait_for_selector('#picks .pk[data-i="1"]')
    editor.click('#picks .pk[data-i="1"]')
    editor.wait_for_selector('#field .ln.on[data-i="1"]')
    size = int(editor.input_value('#ltb [data-sizein]'))
    lines = json.loads(changed_preview(editor, lambda: editor.click('#ltb [data-size="1"]'))['text'])
    assert int(lines[1]['size']) == size + 1
    assert int(lines[0]['size']) != size + 1
    assert editor.input_value('#ltb [data-sizein]') == str(size + 1)
    editor.click('#ltb [data-more]')
    lines = json.loads(changed_preview(editor, lambda: editor.click('.line-menu [data-act="invert"]'))['text'])
    assert lines[1]['inverted'] is True and lines[0]['inverted'] is False
    # Undone, the line is black on white again, the preview of before is
    # shown again without asking the server
    shown = editor.get_attribute('#labelImg', 'src')
    editor.click('#stage', position={'x': 40, 'y': 400})
    editor.click('#undo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[1].inverted === false')
    editor.wait_for_function('src => document.querySelector("#labelImg").src !== src', arg=shown)
    assert int(editor.evaluate('() => labelEditor.S.edit.design.text[1].size')) == size + 1


def test_advanced_changes_reach_the_preview(editor):
    type_label(editor, 'A rather long line of text for the label')
    editor.click('#advBtn')
    editor.wait_for_selector('#adv [data-auto]')
    request = changed_preview(editor, lambda: editor.click('#adv [data-long="wrap"]'))
    assert (request['text_wrap'], request['text_fit']) == ('1', 'shrink')
    assert changed_preview(editor, lambda: editor.click('#adv [data-auto]'))['text_fit'] == 'fill'
    # Filled lines are as wide as the endless tape, they do not wrap
    assert editor.is_disabled('#adv [data-long="wrap"]')
    request = changed_preview(editor, lambda: editor.click('#adv [data-long="shrink"]'))
    assert (request['text_wrap'], request['text_fit']) == ('0', 'fill')
    request = changed_preview(editor, lambda: editor.click('#adv [data-margins="roomy"]'))
    assert int(request['margin_left']) > 35
    # Each frame is named in words
    assert [editor.inner_text(f'#adv [data-frame="{frame}"]') for frame in ('none', 'square', 'rounded')] == ['None', 'Square', 'Rounded']
    request = changed_preview(editor, lambda: editor.click('#adv [data-frame="rounded"]'))
    assert int(request['border_thickness']) > 0 and int(request['border_roundness']) > 0
    request = changed_preview(editor, lambda: editor.click('#adv [data-dir="along"]'))
    assert request['orientation'] == 'rotated'
    assert changed_preview(editor, lambda: editor.click('#adv [data-mirror]'))['mirror'] == '1'


def test_open_a_saved_label(editor):
    editor.click('#templates')
    editor.click('.tpl-nav [data-cat="saved"]')
    # Its thumbnail was its preview, the editor shows it without asking again
    editor.click('.thumb:has-text("TODO List")')
    editor.click('#tplOpen')
    # The field shows it as soon as it is loaded
    editor.wait_for_function('() => labelEditor.S.edit.title === "TODO List"')
    assert editor.inner_text('#field').split('\n') == ['Print shipping label', 'Put items inside', 'Confirm shipping']
    wait_for_preview(editor)
    shown = editor.evaluate('() => labelEditor.S.edit.design')
    assert [line['text'] for line in shown['text']] == ['Print shipping label', 'Put items inside', 'Confirm shipping']
    assert editor.inner_text('#field').split('\n') == ['Print shipping label', 'Put items inside', 'Confirm shipping']
    # Saved before the text size had a mode: sizes as set, nothing shrinks
    assert shown['text_fit'] == 'none'
    assert editor.get_attribute('#labelImg', 'src').startswith('blob:')
    editor.click('#advBtn')
    assert [b.get_attribute('data-long') for b in editor.locator('#adv [data-long].on').all()] == ['keep']
    assert 'TODO List' in editor.get_attribute('#save', 'title')


def test_a_choice_set_in_the_options_of_its_blank_shows_in_fill_in(editor):
    type_label(editor, 'Size [Size]')
    editor.click('#field .chip.blank')
    editor.click('.chip-pop [data-kind="choice"]')
    editor.fill('#cpChoices', 'S\nM\nL')
    editor.locator('#cpChoices').blur()
    editor.wait_for_selector('.chip-pop select[data-value] option:text-is("L")', state='attached')
    assert editor.input_value('#fill select[data-blank="Size"]') == 'S'
    changed_preview(editor, lambda: editor.select_option('.chip-pop select[data-value]', 'L'))
    assert editor.input_value('#fill select[data-blank="Size"]') == 'L'


def test_the_details_shown_in_advanced_belong_to_the_label(editor):
    type_label(editor, 'Shelf 3')
    editor.click('#advBtn')
    editor.click('#adv [data-len="fixed"]')
    editor.click('#adv [data-custom]')
    editor.click('#adv [data-fcustom]')
    editor.wait_for_selector('#adv [data-fcustom].on')
    assert editor.locator('#adv [data-len="fixed"].on, #adv [data-custom].on').count() == 2
    editor.click('#newLabel')
    editor.click('#newDiscard')
    type_label(editor, 'Other')
    assert editor.locator('#adv [data-len="auto"].on').count() == 1 and editor.locator('#adv [data-length]').count() == 0
    assert editor.locator('#adv [data-custom].on, #adv [data-margin]').count() == 0
    changed_preview(editor, lambda: editor.click('#adv [data-frame="square"]'))
    assert editor.locator('#adv [data-fcustom].on').count() == 0



# Text a browser puts into the field outside its lines, e.g. Firefox after
# the field was drawn anew, with the caret after it
STRAY = """([where, text]) => {
    const field = document.querySelector('#field'), lines = field.querySelectorAll('.ln');
    const node = document.createTextNode(text);
    if (where === 'before') field.insertBefore(node, lines[0]); else field.appendChild(node);
    const range = document.createRange();
    range.setStart(node, text.length);
    range.collapse(true);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    field.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
}"""


@pytest.mark.parametrize('where, typed, text, expected', [('before', 'rig', ['Orig'], ['Oarig']), ('after', 'Origi', ['Origin'], ['Origina'])])
def test_text_outside_the_lines_goes_into_the_next_one(editor, where, typed, text, expected):  # noqa: F811
    type_label(editor, typed)
    editor.evaluate(STRAY, [where, 'O' if where == 'before' else 'n'])
    assert editor.evaluate('() => labelEditor.S.edit.design.text.map(l => l.text)') == text
    # The caret stays after it, typing goes on in the line
    editor.keyboard.type('a')
    editor.wait_for_function('t => JSON.stringify(labelEditor.S.edit.design.text.map(l => l.text)) === t', arg=json.dumps(expected))
