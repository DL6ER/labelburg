"""Around the label designer at /labeldesigner/: commands in the search,
the Templates dialog, warnings of the printers, the saved toast, links to
the internet, content shared with the installed app and the phone layout."""
import contextlib
import json
import os

import pytest

from app.labeldesigner import printer_settings
from tests.browser.helpers import png, with_tape
from tests.browser.test_editor import sent, type_label, wait_for_preview


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
    assert errors == []


def command(page, text):
    page.keyboard.press('Control+k')
    page.wait_for_selector('#palQ')
    page.keyboard.type('>' + text)
    page.wait_for_selector('.res.cmd')
    page.keyboard.press('Enter')


def test_commands_in_the_search(designer):
    designer.keyboard.press('Control+k')
    designer.keyboard.type('>')
    assert designer.locator('.res.cmd b').all_inner_texts() == [
        'New label', 'Templates', 'Go to the editor', 'Printer and settings', 'Printed labels', 'Scan to print',
        'Dark mode', 'Light mode', 'Follow the system for dark mode']
    designer.keyboard.type('mode')
    assert designer.locator('.res.cmd').count() == 3
    designer.keyboard.press('ArrowDown')
    designer.keyboard.press('Enter')
    assert designer.evaluate("() => [document.documentElement.dataset.theme, localStorage.getItem('designer_theme')]") == ['light', 'light']
    command(designer, 'follow')
    assert designer.evaluate("() => [document.documentElement.dataset.theme, localStorage.getItem('designer_theme')]") == [None, None]
    command(designer, 'printed')
    designer.wait_for_selector('#setPart[data-part="printed"]')
    # New label opens the editor again, with Undo for the label before
    command(designer, 'new label')
    designer.wait_for_selector('#field .ln')
    type_label(designer, 'Shelf 3')
    command(designer, 'new label')
    designer.click('#newDiscard')
    designer.wait_for_selector('#toast:has-text("Started a new label")')
    designer.wait_for_function("() => labelEditor.S.edit.design.text.map(l => l.text).join('') === ''")
    command(designer, 'templates')
    designer.wait_for_selector('.tpl-nav')


def test_templates_in_other_languages_all_and_made_for_the_tape(designer):
    designer.click('#templates')
    designer.click('.tpl-nav [data-cat="all"]')
    fits = 'd => d.label_size_id === labelEditor.S.tape || labelEditor.S.tapeFits.includes(d.label_size_id)'
    english = designer.evaluate("() => labelEditor.S.demoList.filter(d => d.language === 'en').map(d => d.title)")
    first = designer.evaluate(f"() => labelEditor.S.demoList.filter(d => d.language === 'en').filter({fits})[0].title")
    fitting = designer.evaluate(f"() => labelEditor.S.demoList.filter(d => d.language === 'en').filter({fits}).length")
    assert 0 < fitting < len(english)
    # Made for the tape in the printer: those labels first, and counted
    assert designer.get_attribute('#tplFits', 'aria-pressed') == 'true'
    assert designer.inner_text('.tpl-nav [data-cat="all"] small') == str(fitting)
    assert designer.locator('.tpl-grid .thumb').count() == min(240, len(english))
    assert designer.inner_text('.tpl-grid .thumb >> nth=0 >> b') == first
    designer.click('#tplFits')
    assert designer.get_attribute('#tplFits', 'aria-pressed') == 'false'
    assert designer.inner_text('.tpl-nav [data-cat="all"] small') == str(len(english))
    assert designer.inner_text('.tpl-grid .thumb >> nth=0 >> b') == english[0]
    designer.select_option('#tplLang', 'de')
    german = designer.evaluate("() => labelEditor.S.demoList.filter(d => d.language === 'de').map(d => d.title)")
    assert designer.inner_text('.tpl-grid .thumb >> nth=0 >> b') == german[0]
    assert designer.evaluate("() => JSON.parse(localStorage.getItem('designer_lang'))") == 'de'
    # The search finds the templates of that language too
    designer.keyboard.press('Escape')
    designer.keyboard.press('Control+k')
    designer.keyboard.type(german[0])
    designer.wait_for_selector('.res')
    assert german[0] in designer.inner_text('#palRes')
    designer.keyboard.press('Escape')
    # Saved labels tell when they were saved
    designer.click('#templates')
    designer.click('.tpl-nav [data-cat="saved"]')
    assert ', saved ' in designer.inner_text('.tpl-grid .thumb >> nth=0 >> small')


def printers_tell(page, *printers):
    def answer(route):
        told = [with_tape({'errors': [], 'media_width': None, 'media_length': 0, 'settings': {}, **p}) for p in printers]
        route.fulfill(json={**told[0], 'printers': told, 'selected': told[0]['path']})
    page.route('**/api/printer_status*', answer)
    page.reload()
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


def test_the_printers_tell_weak_wifi_low_battery_and_one_that_is_gone(designer):
    printers_tell(designer, {'path': 'tcp://192.0.2.20', 'model': 'QL-820NWB', 'name': 'Garage', 'weak_connection': True, 'battery_low': True},
                  {'path': 'simulation', 'model': 'QL-800', 'name': 'Office'})
    designer.click('#printerChip')
    row = designer.locator('.pp [data-p="0"]')
    assert 'The Wi-Fi connection seems weak' in row.inner_text()
    assert 'The battery is low, charge the printer.' in row.inner_text()
    assert 'Wi-Fi' not in designer.inner_text('.pp [data-p="1"]')
    designer.click('.pp [data-p="1"]')
    designer.wait_for_selector('#printerChip b:text-is("Office")')
    # The chosen printer is left out of the answer: it stays chosen, as not
    # answering, only a choice switches printers
    printers_tell(designer, {'path': 'tcp://192.0.2.20', 'model': 'QL-820NWB', 'name': 'Garage'})
    assert designer.inner_text('#printerChip b') == 'Office'
    assert 'bad' in designer.get_attribute('#printerChip .d', 'class').split()
    assert designer.locator('#toast:has-text("labels print on")').count() == 0


def test_the_printers_tell_their_battery_in_percent(designer):
    printers_tell(designer, {'path': 'simulation', 'model': 'QL-800', 'name': 'Office', 'battery': 75},
                  {'path': 'simulation', 'model': 'QL-1100', 'name': 'Kitchen', 'battery': None})
    designer.click('#printerChip')
    assert 'Battery 75%' in designer.inner_text('.pp [data-p="0"]')
    assert 'Battery' not in designer.inner_text('.pp [data-p="1"]')
    designer.click('#toSettings')
    designer.wait_for_selector('#setPrinters [data-card="1"]')
    assert 'Battery 75%' in designer.inner_text('[data-card="0"] .set-card-h')
    assert 'Battery' not in designer.inner_text('[data-card="1"] .set-card-h')


def test_printers_are_asked_again_while_bluetooth_is_searched(designer):
    # A printer found on Bluetooth shows without waiting for the next 20 s
    searching, asked = [True], []

    def answer(route):
        asked.append(route.request.url)
        route.fulfill(json={**route.fetch().json(), 'searching': searching.pop(0) if searching else False})
    designer.route('**/api/printer_status*', answer)
    designer.evaluate('() => labelEditor.poll()')
    for _ in range(50):
        if len(asked) == 2:
            break
        designer.wait_for_timeout(100)
    assert len(asked) == 2
    # Not again once the search has ended
    designer.wait_for_timeout(3500)
    assert len(asked) == 2


@pytest.mark.parametrize('ask', ['poll', 'follow'])
@pytest.mark.parametrize('hidden', [True, False])
def test_printers_are_asked_only_from_a_tab_that_is_shown(designer, hidden, ask):
    # A tab left in the background keeps no printer busy, e.g. a B1 its
    # Bluetooth connection or on USB; shown again it asks at once
    asked = []
    designer.on('request', lambda request: '/api/printer_status' in request.url and asked.append(request.url))
    designer.evaluate(f"""() => {{ labelEditor.S.printer.follow = true;
        Object.defineProperty(document, 'hidden', {{ value: {str(hidden).lower()}, configurable: true }}); return labelEditor.{ask}(); }}""")
    designer.wait_for_timeout(300)
    assert bool(asked) is not hidden


def test_an_unchanged_status_of_the_printer_followed_leaves_the_printers_as_they_are(designer):
    # Drawn anew every 2 s, the list would lose what is focused or hovered
    told = designer.evaluate('() => { labelEditor.S.printer.follow = true; return JSON.parse(JSON.stringify(labelEditor.S.printer)); }')
    asked = []
    designer.route('**/api/printer_status?printer=*', lambda route: (asked.append(route.request.url), route.fulfill(json={'printers': [told]})))
    designer.click('#printerChip')
    designer.eval_on_selector('.pp .plist', 'list => { list.dataset.kept = "1"; }')
    for _ in range(3):
        designer.evaluate('() => labelEditor.follow()')
    assert asked
    assert designer.eval_on_selector('.pp .plist', 'list => list.dataset.kept') == '1'


@pytest.mark.parametrize('follow', [True, False])
def test_only_a_printer_the_server_says_to_follow_is_asked_on_its_own(designer, follow):
    # Asking one on the network or on Bluetooth that often costs, it is
    # asked with the others every 20 s
    asked = []
    designer.route('**/api/printer_status?printer=*', lambda route: (asked.append(route.request.url), route.fulfill(status=404, body='{}')))
    designer.evaluate(f"""() => {{ const S = labelEditor.S;
        S.printer = Object.assign({{}}, S.printer, {{ path: 'file:///dev/usb/lp9', follow: {str(follow).lower()} }});
        S.printers = [S.printer]; return labelEditor.follow(); }}""")
    assert bool(asked) is follow


def test_the_saved_toast_opens_the_label_in_templates(designer, server):
    type_label(designer, 'Pantry shelf')
    designer.click('#save')
    designer.fill('#saveName', 'Pantry toast')
    designer.click('#saveGo')
    designer.wait_for_selector('#toast .tbtn:text-is("Show in Templates")')
    designer.click('#newLabel')
    type_label(designer, 'Filled by ')
    designer.keyboard.type('[Name]')
    designer.wait_for_function("() => labelEditor.S.edit.design.text[0].text.endsWith('{{blank:Name}}')")
    wait_for_preview(designer)
    designer.click('#save')
    designer.fill('#saveName', 'Filled toast')
    designer.click('#saveGo')
    designer.click('#toast .tbtn:text-is("Fill in and print")')
    designer.wait_for_url(lambda url: url.endswith('/labeldesigner/#fill/Filled_toast.json'))
    designer.wait_for_selector('#tplDetail:has-text("Filled toast")')


def test_links_to_the_internet_ask_first(designer):
    designer.goto(designer.url.split('#')[0] + '#settings/about')
    designer.wait_for_selector('#setPart[data-part="about"]')
    designer.evaluate('() => { window.open = (...args) => { window.opened = args; return null; }; }')
    link = designer.locator('#setPart a[href="https://github.com/DL6ER/labelburg"]')
    link.click()
    designer.wait_for_selector('.dialog.ask h2:text-is("Open github.com?")')
    designer.click('.dialog.ask [data-close]')
    assert designer.locator('#layer').is_hidden()
    assert designer.evaluate('() => window.opened') is None
    link.click()
    designer.click('#leaveGo')
    assert designer.evaluate('() => window.opened') == ['https://github.com/DL6ER/labelburg', '_blank', 'noopener']
    assert designer.evaluate('() => location.hash') == '#settings/about'


def share(page, server, **fields):
    """Share with the installed app as a phone's share menu does, and open
    the label designer where the server sends it."""
    answer = page.request.post(server + '/labeldesigner/share', multipart=fields, max_redirects=0)
    assert answer.status == 303 and answer.headers['location'].startswith('/labeldesigner/?shared=')
    page.goto(server + answer.headers['location'])
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


def test_shared_text_link_and_file_become_a_label(designer, server):
    type_label(designer, 'Kept label')
    share(designer, server, text='Shelf 3\n\nspices and herbs')
    assert designer.evaluate('() => labelEditor.S.edit.design.text.map(l => l.text)') == ['Shelf 3', 'spices and herbs']
    assert designer.evaluate('() => [location.search, location.hash]') == ['', '#edit']
    designer.click('#toast .tbtn:text-is("Undo")')
    designer.wait_for_function("() => labelEditor.S.edit.design.text[0].text === 'Kept label'")
    share(designer, server, url='https://example.com/a')
    assert designer.evaluate('() => [labelEditor.S.edit.design.print_type, labelEditor.S.edit.design.code_text]') == ['qrcode_text', 'https://example.com/a']
    share(designer, server, file={'name': 'shelf.png', 'mimeType': 'image/png', 'buffer': png()})
    assert designer.evaluate('() => [labelEditor.S.edit.design.print_type, labelEditor.S.edit.design.image.name, labelEditor.S.edit.title]') == ['image', 'shelf.png', 'shelf']
    # Shared once: loading the page again keeps the label, the token is gone
    designer.reload()
    designer.wait_for_selector('#field .ln')
    assert designer.evaluate('() => labelEditor.S.edit.design.print_type') == 'image'


def test_installs_as_an_app(designer, server):
    for name in ('mobile-web-app-capable', 'apple-mobile-web-app-capable'):
        assert designer.get_attribute(f'meta[name="{name}"]', 'content') == 'yes'
    assert designer.get_attribute('meta[name="apple-mobile-web-app-title"]', 'content') == 'Labels'
    icon = designer.get_attribute('link[rel="icon"][sizes="192x192"]', 'href')
    assert designer.request.get(server + icon).ok
    assert designer.request.get(server + designer.get_attribute('link[rel="manifest"]', 'href')).ok


@pytest.mark.parametrize('theme', ['dark', None], ids=['set', 'of the system'])
def test_the_copies_of_print_are_light_in_dark_mode(designer, theme):
    # Chosen dark or dark as the system is, the page looks the same
    designer.emulate_media(color_scheme='dark')
    designer.evaluate('theme => { if (theme) document.documentElement.dataset.theme = theme; else delete document.documentElement.dataset.theme; }', theme)
    assert designer.eval_on_selector('.printbtn .cnt', 'e => getComputedStyle(e).backgroundColor') == 'rgb(201, 205, 210)'


def test_escape_closes_menus_and_advanced(designer):
    type_label(designer, 'Shelf 3')
    designer.click('#ltb [data-more]')
    designer.wait_for_selector('.line-menu')
    designer.keyboard.press('Escape')
    designer.wait_for_selector('.line-menu', state='detached')
    assert designer.get_attribute('#ltb [data-more]', 'aria-expanded') == 'false'
    designer.click('#advBtn')
    designer.wait_for_selector('#adv:not([hidden])')
    designer.keyboard.press('Escape')
    designer.wait_for_selector('#adv', state='hidden')


@pytest.fixture
def phone(browser, server, app):
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    context = browser.new_context(viewport={'width': 390, 'height': 844}, locale='en-US', is_mobile=True, has_touch=True)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    assert errors == []


def box(page, selector):
    return page.locator(selector).bounding_box()


def test_the_phone_layout(phone):
    assert phone.evaluate('() => document.documentElement.scrollWidth') == 390
    # Advanced, copies and Print at the thumb, the tabs give way to a gear
    bar = box(phone, '#mainActs')
    assert bar['y'] + bar['height'] == 844 and bar['width'] == 390
    for button in ('#footAdv', '#save', '#print'):
        assert phone.locator(button).is_visible() and box(phone, button)['height'] >= 40
    assert phone.locator('#tabs').is_hidden() and phone.locator('#advBtn').is_hidden()
    phone.tap('#field')
    phone.keyboard.type('Shelf 3')
    phone.wait_for_function("() => labelEditor.S.edit.design.text[0].text === 'Shelf 3'")
    wait_for_preview(phone)
    # The toolbar of the line is a sheet above the bar
    tools = box(phone, '#ltb')
    assert abs(tools['y'] + tools['height'] - bar['y']) <= 1 and tools['width'] == 390
    phone.tap('#ltb [data-more]')
    menu = box(phone, '.line-menu')
    assert menu['y'] + menu['height'] == 844 and menu['width'] == 390
    phone.keyboard.press('Escape')
    # Advanced is a sheet over the page, a tap beside it closes it
    phone.tap('#footAdv')
    phone.wait_for_selector('#adv:not([hidden])')
    assert phone.locator('#scrim').is_visible()
    adv = box(phone, '#adv')
    assert adv['y'] + adv['height'] == 844 and adv['width'] == 390
    with phone.expect_request(lambda r: r.url.endswith('/api/preview')) as request:
        phone.tap('#adv [data-frame="square"]')
    assert int(sent(request.value)['border_thickness']) > 0
    wait_for_preview(phone)
    phone.tap('#scrim', position={'x': 195, 'y': 60})
    phone.wait_for_selector('#adv', state='hidden')
    assert phone.locator('#scrim').is_hidden()
    # The printers are a sheet too
    phone.tap('#printerChip')
    sheet = box(phone, '.pp')
    assert sheet['y'] + sheet['height'] == 844
    phone.keyboard.press('Escape')
    phone.fill('#copies', '2')
    with phone.expect_response('**/api/print') as answer:
        phone.tap('#print')
    assert answer.value.ok
    assert json.loads(sent(answer.value.request)['text'])[0]['text'] == 'Shelf 3'
    assert sent(answer.value.request)['print_count'] == '2'
    phone.wait_for_selector('#toast:has-text("Printed 2 labels")')
    toast = box(phone, '#toast')
    assert toast['y'] + toast['height'] <= tools['y']
    # The gear opens the settings and leads back
    phone.tap('#gear')
    phone.wait_for_selector('#setPart[data-part="printers"]')
    assert phone.locator('#mainActs').is_hidden()
    assert phone.evaluate('() => document.documentElement.scrollWidth') == 390
    phone.tap('#gear')
    phone.wait_for_selector('#field .ln')
    assert phone.evaluate('() => document.documentElement.scrollWidth') == 390


def test_what_the_designers_before_kept_in_the_browser_goes(designer):
    # Their drafts may hold images of a few MB that nothing reads any more.
    # The oldest kept its settings per printer and its images in IndexedDB.
    old = ['designer_edit', 'designer_edit_images', 'labeldesigner_settings_v1', 'labeldesigner_settings_v1@usb://0x04f9:0x209b',
           'labeldesigner_settings_history_v1', 'labeldesigner_settings_history_v1@tcp://printer.local', 'labeldesigner_printer_v1',
           'labeldesigner_demo_language_v1', 'themeMode']
    designer.evaluate("() => { localStorage.setItem('designer_history', '[]'); localStorage.setItem('designer_lang', '\"en\"'); }")
    type_label(designer, 'Shelf 3')
    designer.evaluate('() => dispatchEvent(new Event("pagehide"))')
    kept = designer.evaluate('() => Object.keys(localStorage).sort()')
    assert {'designer_history', 'designer_lang', 'designer_next_edit'} <= set(kept)
    designer.evaluate('keys => keys.forEach(k => localStorage.setItem(k, JSON.stringify("x".repeat(10))))', old)
    designer.evaluate('''() => new Promise(done => {
        const open = indexedDB.open('brother_ql_images', 1);
        open.onupgradeneeded = () => open.result.createObjectStore('images');
        open.onsuccess = () => { open.result.close(); done(); };
    })''')
    designer.reload()
    designer.wait_for_selector('#field .ln')
    assert designer.evaluate('() => Object.keys(localStorage).sort()') == kept
    designer.wait_for_function("async () => !(await indexedDB.databases()).some(d => d.name === 'brother_ql_images')")

