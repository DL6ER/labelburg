"""The demo printer of the label designer: any model the drivers know,
chosen in the Settings, with the tapes of that model."""
import contextlib
import os

import pytest

from app.labeldesigner import drivers, printer_settings, tape
from app.labeldesigner.drivers import MODELS
from tests.browser.test_editor import sent, type_label, wait_for_preview
from tests.browser.test_settings import back_to_the_editor, open_settings, setting

MODEL = '[data-card="0"] select[data-k="demo_model"]'


@pytest.fixture
def designer(browser, server, app):
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
    # The model chosen here does not reach other tests
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    assert errors == []


def test_the_demo_printer_is_named_so(designer):
    assert designer.inner_text('#printerChip b') == 'Demo printer'
    # The printer drawn below the label names its model
    assert designer.text_content('#slot .model') == designer.text_content('#vslot .model') == 'Demo Brother QL-800'
    open_settings(designer)
    card = designer.inner_text('[data-card="0"]')
    assert 'Demo printer' in card and 'Brother QL-800, Ready, prints nothing' in card
    assert 'simulat' not in card.lower()


def test_the_demo_printer_names_its_roll_without_a_warning(designer):
    # Any roll of its model, none is missing
    assert designer.is_hidden('#tapeNote')
    designer.click('#printerChip')
    assert designer.inner_text('.pp [data-p="0"] small') == 'Brother QL-800, Ready, prints nothing, 62 mm endless'
    designer.keyboard.press('Escape')
    open_settings(designer)
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'VC-500W')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'VC-500W'")
    back_to_the_editor(designer)
    assert designer.is_hidden('#tapeNote')
    designer.click('#printerChip')
    assert designer.inner_text('.pp [data-p="0"] small') == 'Brother VC-500W, Ready, prints nothing, 9 mm color tape'


def test_the_demo_printer_offers_every_model(designer):
    open_settings(designer)
    assert designer.input_value(MODEL) == 'QL-800'
    assert designer.eval_on_selector_all(f'{MODEL} optgroup', 'gs => gs.map(g => g.label)') == [
        'Brother', 'DYMO', 'Epson', 'Niimbot', 'PUQU', 'TSC', 'Zebra']
    # Once each name: not the models shown as another one, nor "Other printer"
    shown_as = {model for driver in drivers.DRIVERS for model in driver.shown_as}
    assert shown_as == {'Epson/ESC-POS-180', 'TSC/TSPL-300', 'Zebra/ZPL-300', 'DYMO/LabelWriter-SE450',
                        'Other/IPP', 'Other/IPP-203'}
    assert sorted(designer.eval_on_selector_all(f'{MODEL} option', 'os => os.map(o => o.value)')) == sorted(set(MODELS) - shown_as)
    for vendor, models in (('Epson', ['ESC-POS']), ('TSC', ['TSPL']), ('Zebra', ['ZPL']),
                           ('DYMO', ['LabelManager 280', 'LabelManager 420P', 'LabelManager PC II', 'LabelManager PnP',
                                     'LabelWriter 4XL', 'LabelWriter 450', 'LabelWriter 450 Twin Turbo'])):
        assert designer.eval_on_selector_all(f'{MODEL} optgroup[label="{vendor}"] option', 'os => os.map(o => o.textContent)') == models
    brother = designer.eval_on_selector_all(f'{MODEL} optgroup[label="Brother"] option', 'os => os.map(o => o.textContent)')
    assert brother[:7] == ['PT-E550W', 'PT-P700', 'PT-P750W', 'PT-P900W', 'PT-P950NW', 'QL-500', 'QL-550']
    assert brother[-2:] == ['QL-1115NWB', 'VC-500W']
    # Shown without the maker, the group names it
    assert designer.eval_on_selector_all(f'{MODEL} optgroup[label="Niimbot"] option', 'os => os.map(o => o.textContent)') == ['B1', 'D110_M']


def test_the_model_chosen_brings_its_tapes_and_prints_as_it(designer):
    open_settings(designer)
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option(MODEL, 'VC-500W')
    assert answer.value.ok
    assert sent(answer.value.request) == {'printer': 'simulation', 'model': 'QL-800', 'demo_model': 'VC-500W'}
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'VC-500W' && labelEditor.S.tape === 'vc9'")
    assert designer.input_value(MODEL) == 'VC-500W'
    assert 'Brother VC-500W' in designer.inner_text('[data-card="0"]')
    offered = designer.eval_on_selector_all('[data-card="0"] select[data-k="label_size"] option', 'os => os.map(o => o.value)')
    assert offered == [''] + tape.takes('VC-500W')
    assert designer.input_value('[data-card="0"] select[data-k="label_size"]') == 'vc9'
    assert 'Any roll this model takes.' in designer.inner_text('[data-card="0"]')
    back_to_the_editor(designer)
    assert designer.text_content('#slot .model') == 'Demo Brother VC-500W'
    type_label(designer, 'Shelf 3')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    fields = sent(request.value)
    assert (fields['printer'], fields['model'], fields['label_size']) == ('simulation', 'VC-500W', 'vc9')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
    # Kept on the server, another visit shows it
    designer.reload()
    open_settings(designer)
    assert designer.input_value(MODEL) == 'VC-500W'


def test_a_dymo_labelwriter_offers_its_address_labels(designer):
    open_settings(designer)
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option(MODEL, 'DYMO/LabelWriter-450')
    assert answer.value.ok
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'DYMO/LabelWriter-450' && labelEditor.S.tape === 'dymo28x89'")
    sizes = '[data-card="0"] select[data-k="label_size"]'
    offered = designer.eval_on_selector_all(f'{sizes} option', 'os => os.map(o => o.value)')
    assert offered == [''] + tape.takes('DYMO/LabelWriter-450') and 'dymo102x152' not in offered
    assert designer.eval_on_selector(f'{sizes} option[value="dymo28x89"]', 'o => o.textContent') == '28 x 89 mm'
    assert designer.input_value(sizes) == 'dymo28x89'
    card = designer.inner_text('[data-card="0"]')
    assert 'DYMO LabelWriter 450' in card and 'Torn off' in card
    back_to_the_editor(designer)
    assert designer.text_content('#slot .model') == 'Demo DYMO LabelWriter 450'
    type_label(designer, 'Jane Doe')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    fields = sent(request.value)
    assert (fields['printer'], fields['model'], fields['label_size']) == ('simulation', 'DYMO/LabelWriter-450', 'dymo28x89')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')


def test_a_dymo_labelmanager_offers_its_tapes(designer):
    open_settings(designer)
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option(MODEL, 'DYMO/LabelManager-280')
    assert answer.value.ok
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'DYMO/LabelManager-280' && labelEditor.S.tape === 'd1-12-280'")
    sizes = '[data-card="0"] select[data-k="label_size"]'
    offered = designer.eval_on_selector_all(f'{sizes} option', 'os => os.map(o => o.value)')
    assert offered == ['', 'd1-12-280', 'd1-6-280', 'd1-9'] == [''] + tape.takes('DYMO/LabelManager-280')
    assert designer.eval_on_selector_all(f'{sizes} option', 'os => os.map(o => o.textContent)')[1:] == ['12 mm endless', '6 mm endless', '9 mm endless']
    card = designer.inner_text('[data-card="0"]')
    assert 'DYMO LabelManager 280' in card and 'Cut by hand' in card
    back_to_the_editor(designer)
    assert designer.text_content('#slot .model') == 'Demo DYMO LabelManager 280'
    type_label(designer, 'Fuse box')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    fields = sent(request.value)
    assert (fields['printer'], fields['model'], fields['label_size']) == ('simulation', 'DYMO/LabelManager-280', 'd1-12-280')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')


def test_a_model_listed_as_another_one_is_named_while_chosen(designer):
    base = designer.url.split('/labeldesigner')[0]
    for model, name, card in (('DYMO/LabelWriter-SE450', 'LabelWriter SE450', 'DYMO LabelWriter SE450'),
                              ('Zebra/ZPL-300', 'ZPL-300', 'Zebra ZPL')):
        assert designer.request.post(f'{base}/labeldesigner/api/settings', form={'printer': 'simulation', 'demo_model': model}).ok
        designer.reload()
        open_settings(designer)
        assert designer.input_value(MODEL) == model
        assert designer.eval_on_selector(f'{MODEL} option[value="{model}"]', 'o => o.textContent') == name
        assert f'{card}, Ready' in designer.inner_text('[data-card="0"]')


def test_a_twin_turbo_prints_on_the_roll_chosen(designer):
    choice = '[data-card="0"] button[data-k="side"]'
    open_settings(designer)
    # A printer of one roll offers none
    assert designer.query_selector(choice) is None
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'DYMO/LabelWriter-450-Twin-Turbo')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'DYMO/LabelWriter-450-Twin-Turbo'")
    designer.wait_for_selector(choice)
    assert designer.eval_on_selector_all(choice, 'bs => bs.map(b => b.textContent)') == ['Left roll', 'Right roll']
    # The left one until another is chosen
    assert designer.inner_text(f'{choice}.on') == 'Left roll'
    # The choice says it, no hint
    assert designer.locator('[data-card="0"] button[data-k="side"]').first.locator('xpath=../..').locator('small').count() == 0
    assert setting(designer, f'{choice}[data-v="right"]') == {
        'printer': 'simulation', 'model': 'DYMO/LabelWriter-450-Twin-Turbo', 'side': 'right'}
    assert designer.inner_text(f'{choice}.on') == 'Right roll'
    # Kept on the server, another visit shows it
    designer.reload()
    open_settings(designer)
    assert designer.inner_text(f'{choice}.on') == 'Right roll'
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'DYMO/LabelWriter-4XL')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'DYMO/LabelWriter-4XL'")
    assert designer.query_selector(choice) is None


def test_a_model_the_server_refuses_is_taken_back(designer):
    open_settings(designer)
    designer.route('**/api/settings', lambda route: route.fulfill(status=400, json={'success': False, 'message': 'Not now'}))
    designer.select_option(MODEL, 'QL-1100')
    designer.wait_for_selector('#toast:has-text("Not now")')
    assert designer.input_value(MODEL) == 'QL-800'
    assert designer.evaluate('() => labelEditor.S.printers[0].model') == 'QL-800'


def test_a_zpl_printer_is_told_how_it_finds_its_labels(designer):
    choice = '[data-card="0"] button[data-k="sensing"]'
    open_settings(designer)
    # A QL printer finds them by itself
    assert designer.query_selector(choice) is None
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'Zebra/ZPL')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'Zebra/ZPL'")
    designer.wait_for_selector(choice)
    assert designer.eval_on_selector_all(choice, 'bs => bs.map(b => b.textContent)') == [
        'Labels with gaps', 'Black marks on the back', 'Continuous roll']
    # None is chosen while none is set, the printer goes on as last told
    assert designer.query_selector(f'{choice}.on') is None
    assert 'Choose what is on the roll.' in designer.inner_text('[data-card="0"]')
    assert setting(designer, f'{choice}[data-v="gap"]') == {'printer': 'simulation', 'model': 'Zebra/ZPL', 'sensing': 'gap'}
    assert designer.inner_text(f'{choice}.on') == 'Labels with gaps'
    assert 'How the printer finds where each label starts.' in designer.inner_text('[data-card="0"]')
    assert setting(designer, f'{choice}[data-v="mark"]') == {'printer': 'simulation', 'model': 'Zebra/ZPL', 'sensing': 'mark'}
    assert designer.inner_text(f'{choice}.on') == 'Black marks on the back'
    setting(designer, f'{choice}[data-v="endless"]')
    assert 'Each label is as long as the tape chosen above.' in designer.inner_text('[data-card="0"]')
    # Kept on the server, another visit shows it
    designer.reload()
    open_settings(designer)
    assert designer.inner_text(f'{choice}.on') == 'Continuous roll'


def test_the_roll_of_a_zpl_printer_is_named_as_set(designer):
    open_settings(designer)
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'Zebra/ZPL')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'Zebra/ZPL'")
    size = designer.evaluate("() => labelEditor.S.printers[0].tape.id")
    # Not named while not set
    assert designer.get_attribute('#printerChip', 'title').endswith(' mm')
    setting(designer, '[data-card="0"] button[data-k="sensing"][data-v="gap"]')
    designer.wait_for_function("() => document.querySelector('#printerChip').title.endsWith(' mm, labels with gaps')")
    setting(designer, '[data-card="0"] button[data-k="sensing"][data-v="endless"]')
    designer.wait_for_function("() => labelEditor.S.printers[0].tape.roll === 'Continuous roll'")
    back_to_the_editor(designer)
    # The label size stays, the roll is named
    assert designer.evaluate("() => labelEditor.S.printers[0].tape.id") == size
    assert designer.get_attribute('#printerChip', 'title').endswith(' mm, continuous roll')
    designer.click('#printerChip')
    shown = designer.inner_text('.pp [data-p="0"] small')
    assert shown.startswith('Zebra ZPL, Ready, prints nothing, ') and shown.endswith(' mm, continuous roll')
    designer.keyboard.press('Escape')
    open_settings(designer)
    setting(designer, '[data-card="0"] button[data-k="sensing"][data-v="mark"]')
    designer.wait_for_function("() => labelEditor.S.printers[0].tape.roll === 'Black marks on the back'")
    back_to_the_editor(designer)
    designer.click('#printerChip')
    assert designer.inner_text('.pp [data-p="0"] small').endswith(' mm, black marks on the back')


def test_a_tspl_printer_offers_4x6_labels_and_the_roll(designer):
    designer.set_viewport_size({'width': 1600, 'height': 924})
    open_settings(designer)
    with designer.expect_response('**/api/settings'):
        designer.select_option(MODEL, 'TSC/TSPL')
    designer.wait_for_function("() => labelEditor.S.printers[0].model === 'TSC/TSPL'")
    assert 'TSC TSPL' in designer.inner_text('[data-card="0"]')
    sizes = '[data-card="0"] select[data-k="label_size"]'
    offered = designer.eval_on_selector_all(f'{sizes} option', 'os => os.map(o => o.value)')
    assert offered == [''] + tape.takes('TSC/TSPL') and 'tspl102x152' in offered
    # 4 x 6 in, among the die-cut labels
    assert designer.eval_on_selector(f'{sizes} option[value="tspl102x152"]', 'o => [o.parentNode.label, o.textContent]') == [
        'Die-cut', '102 x 152 mm']
    choice = '[data-card="0"] button[data-k="sensing"]'
    assert designer.eval_on_selector_all(choice, 'bs => bs.map(b => b.textContent)') == [
        'Labels with gaps', 'Black marks on the back', 'Continuous roll']
    # None is chosen while none is set
    assert designer.query_selector(f'{choice}.on') is None
    with designer.expect_response('**/api/settings'):
        designer.select_option(sizes, 'tspl102x152')
    assert setting(designer, f'{choice}[data-v="mark"]') == {'printer': 'simulation', 'model': 'TSC/TSPL', 'sensing': 'mark'}
    designer.wait_for_function("() => labelEditor.S.printers[0].tape.roll === 'Black marks on the back'")
    back_to_the_editor(designer)
    assert designer.text_content('#slot .model') == 'Demo TSC TSPL'
    type_label(designer, 'Parcel 7')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    fields = sent(request.value)
    assert (fields['printer'], fields['model'], fields['label_size']) == ('simulation', 'TSC/TSPL', 'tspl102x152')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
