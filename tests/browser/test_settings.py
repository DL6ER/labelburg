"""The Settings tab of the label designer at /labeldesigner/: printer
settings reach the next print, the tape is set only where the printer does
not report it, the printer chip switches printers, and the settings of
labels the server keeps for every browser."""
import contextlib
import json
import os
from datetime import datetime
from urllib.parse import quote, urlencode
from urllib.request import urlopen

import pytest

from app.labeldesigner import printer_settings
from app.labeldesigner.dates import format_date
from tests.browser.helpers import shown_again, with_tape
from tests.browser.test_editor import sent, type_label, wait_for_preview


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printers these tests name are known, as if the search found them
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {'tcp://192.0.2.10', 'tcp://192.0.2.11'})


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
    # The settings made here do not reach other tests
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    assert errors == []


def label_settings(server, **changes):
    """The label settings the server keeps, changed first where changes are
    given."""
    with urlopen(f'{server}/labeldesigner/api/settings/labels', urlencode(changes).encode() if changes else None) as r:
        answer = json.load(r)
    return answer['settings'] if changes else answer


def other_family(page):
    # A family of the menu named as its fonts are, the lines tell it by their font
    return page.evaluate("""() => window.DESIGNER_CONFIG.font_families.map(f => f.name)
        .find(f => f !== window.DESIGNER_CONFIG.default_font.split(',')[0] && window.DESIGNER_CONFIG.font_families.find(x => x.name === f).regular.startsWith(f + ','))""")


def choose_font(page, family):
    """The font of new labels chosen in the font menu"""
    page.click('#setFont')
    page.click(f'.font-menu .fm-o[data-family="{family}"] >> nth=-1')


def printers_are(page, server, *printers):
    """Answer the status queries with these printers, each with the settings
    the server keeps for it and the tape it finds, and load the page again."""
    def answer(route):
        told = []
        for p in printers:
            with urlopen(f"{server}/labeldesigner/api/settings?printer={quote(p['path'], safe='')}&model={p['model']}") as r:
                told.append(with_tape({'errors': [], 'media_width': None, 'media_length': 0, **p, 'settings': json.load(r)}))
        route.fulfill(json={**told[0], 'printers': told, 'selected': told[0]['path']})
    page.route('**/api/printer_status*', answer)
    page.reload()
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


def open_settings(page, part='printers'):
    page.click('#tabs [data-tab="settings"]')
    if part != 'printers':
        page.click(f'.set-nav [data-part="{part}"]')
    page.wait_for_selector(f'#setPart[data-part="{part}"]')


def back_to_the_editor(page):
    page.click('#tabs [data-tab="edit"]')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


def setting(page, selector):
    with page.expect_response('**/api/settings') as answer:
        page.click(selector)
    assert answer.value.ok
    return sent(answer.value.request)


def test_quality_and_cutting_set_in_settings_reach_the_next_print(designer, server):
    type_label(designer, 'Shelf 3')
    open_settings(designer)
    assert designer.inner_text('#setPrinters [data-card="0"] [data-k="quality"].on') == 'Quality'
    path = designer.evaluate('() => labelEditor.S.printers[0].path')
    assert setting(designer, '[data-card="0"] [data-k="quality"][data-v="fast"]') == {'printer': path, 'model': 'QL-800', 'quality': 'fast'}
    assert setting(designer, '[data-card="0"] [data-k="cut"][data-v="last"]') == {'printer': path, 'model': 'QL-800', 'cut': 'last'}
    assert designer.inner_text('[data-card="0"] [data-k="cut"].on') == 'After the last'
    assert 'Cut after the last label.' in designer.inner_text('[data-card="0"]')
    back_to_the_editor(designer)
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    fields = sent(request.value)
    assert (fields['fast_printing'], fields['high_res'], fields['cut']) == ('1', '0', 'last')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
    # Kept on the server, another visit shows them
    designer.goto(server + '/labeldesigner/#settings/printers')
    designer.wait_for_selector('#setPrinters [data-card="0"]')
    assert designer.inner_text('[data-card="0"] [data-k="quality"].on') == 'Fast'
    assert designer.inner_text('[data-card="0"] [data-k="cut"].on') == 'After the last'


@pytest.mark.parametrize('asked', ['follow', 'poll'])
def test_a_status_asked_before_a_setting_was_saved_leaves_it_shown(designer, asked):
    # Read before the change reached the server, it comes after it
    open_settings(designer)
    stale = designer.evaluate(f"""() => {{ const p = labelEditor.S.printer; p.follow = {str(asked == 'follow').lower()};
        return JSON.parse(JSON.stringify(p)); }}""")
    held = []
    designer.route('**/api/printer_status?printer=*' if asked == 'follow' else '**/api/printer_status', lambda route: held.append(route))
    designer.evaluate(f'() => {{ labelEditor.{asked}(); }}')
    for _ in range(50):
        if held:
            break
        designer.wait_for_timeout(100)
    assert held
    assert setting(designer, '[data-card="0"] [data-k="quality"][data-v="fast"]')['quality'] == 'fast'
    told = dict(stale, settings={})
    held[0].fulfill(json={'printers': [told]} if asked == 'follow' else {**told, 'printers': [told], 'selected': told['path']})
    designer.wait_for_timeout(300)
    assert designer.inner_text('[data-card="0"] [data-k="quality"].on') == 'Fast'
    assert designer.evaluate('() => labelEditor.S.printer.settings.quality') == 'fast'


def test_a_status_asked_before_a_tape_was_set_leaves_it_shown(designer):
    open_settings(designer)
    stale = designer.evaluate('() => JSON.parse(JSON.stringify(labelEditor.S.printer))')
    held = []
    designer.route('**/api/printer_status', lambda route: held.append(route))
    designer.evaluate('() => { labelEditor.poll(); }')
    for _ in range(50):
        if held:
            break
        designer.wait_for_timeout(100)
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option('[data-card="0"] select[data-k="label_size"]', '29')
    assert answer.value.ok
    designer.wait_for_function("() => labelEditor.S.printer.tape.manual && labelEditor.S.printer.tape.id === '29'")
    held[0].fulfill(json={**stale, 'printers': [stale], 'selected': stale['path']})
    designer.wait_for_timeout(300)
    assert designer.evaluate('() => [labelEditor.S.printer.tape.id, labelEditor.S.tape]') == ['29', '29']
    assert designer.input_value('[data-card="0"] select[data-k="label_size"]') == '29'
    for route in held[1:]:
        route.continue_()


def test_the_tape_is_set_only_where_the_printer_does_not_report_it(designer, server):
    printers_are(designer, server,
                 {'path': 'simulation', 'model': 'QL-800', 'name': 'Kitchen', 'media_width': 29, 'media_length': 90, 'red_roll': False},
                 {'path': 'tcp://192.0.2.10', 'model': 'QL-1100', 'name': 'Garage'},
                 {'path': 'tcp://192.0.2.11', 'model': 'QL-810W', 'name': 'Cellar', 'status_type': 'Error',
                  'errors': ['The printer is switched off or not connected']})
    open_settings(designer)
    reported, unreported = designer.locator('[data-card="0"]'), designer.locator('[data-card="1"]')
    assert reported.locator('select').count() == 0
    assert '29 x 90 mm' in reported.inner_text() and 'read from the printer' in reported.inner_text()
    assert unreported.locator('select[data-k="label_size"]').count() == 1
    assert 'does not report its tape' in unreported.inner_text()
    # The QL-1100 has no "Dense", half cuts or chain printing
    assert unreported.locator('[data-k="quality"]').all_inner_texts() == ['Fast', 'Quality']
    assert unreported.locator('[data-k="half_cut"], [data-k="chain"]').count() == 0
    with designer.expect_response('**/api/settings') as answer:
        designer.select_option('[data-card="1"] select[data-k="label_size"]', '29')
    assert answer.value.ok
    assert sent(answer.value.request) == {'printer': 'tcp://192.0.2.10', 'model': 'QL-1100', 'label_size': '29'}
    designer.wait_for_function("() => labelEditor.S.printers[1].tape.manual && labelEditor.S.printers[1].tape.id === '29'")
    assert designer.input_value('[data-card="1"] select[data-k="label_size"]') == '29'
    assert 'Set by hand' in designer.inner_text('[data-card="1"]')
    # The selected printer and its tape are left alone
    assert designer.evaluate('() => [labelEditor.S.printer.name, labelEditor.S.tape]') == ['Kitchen', '29x90']
    # A printer that cannot be reached is one line, its settings on request
    gone = designer.locator('[data-card="2"]')
    assert 'cannot be reached' in gone.inner_text() and gone.locator('[data-k]').count() == 0
    gone.locator('[data-show]').click()
    assert designer.locator('[data-card="2"] [data-k="cut"]').count() == 3
    assert designer.is_disabled('[data-card="2"] [data-test]')


def test_the_printer_chip_switches_printers(designer, server):
    printers_are(designer, server, {'path': 'simulation', 'model': 'QL-1100', 'name': 'Kitchen'}, {'path': 'simulation', 'model': 'QL-800', 'name': 'Office'})
    assert designer.inner_text('#printerChip b') == 'Kitchen'
    designer.click('#printerChip')
    rows = designer.locator('.pp [data-p]')
    assert rows.count() == 2
    assert designer.locator('.pp #look').count() == 1
    # Nothing else: the settings are on their own page
    assert designer.locator('.pp select, .pp .seg').count() == 0
    rows.nth(1).click()
    designer.wait_for_selector('#printerChip b:text-is("Office")')
    designer.wait_for_selector('.pp [data-p="1"].on')
    designer.click('#toSettings')
    designer.wait_for_selector('#setPart[data-part="printers"]')
    assert designer.evaluate('() => location.hash') == '#settings/printers'
    assert designer.locator('#layer').is_hidden()
    back_to_the_editor(designer)
    type_label(designer, 'Pantry')
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    assert (sent(request.value)['printer'], sent(request.value)['model']) == ('simulation', 'QL-800')
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')


def test_labels_are_kept_on_the_server_and_printed_labels_in_the_browser(designer, server):
    open_settings(designer, 'labels')
    assert designer.inner_text('#setPart .set-h p') == 'Kept on the server, the same in every browser.'
    family = other_family(designer)
    for change in (lambda: choose_font(designer, family), lambda: designer.select_option('#setDate', '%d.%m.%Y'),
                   lambda: designer.click('[data-k="collate"][data-v="0"]')):
        with designer.expect_response('**/api/settings/labels') as answer:
            change()
        assert answer.value.ok
    assert label_settings(server) == {'date_format': '%d.%m.%Y', 'date_language': '', 'collate': '0', 'font_family': family}
    assert designer.locator('[data-k="collate"][data-v="0"].on').count() == 1
    # Nothing of them is kept in this browser
    assert designer.evaluate("() => [localStorage.getItem('designer_settings'), localStorage.getItem('designer_font')]") == [None, None]
    back_to_the_editor(designer)
    designer.click('#newLabel')
    type_label(designer, 'Flour')
    assert designer.evaluate('() => labelEditor.S.edit.design.text[0].font').split(',')[0] == family
    with designer.expect_request('**/api/print') as request:
        designer.click('#print')
    # The server orders the copies as set
    assert 'collate' not in sent(request.value)
    designer.wait_for_selector('#toast:has-text("Printed 1 label")')
    open_settings(designer, 'printed')
    designer.wait_for_selector('#setHist .set-hrow')
    assert 'Flour' in designer.inner_text('#setHist .set-hrow')
    designer.wait_for_selector('#setHist .set-thumb.shown')
    with designer.expect_request('**/api/print'):
        designer.click('#setHist [data-again]')
    designer.wait_for_function('() => labelEditor.S.history.length === 2 && labelEditor.S.history[0].state === "printed"')
    assert designer.locator('#setHist .set-hrow').count() == 2


def test_two_browsers_have_the_same_label_settings(designer, browser, server):
    open_settings(designer, 'labels')
    with designer.expect_response('**/api/settings/labels'):
        designer.select_option('#setDate', '%d.%m.%Y')
    with designer.expect_response('**/api/settings/labels'):
        designer.click('[data-k="collate"][data-v="0"]')
    other = browser.new_context(locale='en-US').new_page()
    try:
        other.goto(server + '/labeldesigner/#settings/labels')
        other.wait_for_selector('#setDate')
        assert other.input_value('#setDate') == '%d.%m.%Y'
        assert other.locator('[data-k="collate"][data-v="0"].on').count() == 1
        assert other.evaluate('() => [labelEditor.S.settings.date_format, labelEditor.S.settings.collate]') == ['%d.%m.%Y', '0']
    finally:
        other.context.close()


def test_label_settings_another_browser_changed_are_read_when_the_page_is_shown_again(designer, browser, server):
    open_settings(designer, 'labels')
    family = other_family(designer)
    other = browser.new_context(locale='en-US').new_page()
    try:
        other.goto(server + '/labeldesigner/#settings/labels')
        other.wait_for_selector('#setDate')
        with other.expect_response('**/api/settings/labels'):
            other.select_option('#setDate', '%d.%m.%Y')
        with other.expect_response('**/api/settings/labels'):
            choose_font(other, family)
    finally:
        other.context.close()
    assert designer.input_value('#setDate') == ''
    shown_again(designer)
    designer.wait_for_function("family => document.querySelector('#setDate').value === '%d.%m.%Y' && document.querySelector('#setFont').dataset.value === family", arg=family)
    back_to_the_editor(designer)
    designer.click('#newLabel')
    type_label(designer, 'Packed [today]')
    designer.wait_for_function("() => labelEditor.S.edit.design.text[0].text === 'Packed {{datetime:%d.%m.%Y}}'")
    assert designer.evaluate('() => labelEditor.S.edit.design.text[0].font').split(',')[0] == family


def test_a_label_setting_the_server_does_not_take_is_taken_back(designer, server):
    open_settings(designer, 'labels')
    designer.route('**/api/settings/labels', lambda route: route.abort())
    designer.select_option('#setDate', '%d.%m.%Y')
    designer.wait_for_selector('#toast:has-text("The label designer could not be reached")', timeout=5000)
    assert designer.input_value('#setDate') == ''
    assert designer.evaluate('() => labelEditor.S.settings.date_format') == ''
    assert label_settings(server)['date_format'] == ''


def test_date_blanks_go_with_the_browsers_format_only_where_the_server_has_none(designer, server):
    # The server writes them as set there
    def format_sent(action):
        with designer.expect_request(lambda r: r.url.endswith('/api/preview') and 'fields' in sent(r)) as request:
            action()
        return json.loads(sent(request.value)['fields'])['Frozen on'].get('format')
    # As the browser writes dates in English
    assert format_sent(lambda: type_label(designer, 'Jam [Frozen on]')) == '%m/%d/%Y'
    label_settings(server, date_format='%d.%m.%Y')
    # The same label, kept for the next visit
    assert format_sent(designer.reload) is None


def test_label_settings_this_browser_kept_are_carried_over_once(designer, server, app):
    # Before they were kept on the server, each browser kept its own: those
    # the server has none of go there, then they are gone from the browser
    family = other_family(designer)
    label_settings(server, collate='1')
    designer.evaluate("""family => {
        localStorage.setItem('designer_settings', JSON.stringify({ dateFmt: '%d.%m.%Y', collate: '0', largePreview: true }));
        localStorage.setItem('designer_font', JSON.stringify(family)); }""", family)
    with designer.expect_response('**/api/settings/labels'):
        designer.reload()
    designer.wait_for_function("() => localStorage.getItem('designer_font') === null && window.labelEditor")
    assert label_settings(server) == {'date_format': '%d.%m.%Y', 'date_language': '', 'collate': '1', 'font_family': family}
    assert designer.evaluate("() => JSON.parse(localStorage.getItem('designer_settings'))") == {'largePreview': True}
    assert designer.evaluate('() => [labelEditor.S.settings.date_format, labelEditor.S.settings.collate]') == ['%d.%m.%Y', '1']
    # Once: removed on the server since, they stay removed
    label_settings(server, date_format='', font_family='')
    designer.reload()
    designer.wait_for_selector('#field .ln')
    wait_for_preview(designer)
    assert label_settings(server) == {'date_format': '', 'date_language': '', 'collate': '1', 'font_family': ''}


def test_connections_have_the_addresses_of_this_server(designer, server):
    open_settings(designer, 'connections')
    codes = designer.locator('.set-conn code').all_inner_texts()
    assert f'{server}/labeldesigner/api/homebox' in codes
    assert f'{server}/labeldesigner/api/repository/print?name=Grocy.json' in codes
    assert f'{server}/mcp' in codes
    assert f'{server}/labeldesigner/api/webhook/print' in codes
    assert any('/api/demos/print?id=office/015-paperless-asn' in c for c in codes)
    # MCP first, with a robot, then the others
    assert designer.locator('.set-conn h3').all_inner_texts() == ['MCP for direct AI connections', 'Homebox', 'Grocy', 'Paperless-ngx', 'Webhook']
    assert designer.locator('.set-conn >> nth=0 >> .set-conn-h > svg.ic').count() == 1
    assert designer.locator('.set-conn-h > svg.ic').count() == 1
    assert 'cannot reach a server in your home network' in designer.inner_text('.set-conn >> nth=0')
    # What rules the cloud out comes before the address
    assert designer.locator('.set-conn >> nth=0').evaluate("""c => {
        const note = [...c.querySelectorAll('p')].find(p => p.textContent.includes('cannot reach'));
        return !!(note.compareDocumentPosition(c.querySelector('code')) & Node.DOCUMENT_POSITION_FOLLOWING); }""")
    # The line of setup of each client, ready to copy
    mcp = f'{server}/mcp'
    lines = {
        'Claude Code': f'claude mcp add --transport http labelburg {mcp}',
        'VS Code': f'{{"servers": {{"labelburg": {{"type": "http", "url": "{mcp}"}}}}}}',
        'Cursor': f'{{"mcpServers": {{"labelburg": {{"url": "{mcp}"}}}}}}',
        'Gemini CLI': f'gemini mcp add --transport http labelburg {mcp}',
        'Codex CLI': f'codex mcp add labelburg --url {mcp}',
        'Zed': f'"context_servers": {{"labelburg": {{"url": "{mcp}"}}}}',
        'LM Studio': f'{{"mcpServers": {{"labelburg": {{"url": "{mcp}"}}}}}}',
        'Home Assistant': mcp,
        'Open WebUI': mcp,
    }
    assert designer.locator('[data-clients] [data-client]').all_inner_texts() == list(lines)
    designer.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    for name, line in lines.items():
        designer.click(f'[data-clients] [data-client]:text-is("{name}")')
        assert designer.inner_text('[data-clients] code') == line
        designer.click('[data-clients] [data-copy]')
        designer.wait_for_function('line => navigator.clipboard.readText().then(t => t === line)', arg=line)
    open_settings(designer, 'about')
    # One label designer, no other one to open or compare
    assert designer.locator('#setPart .set-grid > .k').all_inner_texts() == ['Appearance', 'Version', 'Source code']
    assert 'designer' not in designer.inner_text('#setPart .set-card')
    designer.click('button[data-theme="dark"]')
    assert designer.evaluate("() => [document.documentElement.dataset.theme, localStorage.getItem('designer_theme')]") == ['dark', 'dark']
    assert designer.locator('[data-theme="dark"].on').count() == 1


def test_the_label_is_drawn_again_with_label_settings_changed_elsewhere(designer, server):
    type_label(designer, 'Jam [Frozen on]')
    label_settings(server, date_format='%d.%m.%Y')
    with designer.expect_request(lambda r: r.url.endswith('/api/preview') and r.method == 'POST', timeout=5000):
        shown_again(designer)
    wait_for_preview(designer)


def test_the_language_of_dates_names_the_weekday_in_the_preview(designer, server, monkeypatch):
    from app.labeldesigner import routes
    made = []
    make = routes.create_label_from_request
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: made.append(make(*args, **kwargs)) or made[-1])

    def drawn():
        label = next(label for label in reversed(made) if label.input_text and 'Packed' in label.input_text[0]['text'])
        return label.text[0]['text']
    type_label(designer, 'Packed [today]')
    designer.click('#field .chip.date')
    designer.fill('#cpFormat', '%A')
    with designer.expect_request(lambda r: r.url.endswith('/api/preview') and r.method == 'POST'):
        designer.press('#cpFormat', 'Enter')
    wait_for_preview(designer)
    assert drawn() == 'Packed ' + format_date(datetime.now(), '%A')
    designer.keyboard.press('Escape')
    open_settings(designer, 'labels')
    assert designer.locator('#setDateLang option').all_inner_texts() == [
        'English', 'Čeština', 'Dansk', 'Deutsch', 'Español', 'Français', 'Italiano', 'Nederlands', 'Norsk bokmål', 'Polski', 'Português', 'Suomi', 'Svenska']
    assert designer.inner_text('#setDateLang + small') == 'For dates with the name of the month or weekday, e.g. March or Saturday.'
    with designer.expect_response('**/api/settings/labels'):
        designer.select_option('#setDateLang', 'de')
    designer.wait_for_selector('#toast:has-text("Months and weekdays are written like März and Samstag")')
    assert designer.inner_text('#setDateLang + small') == 'For dates with the name of the month or weekday, e.g. März or Samstag.'
    assert label_settings(server)['date_language'] == 'de'
    # The label is drawn again, with the weekday in German
    with designer.expect_request(lambda r: r.url.endswith('/api/preview') and r.method == 'POST'):
        designer.click('#tabs [data-tab="edit"]')
    wait_for_preview(designer)
    assert drawn() == 'Packed ' + format_date(datetime.now(), '%A', 'de')
    # The codes name it as well
    designer.click('#field .chip.date')
    designer.click('.chip-pop [data-codes]')
    assert designer.inner_text('.codes tr:has(td code:text-is("%A")) td >> nth=2') == 'Samstag'
    assert designer.inner_text('.codes tr:has(td code:text-is("%m")) td >> nth=2') == '03'


def test_the_empty_new_label_takes_the_font_of_new_labels(designer, server):
    family = other_family(designer)
    saved = designer.evaluate('() => labelEditor.S.edit.design.text[0].font')
    open_settings(designer, 'labels')
    assert designer.inner_text('#setFont + small') == 'New labels start with it, a new line takes the font of the line above. Labels you made keep their fonts.'
    with designer.expect_response('**/api/settings/labels'):
        choose_font(designer, family)
    assert designer.inner_text('#setFont') == family
    back_to_the_editor(designer)
    type_label(designer, 'Flour')
    assert designer.evaluate('() => labelEditor.S.edit.design.text[0].font').split(',')[0] == family
    # A label with text keeps its font
    label_settings(server, font_family=saved.split(',')[0])
    shown_again(designer)
    assert designer.evaluate('() => labelEditor.S.edit.design.text[0].font').split(',')[0] == family


def test_the_cutting_of_a_printer_is_said_as_in_the_editor(designer, server):
    printers_are(designer, server, {'path': 'simulation', 'model': 'PT-E550W', 'name': 'Shop'})
    open_settings(designer)
    assert 'The tape fed before the first label is waste, short labels get some extra tape.' in designer.inner_text('#setPrinters')
