"""Adding a printer the search does not find in the Settings of the label
designer: asked what it is first, then listed by its name and printed on,
and removed again. Where it may be is up to ADD_PRINTERS of the server."""
import json
import os
import re

import pytest

from app.labeldesigner import adding, printer_settings
from tests.browser.test_editor import sent, type_label
from tests.browser.test_settings import back_to_the_editor, designer, open_settings  # noqa: F401
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
    # The job ends with "print with feed", status requests of the label
    # designer following the printer may come after it
    assert re.fullmatch(rb'.*\x1a(\x1biS)*', shared.ql.received, re.S)

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
