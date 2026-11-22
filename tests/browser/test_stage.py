"""The stage of the label designer at /labeldesigner/: how the label
leaves the printer, its length as cut, the large preview, the tape line, the
blanks to fill in, renaming a label, the steps to undo, and printing on
another printer than the one chosen."""
import json

import pytest

from tests.browser.helpers import with_tape
from tests.browser.test_editor import changed_preview, editor, sent, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_settings import designer, open_settings, printers_are  # noqa: F401


def status_is(page, printer):
    """Answer the status queries with printer as given, and load the page again."""
    printer = {'errors': [], 'path': 'simulation', 'media_width': None, 'media_length': 0, 'settings': {}, **printer}
    page.route('**/api/printer_status*', lambda route: route.fulfill(json={**printer, 'printers': [printer], 'selected': printer['path']}))
    page.reload()
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


RECT = "sel => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"


def rect(page, sel):
    return page.evaluate(RECT, sel)


def rects(page, *sels):
    """The boxes of sels at once, once the stage stands still."""
    wait_for_preview(page)
    return page.evaluate('sels => sels.map(sel => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })', list(sels))


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_how_the_label_leaves_the_printer(editor):  # noqa: F811
    type_label(editor, 'Shelf 3', 'spices')
    # The printer with its name below the label, which comes out of it
    paper, printer = rects(editor, '#paper', '#slot')
    assert printer[1] < paper[3] < printer[3]
    # The demo printer with the model it prints as
    assert (editor.inner_text('#printerChip b'), editor.inner_text('#slot .model')) == ('Demo printer', 'Demo Brother QL-800')
    assert editor.is_visible('#cutmark')
    # Nothing else on the stage: how copies are cut is in Advanced
    assert editor.locator('#cuts, #tapecap, #flow').count() == 0
    bar, width = rects(editor, '#ltb', '#dimW')
    assert bar[3] <= width[1]
    editor.click('#advBtn')
    assert 'Cut after each label' in editor.inner_text('#cutShow')
    assert editor.locator('#cutShow .cut-strip .lab').count() == 3
    # The length as cut: the one set for the label
    editor.click('#adv [data-len="fixed"]')
    changed_preview(editor, lambda: (editor.fill('#adv [data-length]', '50'), editor.press('#adv [data-length]', 'Enter')))
    assert editor.inner_text('#dimL b') == '50 mm'
    # Cut after the last label: the labels between are not cut
    editor.click('#adv [data-cutchange]')
    editor.select_option('#adv [data-cut]', 'last')
    editor.wait_for_function("() => document.querySelector('#cutShow').innerText.includes('Cut after the last label')")
    assert editor.locator('#cutShow .cm.none').count() == 2


def test_the_bottom_leaving_first_and_the_large_preview(editor):  # noqa: F811
    status_is(editor, with_tape({'model': 'QL-800', 'name': 'Office', 'settings': {'leaves_first': 'bottom'}}))
    type_label(editor, 'Shelf 3')
    assert 'from-top' in editor.get_attribute('#center', 'class').split()
    # The printer above the label, the toolbar above the printer
    paper, printer = rects(editor, '#paper', '#slot')
    assert printer[1] < paper[1] < printer[3]
    bar, width = rects(editor, '#ltb', '#dimW')
    assert bar[3] <= printer[1]
    # The width of the tape below the label, clear of the cut there
    assert width[1] >= paper[3]
    width = editor.evaluate("() => document.querySelector('#labelImg').getBoundingClientRect().width")
    editor.click('#zoom')
    editor.wait_for_function('w => document.querySelector("#labelImg").getBoundingClientRect().width > w', arg=width)
    assert editor.get_attribute('#zoom', 'aria-pressed') == 'true'
    # Kept for the next visit
    editor.reload()
    editor.wait_for_selector('#field .ln')
    wait_for_preview(editor)
    assert editor.get_attribute('#zoom', 'aria-pressed') == 'true'
    editor.click('#zoom')


def test_a_tape_the_printer_does_not_tell_is_noted_with_the_fits(editor):  # noqa: F811
    status_is(editor, with_tape({'model': 'QL-1100', 'name': 'Office'}))
    type_label(editor, 'Shelf 3')
    assert 'Office does not say which tape it has' in editor.inner_text('#tapeNote')
    assert editor.inner_text('#tapeChange') == 'Set the tape'
    editor.click('#tapeChange')
    editor.wait_for_selector('#setPart[data-part="printers"]')
    editor.click('#tabs [data-tab="edit"]')
    editor.wait_for_selector('#field .ln')
    # A printer that measures its labels names them
    status_is(editor, {'model': 'Zebra/ZPL', 'name': 'Zebra', 'tape': {
        'id': 'zpl102x152', 'known': False, 'assumed': True, 'manual': False, 'fits': [], 'moves': [], 'place': {},
        'candidates': ['zpl102x152', 'zpl100x150'], 'unknown': '102 x 152 mm', 'red_unknown': False}})
    editor.wait_for_selector('#tapeChange')
    assert 'Zebra measures labels 102 x 152 mm' in editor.inner_text('#tapeNote')
    assert editor.inner_text('#tapeChange') == 'Choose which ones'


def test_the_tape_in_the_printer_is_told_in_its_list(editor):  # noqa: F811
    status_is(editor, with_tape({'model': 'VC-500W', 'name': 'Color', 'media_width': 25, 'tape_remaining_mm': 2200, 'tape_length_mm': 5000}))
    type_label(editor, 'Jam')
    # Reported, the stage says nothing about the tape
    assert editor.locator('#tapeNote').is_hidden()
    editor.click('#printerChip')
    row = editor.inner_text('.pp [data-p="0"]')
    assert '25 mm color tape in the printer' in row and '2.2 m of 5.0 m left' in row


def test_the_picked_part_is_marked_off_the_print(editor):  # noqa: F811
    type_label(editor, 'Shelf 3', 'spices')
    editor.click('#picks .pk[data-i="1"]')
    # Dashes the browser draws and moves, no ring around the part, also
    # where it is focused from the keyboard
    style = editor.eval_on_selector('#picks .pk.on', '''b => { b.focus(); const s = getComputedStyle(b), r = getComputedStyle(b.querySelector('svg.ants rect'));
        return [s.outlineStyle, s.boxShadow, getComputedStyle(b.querySelector('svg.ants')).display, r.animationName, r.strokeDasharray, b.getAttribute('aria-pressed')]; }''')
    assert style == ['none', 'none', 'block', 'ants', '6px, 4px', 'true']
    assert editor.eval_on_selector('#picks .pk[data-i="0"] svg.ants', 'e => getComputedStyle(e).display') == 'none'
    # A few dots off the part, never on it
    k = editor.evaluate("() => document.querySelector('#labelImg').getBoundingClientRect().width / labelEditor.editorUI.last.w")
    box = editor.evaluate('() => labelEditor.editorUI.last.parts.find(p => p.part === "line" && p.index === 1).box')
    pick, paper = rect(editor, '#picks .pk.on svg.ants'), rect(editor, '#paper')
    assert pick[0] <= paper[0] + box[0] * k - 3 and pick[2] >= paper[0] + box[2] * k + 3
    assert pick[1] < paper[1] + box[1] * k and pick[3] > paper[1] + box[3] * k
    # Esc lets it go, as a click on the empty stage does
    editor.keyboard.press('Escape')
    assert editor.locator('#picks .pk.on').count() == 0 and editor.locator('#ltb').is_hidden()
    editor.click('#picks .pk[data-i="0"]')
    editor.wait_for_selector('#picks .pk.on[data-i="0"]')
    editor.click('#stage', position={'x': 40, 'y': 400})
    assert editor.locator('#picks .pk.on').count() == 0
    # Picked by the caret, with the keyboard: still no ring
    editor.click('#field')
    editor.keyboard.press('ArrowUp')
    editor.wait_for_selector('#picks .pk.on')
    assert editor.eval_on_selector('#picks .pk.on', 'b => { b.focus(); return [b.matches(":focus-visible"), getComputedStyle(b).outlineStyle]; }') == [True, 'none']
    # The dashes stand still for those who ask for less motion
    editor.emulate_media(reduced_motion='reduce')
    editor.click('#picks .pk[data-i="0"]')
    assert editor.eval_on_selector('#picks .pk.on svg.ants rect', 'r => getComputedStyle(r).animationName') == 'none'


INK_ROWS = '''() => { const img = document.querySelector('#labelImg'), c = document.createElement('canvas');
    c.width = img.naturalWidth; c.height = img.naturalHeight;
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    const d = g.getImageData(0, 0, c.width, c.height).data, rows = [];
    for (let y = 0; y < c.height; y++) { let ink = false; for (let x = 0; x < c.width && !ink; x++) ink = d[(y * c.width + x) * 4] < 128; rows.push(ink); }
    return rows; }'''


def ink_bands(page):
    """From where to where down the preview something is printed, in its dots."""
    bands, start = [], None
    for y, ink in enumerate(page.evaluate(INK_ROWS) + [False]):
        if ink and start is None:
            start = y
        elif not ink and start is not None:
            bands.append((start, y))
            start = None
    return bands


def test_the_picked_line_is_outlined_around_its_letters(editor):  # noqa: F811
    # Large and bold over a second line, as in the maintainer's ABC over DEF
    type_label(editor, 'ABC', 'DEF')
    editor.click('#picks .pk[data-i="0"]')
    if 'Bold' not in editor.evaluate('() => labelEditor.S.edit.design.text[0].font'):
        editor.click('#ltb [data-bold]')
        editor.wait_for_function('() => labelEditor.S.edit.design.text[0].font.includes("Bold")')
    editor.fill('#ltb [data-sizein]', '70')
    editor.press('#ltb [data-sizein]', 'Enter')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].size === "70"')
    editor.wait_for_selector('#picks .pk.on[data-i="0"]')
    paper, outline = rects(editor, '#paper', '#picks .pk.on svg.ants')
    k = editor.evaluate("() => document.querySelector('#labelImg').getBoundingClientRect().width / labelEditor.editorUI.last.w")
    box = editor.evaluate('() => labelEditor.editorUI.last.parts.find(p => p.part === "line" && p.index === 0).box')
    (abc_top, abc_bottom), (def_top, _) = ink_bands(editor)
    # The same few pixels around the letters on each side, off the next line
    gaps = [paper[0] + box[0] * k - outline[0], paper[1] + abc_top * k - outline[1],
            outline[2] - paper[0] - box[2] * k, outline[3] - paper[1] - abc_bottom * k]
    assert all(2 <= gap <= 6 for gap in gaps), gaps
    assert outline[3] < paper[1] + def_top * k - 2
    # A click on the top of DEF picks DEF, on the bottom of ABC picks ABC
    x = paper[0] + (box[0] + box[2]) / 2 * k
    editor.mouse.click(x, paper[1] + def_top * k + 1)
    editor.wait_for_selector('#picks .pk.on[data-i="1"]')
    editor.mouse.click(x, paper[1] + abc_bottom * k - 1)
    editor.wait_for_selector('#picks .pk.on[data-i="0"]')
    # Between them, each takes up to half the way, none reaches the other
    buttons = editor.evaluate('''() => [...document.querySelectorAll('#picks .pk')].map(b => { const r = b.getBoundingClientRect(); return [r.top, r.bottom]; })''')
    assert buttons[0][1] <= buttons[1][0] and buttons[0][1] < paper[1] + def_top * k and buttons[1][0] > paper[1] + abc_bottom * k


@pytest.mark.parametrize('scheme', ['light', 'dark'])
def test_the_field_is_marked_in_its_card_when_typing(editor, scheme):  # noqa: F811
    # Its card, rounded, in the color of what is picked, no square ring of its own
    editor.emulate_media(color_scheme=scheme)
    editor.click('#field')
    looks = '''() => { const f = document.querySelector('#field'), c = getComputedStyle(document.querySelector('.composer'));
        const probe = document.createElement('i');
        document.body.append(probe);
        const color = name => { probe.style.color = `var(${name})`; return getComputedStyle(probe).color; };
        const select = color('--select'), soft = color('--select-soft');
        probe.remove();
        return { focused: document.activeElement === f, ring: getComputedStyle(f).outlineStyle, border: c.borderTopColor, select, soft, radius: c.borderTopLeftRadius, shadow: c.boxShadow }; }'''
    on = editor.evaluate(looks)
    assert on['focused'] and on['ring'] == 'none'
    assert on['border'] == on['select'] and on['radius'] == '16px' and on['shadow'].startswith(f"{on['soft']} 0px 0px 0px 3px")
    editor.click('#stage', position={'x': 40, 'y': 400})
    off = editor.evaluate(looks)
    assert not off['focused'] and off['border'] != off['select']


def test_a_square_label_leaves_the_printer_the_way_its_text_runs(editor):  # noqa: F811
    status_is(editor, with_tape({'model': 'QL-800', 'name': 'Office', 'settings': {'label_size': '23x23'}}))
    type_label(editor, 'Shelf 3')
    assert 'side' not in editor.get_attribute('#center', 'class').split()
    editor.click('#advBtn')
    changed_preview(editor, lambda: editor.click('#adv [data-dir="along"]'))
    editor.wait_for_function("() => document.querySelector('#center').classList.contains('side')")


@pytest.mark.parametrize('model, settings, along, width_mm', [
    pytest.param('QL-800', {}, False, '62 mm', id='62 mm across'),
    pytest.param('QL-800', {}, True, '62 mm', id='62 mm along'),
    pytest.param('QL-1100', {'label_size': '104'}, False, '104 mm', id='104 mm across'),
    pytest.param('VC-500W', {'label_size': 'vc25'}, True, '25 mm', id='25 mm color along'),
    pytest.param('VC-500W', {'label_size': 'vc9'}, False, '9 mm', id='9 mm color across'),
])
def test_the_dimensions_span_the_label(editor, model, settings, along, width_mm):  # noqa: F811
    status_is(editor, with_tape({'model': model, 'name': 'Test', 'settings': settings}))
    type_label(editor, 'Shelf 3')
    if along:
        editor.click('#advBtn')
        changed_preview(editor, lambda: editor.click('#adv [data-dir="along"]'))
        editor.click('#adv [data-close-adv]')
        wait_for_preview(editor)
    sels = ['#paper', '#dimW i', '#dimL i', '#cutmark', '#cutmark .ic', '#dimW b', '#dimL b', '#dimW', '#dimL', '#vslot' if along else '#slot']
    r = dict(zip(sels, rects(editor, *sels)))
    paper = r['#paper']
    assert editor.inner_text('#dimW b') == width_mm
    # Across the tape its width runs across the screen, along it up and down;
    # each bar exactly as long as what it measures
    across, upDown = (r['#dimL i'], r['#dimW i']) if along else (r['#dimW i'], r['#dimL i'])
    assert abs(across[0] - paper[0]) <= 1 and abs(across[2] - paper[2]) <= 1
    assert abs(upDown[1] - paper[1]) <= 1 and abs(upDown[3] - paper[3]) <= 1
    # The values beside their bars, nothing written at the cut, the
    # scissors off the label
    for sel in ('#dimW b', '#dimL b'):
        assert not overlap(r[sel], r['#cutmark']), sel
        assert not overlap(r[sel], paper), sel
    assert editor.inner_text('#cutmark') == ''
    assert not overlap(r['#cutmark .ic'], paper)
    # The printer covers neither the label nor the bars
    printer = r[sels[-1]]
    for sel in ('#dimW', '#dimL'):
        assert not overlap(r[sel], printer), sel


def test_the_blanks_to_fill_in(editor):  # noqa: F811
    type_label(editor, 'Box [What] [Count]')
    editor.wait_for_selector('#fill [data-blank="What"]')
    assert editor.inner_text('#fillN') == '2 to fill in'
    request = changed_preview(editor, lambda: editor.fill('#fill [data-blank="What"]', 'Peas'))
    assert json.loads(request['blanks'])['What'] == 'Peas'
    assert editor.inner_text('#fillN') == '1 to fill in'
    # Enter prints, with the values filled in
    with editor.expect_response('**/api/print') as answer:
        editor.press('#fill [data-blank="What"]', 'Enter')
    assert json.loads(sent(answer.value.request)['blanks'])['What'] == 'Peas'
    # A saved label offers the values printed with it before
    editor.click('#save')
    editor.fill('#saveName', 'Freezer box')
    editor.press('#saveName', 'Enter')
    editor.wait_for_function('() => labelEditor.S.edit.origin && labelEditor.S.edit.origin.kind === "saved"')
    with editor.expect_response('**/api/print'):
        editor.press('#fill [data-blank="What"]', 'Enter')
    editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    editor.reload()
    editor.wait_for_selector('#fill .reuse [data-reuse="What"]')
    assert editor.inner_text('#fill .reuse') == 'Peas'


def test_rename_a_saved_label_and_replace_one_with_its_tags(editor):  # noqa: F811
    type_label(editor, 'Spices')
    editor.click('#save')
    editor.fill('#saveName', 'Spice shelf')
    editor.fill('#saveTags', 'Kitchen')
    editor.press('#saveName', 'Enter')
    editor.wait_for_function('() => labelEditor.S.edit.origin && labelEditor.S.edit.origin.kind === "saved"')
    name = editor.evaluate('() => labelEditor.S.edit.origin.id')
    assert editor.input_value('#title') == 'Spice shelf'
    # Renamed, it is saved again under its file name with the new title
    editor.fill('#title', 'Spice rack')
    with editor.expect_request('**/api/repository/save') as request:
        editor.click('#save')
    body = request.value.post_data_json
    assert (body['name'], body['title'], body['replace']) == (name, 'Spice rack', True)
    editor.wait_for_function('() => labelEditor.S.saved.some(s => s.title === "Spice rack")')
    # Another label of that name replaces it and keeps its tags
    editor.click('#newLabel')
    type_label(editor, 'Herbs')
    editor.click('#save')
    editor.fill('#saveName', 'Spice rack')
    editor.fill('#saveTags', 'Pantry')
    editor.press('#saveName', 'Enter')
    editor.wait_for_selector('#saveGo:text-is("Replace it")')
    assert editor.input_value('#saveTags') == 'Kitchen, Pantry'
    with editor.expect_request('**/api/repository/save') as request:
        editor.click('#saveGo')
    assert request.value.post_data_json['tags'] == ['Kitchen', 'Pantry']


def test_the_steps_to_undo_are_counted(editor):  # noqa: F811
    assert editor.locator('#undo .n').is_hidden()
    type_label(editor, 'Shelf 3')
    editor.click('#ltb [data-underline]')
    editor.click('#ltb [data-bold]')
    editor.wait_for_function("() => +document.querySelector('#undo .n').textContent >= 2")
    count = int(editor.inner_text('#undo .n'))
    editor.click('#undo')
    editor.wait_for_function('n => +document.querySelector("#undo .n").textContent === n - 1', arg=count)


def test_the_test_label_prints_on_its_printer(designer, server):  # noqa: F811
    printers_are(designer, server, {'path': 'simulation', 'model': 'QL-1100', 'name': 'Kitchen'}, {'path': 'simulation', 'model': 'QL-800', 'name': 'Office'})
    type_label(designer, 'Pantry')
    open_settings(designer)
    with designer.expect_request('**/api/print') as request:
        designer.click('[data-card="1"] [data-test]')
    assert sent(request.value)['model'] == 'QL-800'
    # The printer chosen stays chosen
    assert designer.evaluate('() => labelEditor.S.printer.name') == 'Kitchen'
    designer.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    assert designer.evaluate('() => labelEditor.S.history[0].printerName') == 'Office'


def test_printers_on_one_path_have_their_own_previews(designer, server):  # noqa: F811
    printers_are(designer, server, {'path': 'simulation', 'model': 'QL-1100', 'name': 'Kitchen'}, {'path': 'simulation', 'model': 'QL-800', 'name': 'Office'})
    type_label(designer, 'Pantry')
    designer.click('#printerChip')
    with designer.expect_request(lambda r: r.url.endswith('/api/preview') and r.method == 'POST') as request:
        designer.locator('.pp [data-p="1"]').click()
    assert sent(request.value)['model'] == 'QL-800'


COLOR = {'path': 'simulation', 'model': 'VC-500W', 'name': 'Color', 'media_width': 25, 'tape_remaining_mm': 2200, 'tape_length_mm': 5000}
OFFICE = {'path': 'simulation', 'model': 'QL-800', 'name': 'Office', 'media_width': 62, 'media_length': 0}


def switch_to(page, i):
    page.click('#printerChip')
    page.locator(f'.pp [data-p="{i}"]').click()
    page.wait_for_function('i => labelEditor.S.printer === labelEditor.S.printers[i]', arg=i)
    page.keyboard.press('Escape')
    page.wait_for_selector('.pp', state='detached')
    wait_for_preview(page)


def saved_size(page, save):
    """The tape the label is saved for when save is clicked."""
    with page.expect_request('**/api/repository/save') as request:
        page.click(save)
    return request.value.post_data_json['label_size']


def test_a_new_label_goes_on_the_tape_of_the_printer_switched_to(designer, server):  # noqa: F811
    # Typed on the color tape, it has no tape of its own
    printers_are(designer, server, COLOR, OFFICE)
    type_label(designer, 'Jam', 'June')
    assert designer.evaluate('() => labelEditor.S.edit.design.label_size') == 'vc25'
    switch_to(designer, 1)
    assert designer.evaluate('() => [labelEditor.S.edit.design.label_size, labelEditor.S.edit.made]') == ['62', None]
    assert designer.locator('#made').is_hidden()
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    assert sent(request.value)['label_size'] == '62'
    designer.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")
    designer.click('#save')
    designer.fill('#saveName', 'Jam on paper')
    assert saved_size(designer, '#saveGo') == '62'


def test_a_template_for_another_tape_is_made_for_it_until_changed(designer, server):  # noqa: F811
    printers_are(designer, server, OFFICE)
    designer.keyboard.press('Control+k')
    designer.keyboard.type('Labelburg sticker')
    designer.click('#palRes .res:has-text("Labelburg sticker")')
    designer.wait_for_function('() => labelEditor.S.edit.title === "Labelburg sticker"')
    wait_for_preview(designer)
    assert designer.inner_text('#made').startswith('Made for 50 mm color tape, shown as it prints on the 62 mm')
    assert designer.evaluate('() => labelEditor.S.edit.design.label_size') == '62'
    # Changed on the tape in the printer, it is saved for that tape
    designer.click('#field .ln >> nth=0')
    designer.keyboard.press('End')
    designer.keyboard.type('!')
    designer.wait_for_selector('#made', state='hidden')
    designer.click('#save')
    designer.fill('#saveName', 'Sticker on paper')
    assert saved_size(designer, '#saveGo') == '62'
    # Undone, it stays the label on this tape
    designer.wait_for_selector('.dialog', state='detached')
    designer.click('#undo')
    wait_for_preview(designer)
    assert designer.locator('#made').is_hidden()


def test_a_saved_label_unchanged_is_saved_for_its_own_tape(designer, server):  # noqa: F811
    printers_are(designer, server, COLOR, OFFICE)
    type_label(designer, 'Plum jam')
    designer.click('#save')
    designer.fill('#saveName', 'Plum jam on color')
    assert saved_size(designer, '#saveGo') == 'vc25'
    designer.wait_for_selector('.dialog', state='detached')
    switch_to(designer, 1)
    assert designer.inner_text('#made').startswith('Made for 25 mm')
    assert saved_size(designer, '#save') == 'vc25'
