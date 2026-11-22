"""The demo printer of the label designer: any model the drivers know,
chosen in the Settings, with the tapes of that model."""
import contextlib
import os

import pytest

from app.labeldesigner import printer_settings, tape
from app.labeldesigner.drivers import MODELS
from tests.browser.test_editor import sent, type_label, wait_for_preview
from tests.browser.test_settings import back_to_the_editor, open_settings

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
    assert designer.eval_on_selector_all(f'{MODEL} optgroup', 'gs => gs.map(g => g.label)') == ['Brother', 'Epson', 'Niimbot', 'PUQU', 'Zebra']
    assert sorted(designer.eval_on_selector_all(f'{MODEL} option', 'os => os.map(o => o.value)')) == sorted(MODELS)
    brother = designer.eval_on_selector_all(f'{MODEL} optgroup[label="Brother"] option', 'os => os.map(o => o.textContent)')
    assert brother[:15] == ['PT-D410', 'PT-D460BT', 'PT-D610BT', 'PT-E310BT', 'PT-E550W', 'PT-E560BT', 'PT-P700', 'PT-P710BT',
                            'PT-P750W', 'PT-P900', 'PT-P900W', 'PT-P910BT', 'PT-P950NW', 'QL-500', 'QL-550']
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


@pytest.mark.parametrize('model, tapes, cutting', [
    ('PT-D610BT', ['pt6', 'pt9', 'pt12', 'pt18', 'pt24'], 'Cut at the printer'),
    ('PT-E310BT', ['pt6', 'pt9', 'pt12', 'pt18'], 'Cut by hand')])
def test_the_demo_printer_offers_the_pt_d460bt_family_with_its_tapes(designer, model, tapes, cutting):
    open_settings(designer)
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option(MODEL, model)
    assert answer.value.ok
    designer.wait_for_function(f"() => labelEditor.S.printers[0].model === '{model}' && labelEditor.S.tape === 'pt6'")
    offered = designer.eval_on_selector_all('[data-card="0"] select[data-k="label_size"] option', 'os => os.map(o => o.value)')
    assert offered == [''] + tapes
    card = designer.inner_text('[data-card="0"]')
    assert f'Brother {model}' in card and cutting in card
    assert 'This printer prints in one quality only.' in card
    back_to_the_editor(designer)
    assert designer.text_content('#slot .model') == f'Demo Brother {model}'


def test_a_model_the_server_refuses_is_taken_back(designer):
    open_settings(designer)
    designer.route('**/api/settings', lambda route: route.fulfill(status=400, json={'success': False, 'message': 'Not now'}))
    designer.select_option(MODEL, 'QL-1100')
    designer.wait_for_selector('#toast:has-text("Not now")')
    assert designer.input_value(MODEL) == 'QL-800'
    assert designer.evaluate('() => labelEditor.S.printers[0].model') == 'QL-800'
