"""Templates of the label designer at /labeldesigner/: the text typed into
the templates, fill in and print, use from anywhere, tags, removing a saved
label; and the editor's New, Undo, Redo, also after a visit, and Save."""
import io
import json
import os
import random
import time

import pytest
from PIL import Image

from tests.browser.helpers import png
from tests.browser.test_demo_printer import MODEL, designer  # noqa: F401
from tests.browser.test_editor import changed_preview, editor, sent, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_settings import back_to_the_editor, open_settings


def save_new(page, name, *lines):
    type_label(page, *lines)
    page.click('#save')
    page.fill('#saveName', name)
    page.click('#saveGo')
    page.wait_for_function('n => labelEditor.S.saved.some(s => s.title === n)', arg=name)
    page.wait_for_selector('.dialog', state='detached')


def open_saved(page, title):
    page.click('#templates')
    page.click('.tpl-nav [data-cat="saved"]')
    page.click(f'.thumb:has-text("{title}")')
    page.wait_for_selector('#tplPrint')


def shown_width(page, group):
    """The width of the Templates showing the group, once the thumbnails in
    sight are drawn."""
    page.click(f'.tpl-nav [data-cat="{group}"]')
    page.wait_for_selector(f'.tpl-nav [data-cat="{group}"].on')
    page.wait_for_function("""() => { const g = document.querySelector('.tpl-grid').getBoundingClientRect();
        return [...document.querySelectorAll('.tpl-grid .thumb img')].filter(i => i.getBoundingClientRect().top < g.bottom)
            .every(i => i.complete && i.naturalWidth > 0); }""")
    return page.evaluate("() => document.querySelector('.dialog.tpl').getBoundingClientRect().width")


def test_the_templates_keep_their_width_for_every_group_and_tape(designer):  # noqa: F811
    # As wide as set, whatever the size of the thumbnails shown
    widths = set()
    for model, size in (('QL-800', '62'), ('Niimbot/B1', 'niimbot50x30')):
        if model != 'QL-800':
            open_settings(designer)
            designer.select_option(MODEL, model)
            designer.wait_for_function('m => labelEditor.S.printers[0].model === m', arg=model)
            designer.select_option('[data-card="0"] select[data-k="label_size"]', size)
            back_to_the_editor(designer)
        designer.wait_for_function('t => labelEditor.S.tape === t', arg=size)
        designer.click('#templates')
        widths |= {shown_width(designer, group) for group in ('all', 'workshop', 'kitchen', 'office', 'saved')}
        designer.click('.dialog [data-close]')
    assert widths == {1060}


def test_templates_replace_the_wizard(editor):  # noqa: F811
    assert editor.locator('#tabs [data-tab="wizard"], a[href*="#wizard"]').count() == 0
    editor.keyboard.press('Control+k')
    editor.wait_for_selector('.palette #palQ:focus')
    editor.keyboard.type('>new')
    editor.wait_for_selector('.res.cmd')
    assert editor.locator('.res.cmd b').first.inner_text() == 'New label'
    editor.fill('#palQ', '>wizard')
    editor.wait_for_selector('#palRes p.muted:has-text("Nothing found")')
    assert editor.locator('.res.cmd').count() == 0


def test_fill_in_and_print_without_leaving_the_gallery(editor):  # noqa: F811
    save_new(editor, 'Gallery jar', 'Jar [What]')
    open_saved(editor, 'Gallery jar')
    assert editor.inner_text('#tplPrint') == 'Fill in and print'
    editor.fill('#tplFields [data-blank="What"]', 'Peas')
    with editor.expect_request('**/api/print') as request:
        editor.press('#tplFields [data-blank="What"]', 'Enter')
    fields = sent(request.value)
    assert json.loads(fields['blanks'])['What'] == 'Peas'
    # Still in the gallery, the blank empty for the next one
    editor.wait_for_function("() => document.querySelector('#tplFields [data-blank=\"What\"]').value === ''")
    assert editor.locator('.tpl').is_visible()
    # Opened in the editor from there
    editor.click('#tplOpen')
    editor.wait_for_selector('.tpl', state='detached')
    assert editor.evaluate('() => labelEditor.S.edit.title') == 'Gallery jar'


def test_the_text_typed_goes_into_the_templates(editor):  # noqa: F811
    editor.click('#templates')
    editor.fill('#tplText', 'Chili')
    editor.wait_for_selector('.tpl-nav [data-cat="typed"].on')
    editor.wait_for_selector('.thumb:has-text("Big and simple")')
    editor.wait_for_function("() => document.querySelector('.tpl-grid .thumb img').src.startsWith('blob:')")
    editor.click('.thumb:has-text("Big and simple")')
    with editor.expect_request('**/api/print') as request:
        editor.click('#tplPrint')
    assert json.loads(sent(request.value)['text'])[0]['text'] == 'Chili'


def test_ctrl_enter_in_the_text_prints_the_first_look(editor):  # noqa: F811
    editor.click('#templates')
    editor.fill('#tplText', 'Tomato soup')
    editor.wait_for_selector('.thumb:has-text("Big and simple")')
    with editor.expect_request('**/api/print') as request:
        editor.press('#tplText', 'Control+Enter')
    text = json.loads(sent(request.value)['text'])
    assert [(line['text'], line['size']) for line in text] == [('Tomato soup', '84')]
    editor.wait_for_selector('#toast:has-text("Printed 1 label")')
    # The text stays for another look, the gallery open
    assert editor.input_value('#tplText') == 'Tomato soup'
    # A blank is asked for first
    editor.fill('#tplText', 'Box [Number]')
    editor.wait_for_selector('.thumb:has-text("Big and simple")')
    editor.press('#tplText', 'Control+Enter')
    editor.wait_for_selector('#tplFields input[data-blank="Number"]:focus', timeout=5000)
    assert editor.evaluate('() => labelEditor.S.history.length') == 1


def test_tags_and_removing_a_saved_label_with_undo(editor):  # noqa: F811
    save_new(editor, 'Gallery spices', 'Spices')
    open_saved(editor, 'Gallery spices')
    editor.click('#tplTags')
    editor.fill('#tplTagsIn', 'Kitchen')
    editor.click('#tplTagsForm [type="submit"]')
    editor.wait_for_selector('.tpl-nav [data-cat="tag:Kitchen"]')
    editor.click('.thumb:has-text("Gallery spices")')
    editor.click('#tplRemove')
    editor.wait_for_function('() => !labelEditor.S.saved.some(s => s.title === "Gallery spices")')
    editor.click('#toast .tbtn:text-is("Undo")')
    editor.wait_for_function('() => labelEditor.S.saved.some(s => s.title === "Gallery spices" && s.tags.includes("Kitchen"))')


def test_use_from_anywhere_and_the_links_to_the_gallery(editor, server):  # noqa: F811
    save_new(editor, 'Gallery box', 'Box [What]')
    name = editor.evaluate('() => labelEditor.S.edit.origin.id')
    open_saved(editor, 'Gallery box')
    editor.click('#tplLinks')
    link = editor.input_value('#tplLink')
    assert link.endswith(f'/labeldesigner/#fill/{name}?What=Chili')
    editor.wait_for_function("() => document.querySelector('#tplSticker').src.startsWith('blob:')")
    # The link opens the label in the gallery, its blank filled in
    editor.goto(f'{server}/labeldesigner/#fill/{name}?What=Beans')
    editor.wait_for_selector('#tplFields [data-blank="What"]')
    assert editor.input_value('#tplFields [data-blank="What"]') == 'Beans'
    editor.goto(f'{server}/labeldesigner/#templates/all')
    editor.wait_for_selector('.tpl-nav [data-cat="all"].on')
    editor.keyboard.press('Escape')
    editor.wait_for_function("() => location.hash === '#edit'")


def test_new_asks_before_a_label_with_changes_goes(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#newLabel')
    editor.wait_for_selector('.dialog:has-text("Start a new label?")')
    editor.click('.dialog [data-close]')
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].text') == 'Shelf 3'
    # Saved first, then the new label starts
    editor.click('#newLabel')
    editor.click('#newSave')
    editor.fill('#saveName', 'Gallery shelf')
    editor.click('#saveGo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "" && labelEditor.S.saved.some(s => s.title === "Gallery shelf")')
    # Discarded
    type_label(editor, 'Shelf 4')
    editor.click('#newLabel')
    editor.click('#newDiscard')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === ""')
    # A saved label without changes goes without a question
    open_saved(editor, 'Gallery shelf')
    editor.click('#tplOpen')
    editor.wait_for_function('() => labelEditor.S.edit.title === "Gallery shelf"')
    wait_for_preview(editor)
    editor.click('#newLabel')
    editor.wait_for_function('() => labelEditor.S.edit.title === "New label"')
    assert editor.locator('.dialog').count() == 0


def test_undo_and_redo_are_in_the_field_bar_and_save_has_its_icon(editor):  # noqa: F811
    assert editor.locator('.top #undo, .top #redo').count() == 0
    assert editor.locator('.cbar #undo').count() == 1 and editor.locator('.cbar #redo').count() == 1
    type_label(editor, 'Shelf 3')
    editor.wait_for_function("() => document.querySelector('#undo .n') && !document.querySelector('#undo .n').hidden")
    editor.click('#undo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === ""')
    editor.click('#redo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Shelf 3"')
    assert editor.get_attribute('#save use', 'href') == '#i-save'
    assert editor.locator('use[href="#i-bookmark"]').count() == 0


def search_and_open(page, title):
    page.keyboard.press('Control+k')
    page.wait_for_selector('.palette #palQ:focus')
    page.keyboard.type(title)
    page.click(f'#palRes .res:has-text("{title}")')


def open_another(page, way, title):
    """Opens the label title, a saved one, the way given."""
    if way == 'search':
        search_and_open(page, title)
    elif way == 'templates':
        open_saved(page, title)
        page.click('#tplOpen')
    elif way == 'printed':
        page.click('#tabs [data-tab="settings"]')
        page.click('.set-nav [data-part="printed"]')
        page.click(f'#setHist .set-hrow:has-text("{title}") [data-open]')
    else:
        page.click('#tabs [data-tab="settings"]')
        page.click('.set-nav [data-part="connections"]')
        page.click('[data-tpl] >> nth=0')


@pytest.mark.parametrize('way', ['search', 'templates', 'printed', 'connections'])
def test_opening_another_label_asks_before_a_label_with_changes_goes(editor, way):  # noqa: F811
    title = f'Gallery other {way}'
    save_new(editor, title, 'Other label')
    if way == 'printed':
        with editor.expect_request('**/api/print'):
            editor.click('#print')
        editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    editor.click('#newLabel')
    type_label(editor, 'My unsaved work')
    open_another(editor, way, title)
    editor.wait_for_selector('.dialog[role="alertdialog"]:has-text("has changes that are not saved")')
    editor.click('.dialog [data-close]')
    assert editor.evaluate('() => [labelEditor.S.edit.title, labelEditor.S.edit.design.text[0].text]') == ['New label', 'My unsaved work']
    open_another(editor, way, title)
    editor.click('#newDiscard')
    editor.wait_for_function('() => labelEditor.S.edit.title !== "New label"')
    editor.wait_for_selector('#field .ln')
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].text') != 'My unsaved work'


def test_opening_another_label_saves_the_changes_first_when_asked(editor):  # noqa: F811
    save_new(editor, 'Gallery first', 'First label')
    editor.click('#newLabel')
    type_label(editor, 'Second label')
    search_and_open(editor, 'Gallery first')
    editor.click('#newSave')
    editor.fill('#saveName', 'Gallery second')
    editor.click('#saveGo')
    editor.wait_for_function('() => labelEditor.S.edit.title === "Gallery first" && labelEditor.S.saved.some(s => s.title === "Gallery second")')
    # Unchanged, the next one opens without a question
    search_and_open(editor, 'Gallery second')
    editor.wait_for_function('() => labelEditor.S.edit.title === "Gallery second"')
    assert editor.locator('.dialog').count() == 0


def saved_elsewhere(page, server, app, text, replace, later):
    """Saves "Gallery fresh" as another browser does, later than before."""
    page.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                      data=json.dumps({'name': 'Gallery_fresh.json', 'title': 'Gallery fresh', 'label_size': '62', 'replace': replace,
                                       'text': [{'text': text, 'size': '40', 'font': 'DejaVu Sans,Book', 'align': 'center'}]}))
    # The server tells changes by the second they were saved in
    when = time.time() + later
    os.utime(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Gallery_fresh.json'), (when, when))


def server_text(page, server):
    text = page.request.get(server + '/labeldesigner/api/repository/load?name=Gallery_fresh.json').json()['text']
    return (json.loads(text) if isinstance(text, str) else text)[0]['text']


def test_a_saved_label_opens_as_saved_last_and_is_not_replaced_unasked(editor, server, app):  # noqa: F811
    saved_elsewhere(editor, server, app, 'Version one', False, 0)
    search_and_open(editor, 'Gallery fresh')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Version one"')
    # Saved again in another browser: opened as it is now, from the search
    # and from Templates
    saved_elsewhere(editor, server, app, 'Version two', True, 100)
    editor.click('#newLabel')
    search_and_open(editor, 'Gallery fresh')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Version two"')
    saved_elsewhere(editor, server, app, 'Version three', True, 200)
    editor.click('#newLabel')
    open_saved(editor, 'Gallery fresh')
    editor.click('#tplOpen')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Version three"')
    # Changed here while another browser saved it again: saving asks
    editor.click('#field .ln >> nth=0')
    editor.keyboard.press('End')
    editor.keyboard.type(' here')
    saved_elsewhere(editor, server, app, 'Version four', True, 300)
    editor.click('#save')
    editor.wait_for_selector('.dialog:has-text("Replace the newer version?")')
    editor.click('.dialog [data-close]')
    assert server_text(editor, server) == 'Version four'
    editor.click('#save')
    editor.click('#saveOver')
    editor.wait_for_selector('#toast:has-text("Saved \\"Gallery fresh\\"")')
    assert server_text(editor, server) == 'Version three here'
    # Saved here last, it is saved again without a question
    editor.keyboard.type('!')
    with editor.expect_request('**/api/repository/save'):
        editor.click('#save')
    assert editor.locator('.dialog').count() == 0


def test_a_server_that_cannot_be_reached_is_said_so(editor):  # noqa: F811
    unreachable = 'The label designer could not be reached'
    save_new(editor, 'Gallery offline', 'Offline')
    editor.click('#newLabel')
    type_label(editor, 'Not saved')
    editor.route('**/api/repository/save', lambda route: route.abort())
    editor.click('#save')
    editor.fill('#saveName', 'Gallery not saved')
    editor.click('#saveGo')
    editor.wait_for_selector(f'#saveErr:has-text("{unreachable}")')
    editor.keyboard.press('Escape')
    editor.route('**/api/repository/load*', lambda route: route.abort())
    search_and_open(editor, 'Gallery offline')
    editor.wait_for_selector(f'#toast:has-text("{unreachable}")')
    editor.unroute('**/api/repository/load*')
    open_saved(editor, 'Gallery offline')
    editor.route('**/api/repository/tags', lambda route: route.abort())
    editor.click('#tplTags')
    editor.click('#tplTagsForm [type="submit"]')
    editor.wait_for_selector(f'#toast:has-text("{unreachable}")')
    editor.route('**/api/repository/delete', lambda route: route.abort())
    editor.click('#tplRemove')
    editor.wait_for_selector(f'#toast:has-text("{unreachable}")')
    # Removed, its Undo is not reached
    editor.unroute('**/api/repository/delete')
    editor.click('#tplRemove')
    editor.wait_for_selector('#toast:has-text("Removed")')
    editor.click('#toast .tbtn:text-is("Undo")')
    editor.wait_for_selector(f'#toast:has-text("{unreachable}")')


def undo_count(page):
    """The steps the Undo button counts, 0 where it shows none."""
    return page.evaluate("() => { const n = document.querySelector('#undo .n'); return n && !n.hidden ? +n.textContent : 0; }")


def reload(page):
    page.reload()
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


def test_the_steps_to_undo_are_kept_for_the_next_visit(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    editor.click('#ltb [data-underline]')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].underline === true')
    editor.click('#ltb [data-align]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent === 3")
    reload(editor)
    assert undo_count(editor) == 3
    for _ in range(3):
        editor.click('#undo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === ""')
    assert editor.is_disabled('#undo')
    for _ in range(3):
        editor.click('#redo')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Shelf 3" && labelEditor.S.edit.design.text[0].underline === true')
    assert editor.is_disabled('#redo')
    # Undone steps can be redone after a visit too
    editor.click('#undo')
    reload(editor)
    assert editor.is_enabled('#redo') and undo_count(editor) == 2
    # Another label has its own steps, those of the one before go with it
    editor.click('#newLabel')
    editor.click('#newDiscard')
    assert undo_count(editor) == 0
    reload(editor)
    assert undo_count(editor) == 0 and editor.is_disabled('#redo')


def test_the_steps_of_a_label_with_an_image_keep_the_image_once(editor):  # noqa: F811
    type_label(editor, 'Jar')
    changed_preview(editor, lambda: editor.set_input_files('#imageFile', files=[{'name': 'jar.png', 'mimeType': 'image/png', 'buffer': png(160, 90)}]))
    editor.click('#picks .pk[data-i="0"]')
    editor.click('#ltb [data-underline]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent === 3")
    reload(editor)
    assert undo_count(editor) == 3
    data = editor.evaluate('() => labelEditor.S.edit.design.image.data')
    # Kept once, by the draft, not with each step
    assert editor.evaluate('data => Object.keys(localStorage).reduce((n, k) => n + localStorage.getItem(k).split(data).length - 1, 0)', data) == 1
    editor.click('#undo')
    editor.click('#undo')
    editor.wait_for_function('() => !labelEditor.S.edit.design.image')
    # Gone from the draft, its steps keep it
    reload(editor)
    editor.click('#redo')
    editor.wait_for_function('data => labelEditor.S.edit.design.image && labelEditor.S.edit.design.image.data === data', arg=data)


def test_the_last_steps_are_kept_up_to_a_size(editor):  # noqa: F811
    type_label(editor, 'Shelf 3')
    for _ in range(55):
        editor.click('#ltb [data-align]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent === 56")
    reload(editor)
    assert undo_count(editor) == 50
    # Large steps: fewer of them, the newest
    editor.evaluate("() => { labelEditor.S.edit.design.text[0].text = 'x'.repeat(40000); }")
    for _ in range(12):
        editor.click('#ltb [data-align]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent === 63")
    reload(editor)
    assert 0 < undo_count(editor) < 12
    editor.click('#undo')
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].text.length') == 40000


def test_steps_the_browser_has_no_room_for_stay_until_the_page_is_left(editor):  # noqa: F811
    editor.evaluate("""() => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
            if (key.includes('history')) throw new DOMException('The quota has been exceeded.', 'QuotaExceededError');
            return original.call(this, key, value);
        };
    }""")
    type_label(editor, 'Shelf 3')
    editor.click('#ltb [data-underline]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent === 2")
    editor.click('#undo')
    editor.wait_for_function('() => !labelEditor.S.edit.design.text[0].underline')
    assert editor.locator('#toast:visible').count() == 0
    reload(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].text') == 'Shelf 3'
    assert undo_count(editor) == 0


def print_jar(page, what, copies=1):
    """Prints "Jar [What]" with What filled in, copies of it."""
    type_label(page, 'Jar [What]')
    page.fill('#fill [data-blank="What"]', what)
    for _ in range(copies - 1):
        page.click('.printbtn [data-step="1"]')
    with page.expect_request('**/api/print'):
        page.press('#fill [data-blank="What"]', 'Enter')
    page.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")


def test_labels_printed_here_are_a_group_of_templates(editor):  # noqa: F811
    print_jar(editor, 'Peas', copies=2)
    editor.click('#newLabel')
    editor.click('#newDiscard')
    editor.click('#templates')
    editor.wait_for_selector('.tpl-nav [data-cat="printed"]:has-text("Printed")')
    editor.click('.tpl-nav [data-cat="printed"]')
    editor.click('.tpl-grid .thumb:has-text("Peas")')
    # With its values and copies, printed again as it was
    editor.wait_for_selector('#tplPrint:has-text("Print again")')
    assert editor.input_value('#tplFields [data-blank="What"]') == 'Peas'
    assert editor.input_value('#tplCopies') == '2'
    with editor.expect_request('**/api/print') as request:
        editor.click('#tplPrint')
    fields = sent(request.value)
    assert json.loads(fields['blanks'])['What'] == 'Peas' and fields['print_count'] == '2'
    assert json.loads(fields['text'])[0]['text'] == 'Jar {{blank:What}}'
    editor.wait_for_function("() => labelEditor.S.history.length === 2 && labelEditor.S.history[0].state === 'printed'")
    # And opened in the editor with them
    editor.click('#tplOpen')
    editor.wait_for_selector('.tpl', state='detached')
    assert editor.evaluate('() => [labelEditor.S.edit.design.text[0].text, labelEditor.S.edit.values.What]') == ['Jar {{blank:What}}', 'Peas']
    editor.goto(editor.url.split('#')[0] + '#templates/printed')
    editor.wait_for_selector('.tpl-nav [data-cat="printed"].on')


def test_a_label_printed_before_is_printed_again_from_the_search(editor):  # noqa: F811
    type_label(editor, 'Jar [What] [Where]')
    editor.fill('#fill [data-blank="What"]', 'Beans')
    # Saved labels come first in the search, those of other tests must not
    # match it
    editor.fill('#fill [data-blank="Where"]', 'Larder')
    for _ in range(2):
        editor.click('.printbtn [data-step="1"]')
    with editor.expect_request('**/api/print'):
        editor.press('#fill [data-blank="Where"]', 'Enter')
    editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    editor.click('#newLabel')
    editor.click('#newDiscard')
    editor.keyboard.press('Control+k')
    editor.wait_for_selector('.palette #palQ:focus')
    # Found by a value printed besides the one it is named by
    editor.keyboard.type('larder')
    row = editor.locator('#palRes .res.on:has-text("Beans")')
    assert 'Larder' in row.inner_text() and '3 x' in row.inner_text()
    assert 'Enter print again' in editor.inner_text('.pal-foot')
    with editor.expect_request('**/api/print') as request:
        editor.keyboard.press('Enter')
    fields = sent(request.value)
    assert json.loads(fields['blanks']) == {'What': 'Beans', 'Where': 'Larder'} and fields['print_count'] == '3'
    editor.wait_for_selector('#toast:has-text("Printed 3 labels: Beans")')
    # The editor keeps the label it had
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].text') == ''
    # Enter opens what is not printed before
    editor.keyboard.press('Control+k')
    editor.keyboard.type('todo')
    editor.wait_for_selector('#palRes .res:has-text("TODO List")')
    assert 'Enter open' in editor.inner_text('.pal-foot') and 'print again' not in editor.inner_text('.pal-foot')


def test_values_typed_for_a_label_printed_before_leave_it_as_printed(editor):  # noqa: F811
    print_jar(editor, 'Peas', copies=2)
    editor.click('#newLabel')
    editor.click('#newDiscard')
    editor.click('#templates')
    editor.click('.tpl-nav [data-cat="printed"]')
    editor.click('.tpl-grid .thumb:has-text("Peas")')
    editor.fill('#tplFields [data-blank="What"]', 'Soup')
    editor.keyboard.press('Escape')
    editor.wait_for_selector('.tpl', state='detached')
    assert editor.evaluate('() => labelEditor.S.history[0].values') == {'What': 'Peas'}


def held(page, key, times=5):
    """A key held down: the browser repeats it until it is let go."""
    for _ in range(times):
        page.keyboard.down(key)
    page.keyboard.up(key)


def prints(page) -> list:
    """The print requests the page sends from now on."""
    sent_now = []
    page.on('request', lambda r: sent_now.append(r) if r.url.endswith('/api/print') else None)
    return sent_now


@pytest.mark.parametrize('where', ['fill in', 'ctrl enter', 'templates', 'typed text'])
def test_enter_held_down_prints_once(editor, where):  # noqa: F811
    type_label(editor, 'Jar [What]')
    editor.fill('#fill [data-blank="What"]', 'Peas')
    if where == 'templates':
        editor.click('#save')
        editor.fill('#saveName', f'Gallery held {where}')
        editor.click('#saveGo')
        editor.wait_for_selector('.dialog', state='detached')
        open_saved(editor, f'Gallery held {where}')
        editor.fill('#tplFields [data-blank="What"]', 'Beans')
        editor.focus('#tplFields [data-blank="What"]')
    elif where == 'typed text':
        editor.click('#templates')
        editor.fill('#tplText', 'Chili')
        editor.wait_for_selector('.thumb:has-text("Big and simple")')
        editor.focus('#tplText')
    elif where == 'fill in':
        editor.focus('#fill [data-blank="What"]')
    else:
        editor.click('#field')
    sent_now = prints(editor)
    if where in ('ctrl enter', 'typed text'):
        editor.keyboard.down('Control')
        held(editor, 'Enter')
        editor.keyboard.up('Control')
    else:
        held(editor, 'Enter')
    editor.wait_for_function("() => labelEditor.S.history.length && labelEditor.S.history.every(h => h.state !== 'printing')")
    editor.wait_for_timeout(300)
    assert len(sent_now) == 1


# The browser's storage as full as a small quota makes it, all keys together
SMALL_QUOTA = """(limit => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
        let used = 0;
        for (let i = 0; i < this.length; i++) { const k = this.key(i); if (k !== key) used += k.length + this.getItem(k).length; }
        if (used + key.length + String(value).length > limit) throw new DOMException('The quota has been exceeded.', 'QuotaExceededError');
        return original.call(this, key, value);
    };
})(560000)"""


def noise(size, seed) -> bytes:
    """A PNG that does not compress: about size * size bytes."""
    generator = random.Random(seed)
    out = io.BytesIO()
    Image.frombytes('L', (size, size), bytes(generator.getrandbits(8) for _ in range(size * size))).save(out, 'PNG')
    return out.getvalue()


def with_image(page, data, name):
    page.set_input_files('#imageFile', files=[{'name': name, 'mimeType': 'image/png', 'buffer': data}])
    page.wait_for_function('n => labelEditor.S.edit.design.image && labelEditor.S.edit.design.image.name === n', arg=name)
    wait_for_preview(page)
    # Kept for the next visit after a pause
    page.wait_for_timeout(600)


def small_quota(page):
    page.add_init_script(SMALL_QUOTA)
    reload(page)


def test_the_steps_make_room_for_the_image_of_the_label(editor):  # noqa: F811
    small_quota(editor)
    type_label(editor, 'Photo')
    for i, name in enumerate(('a.png', 'b.png', 'c.png')):
        with_image(editor, noise(300, i), name)
    # Too large besides the images of the steps, alone it fits
    with_image(editor, noise(500, 9), 'd.png')
    editor.click('#field .ln >> nth=0')
    editor.keyboard.press('End')
    editor.keyboard.type(' now')
    editor.wait_for_timeout(900)
    steps = undo_count(editor)
    reload(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.image && labelEditor.S.edit.design.image.name') == 'd.png'
    # No step without a change: each undo changes the label
    assert 0 < undo_count(editor) <= steps
    shown = editor.evaluate('() => JSON.stringify(labelEditor.S.edit.design)')
    editor.click('#undo')
    editor.wait_for_function('s => JSON.stringify(labelEditor.S.edit.design) !== s', arg=shown)


def test_the_steps_make_room_for_the_labels_printed(editor):  # noqa: F811
    small_quota(editor)
    type_label(editor, 'Photo')
    for i, name in enumerate(('a.png', 'b.png', 'c.png')):
        with_image(editor, noise(300, i), name)
    for _ in range(2):
        with editor.expect_request('**/api/print'):
            editor.click('#print')
        editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    # Both printed labels can be printed again with their image
    assert editor.evaluate('() => labelEditor.S.history.map(h => !!h.design.image && !h.noImage)') == [True, True]
    reload(editor)
    assert editor.evaluate('() => labelEditor.S.history.map(h => !!h.design.image && !h.noImage)') == [True, True]
    assert editor.evaluate('() => labelEditor.S.edit.design.image && labelEditor.S.edit.design.image.name') == 'c.png'


def test_images_another_tab_kept_are_kept_again(editor):  # noqa: F811
    other = editor.context.new_page()
    other.goto(editor.url.split('#')[0])
    other.wait_for_selector('#field .ln')
    wait_for_preview(other)
    type_label(editor, 'Jar')
    with_image(editor, png(120, 60), 'a.png')
    with_image(other, png(100, 50), 'b.png')
    # The draft is the one changed last, with its own image
    editor.click('#field .ln >> nth=0')
    editor.keyboard.press('End')
    editor.keyboard.type(' now')
    editor.wait_for_timeout(900)
    other.close()
    reload(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.image && labelEditor.S.edit.design.image.name') == 'a.png'


@pytest.mark.parametrize('way', ['search', 'printed'])
def test_a_page_started_in_the_settings_asks_before_another_label_replaces_the_draft(editor, way):  # noqa: F811
    if way == 'printed':
        print_jar(editor, 'Peas')
        editor.click('#field .ln >> nth=0')
    else:
        type_label(editor, 'My unsaved draft')
    editor.click('#ltb [data-underline]')
    editor.wait_for_function("() => labelEditor.S.edit.design.text[0].underline === true")
    editor.wait_for_timeout(600)
    steps = undo_count(editor)
    editor.evaluate("() => { location.hash = 'settings/printed'; }")
    editor.wait_for_selector('#setPart[data-part="printed"]')
    editor.reload()
    editor.wait_for_selector('#setPart[data-part="printed"]')
    if way == 'search':
        editor.keyboard.press('Control+k')
        editor.wait_for_selector('.palette #palQ:focus')
        editor.keyboard.type('todo')
        editor.wait_for_selector('#palRes .res:has-text("TODO List")')
        editor.keyboard.press('Enter')
    else:
        editor.click('#setHist [data-open] >> nth=0')
    editor.wait_for_selector('.dialog[role="alertdialog"]:has-text("has changes that are not saved")')
    editor.click('.dialog [data-close]')
    editor.click('#tabs [data-tab="edit"]')
    editor.wait_for_selector('#field .ln')
    wait_for_preview(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.text[0].underline') is True
    assert undo_count(editor) == steps
