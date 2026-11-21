"""Rolls with several labels to a page in the label designer: one label is
designed, the stage tells when the copies leave the last page not full."""
from tests.browser.helpers import with_tape
from tests.browser.test_editor import editor, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_gallery import open_saved, save_new
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
