"""Rolls with several labels to a page in the label designer: one label is
designed, the stage tells when the copies leave the last page not full."""
from tests.browser.helpers import png, with_tape
from tests.browser.test_editor import editor, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_gallery import open_saved, save_new
from tests.browser.test_media import pdf
from tests.browser.test_stage import status_is

# A Niimbot B1 with a roll of 15 x 8 mm labels, 2 across and 3 along a page
B1 = {'model': 'Niimbot/B1', 'name': 'Labels', 'media_width': 30, 'media_length': 24}


def on_roll(page, **printer):
    status_is(page, with_tape({**B1, **printer}))
    type_label(page, 'Spice')


def set_copies(page, n):
    page.fill('#copies', str(n))
    page.dispatch_event('#copies', 'input')


def note_shown(page) -> bool:
    """Whether the note shows once the label is drawn. Copies changed later
    do not mark the stage busy, wait for the note itself then."""
    wait_for_preview(page)
    return page.is_visible('#sheetNote')


def test_a_page_roll_shows_one_label(editor):  # noqa: F811
    on_roll(editor)
    assert editor.inner_text('#dimW b') == '15 mm'
    assert editor.inner_text('#dimL b') == '8 mm'
    assert '30 x 24 mm, 6 labels of 15 x 8' in editor.get_attribute('#printerChip', 'title')


def test_the_last_page_not_full_is_told(editor):  # noqa: F811
    on_roll(editor)
    assert note_shown(editor)
    assert 'The last page is not full.' in editor.inner_text('#sheetNote')
    assert editor.locator('#sheetNote .sheet i').count() == 6
    assert editor.locator('#sheetNote .sheet i.on').count() == 1


def test_fill_it_fills_the_page(editor):  # noqa: F811
    on_roll(editor)
    editor.click('#sheetNote [data-fill]')
    editor.wait_for_selector('#sheetNote', state='hidden')
    assert editor.input_value('#copies') == '6'
    set_copies(editor, 7)
    editor.wait_for_selector('#sheetNote:not([hidden])')
    assert editor.locator('#sheetNote .sheet i.on').count() == 1


def test_lying_labels_drawn_one_above_the_other(editor):  # noqa: F811
    on_roll(editor, media_width=28, media_length=35)
    assert note_shown(editor)
    first, second = editor.evaluate("() => [...document.querySelectorAll('#sheetNote .sheet i')].map(i => { const r = i.getBoundingClientRect(); return [r.top, r.bottom]; })")
    assert second[0] >= first[1]


def test_single_label_rolls_tell_nothing(editor):  # noqa: F811
    on_roll(editor, media_width=50, media_length=30)
    assert not note_shown(editor)
    asked = []
    editor.on('request', lambda r: asked.append(r.url) if r.url.endswith('/api/preview') else None)
    set_copies(editor, 5)
    # Nothing to wait for: no request is the point
    editor.wait_for_timeout(500)
    assert not editor.is_visible('#sheetNote')
    assert asked == []


def test_templates_tell_the_last_page(editor):  # noqa: F811
    status_is(editor, with_tape(B1))
    save_new(editor, 'Spice tin', 'Paprika')
    open_saved(editor, 'Spice tin')
    editor.wait_for_selector('#tplSheet:not([hidden])')
    assert 'The last page is not full.' in editor.inner_text('#tplSheet')
    assert editor.locator('#tplSheet .sheet i.on').count() == 1
    editor.click('#tplSheet [data-fill]')
    editor.wait_for_selector('#tplSheet', state='hidden')
    assert editor.input_value('#tplCopies') == '6'
    editor.click('#tplDetail [data-cp="1"]')
    editor.wait_for_selector('#tplSheet:not([hidden])')
    assert editor.locator('#tplSheet .sheet i.on').count() == 1


def test_progress_counts_pages(editor):  # noqa: F811
    status_is(editor, with_tape({**B1, 'job': {'labels': 2, 'label': 1, 'unit': 'page'}}))
    assert 'Page 1 of 2' in editor.get_attribute('#printerChip', 'title')


def on_count(page, n):
    page.wait_for_function('n => document.querySelectorAll("#sheetNote .sheet i.on").length === n', arg=n)


def test_files_and_pages_of_a_pdf_count_on_the_page(editor):  # noqa: F811
    on_roll(editor)
    editor.set_input_files('#imageFile', files=[
        {'name': 'two.pdf', 'mimeType': 'application/pdf', 'buffer': pdf((200, 100), (100, 200))},
        {'name': 'one.png', 'mimeType': 'image/png', 'buffer': png()}])
    editor.wait_for_selector('#ltb [data-unitof]:text-is("1 of 2")')
    editor.click('#ltb [data-more]')
    editor.click('.media-menu [data-allpages]')
    editor.wait_for_selector('#ltb [data-unitof]:text-is("1 of 3")')
    on_count(editor, 3)
    assert editor.get_attribute('#sheetNote [data-fill]', 'data-fill') == '2'
    # Another page or file shown, the copies print the same
    editor.keyboard.press('Escape')
    editor.click('#ltb [data-unit="1"]')
    editor.wait_for_selector('#ltb [data-unitof]:text-is("2 of 3")')
    wait_for_preview(editor)
    assert editor.locator('#sheetNote .sheet i.on').count() == 3
    editor.click('#ltb [data-unit="1"]')
    editor.wait_for_selector('#ltb [data-unitof]:text-is("3 of 3")')
    wait_for_preview(editor)
    assert editor.locator('#sheetNote .sheet i.on').count() == 3


HOLD_THREE = """() => {
    const original = window.fetch;
    window.fetch = (url, options = {}) => {
        const body = options.body;
        if (!window.letGo && String(url).endsWith('/api/preview') && body && body.get && body.get('print_count') === '3') {
            return new Promise(resolve => {
                window.letGo = () => resolve(original(url, options).then(r => { setTimeout(() => window.answered = true, 0); return r; }));
            });
        }
        return original(url, options);
    };
}"""


def test_templates_show_the_note_of_the_copies_asked_last(editor):  # noqa: F811
    status_is(editor, with_tape(B1))
    save_new(editor, 'Spice tin', 'Paprika')
    # The preview of 3 copies is held back until it is let go
    editor.evaluate(HOLD_THREE)
    open_saved(editor, 'Spice tin')
    editor.fill('#tplCopies', '3')
    editor.dispatch_event('#tplCopies', 'input')
    editor.wait_for_function('() => !!window.letGo')
    editor.fill('#tplCopies', '4')
    editor.dispatch_event('#tplCopies', 'input')
    editor.wait_for_function('() => document.querySelectorAll("#tplSheet .sheet i.on").length === 4')
    # The answer for 3 copies comes late and must not replace it
    editor.evaluate('() => window.letGo()')
    editor.wait_for_function('() => window.answered')
    editor.wait_for_timeout(200)
    assert editor.locator('#tplSheet .sheet i.on').count() == 4
