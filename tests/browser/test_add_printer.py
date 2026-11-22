"""Adding a printer the search does not find in the Settings of the label
designer: asked what it is first, then listed by its name and printed on,
and removed again. Where it may be is up to ADD_PRINTERS of the server."""
import json
import os

import pytest

from app.labeldesigner import adding, printer_settings
from tests.browser.test_editor import sent, type_label
from tests.browser.test_settings import back_to_the_editor, designer, open_settings  # noqa: F401
from tests.printing.test_other_printer import FakePrinter, printer_attributes
from tests.printing.test_usbip import FakeQL
from tests.printing.usbip_host import FakeHost, SharedDevice


@pytest.fixture
def shared():
    host = FakeHost(SharedDevice())
    host.ql = FakeQL(host.devices[0])
    yield host
    host.close()


def kept(app) -> dict:
    with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), encoding='utf-8') as fh:
        return json.load(fh)


def test_a_shared_printer_is_added_printed_on_and_removed(designer, app, shared):  # noqa: F811
    open_settings(designer)
    designer.wait_for_selector('#setAdd:not([hidden]) #addAddress')
    assert adding.ALLOWS['local'] in designer.inner_text('#setAdd')
    designer.click('#setAdd [data-example^="usbip"]')
    assert designer.input_value('#addAddress') == 'usbip://'
    assert designer.evaluate('() => document.activeElement.id') == 'addAddress'
    designer.keyboard.type(shared.address().removeprefix('usbip://'))
    designer.keyboard.press('Enter')
    designer.wait_for_selector('.set-found')
    found = designer.inner_text('.set-found')
    assert 'Brother QL-1100' in found and 'in the printer' in found and shared.address() in found
    # Only asked, nothing kept yet
    assert not os.path.exists(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    assert designer.evaluate('() => document.activeElement.id') == 'addName'
    designer.keyboard.type('Garage')
    designer.click('#addSave')
    designer.wait_for_selector('#toast:has-text("Added Garage")')
    card = designer.locator('[data-card]', has_text='Garage')
    card.wait_for()
    assert card.locator('[data-remove]').count() == 1
    assert designer.locator('[data-card]', has_text='Simulator').locator('[data-remove]').count() == 0
    assert designer.input_value('#addAddress') == '' and designer.locator('.set-found').count() == 0
    assert kept(app)['added'] == [{'address': shared.address(), 'model': 'QL-1100', 'name': 'Garage'}]

    back_to_the_editor(designer)
    designer.click('#printerChip')
    designer.locator('.pp [data-p]', has_text='Garage').click()
    designer.wait_for_selector('#printerChip b:text-is("Garage")')
    designer.keyboard.press('Escape')
    designer.locator('#layer').wait_for(state='hidden')
    type_label(designer, 'Shed')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    assert (sent(request.value)['printer'], sent(request.value)['model']) == (shared.address(), 'QL-1100')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
    assert shared.ql.received.endswith(b'\x1a')

    open_settings(designer)
    designer.locator('[data-card]', has_text='Garage').locator('[data-remove]').click()
    designer.wait_for_selector('#toast:has-text("Removed Garage")')
    designer.locator('[data-card]', has_text='Garage').wait_for(state='detached')
    assert kept(app)['added'] == []
    # Printed on the printer left
    assert designer.evaluate('() => labelEditor.S.printer.path') == 'simulation'


def test_addresses_the_server_does_not_allow_are_refused_plainly(designer):  # noqa: F811
    open_settings(designer)
    designer.wait_for_selector('#setAdd:not([hidden]) #addAddress')
    designer.fill('#addAddress', 'usbip://8.8.8.8')
    designer.click('#addAsk')
    designer.wait_for_selector('#setAdd .set-bad')
    assert designer.inner_text('#setAdd .set-bad') == adding.ONLY_LOCAL
    assert designer.locator('#addSave').count() == 0
    # Typing again clears it
    designer.press('#addAddress', 'Backspace')
    designer.wait_for_selector('#setAdd .set-bad', state='detached')
    assert designer.evaluate('() => document.activeElement.id') == 'addAddress'


def test_added_printers_are_not_used_while_adding_is_off(designer, app, shared):  # noqa: F811
    with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w', encoding='utf-8') as fh:
        json.dump({'printers': {}, 'added': [{'address': shared.address(), 'model': 'QL-1100', 'name': 'Garage'}]}, fh)
    app.config['ADD_PRINTERS'] = 'off'
    try:
        open_settings(designer)
        designer.wait_for_selector('#setAdd:not([hidden]) .set-unused')
        assert adding.ALLOWS['off'] in designer.inner_text('#setAdd')
        assert 'Garage' in designer.inner_text('.set-unused') and 'not used' in designer.inner_text('.set-unused')
        assert designer.locator('#addAddress').count() == 0
        assert designer.locator('[data-card]', has_text='Garage').count() == 0
        designer.click('.set-unused [data-remove]')
        designer.wait_for_selector('#toast:has-text("Removed Garage")')
        designer.wait_for_selector('.set-unused', state='detached')
        assert kept(app)['added'] == []
        assert shared.connections == []
    finally:
        app.config['ADD_PRINTERS'] = 'local'


@pytest.fixture
def on_ipp():
    printer = FakePrinter()
    yield printer
    printer.close()


def test_a_printer_on_ipp_is_added_as_other_printer(designer, app, on_ipp):  # noqa: F811
    designer.set_viewport_size({'width': 1600, 'height': 924})
    open_settings(designer)
    designer.wait_for_selector('#setAdd:not([hidden]) #addAddress')
    designer.click('#setAdd [data-example^="ipp"]')
    assert designer.input_value('#addAddress') == 'ipp://'
    designer.keyboard.type(on_ipp.address.removeprefix('ipp://'))
    designer.keyboard.press('Enter')
    designer.wait_for_selector('.set-found')
    assert designer.inner_text('.set-found b') == 'Other printer (Zebra ZD421)'
    assert on_ipp.address in designer.inner_text('.set-found')
    designer.keyboard.type('Shipping')
    designer.click('#addSave')
    designer.wait_for_selector('#toast:has-text("Added Shipping")')
    card = designer.locator('[data-card]', has_text='Shipping')
    card.wait_for()
    # Its roll as it tells it, the cutting as it is set up
    assert 'read from the printer' in card.inner_text()
    assert 'As set on the printer' in card.inner_text()
    assert 'Set on the printer itself.' not in card.inner_text()
    assert kept(app)['added'] == [{'address': on_ipp.address, 'model': 'Other/IPP-203', 'name': 'Shipping'}]

    back_to_the_editor(designer)
    designer.click('#printerChip')
    designer.locator('.pp [data-p]', has_text='Shipping').click()
    designer.wait_for_selector('#printerChip b:text-is("Shipping")')
    designer.keyboard.press('Escape')
    designer.locator('#layer').wait_for(state='hidden')
    type_label(designer, 'Parcel')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    assert (sent(request.value)['printer'], sent(request.value)['label_size']) == (on_ipp.address, 'ipp203-102x152')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
    assert len(on_ipp.jobs()) == 1


def test_an_other_printer_that_does_not_tell_its_roll(designer, app, on_ipp):  # noqa: F811
    on_ipp.printer = [printer_attributes(loaded=None)]
    with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE), 'w', encoding='utf-8') as fh:
        json.dump({'printers': {}, 'added': [{'address': on_ipp.address, 'model': 'Other/IPP-203', 'name': 'Shipping'}]}, fh)
    designer.set_viewport_size({'width': 1600, 'height': 924})
    designer.reload()
    open_settings(designer)
    card = designer.locator('[data-card]', has_text='Shipping')
    card.wait_for()
    assert 'This printer does not report its tape' in card.inner_text()
    assert 'read from the printer' not in card.inner_text()
