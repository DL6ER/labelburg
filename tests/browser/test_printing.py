"""Printing in the label designer: the printer panel, tapes, label sizes and
copies."""

import base64
import json
from urllib.parse import parse_qs

import pytest

from app.labeldesigner import printer_settings, tape
from tests.browser.helpers import fake_printer, fits_note, kept, png, preview_size, printed, set_tape, type_line, wait_for_held, wait_for_preview, with_tape


# Too large for 29 x 90 mm labels, the text is cut off there
TOO_BIG = "Object.assign(baseDesign('62'), { text_fit: 'none', text: [L('A rather long shelf label', 150)] })"


def test_ctrl_enter_prints(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Hello')
    designer.keyboard.press('Control+Enter')
    assert printed(designer) == ['printed']


def test_printer_failure_is_shown(designer, monkeypatch):
    # The server answers 503 for a print the printer did not do, the message
    # is shown as for a refused request
    monkeypatch.setattr('app.labeldesigner.printer.PrinterQueue.process_queue', lambda self: 'The printer reports: Replace media error')
    type_line(designer, 0, 'Hello')
    designer.click('#edPrint')
    assert printed(designer) == ['failed']
    designer.wait_for_selector('#layer h2:has-text("Not printed")')
    assert 'The printer reports: Replace media error' in designer.inner_text('#layer')
    # "Printing 1 label..." is gone
    assert designer.is_hidden('#toast')


def test_copies_buttons(designer, monkeypatch, tmp_path):
    # Never fewer than one copy, each copy prints as a label of its own
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Hello')
    for _ in range(2):
        designer.click('.ed-top [data-step="1"]')
    assert designer.input_value('#edCopies') == '3'
    for _ in range(5):
        designer.click('.ed-top [data-step="-1"]')
    assert designer.input_value('#edCopies') == '1'
    designer.click('.ed-top [data-step="1"]')
    designer.click('#edPrint')
    assert printed(designer) == ['printed']
    assert len(list(tmp_path.glob('*.png'))) == 2


def test_printer_panel_replaced_by_another_layer(designer):
    # Opened by its link, replaced by search, it does not come back with the
    # link. A setting changed in it shows once search is closed.
    designer.evaluate("() => { location.hash = '#printer'; }")
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.evaluate("() => { document.querySelector('#view').firstElementChild.dataset.before = '1'; }")
    designer.keyboard.press('Control+k')
    designer.wait_for_selector('#layer:not([hidden]) #palRes')
    assert designer.evaluate('() => location.hash') == '#wizard'
    designer.keyboard.press('Escape')
    designer.wait_for_function("() => document.getElementById('layer').hidden")
    assert designer.evaluate("() => !document.querySelector('#view [data-before]')")


def test_view_is_not_drawn_anew_long_after_the_printer_panel(designer):
    # A setting changed in the printer panel, which search took the place of.
    # Search closed for a command, the view was drawn anew at a later close.
    designer.click('#printerChip')
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.keyboard.press('Control+k')
    designer.wait_for_selector('#layer:not([hidden]) #palRes')
    # Drawn once right after what search closed for, so that the change shows
    designer.evaluate("""() => {
        window.routed = 0;
        const draw = route;
        route = () => { window.routed++; draw(); };
        closeLayer(true);
    }""")
    designer.wait_for_function('() => window.routed === 1')
    designer.evaluate("""() => {
        document.querySelector('#view').firstElementChild.dataset.before = '1';
        openLayer('<div class="pop">Another</div>', 'pop-layer');
        closeLayer();
    }""")
    designer.wait_for_timeout(200)
    assert designer.evaluate("() => !!document.querySelector('#view [data-before]') && window.routed === 1")


def offered_tapes(page):
    """The tapes the printer panel offers to set by hand."""
    page.click('#printerChip')
    offered = page.eval_on_selector_all('#setTape option', 'options => options.map(o => o.value).filter(Boolean)')
    page.keyboard.press('Escape')
    page.wait_for_selector('.pp', state='detached')
    return offered


def test_label_sizes_follow_the_printer_model(designer):
    # The simulator is a QL-800
    offered = offered_tapes(designer)
    assert '62' in offered and '62red' in offered
    assert 'pt36' not in offered and '103x164' not in offered
    # It does not say which tape it has, a label made for another printer
    # keeps its size
    designer.evaluate("() => { S.edit.design.label_size = 'pt36'; renderEditor(); }")
    assert designer.inner_text('.tape-line .grow') == designer.evaluate("() => sizeName('pt36')")
    assert designer.inner_text('#edTapeChange') == 'Set the tape'


def test_offered_tapes_are_those_the_status_names(designer):
    # The server tells them: every size that has printers for a model no
    # label size names
    fake_printer(designer, model='Unknown')
    assert sorted(offered_tapes(designer)) == sorted(s for s in tape.SIZES if tape.TAKEN_BY[s])
    printer = {**with_tape({'errors': [], 'model': 'QL-800', 'path': 'simulation'}), 'takes': ['29', '62']}
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={**printer, 'printers': [printer], 'selected': 'simulation'}))
    designer.evaluate('() => refreshPrinters()')
    assert sorted(offered_tapes(designer)) == ['29', '62']


def test_without_a_printer_the_tapes_are_those_of_a_printer_that_is_not_known(designer):
    designer.evaluate('() => { S.printer = null; S.printers = []; openPrinterPanel(); }')
    offered = designer.eval_on_selector_all('#setTape option', 'options => options.map(o => o.value).filter(Boolean)')
    assert '103' not in offered
    assert sorted(offered) == sorted(s for s in tape.SIZES if tape.TAKEN_BY[s])


def use_printer(page, printer):
    page.evaluate("p => { S.printer = p; detectTape(); }", with_tape(printer))


def test_tape_follows_the_printer(designer):
    # DK and TZe tape of 12 mm have the same size, the model tells them apart
    fake_printer(designer, model='PT-P750W', media_width=12)
    assert designer.evaluate('() => [S.tape, S.tapeAssumed]') == ['pt12', False]
    # A tape set by hand counts only where the printer reports none
    fake_printer(designer, manual='pt24', model='PT-P750W', media_width=12)
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['pt12', False]
    fake_printer(designer, manual='pt24', model='PT-P750W', media_width=None)
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['pt24', True]
    assert designer.inner_text('.tape-note').strip() == 'Set in the printer settings'
    # A PUQU Q00 does not tell its tape, the server assumes the one it would
    # print on
    use_printer(designer, {'path': 'c', 'model': 'PUQU/Q00', 'media_width': 62, 'media_length': 0})
    assert designer.evaluate('() => [S.tape, S.tapeAssumed, S.tapeUnknown]') == ['puqu50x30', True, '']


def test_tape_set_by_hand_belongs_to_its_printer(designer):
    other = 'tcp://192.0.2.10'
    printers = [with_tape({'errors': [], 'path': path, 'model': 'QL-800', 'media_width': None, 'media_length': 0, 'settings': settings})
                for path, settings in (('simulation', {'label_size': '29'}), (other, {}))]
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={**printers[0], 'printers': printers, 'selected': 'simulation'}))
    designer.evaluate('() => refreshPrinters()')
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['29', True]
    designer.click('#printerChip')
    designer.click(f'[data-printer="{other}"]')
    assert designer.evaluate("() => [S.tape, S.tapeManual, $('#setTape').value]") == ['62', False, '']
    designer.click('[data-printer="simulation"]')
    assert designer.evaluate("() => [S.tape, S.tapeManual, $('#setTape').value]") == ['29', True, '29']


def test_tape_is_the_one_the_server_tells(designer):
    # The server tells the tape, the label designer does not match the tape
    # itself
    told = {**with_tape({'path': 'a', 'model': 'QL-800', 'media_width': 29, 'media_length': 90}), 'media_width': 62, 'media_length': 0}
    designer.evaluate("p => { S.printer = p; detectTape(); }", told)
    assert designer.evaluate('() => [S.tape, S.tapeAssumed]') == ['29x90', False]


def test_tape_the_printer_reports_is_not_set_by_hand(designer):
    fake_printer(designer, media_width=29, media_length=90, red_roll=False)
    assert designer.inner_text('.tape-note').strip() == 'In the printer'
    assert designer.locator('#edTapeChange').count() == 0
    designer.click('#printerChip')
    assert designer.locator('#setTape').count() == 0
    assert designer.inner_text('#ppTape') == designer.evaluate("() => sizeName('29x90')")
    assert designer.inner_text('#ppTapeNote') == 'Read from the printer.'


def test_tape_set_by_hand_is_kept_on_the_server(designer, browser):
    set_tape(designer, '29')
    status = designer.evaluate("async () => (await (await fetch(API + '/printer_status')).json())")
    assert [(p['settings'].get('label_size'), p['tape']['id'], p['tape']['manual']) for p in status['printers'] if p['path'] == 'simulation'] == [('29', '29', True)]
    # Another browser sees it: the tape is in the printer, not in one browser
    other = browser.new_context(locale='en-US').new_page()
    try:
        other.goto(designer.url)
        wait_for_preview(other)
        assert other.evaluate('() => [S.tape, S.tapeManual]') == ['29', True]
    finally:
        other.context.close()


def test_tape_set_by_hand_shows_before_the_status_answers(designer):
    # Slow printers answer late. A status asked for before the tape was set
    # does not undo it.
    held = []
    designer.route('**/api/printer_status*', lambda route: held.append(route))
    designer.evaluate('() => { window.asked = refreshPrinters(); }')
    wait_for_held(designer, held)
    designer.click('#edTapeChange')
    designer.select_option('#setTape', '29')
    designer.wait_for_function("() => S.tape === '29' && S.tapeManual")
    assert designer.inner_text('#printerChip .tape') == designer.evaluate("() => sizeName('29')")
    old = with_tape({'errors': [], 'model': 'QL-800', 'path': 'simulation'})
    held[0].fulfill(json={**old, 'printers': [old], 'selected': 'simulation'})
    designer.evaluate('() => window.asked')
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['29', True]
    # The status is asked for again, the server has the tape too
    wait_for_held(designer, held, 2)
    for route in held[1:]:
        route.continue_()
    designer.unroute('**/api/printer_status*')
    designer.evaluate('() => refreshPrinters()')
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['29', True]


def test_tape_the_printer_does_not_take_is_refused_with_the_reason(designer):
    designer.click('#printerChip')
    # Offered before another printer took the place of this one
    designer.evaluate("() => $('#setTape').add(new Option('12 mm', 'pt12'))")
    designer.select_option('#setTape', 'pt12')
    designer.wait_for_selector('#toast:has-text("cannot be printed on")')
    designer.wait_for_function("() => $('#setTape').value === ''")
    assert designer.evaluate('() => [S.tapeManual, kept().label_size]') == [False, None]


def test_tape_set_for_another_model_is_not_selected(designer):
    # Kept for the printer at this address before, the server does not use it
    fake_printer(designer, manual='pt24')
    designer.click('#printerChip')
    assert designer.evaluate("() => [kept().label_size, S.tapeManual, $('#setTape').value]") == ['pt24', False, '']
    assert designer.locator('#setTape option[value="pt24"]').count() == 0


def test_tape_chosen_stays_selected_while_it_is_saved(designer):
    # The server tells whether it uses the tape once it has it, the panel
    # drawn anew meanwhile shows the tape chosen
    held = []
    designer.route('**/api/settings', lambda route: held.append(route))
    designer.click('#printerChip')
    designer.select_option('#setTape', '29')
    wait_for_held(designer, held)
    assert designer.evaluate("() => [$('#setTape').value, kept().label_size, S.tapeManual]") == ['29', '29', False]
    designer.click('[data-q="fast"]')
    wait_for_held(designer, held, 2)
    designer.evaluate('() => refreshPrinters()')
    assert designer.evaluate("() => [$('#setTape').value, S.tapeManual]") == ['29', False]
    for route in held:
        route.continue_()
    designer.wait_for_function("() => S.tapeManual && $('#setTape').value === '29'")


def test_tape_refused_while_it_was_saved_shows_the_one_before(designer):
    set_tape(designer, '29')
    held = []
    designer.route('**/api/settings', lambda route: held.append(route))
    designer.click('#printerChip')
    # Offered before another printer took the place of this one
    designer.evaluate("() => $('#setTape').add(new Option('12 mm', 'pt12'))")
    designer.select_option('#setTape', 'pt12')
    wait_for_held(designer, held)
    designer.evaluate('() => refreshPrinters()')
    assert designer.evaluate("() => $('#setTape').value") == 'pt12'
    held[0].continue_()
    # Taken back on the printer the status brought meanwhile
    designer.wait_for_selector('#toast:has-text("cannot be printed on")')
    assert designer.evaluate("() => [$('#setTape').value, S.tapeManual, kept().label_size]") == ['29', True, '29']


def test_label_is_placed_on_the_loaded_tape(designer):
    # The printer reports 29 x 90 mm labels, the label is for 62 mm tape
    fake_printer(designer, media_width=29, media_length=90)
    designer.evaluate("() => openEditor({ title: 'Hello', design: Object.assign(baseDesign('62'), { text: [L('Hello', 40)] }) })")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '29x90'
    assert designer.inner_text('.tape-line .grow') == '29 x 90 mm'
    assert designer.inner_text('.tape-note').strip() == 'In the printer'
    # It fits as it is, which tape it was made for is not said
    assert not designer.is_visible('#edMade')
    assert designer.inner_text('#edStage .dim-x') == '90 mm'
    # Printed as shown without a question, the history keeps it as made
    sent = []
    designer.route('**/api/print', lambda route: (sent.append(route.request.post_data_buffer), route.continue_()))
    designer.click('#edPrint')
    assert printed(designer) == ['printed']
    assert parse_qs(sent[0].decode())['label_size'] == ['29x90']
    assert designer.evaluate('() => S.history[0].design.label_size') == '62'
    # With its own tape in the printer again, it is the label as made
    kept(designer)
    fake_printer(designer, media_width=62, media_length=0)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.design.orientation]') == ['62', 'standard']
    assert designer.locator('#edMade').count() == 0


def test_a_label_that_does_not_fit_on_the_loaded_tape_says_what_it_was_made_for(designer):
    fake_printer(designer, media_width=29, media_length=90)
    designer.evaluate(f"() => openEditor({{ title: 'Shelf', design: {TOO_BIG} }})")
    wait_for_preview(designer)
    assert 'does not fit' in fits_note(designer)
    assert designer.is_visible('#edMade') and designer.inner_text('#edMade').strip() == 'Made for 62 mm endless, shown as it prints on the 29 x 90 mm in the Brother QL-800.'


def test_an_empty_label_takes_the_loaded_tape(designer):
    # Started while other labels were in the printer, nothing on it was made for them
    designer.evaluate("() => openEditor({ title: 'New label', design: Object.assign(baseDesign('62'), { text: [L('', 40)] }) })")
    wait_for_preview(designer)
    kept(designer)
    fake_printer(designer, media_width=29, media_length=90)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.made]') == ['29x90', None]
    assert designer.locator('#edMade').count() == 0
    type_line(designer, 0, 'Shelf')
    assert designer.locator('#edMade').count() == 0


def test_label_saved_as_made_unless_changed_on_the_loaded_tape(designer):
    fake_printer(designer, media_width=29, media_length=90)
    designer.evaluate("() => openEditor({ title: 'Placed shelf', design: Object.assign(baseDesign('62'), { text: [L('Shelf', 40)] }) })")
    wait_for_preview(designer)
    saved_size = "() => (S.saved.find(s => s.title === 'Placed shelf') || {}).size"
    # Not changed: saved for the tape it was made for, which is said
    designer.click('#edSave')
    assert 'It is saved as 62 mm endless label' in designer.inner_text('.dialog-h p')
    designer.fill('#saveName', 'Placed shelf')
    designer.press('#saveName', 'Enter')
    designer.wait_for_selector('#toast:has-text("Saved \\"Placed shelf\\" to your labels as 62 mm endless label")')
    assert designer.evaluate(saved_size) == '62'
    assert designer.evaluate('() => S.edit.made.as.label_size') == '62'
    # Changed on the tape in the printer: saved for it, made for it now
    type_line(designer, 0, 'Shelf 2')
    designer.click('#edSave')
    designer.wait_for_selector('#toast:has-text("Saved \\"Placed shelf\\" as 29 x 90 mm label")')
    designer.wait_for_function(f'() => ({saved_size})() === "29x90"')
    assert designer.locator('#edMade').count() == 0


def test_label_of_no_orientation_is_placed_on_the_loaded_tape(designer):
    # Anything but rotated is standard, as the server renders it
    box = "() => openEditor({ title: 'Box', design: Object.assign(baseDesign('62'), { orientation: '', text: [L('Box', 40)] }) })"
    designer.evaluate(box)
    wait_for_preview(designer)
    assert designer.inner_text('[data-along].on') == 'Across the tape'
    fake_printer(designer, media_width=29, media_length=90)
    designer.evaluate(box)
    wait_for_preview(designer)
    assert designer.inner_text('#edStage .dim-x') == '90 mm'
    designer.route('**/api/print', lambda route: route.fulfill(json={'success': True}))
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    form = parse_qs(request.value.post_data)
    assert (form['label_size'], form['orientation']) == (['29x90'], ['standard'])


def test_configured_printers_are_offered_and_the_choice_is_kept(designer, app, monkeypatch):
    # Not the printers of the machine running the tests, the page would print
    # on them. One that does not answer is offered with the reason.
    assert designer.evaluate('() => S.printers.map(p => p.path)') == ['simulation']
    other = 'file:///nonexistent/lp0'
    monkeypatch.setitem(app.config, 'PRINTERS', [('QL-800', 'simulation'), ('QL-1100', other)])
    designer.reload()
    wait_for_preview(designer)
    designer.click('#printerChip')
    assert designer.eval_on_selector_all('.plist [data-printer]', 'rows => rows.map(r => r.dataset.printer)') == ['simulation', other]
    assert 'switched off or not connected' in designer.inner_text(f'[data-printer="{other}"]')
    designer.click(f'[data-printer="{other}"]')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    # Kept after reloading, the label sizes follow its model
    designer.reload()
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'Brother QL-1100'
    offered = offered_tapes(designer)
    assert '103x164' in offered and '62red' not in offered


def test_configured_printers_are_shown_by_their_names(designer, app, monkeypatch):
    other = 'file:///nonexistent/lp0'
    monkeypatch.setitem(app.config, 'PRINTERS', [('QL-800', 'simulation', 'Desk'), ('QL-1100', other, 'Shelf'), ('QL-700', 'file:///nonexistent/lp1')])
    designer.reload()
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'Desk'
    assert designer.inner_text('#edStage .printer .model') == 'Desk'
    designer.click('#printerChip')
    rows = designer.eval_on_selector_all('.plist [data-printer] .grow', 'rows => rows.map(r => [r.querySelector("b").textContent, r.querySelector(".sm").textContent])')
    # The model next to the name, a printer without one shows its model
    assert [name for name, _ in rows] == ['Desk', 'Shelf', 'Brother QL-700']
    assert rows[0][1].startswith('Brother QL-800, ') and rows[1][1].startswith('Brother QL-1100, ')
    assert not rows[2][1].startswith('Brother QL-700')
    designer.click(f'[data-printer="{other}"]')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'Shelf'


@pytest.mark.parametrize('name, called', [('Kitchen', 'Kitchen'), ('', 'the Brother QL-800')])
def test_sentences_call_a_printer_by_its_name(designer, name, called):
    # "the" before a model, not before a name
    fake_printer(designer, name=name)
    assert designer.inner_text('.tape-note') == f'{called[0].upper()}{called[1:]} does not say which tape it has.'
    fake_printer(designer, name=name, media_width=29, media_length=90)
    designer.evaluate(f"() => openEditor({{ title: 'Box', design: {TOO_BIG} }})")
    wait_for_preview(designer)
    assert designer.inner_text('#edMade').endswith(f'on the 29 x 90 mm in {called}.')


def test_no_600_dpi_on_the_ql1100(designer):
    type_line(designer, 0, 'Hello')
    width, _ = preview_size(designer)
    designer.click('#printerChip')
    designer.click('[data-q="high"]')
    designer.keyboard.press('Escape')
    designer.wait_for_function("() => document.querySelector('#edStage .paper img')")
    wait_for_preview(designer)
    assert preview_size(designer)[0] == 2 * width
    # The choice is kept for other printers, this one previews and prints at 300 dpi
    # as the server tells it with each printer
    fake_printer(designer, model='QL-1100', media_width=62, settings={'quality': 'high'})
    assert preview_size(designer)[0] == width
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('high_res')") == '0'
    designer.click('#printerChip')
    assert designer.is_disabled('[data-q="high"]') and 'on' in designer.get_attribute('[data-q="high"]', 'class')
    assert 'The Brother QL-1100 cannot print "Dense".' in designer.inner_text('.settings')
    # Nor can a P-touch printer
    designer.keyboard.press('Escape')
    fake_printer(designer, model='PT-P750W', media_width=24)
    designer.click('#printerChip')
    assert designer.is_disabled('[data-q="high"]')
    assert 'The Brother PT-P750W cannot print "Dense".' in designer.inner_text('.settings')


def test_dense_printing_is_named_without_dpi(designer):
    # The resolution of a printer head is nothing users need to know
    designer.click('#printerChip')
    designer.click('#qInfoBtn')
    assert designer.inner_text('[data-q="high"]') == 'Dense, slow'
    assert '"Dense" prints twice as many lines along the label' in designer.inner_text('#qInfo')
    assert 'dpi' not in designer.inner_text('.settings')
    designer.keyboard.press('Escape')
    fake_printer(designer, model='QL-1100', media_width=62)
    designer.click('#printerChip')
    assert designer.get_attribute('[data-q="high"]', 'title') == 'This printer cannot print "Dense"'
    assert 'dpi' not in designer.inner_text('.settings')


def test_ptouch_tape_is_shown_at_180_dpi(designer):
    # Offered to set by hand where the printer does not report its tape
    fake_printer(designer, model='PT-E550W')
    offered = offered_tapes(designer)
    assert 'pt6' in offered and 'pt9' in offered and '62' not in offered
    fake_printer(designer, model='PT-E550W', media_width=12)
    designer.evaluate("() => { S.edit.design.label_size = S.tape; renderEditor(); }")
    # 35 dots at 180 dpi
    assert designer.input_value('[data-margin="left"]') == '4.9'
    type_line(designer, 0, 'Hello')
    _, height = preview_size(designer)
    assert designer.inner_text('#edStage .dim-x') == '12 mm'
    assert designer.inner_text('#edStage .dim-y') == f'{round(height / 180 * 25.4)} mm'


def test_wide_ptouch_tape_is_shown_at_360_dpi(designer):
    use_printer(designer, {'path': 'a', 'model': 'PT-P900W', 'media_width': 36, 'media_length': 0})
    designer.evaluate("() => { S.edit.design.label_size = S.tape; renderEditor(); }")
    assert designer.evaluate('() => S.edit.design.label_size') == 'pt36'
    # 35 dots at 360 dpi
    assert designer.input_value('[data-margin="left"]') == '2.5'
    type_line(designer, 0, 'Hello')
    _, height = preview_size(designer)
    assert designer.inner_text('#edStage .dim-x') == '36 mm'
    assert designer.inner_text('#edStage .dim-y') == f'{round(height / 360 * 25.4)} mm'


def test_switch_shows_how_the_text_runs_on_the_tape(designer):
    def runs():
        wait_for_preview(designer)
        return designer.inner_text('[data-along].on')
    type_line(designer, 0, 'Spices')
    assert runs() == 'Across the tape'
    # 62 x 29 mm labels are wider than long, standard is across the tape
    set_tape(designer, '62x29')
    assert runs() == 'Across the tape'
    designer.click('[data-along="1"]')
    assert runs() == 'Along'
    assert designer.evaluate('() => S.edit.design.orientation') == 'rotated'
    set_tape(designer, '29x90')
    assert runs() == 'Along'
    # Round labels are shown round
    set_tape(designer, 'd24')
    assert runs() == 'Across the tape'
    assert 'round' in designer.get_attribute('#edStage', 'class')
    # Endless tape keeps the direction of the label as last changed, on
    # 62 x 29 mm labels
    set_tape(designer, '62')
    assert runs() == 'Along'


def test_red_prints_black_on_tape_without_red(designer):
    type_line(designer, 0, 'Danger')
    # A label for black tape takes the name of black and red tape of its size
    set_tape(designer, '62red')
    assert designer.evaluate('() => S.edit.design.label_size') == '62red'
    assert designer.locator('#edMade').count() == 0
    designer.click('[data-row="0"] [data-act="red"]')
    wait_for_preview(designer)
    set_tape(designer, '62')
    assert 'Made for 62 mm endless, black and red' in designer.inner_text('#edMade')
    assert 'red parts print black' in fits_note(designer)
    assert 'nored' in designer.get_attribute('[data-row="0"] [data-act="red"]', 'class')
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == 'black'
    assert designer.evaluate('() => S.edit.design.text[0].color') == 'red'


def test_job_printed_again_keeps_its_image(designer):
    # Printed again, the ninth job moves to the front and the history keeps
    # its image no longer, the request still has it. Jobs whose image was
    # not kept are not printed again without it.
    bodies = []
    designer.route('**/api/print', lambda route: (bodies.append(route.request.post_data_buffer), route.continue_()))
    designer.evaluate("""data => {
        S.history = Array.from({ length: 11 }, (_, i) => ({ id: 'h' + i, time: Date.now() - i * 1000, title: 'Job ' + i, values: {}, copies: 1,
            source: null, printer: 'QL-800', state: 'printed', message: '',
            design: Object.assign(baseDesign('62'), { print_type: 'image', image: { data, mime: 'image/png', name: 'job.png' } }) }));
        saveHistory();
    }""", base64.b64encode(png()).decode())
    assert designer.evaluate('() => S.history.map(h => !!h.noImage)') == [False] * 9 + [True] * 2
    designer.evaluate('() => again(S.history[8])')
    printed(designer, 12)
    assert len(bodies) == 1 and b'filename="job.png"' in bodies[0]
    designer.evaluate('() => again(S.history[11])')
    designer.wait_for_timeout(300)
    assert len(bodies) == 1


def test_copies_stay_when_the_editor_is_drawn_again(designer):
    type_line(designer, 0, 'Jars')
    designer.fill('#edCopies', '2')
    designer.click('.ed-top [data-step="1"]')
    # Bold draws the editor again
    designer.click('[data-row="0"] [data-act="bold"]')
    wait_for_preview(designer)
    assert designer.input_value('#edCopies') == '3'
    designer.click('#edPrint')
    printed(designer)
    assert designer.evaluate('() => S.history[0].copies') == 3


def test_printer_that_is_not_connected_is_reported(designer):
    # The chosen printer was unplugged: the labels print on another one, not
    # without a word
    designer.evaluate("() => localStorage.setItem('designer_printer', JSON.stringify('file:///dev/usb/lp7'))")
    designer.reload()
    designer.wait_for_selector('#toast:has-text("/dev/usb/lp7 is not connected, labels print on the Brother QL-800")')


def test_black_and_red_roll_is_not_a_mismatch(designer):
    # A printer that does not tell the color of its roll reports a black and
    # red one as 62 mm endless
    fake_printer(designer, media_width=62, media_length=0, media_type='Continuous length tape')
    designer.evaluate("() => openEditor({ title: 'Red', design: Object.assign(baseDesign('62red'), { text: [L('Red', 40, false, 'center', { color: 'red' })] }) })")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '62red'
    assert designer.locator('#edMade, .tape-note.warn').count() == 0
    assert designer.evaluate("() => fitsTape('62red') && onTape(S.edit.design).label_size === '62red'")


def test_black_and_red_roll_is_detected(designer):
    # The QL-800 series tells the color of the roll
    fake_printer(designer, media_width=62, media_length=0, media_type='Continuous length tape', red_roll=True)
    assert designer.inner_text('#printerChip .tape') == '62 mm endless, black and red'
    # A label for black tape takes the name of the roll, red prints red on it
    designer.evaluate("() => openEditor({ title: 'Red', design: Object.assign(baseDesign('62'), { text: [L('Red', 40, false, 'center', { color: 'red' })] }) })")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '62red'
    assert designer.inner_text('.tape-note').strip() == 'In the printer'
    assert designer.locator('#edMade, .tape-note.warn').count() == 0
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == 'red'


def test_black_roll_is_no_black_and_red_one(designer):
    fake_printer(designer, media_width=62, media_length=0, media_type='Continuous length tape', red_roll=False)
    assert designer.inner_text('#printerChip .tape') == '62 mm endless'
    # A label for black and red tape is placed on it, its red prints black
    designer.evaluate("() => openEditor({ title: 'Red', design: Object.assign(baseDesign('62red'), { text: [L('Red', 40, false, 'center', { color: 'red' })] }) })")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '62'
    assert designer.inner_text('#edMade').strip() == 'Made for 62 mm endless, black and red, shown as it prints on the 62 mm endless in the Brother QL-800.'
    assert 'red parts print black' in fits_note(designer)
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == 'black'


def test_red_parts_on_a_black_roll_are_said_in_the_warning_box(designer):
    # The note of the tape the label was made for stays below it
    fake_printer(designer, media_width=62, media_length=0, media_type='Continuous length tape', red_roll=False)
    designer.evaluate("() => openEditor({ title: 'Red', design: Object.assign(baseDesign('62red'), { text: [L('Red', 40, false, 'center', { color: 'red' })] }) })")
    wait_for_preview(designer)
    assert designer.inner_text('#edFits.warn .say b') == 'This tape has no red, the red parts print black.'
    assert designer.is_visible('#edMade') and designer.locator('#edFits #edMade').count() == 0
    assert designer.is_enabled('#edPrint')


def test_tape_with_red_text_is_found(designer):
    # The color of the text on TZe tape says nothing about black and red rolls
    fake_printer(designer, model='PT-P750W', media_width=12, media_length=0, media_type='Laminated tape', tape_color='White', text_color='Red')
    assert designer.evaluate('() => [S.tape, S.tapeUnknown]') == ['pt12', '']


def test_loaded_label_that_is_not_offered(designer):
    # The simulator reports no tape, labels keep their own
    assert designer.inner_text('.tape-note') == 'The Brother QL-800 does not say which tape it has.'
    designer.evaluate("() => openEditor({ title: 'Box', design: Object.assign(baseDesign('29x90'), { text: [L('Box', 40)] }) })")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '29x90'
    assert designer.locator('#edMade').count() == 0
    fake_printer(designer, media_width=40, media_length=0)
    assert designer.inner_text('.tape-note') == 'The Brother QL-800 reports 40 mm tape, which is not offered here.'
    designer.click('#printerChip')
    # The assumed tape is named, not called the default
    assert designer.inner_text('.pp #setTape + .muted') == 'The printer reports 40 mm tape, which is not offered here. Labels keep their own size, new ones are ' + designer.evaluate('() => sizeName(S.tape)') + '.'
    # Neither the chip nor the printer panel names it as the tape in the printer
    assert designer.locator('#printerChip .tape').count() == 0
    assert designer.evaluate("() => $('#setTape').selectedOptions[0].textContent") == 'Automatic'
    # Set by hand it is the tape in the printer
    designer.select_option('#setTape', '62')
    designer.wait_for_function("() => S.tapeManual")
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip .tape') == '62 mm endless'
    assert designer.inner_text('.tape-note').strip() == 'Set in the printer settings'
    assert designer.evaluate('() => S.edit.design.label_size') == '62'


def test_printer_that_measures_only_the_length_of_its_labels(designer):
    # A ZPL printer measured 50 mm, the length of labels of two widths
    fake_printer(designer, model='Zebra/ZPL', media_length=50)
    who = designer.evaluate('() => thePrinter(S.printer, true)')
    assert designer.inner_text('.tape-note') == f'{who} measures labels 50 mm long. Choose which ones in the printer settings.'
    designer.click('#printerChip')
    assert designer.inner_text('.pp #setTape + .muted') == 'The printer measures labels 50 mm long, choose which ones. Labels keep their own size, new ones are ' + designer.evaluate('() => sizeName(S.tape)') + '.'
    # Those of that length are offered first, once
    groups = designer.evaluate("() => [...document.querySelectorAll('#setTape optgroup')].map(g => [g.label, [...g.children].map(o => o.value)])")
    assert groups[0] == ['Of the measured length', ['zpl100x50', 'zpl80x50']]
    assert not any(size in sizes for _, sizes in groups[1:] for size in ('zpl100x50', 'zpl80x50'))
    designer.select_option('#setTape', 'zpl80x50')
    designer.wait_for_function('() => S.tapeManual')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    assert designer.inner_text('.tape-note').strip() == 'Set in the printer settings'


def test_tape_set_by_hand_is_known(designer):
    # A roll the designer does not offer says nothing about a tape set by
    # hand, nor does a printer that reports none about its color
    fake_printer(designer, media_width=40)
    assert designer.inner_text('.tape-note') == 'The Brother QL-800 reports 40 mm tape, which is not offered here.'
    fake_printer(designer, manual='62', media_width=40)
    assert designer.inner_text('.tape-note').strip() == 'Set in the printer settings'
    assert designer.evaluate('() => [S.tapeUnknown, S.tapeManual]') == ['', True]
    red = "() => openEditor({ title: 'Red', design: Object.assign(baseDesign('62red'), { text: [L('Red', 40)] }) })"
    fake_printer(designer, media_width=62)
    designer.evaluate(red)
    wait_for_preview(designer)
    assert designer.locator('#edMade').count() == 0
    assert designer.evaluate("() => fitsTape('62red')")
    fake_printer(designer, manual='62')
    designer.evaluate(red)
    wait_for_preview(designer)
    # Placed on it, without red parts it fits as it is and says nothing of it
    assert designer.evaluate('() => S.edit.made.as.label_size') == '62red' and not designer.is_visible('#edMade')
    assert not designer.evaluate("() => fitsTape('62red')")


def test_printer_panel_of_a_link_stays_closed(designer):
    designer.evaluate("() => { location.hash = '#printer'; }")
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.keyboard.press('Escape')
    designer.wait_for_timeout(500)
    assert designer.evaluate("() => document.getElementById('layer').hidden")


def test_cutting_per_printer_and_per_label(designer):
    # Set in the printer panel for each printer, half cuts and chain printing
    # only for P-touch printers. A label can keep its own cutting.
    def sent():
        with designer.expect_request('**/api/print') as request:
            designer.click('#edPrint')
        # Without files a plain form
        form = parse_qs(request.value.post_data)
        return {k: form[k][0] for k in ('cut', 'half_cut', 'chain')}

    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    designer.evaluate("() => { S.printer.settings = Object.assign({}, S.printer.settings, { cut: 'last' }); }")
    type_line(designer, 0, 'Hello')
    # Printers without a setting of their own keep the one set before for all
    assert sent() == {'cut': 'last', 'half_cut': '1', 'chain': '0'}
    designer.evaluate("() => openPrinterPanel()")
    # Only P-touch printers half cut and chain print, shown off
    assert designer.locator('[data-c="half_cut"][data-v="0"].on:disabled, [data-c="chain"][data-v="0"].on:disabled').count() == 2
    # A PT-E550W does not report its tape over the network, the 62 mm label
    # keeps its size
    fake_printer(designer, model='PT-E550W')
    designer.evaluate("() => openPrinterPanel()")
    # Not cut is chain printing on P-touch printers
    designer.click('[data-c="cut"][data-v="none"]')
    assert designer.locator('[data-c="chain"][data-v="1"].on:disabled').count() == 1
    assert designer.get_attribute('.pp .cut-strip', 'title') == ('Half cut between the labels, not cut. The last label stays in the printer, the next print cuts it off. '
                                                                 'The tape fed before the first label is waste, short labels get some extra tape.')
    designer.click('[data-c="cut"][data-v="last"]')
    designer.click('[data-c="chain"][data-v="1"]')
    designer.keyboard.press('Escape')
    assert sent() == {'cut': 'last', 'half_cut': '1', 'chain': '1'}
    # Saved with the label, it is cut its own way
    designer.select_option('#edCut', 'each')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.cut, S.edit.design.chain]') == ['each', '1']
    assert sent() == {'cut': 'each', 'half_cut': '1', 'chain': '1'}
    designer.select_option('#edCut', '')
    wait_for_preview(designer)
    assert sent()['cut'] == 'last'


def test_no_half_cut_on_a_ptouch_printer_without_a_half_cutter(designer):
    # The PT-P700 cuts and chain prints, but has no half cutter: half cuts
    # are shown off and disabled with the reason, also when they are on
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PT-P700' }); S.printer.settings = Object.assign({}, S.printer.settings, { cut: 'last' }); openPrinterPanel(); }")
    half = designer.locator('.pp [data-c="half_cut"][data-v="0"]')
    assert half.get_attribute('class') == 'on' and half.is_disabled()
    assert designer.get_attribute('.pp div:has(> .seg [data-c="half_cut"])', 'title') == 'This printer has no half cutter'
    assert designer.is_enabled('.pp [data-c="chain"][data-v="1"]')
    assert designer.get_attribute('.pp .cut-strip', 'title').startswith('Cut after the last label.')


def test_look_for_printers_looks_also_for_those_that_did_not_answer(designer):
    # The status every 20 s leaves Bluetooth printers that did not answer alone
    with designer.expect_request('**/api/printer_status*') as request:
        designer.evaluate('() => refreshPrinters()')
    assert 'look=1' not in request.value.url
    designer.evaluate('() => openPrinterPanel()')
    with designer.expect_request('**/api/printer_status*') as request:
        designer.click('#ppRefresh')
    assert 'look=1' in request.value.url


@pytest.mark.parametrize('hidden', [True, False])
def test_printers_are_asked_only_from_a_tab_that_is_shown(designer, hidden):
    # A tab left in the background keeps no printer busy, e.g. a B1 its
    # Bluetooth connection; shown again it asks at once
    asked = []
    designer.on('request', lambda request: '/api/printer_status' in request.url and asked.append(request.url))
    designer.evaluate(f"() => {{ Object.defineProperty(document, 'hidden', {{ value: {str(hidden).lower()}, configurable: true }}); pollPrinters(); }}")
    designer.wait_for_timeout(300)
    assert bool(asked) is not hidden


@pytest.mark.parametrize('follow', [True, False])
def test_only_a_printer_the_server_says_to_follow_is_asked_on_its_own(designer, follow):
    # Asking one on the network or on Bluetooth with a beep that often costs,
    # it is asked with the others every 20 s
    asked = []
    designer.route('**/api/printer_status?printer=*', lambda route: (asked.append(route.request.url), route.fulfill(status=404, body='{}')))
    designer.evaluate(f"() => {{ S.printer = Object.assign({{}}, S.printer, {{ path: 'usbip://192.0.2.7/1-4.1.3', follow: {str(follow).lower()} }}); S.printers = [S.printer]; return followPrinter(); }}")
    assert len(asked) == follow


def test_printer_with_a_low_battery_asks_to_charge_it(designer):
    hint = 'The battery is low, charge the printer.'
    for low in (True, False):
        designer.evaluate(f"""() => {{ S.printers = [Object.assign({{}}, S.printer, {{ status_type: 'Reply to status request', battery_low: {str(low).lower()} }})]; S.printer = S.printers[0]; openPrinterPanel(); }}""")
        assert (hint in designer.inner_text('.plist')) is low
        assert designer.evaluate('() => printerState(S.printer).text') == 'Ready'
        designer.evaluate('() => closeLayer(true)')


def test_printer_tells_its_battery_in_percent(designer):
    for battery, shown in ((75, 'Battery 75%'), (None, None)):
        value = 'null' if battery is None else battery
        designer.evaluate(f"""() => {{ S.printers = [Object.assign({{}}, S.printer, {{ status_type: 'Reply to status request', battery: {value} }})]; S.printer = S.printers[0]; openPrinterPanel(); }}""")
        text = designer.inner_text('.plist')
        assert (shown in text) if shown else 'Battery' not in text
        designer.evaluate('() => closeLayer(true)')


def test_printer_still_being_asked_is_not_shown_as_failing(designer):
    # A printer on Bluetooth takes a few seconds to answer, it was red meanwhile
    assert designer.evaluate("() => printerState({ status_type: 'Asking the printer', errors: [] })") == {'cls': 'off', 'text': 'Asking the printer…'}


def test_printer_busy_with_another_program_is_not_shown_as_ready(designer):
    # Its model is not known before it answered
    assert designer.evaluate("() => printerState({ status_type: 'Printing', errors: [], model: 'Unknown' })") == {'cls': '', 'text': 'Printing'}
    assert designer.evaluate("() => [printerLabel({ model: 'Unknown' }), thePrinter({ model: 'Unknown' })]") == ['Printer', 'the printer']


def test_labels_of_a_q00_leave_it_turned(designer):
    # Their top comes out first
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.tape = 'puqu50x30';
        openEditor({ title: 'Q', design: Object.assign(baseDesign('puqu50x30'), { text: [L('Q', 40)] }) }); }""")
    wait_for_preview(designer)
    assert 'turned' in designer.get_attribute('#edStage', 'class').split()
    # The printer stands below the label, which leaves it upwards
    assert designer.evaluate("() => getComputedStyle(document.querySelector('#edStage')).flexDirection") == 'column-reverse'
    assert designer.locator('#edStage .flow .fa-arrow-up-long').count() == 1
    # Labels that leave it sideways have the printer to their left
    designer.evaluate("() => { S.tape = 'puqu50x80'; openEditor({ title: 'Q', design: Object.assign(baseDesign('puqu50x80'), { text: [L('Q', 40)] }) }); }")
    wait_for_preview(designer)
    classes = designer.get_attribute('#edStage', 'class').split()
    assert 'side' in classes and 'turned' not in classes
    # As those of a QL or P-touch printer and the VC-500W, their top first
    for model, tape, turned in (('QL-700', '62', True), ('PT-P700', 'pt12', True), ('VC-500W', 'vc25', True)):
        designer.evaluate(f"() => {{ S.printer = Object.assign({{}}, S.printer, {{ model: '{model}' }}); S.tape = '{tape}'; openEditor({{ title: 'Q', design: Object.assign(baseDesign('{tape}'), {{ text: [L('Q', 40)] }}) }}); }}")
        wait_for_preview(designer)
        assert ('turned' in designer.get_attribute('#edStage', 'class').split()) is turned, model


def test_labels_can_leave_the_printer_bottom_first(designer):
    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    q00 = """tape => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.tape = tape;
        openEditor({ title: 'Q', design: Object.assign(baseDesign(tape), { text: [L('Q', 40)] }) }); }"""
    designer.evaluate(q00, 'puqu50x30')
    wait_for_preview(designer)
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    # As the driver prints them until set
    assert b'leaves_first' not in request.value.post_data_buffer
    designer.evaluate("() => openPrinterPanel()")
    # Kept on the server with the other settings of the printer
    with designer.expect_response('**/api/settings') as response:
        designer.click('.pp [data-lf="bottom"]')
    assert response.value.json()['settings']['leaves_first'] == 'bottom'
    assert designer.locator('.pp [data-lf="bottom"].on').count() == 1
    # The test label names its ends, printed as set
    with designer.expect_request('**/api/print') as request:
        designer.click('.pp #setLfTest')
    body = request.value.post_data_buffer
    assert b'leaves_first=bottom' in body and b'TOP' in body and b'BOTTOM' in body
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    # The printer stands above the label, which leaves it downwards
    assert 'turned' not in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .flow .fa-arrow-down-long').count() == 1
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    assert b'leaves_first=bottom' in request.value.post_data_buffer
    # Labels that leave it sideways have the printer to their right
    designer.evaluate(q00, 'puqu50x80')
    wait_for_preview(designer)
    assert {'side', 'back'} <= set(designer.get_attribute('#edStage', 'class').split())
    assert designer.evaluate("() => getComputedStyle(document.querySelector('#edStage')).flexDirection") == 'row-reverse'
    assert designer.locator('#edStage .flow .fa-arrow-left-long').count() == 1


def test_receipt_shows_the_blank_paper_before_it(designer):
    # A TM-T20II feeds 12 mm to its cutter after a label, blank before the next
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'Epson/ESC-POS' }); S.tape = 'escpos80';
        openEditor({ title: 'R', design: Object.assign(baseDesign('escpos80'), { text: [L('R', 40)] }) }); }""")
    wait_for_preview(designer)
    assert 'turned' in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .paper .lead').count() == 1
    shown = int(designer.text_content('#edStage .dim-y').split()[0])
    img = designer.evaluate("() => document.querySelector('#edStage .paper img').naturalHeight")
    assert shown == round(img / 203 * 25.4) + 12
    # Also at the right end of a label that leaves sideways, which leaves first
    designer.evaluate("""() => openEditor({ title: 'R', design: Object.assign(baseDesign('escpos80'), { orientation: 'rotated', text: [L('Receipt', 40)] }) })""")
    wait_for_preview(designer)
    assert 'side' in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .paper img + .lead').count() == 1
    shown = int(designer.text_content('#edStage .dim-x').split()[0])
    img = designer.evaluate("() => document.querySelector('#edStage .paper img').naturalWidth")
    assert shown == round(img / 203 * 25.4) + 12
    # Drawn 12 mm long, at the scale of the label
    drawn = designer.evaluate("""() => { const r = s => document.querySelector('#edStage .paper ' + s).getBoundingClientRect().width;
        return r('.lead') / r('img') * document.querySelector('#edStage .paper img').naturalWidth; }""")
    assert abs(drawn - 12 / 25.4 * 203) < 3


def test_cutting_is_disabled_on_a_printer_without_a_cutter(designer):
    # The labels of a PUQU Q00 are torn off, cutting after each or the last
    # label was offered all the same
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.printer.settings = Object.assign({}, S.printer.settings, { cut: 'each' }); openPrinterPanel(); }")
    cuts = designer.locator('.pp [data-c="cut"]')
    assert cuts.count() == 3 and all(cuts.nth(i).is_disabled() for i in range(3))
    assert designer.locator('.pp [data-c="cut"].on').count() == 0
    assert designer.get_attribute('.pp div:has(> .seg [data-c="cut"])', 'title') == 'This printer has no cutter'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'No cutter, the labels are torn off.'
    # Labels saved with their own cutting are torn off as well
    assert designer.inner_text('.pp .cut-show .muted') == 'No cutter, the labels are torn off.'
    # Not cut is what is sent, also for a label saved with its own cutting
    assert designer.evaluate("() => [printerCut().cut, cutOf({ cut: 'each', label_size: 'puqu50x30' }).cut]") == ['none', 'none']
    assert 'disabled title="This printer has no cutter"' in designer.evaluate("() => cutField({ cut: '', label_size: 'puqu50x30' })")
    # It prints in one quality, Fast did nothing
    designer.evaluate("() => { S.printer.settings = Object.assign({}, S.printer.settings, { quality: 'fast' }); closeLayer(true); openPrinterPanel(); }")
    fast, quality = designer.locator('.pp [data-q="fast"]'), designer.locator('.pp [data-q="quality"]')
    assert fast.is_disabled() and quality.is_disabled() and 'on' in quality.get_attribute('class').split()
    assert quality.get_attribute('title') == 'This printer prints in one quality only'


def test_ql500_labels_are_cut_by_hand(designer):
    # The QL-500 has a lever instead of a cutter, cutting was offered
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'QL-500' }); S.printer.settings = Object.assign({}, S.printer.settings, { cut: 'each' }); openPrinterPanel(); }")
    cuts = designer.locator('.pp [data-c="cut"]')
    assert cuts.count() == 3 and all(cuts.nth(i).is_disabled() for i in range(3))
    assert designer.get_attribute('.pp div:has(> .seg [data-c="cut"])', 'title') == 'This printer has no automatic cutter'
    assert designer.inner_text('.pp .cut-show .muted') == 'No automatic cutter, cut the labels off with the lever on the printer.'
    assert designer.evaluate("() => [printerCut().cut, cutOf({ cut: 'each', label_size: '62' }).cut]") == ['none', 'none']
    assert 'disabled title="This printer has no automatic cutter"' in designer.evaluate("() => cutField({ cut: '', label_size: '62' })")
    # Next to the preview
    designer.evaluate("() => { closeLayer(true); S.tape = '62'; openEditor({ title: 'Q', design: Object.assign(baseDesign('62'), { text: [L('Q', 40)] }) }); }")
    wait_for_preview(designer)
    assert designer.inner_text('#edStage .cuts > span:not(.cut-strip)') == 'Cut by hand'
    # Labels for it and other QL printers are cut as these cut them
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PT-E550W' }); }")
    assert designer.evaluate("() => noCutter(sizeCaps('62'))") is False


def test_assumed_tape_is_one_the_printer_takes(designer):
    # A PUQU Q00 does not report its labels, 62 mm endless tape was assumed
    use_printer(designer, {'path': 'simulation', 'model': 'PUQU/Q00', 'media_width': None, 'media_length': 0})
    assert designer.evaluate('() => [S.tape, S.tapeAssumed]') == ['puqu50x30', True]
    # New labels start on it, the printer panel names it, not 62 mm endless
    designer.evaluate('() => openPrinterPanel()')
    assert designer.inner_text('.pp #setTape + .muted') == 'The printer does not report its tape. Labels keep their own size, new ones are 50 x 30 mm.'
    designer.evaluate('() => closeLayer(true)')
    use_printer(designer, {'path': 'simulation', 'model': 'QL-700', 'media_width': None, 'media_length': 0})
    assert designer.evaluate('() => S.tape') == '62'


def test_printer_of_another_driver_gets_what_it_offers(designer):
    # The label designer knows printers only by what their driver tells
    designer.evaluate("""() => {
        C.models['Other-1'] = { qualities: ['fast', 'quality'], high_res: false, half_cut: false, chain: false,
                                feed_waste: false, colors: 'black', never_cut: 'It keeps them.' };
        SIZES['o48'] = Object.assign({}, SIZES['62'], { id: 'o48', models: ['Other-1'], dpi: [203, 203] });
        S.printer = Object.assign({}, S.printer, { model: 'Other-1' }); S.printer.settings = Object.assign({}, S.printer.settings, { quality: 'high' }); S.printer.settings = Object.assign({}, S.printer.settings, { cut: 'last' });
        openPrinterPanel(); }""")
    assert designer.eval_on_selector_all('.pp [data-q]', 'buttons => buttons.map(b => b.textContent)') == ['Fast', 'Quality']
    assert designer.inner_text('.pp [data-q].on') == 'Quality'
    assert designer.evaluate("() => [highRes(), dpiOf('o48'), dpiOf('o48', true)]") == [False, 203, 203]
    assert designer.get_attribute('.pp div:has(> .seg [data-c="half_cut"])', 'title') == 'This printer has no half cutter'
    assert designer.get_attribute('.pp div:has(> .seg [data-c="chain"])', 'title') == 'Only for P-touch printers'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'Cut after the last label.'
    never = designer.locator('.pp [data-c="cut"][data-v="none"]')
    assert never.is_disabled() and never.get_attribute('title') == 'It keeps them.'
    # A driver that names no maker: the model alone
    assert designer.evaluate("() => printerName(S.printer)") == 'Other-1'


def test_printers_are_named_with_their_maker(designer):
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert 'Brother QL-800' in designer.inner_text('.plist')
    assert designer.inner_text('#printerChip b') == 'Brother QL-800'
    # Phones show the model only
    designer.set_viewport_size({'width': 390, 'height': 800})
    assert designer.inner_text('#printerChip b') == 'QL-800'
    # Also a printer of another maker
    assert designer.evaluate("() => printerName({ model: 'PUQU/Q00' })") == 'PUQU Q00'


def test_cutting_saved_through_the_api(designer, server):
    # half_cut 0 was shown and printed as on, chain 1 was sent but not shown
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Own.json', 'title': 'Own', 'label_size': 'pt12', 'text': [{'text': 'Own', 'size': '40'}],
                                           'cut': 'last', 'half_cut': 0, 'chain': True}))
    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    designer.evaluate("""async () => {
        S.printer = Object.assign({}, S.printer, { model: 'PT-E550W' });
        S.tape = 'pt12';
        openEditor({ title: 'Own', design: await itemDesign(savedItem({ name: 'Own.json', title: 'Own' })) });
    }""")
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.cut, S.edit.design.half_cut, S.edit.design.chain]') == ['last', '0', '1']
    assert designer.locator('[data-lcut="half_cut"][data-v="0"].on, [data-lcut="chain"][data-v="1"].on').count() == 2
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    form = parse_qs(request.value.post_data)
    assert {k: form[k][0] for k in ('cut', 'half_cut', 'chain')} == {'cut': 'last', 'half_cut': '0', 'chain': '1'}


def test_cutting_does_not_render_the_preview_again(designer):
    # The fields went into the preview request and its cache key
    type_line(designer, 0, 'Hello')
    asked = []
    designer.on('request', lambda r: asked.append(r) if '/api/preview' in r.url else None)
    designer.select_option('#edCut', 'none')
    wait_for_preview(designer)
    assert asked == []


def test_printer_on_the_stage_follows_its_status(designer):
    # It kept the status of the last preview, red while the printer list was green
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { status_type: 'Reply to status request', errors: ['The cover of the printer is open'] }); renderPrinterChip(); }""")
    assert designer.get_attribute('#edStage .printer .led', 'class') == 'led bad'
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { errors: [] }); renderPrinterChip(); }""")
    assert designer.get_attribute('#edStage .printer .led', 'class') == 'led '
    assert designer.get_attribute('#edStage .printer .led', 'title') == 'Ready'


def test_open_printer_panel_follows_the_status(designer):
    # It showed the status of when it was opened until "Look for printers"
    printer = {'path': 'simulation', 'model': 'QL-800', 'status_type': 'Reply to status request', 'errors': []}
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={'printers': [with_tape(printer)]}))
    designer.evaluate("() => refreshPrinters()")
    designer.evaluate("() => openPrinterPanel()")
    designer.focus('.pp .prow')
    designer.evaluate("() => refreshPrinters()")
    assert designer.evaluate("() => document.activeElement.matches('.pp .prow')")
    printer.update(errors=['The cover of the printer is open'], media_width=29, media_length=90)
    designer.evaluate("() => refreshPrinters()")
    assert 'The cover of the printer is open' in designer.inner_text('.pp .plist')
    assert designer.locator('.pp .plist .dot.bad').count() == 1
    assert designer.inner_text('#ppTape') == '29 x 90 mm'


def test_editor_follows_the_tape_the_printer_reports(designer):
    printer = {'path': 'simulation', 'model': 'QL-800', 'status_type': 'Reply to status request', 'errors': [], 'media_width': 62, 'media_length': 0}
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={'printers': [with_tape(printer)]}))
    type_line(designer, 0, 'Jars')
    designer.evaluate("() => refreshPrinters()")
    assert designer.inner_text('.tape-note').strip() == 'In the printer'
    assert designer.inner_text('.tape-line .grow') == '62 mm endless'
    # Another roll: the label is placed on it
    printer.update(media_width=29, media_length=90)
    designer.evaluate("() => refreshPrinters()")
    wait_for_preview(designer)
    assert designer.inner_text('.tape-line .grow') == '29 x 90 mm'
    assert designer.evaluate('() => S.edit.made.as.label_size') == '62'
    # The same roll leaves the editor as it is
    designer.focus('[data-row="0"] .tx')
    designer.evaluate("() => refreshPrinters()")
    assert designer.evaluate("() => document.activeElement.matches('[data-row=\"0\"] .tx')")
    # Its own roll again, the label is as made
    printer.update(media_width=62, media_length=0)
    designer.evaluate("() => refreshPrinters()")
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.label_size') == '62'
    assert designer.locator('#edMade').count() == 0


def test_sheet_after_printing_on_a_phone_names_the_printer_with_its_maker(designer):
    # It showed "on the PUQU/Q00"
    designer.set_viewport_size({'width': 700, 'height': 900})
    designer.evaluate("() => announce({ state: 'printed', copies: 1, title: 'Q', values: {}, design: baseDesign('puqu50x30'), printer: 'PUQU/Q00' })")
    assert 'Q on the PUQU Q00' in designer.inner_text('body')


def test_settings_are_kept_on_the_server_for_every_browser(designer, browser, server):
    designer.click('#printerChip')
    with designer.expect_response('**/api/settings'):
        designer.click('[data-q="fast"]')
    with designer.expect_response('**/api/settings'):
        designer.click('[data-c="cut"][data-v="last"]')
    other = browser.new_context(locale='en-US').new_page()
    try:
        other.goto(server + '/labeldesigner/#edit')
        wait_for_preview(other)
        assert other.evaluate("() => [kept().quality, printerCut().cut]") == ['fast', 'last']
        # A change in one browser keeps the change of the other
        other.click('#printerChip')
        with other.expect_response('**/api/settings'):
            other.click('[data-c="cut"][data-v="each"]')
        designer.evaluate("() => refreshPrinters()")
        assert designer.evaluate("() => [kept().quality, printerCut().cut]") == ['fast', 'each']
    finally:
        other.context.close()


def test_the_order_of_copies_and_the_date_format_stay_in_each_browser(designer, browser, server):
    designer.click('#printerChip')
    designer.select_option('#setDate', '%d.%m.%Y')
    designer.click('[data-col="0"]')
    other = browser.new_context(locale='en-US').new_page()
    try:
        other.goto(server + '/labeldesigner/#edit')
        wait_for_preview(other)
        other.evaluate('() => refreshPrinters()')
        assert other.evaluate('() => [dateFormat() === browserDateFormat(), collated()]') == [True, True]
    finally:
        other.context.close()
    # This browser keeps them
    designer.reload()
    wait_for_preview(designer)
    assert designer.evaluate('() => [dateFormat(), collated()]') == ['%d.%m.%Y', False]


def test_a_change_the_server_does_not_get_is_taken_back_in_plain_words(designer):
    designer.click('#printerChip')
    designer.route('**/api/settings', lambda route: route.abort())
    designer.click('[data-q="fast"]')
    designer.wait_for_selector('#toast:has-text("The label designer could not be reached")', timeout=5000)
    assert designer.inner_text('.pp [data-q].on') == 'Quality'
    # Nor without a printer, as where its status did not come either
    designer.evaluate("() => { $('#toast').hidden = true; S.printers = []; S.printer = null; closeLayer(true); openPrinterPanel(); }")
    designer.click('[data-q="fast"]')
    designer.wait_for_selector('#toast:has-text("The label designer could not be reached")', timeout=5000)


def test_a_status_asked_for_before_a_change_keeps_it(designer):
    # The status polled every 20 s may have been read on the server before
    # the change was saved
    designer.click('#printerChip')
    before = designer.evaluate("() => fetch(API + '/printer_status').then(r => r.text())")
    held = []
    designer.route('**/api/printer_status*', lambda route: held.append(route) if not held else route.continue_())
    designer.evaluate('() => { window.polled = refreshPrinters(); }')
    with designer.expect_response('**/api/settings'):
        designer.click('[data-q="fast"]')
    held[0].fulfill(content_type='application/json', body=before)
    designer.evaluate('() => window.polled')
    assert designer.inner_text('.pp [data-q].on') == 'Fast'
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('fast_printing')") == '1'


def test_a_refused_change_is_taken_back_on_the_printers_a_status_brought(designer):
    # The status replaces the printers while the change is on its way, the
    # one refused is taken back at once, not with the next status
    held = []
    designer.route('**/api/settings', lambda route: held.append(route))
    designer.click('#printerChip')
    designer.click('[data-q="fast"]')
    wait_for_held(designer, held)
    designer.evaluate('() => refreshPrinters()')
    assert designer.inner_text('.pp [data-q].on') == 'Fast'
    held[0].fulfill(status=400, json={'success': False, 'message': printer_settings.TOO_MANY_PRINTERS})
    designer.wait_for_selector(f'#toast:has-text("{printer_settings.TOO_MANY_PRINTERS}")', timeout=5000)
    assert designer.inner_text('.pp [data-q].on') == 'Quality'
    assert designer.evaluate('() => kept().quality') is None


def test_labels_of_a_printer_that_prints_the_bottom_first_leave_it_as_set(designer):
    # As its driver prints them, bottom first, until set otherwise
    designer.evaluate("""() => { C.models['VC-500W'] = Object.assign({}, C.models['VC-500W'], { leaves_turned: false });
        S.printer = Object.assign({}, S.printer, { model: 'VC-500W', settings: {} }); S.tape = 'vc25';
        openEditor({ title: 'V', design: Object.assign(baseDesign('vc25'), { text: [L('V', 40)] }) }); }""")
    wait_for_preview(designer)
    assert 'turned' not in designer.get_attribute('#edStage', 'class').split()
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('leaves_first')") is None
    designer.evaluate("() => openPrinterPanel()")
    assert designer.locator('.pp [data-lf="bottom"].on').count() == 1
    designer.click('.pp [data-lf="top"]')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    assert 'turned' in designer.get_attribute('#edStage', 'class').split()
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('leaves_first')") == 'top'


def test_test_label_names_the_ends_of_a_label_that_leaves_sideways(designer):
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'QL-800', settings: {} }); S.tape = '29x90';
        openEditor({ title: 'T', design: endsTest() }); }""")
    wait_for_preview(designer)
    # The printer on its left, its right end leaves first
    classes = designer.get_attribute('#edStage', 'class').split()
    assert 'side' in classes and 'back' not in classes
    ends = "tape => { S.tape = tape; return endsTest().text.map(l => [l.text, l.align]); }"
    assert designer.evaluate(ends, '29x90') == [['TOP', 'right'], ['Which end came out first?', 'center'], ['BOTTOM', 'left']]
    assert designer.evaluate(ends, '62') == [['TOP', 'center'], ['Which end came out first?', 'center'], ['BOTTOM', 'center']]


def test_receipt_shows_the_blank_paper_at_the_end_that_leaves_first(designer):
    receipt = """leaves => { S.printer = Object.assign({}, S.printer, { model: 'Epson/TM-T20II', settings: { leaves_first: leaves } }); S.tape = 'escpos80';
        openEditor({ title: 'R', design: Object.assign(baseDesign('escpos80'), { text: [L('R', 40)] }) }); }"""
    paper = "() => [...document.querySelectorAll('#edStage .paper > *')].map(e => e.tagName === 'IMG' ? 'label' : e.className)"
    for leaves, order in (('top', ['lead', 'label']), ('bottom', ['label', 'lead'])):
        designer.evaluate(receipt, leaves)
        wait_for_preview(designer)
        assert designer.evaluate(paper) == order, leaves


def test_test_label_button_says_why_it_is_disabled(designer):
    designer.evaluate("() => { S.printer = null; openPrinterPanel(); }")
    button = designer.locator('.pp #setLfTest')
    assert button.is_disabled() and button.get_attribute('title') == 'No printer answered'


def test_receipt_printers_are_named_alike(designer):
    assert designer.evaluate("() => printerName({ model: 'Epson/ESC-POS-180' })") == 'Epson ESC-POS'


def test_label_for_the_other_receipt_paper_is_shown_on_the_printers(designer):
    # The paper in the printer is not set: shown and printed on its size of
    # the same paper, as the server places it
    fake_printer(designer, model='Epson/ESC-POS-180')
    assert designer.evaluate('() => S.tapeAssumed')
    assert designer.evaluate("() => ['escpos80', 'escpos58', '62'].map(id => onTape(baseDesign(id)).label_size)") == [
        'escpos80-180', 'escpos58-180', '62']


def test_receipt_paper_kept_for_the_other_model_is_shown_as_the_one_used(designer):
    # escpos80 kept from before the printer was known to have a 180 dpi head:
    # the panel selects the size the server prints on, offered once
    fake_printer(designer, manual='escpos80', model='Epson/ESC-POS-180')
    designer.click('#printerChip')
    assert designer.evaluate("() => [S.tape, S.tapeManual, $('#setTape').value]") == ['escpos80-180', True, 'escpos80-180']
    names = designer.eval_on_selector_all('#setTape option', 'options => options.map(o => o.textContent)')
    assert names.count(designer.evaluate("() => sizeName('escpos80-180')")) == 1


def print_held(page, job, large=False):
    """Print on a QL-800 that tells the job, the answer held back, with large
    the preview shown large. The held request is returned."""
    printer = {'errors': [], 'model': 'QL-800', 'path': 'file:///dev/usb/lp0', 'media_width': 62, 'media_length': 0, 'job': job}
    body = json.dumps({**printer, 'printers': [printer], 'selected': printer['path']})
    page.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    page.reload()
    wait_for_preview(page)
    type_line(page, 0, 'Spices')
    if large:
        page.click('#edStage .zoom')
        wait_for_preview(page)
    pending = []
    page.route('**/api/print', lambda route: pending.append(route))
    page.click('#edPrint')
    return pending


def test_time_left_is_shown_while_printing(designer):
    # On every printer, not only the VC-500W that tells its stages
    pending = print_held(designer, {'labels': 3, 'label': 2, 'seconds_left': 40})
    designer.wait_for_function("() => document.querySelector('#toast').textContent === 'Label 2 of 3, about 40 s left'")
    pending[0].fulfill(content_type='application/json', body=json.dumps({'success': True}))
    assert printed(designer) == ['printed']


def overlap(a, b) -> bool:
    return a['x'] < b['x'] + b['width'] and b['x'] < a['x'] + a['width'] and a['y'] < b['y'] + b['height'] and b['y'] < a['y'] + a['height']


@pytest.mark.parametrize('width, height, large', [(1326, 924, False), (1326, 924, True), (1600, 900, False), (390, 844, False)])
def test_messages_leave_the_preview_controls_visible(designer, width, height, large):
    # Neither the cutting below the label nor the large preview button
    designer.set_viewport_size({'width': width, 'height': height})
    pending = print_held(designer, {'labels': 30, 'label': 12, 'stage': 'Waiting for the printer to cool down', 'seconds_left': 400}, large)
    designer.wait_for_function("() => document.querySelector('#toast').textContent.startsWith('Waiting')")
    toast = designer.locator('#toast').bounding_box()
    for selector in ('#edStage .cuts', '#edStage .zoom'):
        if designer.locator(selector).is_visible():
            assert not overlap(toast, designer.locator(selector).bounding_box()), selector
    if width > 900:
        # Over the preview, not over the settings beside it
        stage = designer.locator('#edStage').bounding_box()
        assert stage['x'] <= toast['x'] and toast['x'] + toast['width'] <= stage['x'] + stage['width']
    pending[0].fulfill(content_type='application/json', body=json.dumps({'success': True}))


@pytest.mark.parametrize('job, text', [
    ({'labels': 3, 'label': 2, 'seconds_left': 42}, 'Label 2 of 3, about 40 s left'),
    ({'labels': 1, 'label': 1, 'seconds_left': 130}, 'About 2 min left'),
    ({'labels': 1, 'label': 1, 'seconds_left': 70}, 'About 1 min left'),
    # A printer that does not tell its labels printed
    ({'labels': 20, 'seconds_left': 3}, 'A few seconds left'),
    ({'labels': 2}, ''),
    ({'stage': 'Preheating', 'label': 1, 'labels': 2, 'seconds_left': 75}, 'Preheating, label 1 of 2, about 1 min left'),
])
def test_time_left_in_words(designer, job, text):
    assert designer.evaluate('job => jobText(job)', job) == text


def test_printer_list_tells_a_missing_libusb(designer):
    hint = 'Printers on usb:// need libusb, please install it'
    simulator = {'errors': [], 'model': 'QL-800', 'path': 'simulation', 'status_type': 'Simulator'}
    body = json.dumps({**simulator, 'printers': [simulator], 'selected': 'simulation', 'hints': [hint]})
    designer.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    designer.reload()
    wait_for_preview(designer)
    designer.click('#printerChip')
    # Below the printers, the simulator is still offered and ready
    assert designer.eval_on_selector_all('.plist [data-printer]', 'rows => rows.map(r => r.dataset.printer)') == ['simulation']
    assert hint in designer.inner_text('.plist')
    assert designer.evaluate('() => printerState(S.printer).text') == 'Ready (simulated)'
