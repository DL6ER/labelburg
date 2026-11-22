"""Sheets of labels on an office printer in the label designer: one quiet
line under the label, the dialog "Label sheet", the question where a sheet
partly used goes on, the printer's card in the Settings. Nothing of it shows
with a label printer."""
import json
import os
from urllib.parse import parse_qs

from app.labeldesigner import printer_settings
from tests.browser.test_editor import type_label, wait_for_preview
from tests.browser.test_gallery import open_saved, save_new
from tests.browser.test_settings import designer, open_settings, printers_are  # noqa: F401

OFFICE = {'path': 'simulation', 'model': 'Other/Office', 'name': 'Office'}


def office(page, server, app, continue_at=None, **settings):
    """The page with an office printer of these settings kept on the server."""
    client = app.test_client()
    for name, value in settings.items():
        answer = client.post('/labeldesigner/api/settings', data={'printer': 'simulation', 'model': 'Other/Office', name: value})
        assert answer.status_code == 200, answer.get_json()
    if continue_at:
        path = os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE)
        with open(path, encoding='utf-8') as f:
            data = printer_settings.parsed(f.read())
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(printer_settings.continued(data, 'simulation', 'sheet-l7160', continue_at), f)
    printers_are(page, server, OFFICE)
    type_label(page, 'Anna')


def line(page) -> str:
    page.wait_for_selector('#sheetNote:not([hidden])')
    return ' '.join(page.inner_text('#sheetNote').split())


def sent(request) -> dict:
    return {k: v[-1] for k, v in parse_qs(request.post_data or '', keep_blank_values=True).items()}


def prints(page) -> list:
    """The print requests of the page, answered without printing: a job
    printed would keep where the sheet goes on for the next test."""
    asked = []

    def answer(route):
        asked.append(sent(route.request))
        route.fulfill(json={'success': True})
    page.route('**/api/print', answer)
    return asked


def printed(page):
    """Wait until the print of the page was answered."""
    page.wait_for_function("() => labelEditor.S.history.length && labelEditor.S.history[0].state === 'printed'")


def set_copies(page, n):
    page.fill('#copies', str(n))
    page.dispatch_event('#copies', 'input')
    wait_for_preview(page)


def test_a_label_printer_shows_nothing_of_sheets(designer):  # noqa: F811
    type_label(designer, 'Anna')
    assert designer.is_hidden('#sheetNote') and designer.locator('.sheet.office').count() == 0
    open_settings(designer)
    card = designer.inner_text('#setPrinters')
    assert 'Tape' in card and 'Sheets' not in card and 'Plain paper' not in card


def test_one_label_of_the_sheet_and_a_quiet_line(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    assert designer.eval_on_selector('#labelImg', 'img => [img.naturalWidth, img.naturalHeight]') == [750, 450]
    assert designer.eval_on_selector('#paper', 'p => p.style.borderRadius') != ''
    assert line(designer) == 'Avery L7160: label 1 of 21 Change'
    assert designer.locator('#sheetNote .sheet i').count() == 21 and designer.locator('#sheetNote .sheet i.on').count() == 1
    assert designer.is_hidden('#tapeNote') and designer.locator('#pagecuts rect').count() == 0
    set_copies(designer, 3)
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('labels 1 to 3 of 21')")
    set_copies(designer, 25)
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('Avery L7160: 2 sheets')")


def test_a_printer_without_its_sheets_asks_for_them(designer, server, app):  # noqa: F811
    office(designer, server, app)
    assert line(designer) == 'Which label sheets are in the printer? Choose'
    assert 'warn' in designer.get_attribute('#sheetNote', 'class')
    designer.click('#sheetNote [data-sheet]')
    designer.wait_for_selector('.sheet-dialog')
    assert designer.eval_on_selector('#shSel', 's => s.value') == ''
    assert designer.eval_on_selector_all('#shSel optgroup', 'gs => gs.map(g => g.label)') == ['A4', 'US Letter']
    assert designer.eval_on_selector('#shSel option', 'o => o.textContent') == 'Choose the number on the pack'
    designer.select_option('#shSel', 'sheet-5160')
    designer.wait_for_selector('.sheet-dialog .sh-start')
    designer.click('.sheet-dialog [data-shut] >> nth=-1')
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('Avery 5160: label 1 of 30')")


def test_the_dialog_chooses_where_this_print_starts(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    designer.click('#sheetNote .pill')
    designer.wait_for_selector('.sheet-dialog')
    assert 'Tap the first free label. For this print only.' in designer.inner_text('.sheet-dialog')
    with designer.expect_request(lambda r: r.url.endswith('/api/preview')) as request:
        designer.click('.sheet-dialog [data-at="6"]')
    assert sent(request.value)['sheet_start'] == '6'
    assert designer.input_value('#sheetAt') == '6'
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('label 6 of 21')")
    assert designer.locator('#sheetNote .sheet i.used').count() == 5
    # Printed from there without a question
    designer.click('.sheet-dialog [data-shut] >> nth=-1')
    asked = prints(designer)
    designer.click('#print')
    printed(designer)
    assert [ask['sheet_start'] for ask in asked] == ['6']
    assert designer.locator('#sheetLayer:not([hidden])').count() == 0


def test_plain_paper_is_kept_for_the_printer(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    designer.click('#sheetNote .pill')
    designer.click('.sheet-dialog [data-paper="1"]')
    designer.wait_for_selector('.sheet-dialog [data-paper="1"].on')
    assert 'Thin lines show where to cut.' in designer.inner_text('.sheet-dialog')
    assert app.test_client().get('/labeldesigner/api/settings?printer=simulation').get_json()['cut_lines'] == '1'
    designer.click('.sheet-dialog [data-shut] >> nth=-1')
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('L7160 on plain paper: label 1 of 21')")


def test_labels_at_the_paper_edge_are_pointed_out(designer, server, app):  # noqa: F811
    client = app.test_client()
    assert client.post('/labeldesigner/api/settings', data={'printer': 'simulation', 'model': 'Other/Office', 'label_size': 'sheet-3474'}).status_code == 200
    printers_are(designer, server, {**OFFICE, 'blank_edge': 4})
    designer.click('#sheetNote .pill')
    designer.wait_for_selector('.sheet-dialog')
    assert 'Labels at the paper edge may print cut off.' in designer.inner_text('.sheet-dialog')
    assert ' mm ' not in designer.inner_text('.sheet-dialog .set-warn')


def test_fill_the_sheet(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    set_copies(designer, 3)
    designer.click('#sheetNote .pill')
    designer.click('.sheet-dialog [data-fill]')
    assert designer.input_value('#copies') == '21'
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('labels 1 to 21 of 21')")


def test_a_sheet_partly_used_asks_where_to_go_on(designer, server, app):  # noqa: F811
    office(designer, server, app, continue_at=7, label_size='sheet-l7160')
    assert line(designer) == 'Avery L7160: label 7 of 21 Change'
    designer.click('#print')
    designer.wait_for_selector('.sheet-dialog[aria-label="Where to continue?"]')
    assert designer.inner_text('.sheet-dialog .btn.primary') == 'Continue at label 7'
    assert designer.inner_text('.sheet-dialog [data-go="1"]') == 'New sheet'
    designer.click('.sheet-dialog [data-at="3"]')
    assert designer.inner_text('.sheet-dialog .btn.primary') == 'Start at label 3'
    asked = prints(designer)
    designer.click('.sheet-dialog .btn.primary')
    printed(designer)
    assert [ask['sheet_start'] for ask in asked] == ['3']


def test_a_new_sheet_and_closing_the_question(designer, server, app):  # noqa: F811
    office(designer, server, app, continue_at=7, label_size='sheet-l7160')
    asked = prints(designer)
    designer.click('#print')
    designer.wait_for_selector('.sheet-dialog')
    designer.keyboard.press('Escape')
    designer.wait_for_selector('#sheetLayer', state='hidden')
    assert asked == [] and designer.evaluate('() => labelEditor.S.history.length') == 0
    designer.click('#print')
    designer.click('.sheet-dialog [data-go="1"]')
    printed(designer)
    assert [ask['sheet_start'] for ask in asked] == ['1']


def test_going_on_leaves_the_label_to_the_server(designer, server, app):  # noqa: F811
    # Worked out once the jobs queued before it printed
    office(designer, server, app, continue_at=7, label_size='sheet-l7160')
    asked = prints(designer)
    designer.click('#print')
    designer.click('.sheet-dialog .btn.primary')
    printed(designer)
    assert [ask['sheet_start'] for ask in asked] == ['next']


def test_two_prints_in_a_row_go_on_on_the_sheet(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    set_copies(designer, 3)
    designer.click('#print')
    printed(designer)
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('labels 4 to 6 of 21')")
    asked = prints(designer)
    designer.click('#print')
    designer.wait_for_selector('.sheet-dialog[aria-label="Where to continue?"]')
    assert designer.inner_text('.sheet-dialog .btn.primary') == 'Continue at label 4'
    designer.click('.sheet-dialog .btn.primary')
    designer.wait_for_function("() => labelEditor.S.history.length === 2 && labelEditor.S.history[0].state === 'printed'")
    assert [ask['sheet_start'] for ask in asked] == ['next']


def test_a_print_tried_again_goes_on_on_the_sheet(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    asked = []

    def answer(route):
        asked.append(sent(route.request))
        route.fulfill(json={'success': True} if len(asked) > 1 else {'success': False, 'message': 'The printer could not print the label'})
    designer.route('**/api/print', answer)
    designer.click('#print')
    designer.wait_for_selector('#retry')
    designer.click('#retry')
    designer.wait_for_function("() => labelEditor.S.history.length === 2 && labelEditor.S.history[0].state === 'printed'")
    # Where the labels the printer took before it failed end, without a question
    assert [ask['sheet_start'] for ask in asked] == ['1', 'next']


def test_no_question_without_a_sheet_partly_used(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    asked = prints(designer)
    designer.click('#print')
    printed(designer)
    assert [ask['sheet_start'] for ask in asked] == ['1']
    assert designer.locator('#sheetLayer:not([hidden])').count() == 0


def test_a_whole_sheet_of_copies_is_offered(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l4731rev')
    set_copies(designer, 189)
    assert designer.input_value('#copies') == '189'
    designer.wait_for_function("() => document.querySelector('#sheetNote').innerText.includes('labels 1 to 189 of 189')")


def test_templates_tell_the_sheet_and_open_its_dialog(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    save_new(designer, 'Badge', 'Ben')
    open_saved(designer, 'Badge')
    designer.wait_for_selector('#tplSheet:not([hidden])')
    assert ' '.join(designer.inner_text('#tplSheet').split()) == 'Avery L7160: label 1 of 21 Change'
    # Above the Templates, which stay open
    designer.click('#tplSheet .pill')
    designer.wait_for_selector('.sheet-dialog')
    designer.click('.sheet-dialog [data-fill]')
    assert designer.input_value('#tplCopies') == '21'
    assert designer.is_visible('#tplDetail')


def test_the_card_of_an_office_printer(designer, server, app):  # noqa: F811
    office(designer, server, app, label_size='sheet-l7160')
    open_settings(designer)
    card = designer.inner_text('#setPrinters')
    for shown in ('Sheets', 'The number is on the pack.', 'Paper', 'Label sheets', 'Plain paper', 'Plain paper gets thin lines to cut along.',
                  'Position', 'Calibrate printer', 'If labels print off their place.'):
        assert shown in card
    for left_out in ('Tape', 'Cutting', 'Leaves first', 'Quality', 'Print a test label'):
        assert left_out not in card
    assert designer.eval_on_selector('#setPrinters select[data-k="label_size"]', 's => s.value') == 'sheet-l7160'
    designer.click('#setPrinters [data-k="cut_lines"][data-v="1"]')
    designer.wait_for_selector('#setPrinters [data-k="cut_lines"][data-v="1"].on')
    designer.click('#setPrinters [data-cal-open]')
    designer.wait_for_selector('.cal-dialog')
    dialog = designer.inner_text('.cal-dialog')
    for shown in ('Put a label sheet in the printer.', 'Print test sheet', 'Read the number where each scale crosses the cut.', 'Across', 'Down'):
        assert shown in dialog
    assert designer.locator('#calAcross option').count() == 14
    with designer.expect_request(lambda r: r.url.endswith('/api/calibration') and r.method == 'POST') as request:
        designer.select_option('#calAcross', '-1')
        designer.click('#calSave')
    assert {k: v for k, v in sent(request.value).items() if k.startswith('read')} == {'read_across': '-1', 'read_along': '0'}


def test_the_office_printer_is_an_example_of_an_address(designer):  # noqa: F811
    open_settings(designer)
    designer.wait_for_selector('#setAdd:not([hidden]) #addAddress')
    assert 'Office printer ipp://192.168.1.90/ipp/print' in ' '.join(designer.inner_text('#setAdd .set-examples').split())
