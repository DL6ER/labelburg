"""A label printer the search found that Labelburg cannot print on yet: its
card in the Settings under Printers, the report to copy and the form for new
printers on GitHub, opened only by the user."""
import json
from urllib.request import urlopen

import pytest

from app.labeldesigner import unknown
from tests.browser.test_editor import wait_for_preview
from tests.browser.test_settings import open_settings

DYMO = unknown.on_usb('MFG:DYMO;CMD: ;MDL:LabelWriter 450;CLS:PRINTER;DES:DYMO LabelWriter 450;SN:12345678901;', (0x0922, 0x0020))
B21 = unknown.on_bluetooth({'address': '00:00:5E:00:53:01', 'name': 'B21-H425040311', 'le': True, 'services': []})
CARD = '.set-unknown'


@pytest.fixture
def page(browser, server, monkeypatch):
    found = []
    monkeypatch.setattr('app.labeldesigner.routes.unknown_printers', lambda config: found)
    context = browser.new_context(viewport={'width': 1600, 'height': 1000}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.found = found
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    assert errors == []


def found_by_the_server(server):
    with urlopen(f'{server}/labeldesigner/api/printer_status') as r:
        return json.load(r)['unknown']


def test_a_printer_found_is_reported_on_github_only_by_the_user(page, server):
    page.found += [DYMO, B21]
    requests = []
    page.on('request', lambda r: requests.append(r.url))
    open_settings(page)
    page.click('#setLook')
    page.wait_for_selector(f'{CARD} >> nth=1')
    dymo, b21 = found_by_the_server(server)
    cards = page.locator(CARD)
    assert cards.nth(0).locator('b').inner_text() == 'Labelburg found a printer it cannot print on yet: DYMO LabelWriter 450'
    assert cards.nth(1).locator('b').inner_text() == 'Labelburg found a printer it cannot print on yet: Niimbot B21'
    # Plain: the IDs are in the report only
    text = cards.nth(0).inner_text()
    assert '0922' not in text and 'MFG' not in text and dymo['report'].split('\n')[2] not in text
    link = cards.nth(0).locator('a:text-is("Report it on GitHub")')
    assert (link.get_attribute('href'), link.get_attribute('target'), link.get_attribute('rel')) == (dymo['report_url'], '_blank', 'noopener')
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    cards.nth(1).locator('button:text-is("Copy report")').click()
    page.wait_for_function('text => navigator.clipboard.readText().then(t => t === text)', arg=b21['report'])
    page.wait_for_selector('#toast:has-text("Copied")')
    assert page.locator('[data-report]').count() == 0
    # Asked first like every link to the internet, the form opens in a tab of its own
    page.evaluate('() => { window.open = (...args) => { window.opened = args; return null; }; }')
    link.click()
    page.wait_for_selector('.dialog.ask h2:text-is("Open github.com?")')
    page.click('#leaveGo')
    assert page.evaluate('() => window.opened') == [dymo['report_url'], '_blank', 'noopener']
    # Nothing went anywhere but to this server
    assert [u for u in requests if not u.startswith(server)] == []


def test_a_report_the_browser_cannot_copy_is_shown_to_copy_by_hand(page, server):
    page.found.append(DYMO)
    # e.g. the label designer opened without HTTPS by its address in the network
    page.add_init_script('Object.defineProperty(navigator, "clipboard", { value: undefined })')
    page.reload()
    page.wait_for_selector('#field .ln')
    open_settings(page)
    page.click(f'{CARD} button:text-is("Copy report")')
    page.wait_for_selector('#toast:has-text("Press Ctrl C to copy")')
    [dymo] = found_by_the_server(server)
    assert page.inner_text(f'{CARD} [data-report]') == dymo['report']
    assert page.evaluate('() => getSelection().toString()') == dymo['report']


def test_no_card_without_a_printer_found(page):
    open_settings(page)
    page.wait_for_selector('#setPrinters [data-card="0"]')
    assert page.locator(CARD).count() == 0
