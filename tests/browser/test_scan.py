"""The scan station of the label designer at #scan/<name>: a saved label
chosen once, each code a scanner types into it printed with the code in
its blanks."""
import base64
import contextlib
import json
import os
from urllib.parse import parse_qs

import pytest

from app.labeldesigner import printer_settings
from tests.browser.helpers import png

IMAGE = base64.b64encode(png(40, 20)).decode()
BOX = 'Box.json'


class Station:
    """The page with the station open; the requests to /api/scan recorded
    and answered as the test says."""

    def __init__(self, page):
        self.page = page
        self.requests = []
        self.answer = None
        self.held = []
        self.hold = False
        page.route('**/api/scan', self.handle)

    def handle(self, route):
        fields = {k: v[-1] for k, v in parse_qs(route.request.post_data or '', keep_blank_values=True).items()}
        self.requests.append(fields)
        if self.hold:
            self.held.append((route, fields))
            return
        self.fulfill(route, fields)

    def fulfill(self, route, fields):
        if self.answer and self.answer[1] is None:
            route.fulfill(status=self.answer[0], content_type='text/html', body='<html><body>Sign in</body></html>')
            return
        if self.answer:
            status, body = self.answer
        else:
            status, body = 200, {'success': True, 'printed': fields.get('preview_only') != '1', 'label': 'Box',
                                 'filled': {'code': fields.get('code')}, 'image': IMAGE}
        route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

    def codes(self):
        return [r['code'] for r in self.requests]

    def wait_for(self, count, timeout=3000):
        for _ in range(timeout // 20):
            if len(self.requests) >= count:
                return
            self.page.wait_for_timeout(20)
        raise AssertionError(f'{len(self.requests)} scans sent, waited for {count}')

    def none_for(self, ms):
        self.page.wait_for_timeout(ms)
        return len(self.requests)

    def scan(self, text, end='Enter', delay=5):
        self.page.keyboard.type(text, delay=delay)
        if end:
            self.page.keyboard.press(end)


def save_box(page, server):
    answer = page.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                               data=json.dumps({'name': BOX, 'title': 'Box', 'label_size': '62', 'replace': True, 'print_type': 'qrcode_text',
                                                'code_text': '{{blank:code}}', 'text': [{'text': 'Box', 'size': '40', 'font': 'DejaVu Sans,Book', 'align': 'center'}]}))
    assert answer.ok


def open_station(browser, server, app, viewport, hash_=f'#scan/{BOX}', **options):
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    context = browser.new_context(viewport=viewport, locale='en-US', **options)
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    save_box(page, server)
    station = Station(page)
    page.goto(server + '/labeldesigner/' + hash_)
    return context, page, station, errors


@pytest.fixture
def station(browser, server, app):
    context, page, station, errors = open_station(browser, server, app, {'width': 1600, 'height': 900})
    page.wait_for_selector('#scanField:focus')
    yield station
    context.close()
    assert errors == []


def card(page):
    return page.inner_text('#scanResult')


def test_fast_scan_ending_on_enter_prints_once(station):
    page = station.page
    assert 'Each scan fills: code' in page.inner_text('.scan-label')
    station.scan('A-17')
    station.wait_for(1)
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    assert station.none_for(300) == 1
    assert station.codes() == ['A-17']
    assert station.requests[0]['name'] == BOX
    assert 'preview_only' not in station.requests[0]
    assert station.requests[0]['printer']
    assert 'A-17' in card(page)
    assert page.locator('#scanResult img').count() == 1
    assert page.input_value('#scanField') == ''


def test_fast_scan_without_enter_ends_after_a_pause(station):
    page = station.page
    with page.expect_request('**/api/scan', timeout=500):
        station.scan('A-17', end=None)
    assert station.none_for(300) == 1
    assert station.codes() == ['A-17']


def test_tab_ends_a_scan(station):
    station.scan('B-42', end='Tab')
    station.wait_for(1)
    assert station.none_for(300) == 1
    assert station.codes() == ['B-42']
    assert station.page.evaluate('() => document.activeElement.id') == 'scanField'


def test_hand_typing_waits_for_enter(station):
    page = station.page
    station.scan('A-17', end=None, delay=120)
    assert station.none_for(600) == 0
    assert page.input_value('#scanField') == 'A-17'
    page.keyboard.press('Enter')
    station.wait_for(1)
    assert station.none_for(300) == 1
    assert station.codes() == ['A-17']


def test_enter_inside_a_fast_scan_is_a_line_break(station):
    page = station.page
    for line in ('BEGIN:VCARD', 'FN:Erika Muster', 'END:VCARD'):
        page.keyboard.type(line, delay=5)
        page.keyboard.press('Enter')
    station.wait_for(1)
    assert station.none_for(400) == 1
    assert station.codes() == ['BEGIN:VCARD\nFN:Erika Muster\nEND:VCARD']


def test_scans_while_printing_wait_their_turn(station):
    page = station.page
    station.hold = True
    station.scan('A')
    station.wait_for(1)
    page.wait_for_selector('#scanResult:has-text("Printing...")')
    station.scan('B')
    page.wait_for_selector('#scanResult:has-text("Printing, the next scans wait their turn")')
    # The first one answers after a second, the second is sent only then
    assert station.none_for(1000) == 1
    station.hold = False
    route, fields = station.held.pop()
    station.fulfill(route, fields)
    station.wait_for(2)
    assert station.codes() == ['A', 'B']
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    page.wait_for_function("() => document.querySelector('#scanResult .scan-code').textContent === 'B'")


def test_scan_after_a_click_elsewhere_is_caught(station):
    page = station.page
    page.click('#scanShow')
    page.click('#scanShow')
    assert not page.is_checked('#scanShow')
    page.mouse.click(1500, 850)
    station.scan('C-3')
    station.wait_for(1)
    assert station.none_for(300) == 1
    assert station.codes() == ['C-3']
    assert 'preview_only' not in station.requests[0]


def test_typing_outside_the_station_never_prints(station):
    page = station.page
    page.evaluate("() => { location.hash = 'edit'; }")
    page.wait_for_selector('#field')
    assert page.locator('#scanField').count() == 0
    page.click('#field')
    page.keyboard.type('A-17', delay=5)
    page.keyboard.press('Enter')
    page.keyboard.insert_text('Pasted text\nwith two lines')
    page.evaluate("() => { dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); }")
    assert station.none_for(600) == 0
    assert page.evaluate("() => document.activeElement.id") == 'field'


def test_show_only_sends_preview_only(station):
    page = station.page
    page.click('#scanShow')
    assert page.is_checked('#scanShow')
    station.scan('A-17')
    station.wait_for(1)
    assert station.requests[0]['preview_only'] == '1'
    page.wait_for_selector('#scanResult:has-text("Shown, not printed")')
    assert page.locator('#scanResult img').count() == 1
    # Off again after the page is loaded again
    page.reload()
    page.wait_for_selector('#scanField:focus')
    assert not page.is_checked('#scanShow')


def test_refusal_is_shown_without_an_image(station):
    page = station.page
    message = 'The label has several blanks, so it is not clear which one the code fills. Name the blank for the code "code".'
    station.answer = (400, {'success': False, 'message': message})
    station.scan('A-17')
    page.wait_for_selector('#scanResult .warn')
    assert message in card(page)
    assert page.locator('#scanResult img').count() == 0


def test_label_no_longer_saved(station):
    page = station.page
    station.answer = (404, {'success': False, 'message': 'Not found'})
    station.scan('<b>A-17</b>')
    page.wait_for_selector('#scanResult:has-text("The label Box is no longer saved. Choose another one.")')
    assert page.locator('#scanResult img, #scanResult .scan-code b').count() == 0
    assert '<b>A-17</b>' in card(page)


def test_long_codes_are_shortened(station):
    page = station.page
    page.keyboard.insert_text('x' * 300)
    page.keyboard.press('Enter')
    station.wait_for(1)
    assert len(station.codes()[0]) == 300
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    assert page.inner_text('#scanResult .scan-code') == 'x' * 200 + '...'


def test_station_remembers_its_label(station, server):
    page = station.page
    page.goto(server + '/labeldesigner/#scan')
    page.reload()
    page.wait_for_selector('#scanField:focus')
    assert page.inner_text('#scanTitle') == 'Box'
    station.scan('A-17')
    station.wait_for(1)
    assert station.requests[0]['name'] == BOX


def test_change_picks_another_label(station):
    page = station.page
    page.click('#scanChange')
    page.click('.scan-pick [data-name="TODO-List.json"]')
    page.wait_for_function("() => location.hash === '#scan/TODO-List.json'")
    page.wait_for_selector('#scanTitle:text-is("TODO List")')
    page.wait_for_selector('#scanField:focus')
    station.scan('A-17')
    station.wait_for(1)
    assert station.none_for(300) == 1
    assert station.requests[0]['name'] == 'TODO-List.json'


def test_a_station_opened_again_sends_each_scan_once(station):
    page = station.page
    page.evaluate("() => { location.hash = 'edit'; }")
    page.wait_for_selector('#field')
    page.evaluate(f"() => {{ location.hash = 'scan/{BOX}'; }}")
    page.wait_for_selector('#scanField:focus')
    page.click('#scanChange')
    page.click(f'.scan-pick [data-name="{BOX}"]')
    page.wait_for_selector('.scan-pick', state='detached')
    page.wait_for_selector('#scanField:focus')
    station.scan('A-17')
    station.wait_for(1)
    assert station.none_for(300) == 1
    assert station.codes() == ['A-17']


def test_a_label_opened_from_the_search_leaves_the_station(station):
    page = station.page
    page.keyboard.press('Control+k')
    page.wait_for_selector('.palette #palQ:focus')
    page.keyboard.type('Box')
    page.wait_for_selector('.res:has-text("Box")')
    page.keyboard.press('Enter')
    page.wait_for_selector('#print', state='visible')
    assert page.evaluate('() => document.body.dataset.view') == 'edit'
    assert page.evaluate('() => labelEditor.S.edit.title') == 'Box'
    assert page.locator('#scanField').count() == 0
    station.scan('A-17', end=None)
    assert station.none_for(500) == 0


def test_a_stray_character_does_not_slow_a_later_scan(station):
    page = station.page
    page.keyboard.type('x')
    page.wait_for_timeout(2300)
    station.scan('A-17', end=None)
    station.wait_for(1)
    page.keyboard.type('y')
    page.wait_for_timeout(2300)
    station.scan('B-42')
    station.wait_for(2)
    assert station.none_for(300) == 2
    assert station.codes() == ['A-17', 'B-42']


def test_an_answer_that_is_no_json_is_a_failure(station):
    page = station.page
    station.answer = (200, None)
    station.scan('A-17')
    page.wait_for_selector('#scanResult .warn')
    assert 'Shown, not printed' not in card(page)
    assert 'Printed' not in card(page)
    assert page.locator('#scanResult img').count() == 0


def test_the_code_in_the_card_can_be_selected(station):
    page = station.page
    station.scan('A-17')
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    code = page.locator('#scanResult .scan-code').bounding_box()
    page.mouse.move(code['x'] + 1, code['y'] + code['height'] / 2)
    page.mouse.down()
    page.mouse.move(code['x'] + code['width'] - 1, code['y'] + code['height'] / 2, steps=5)
    page.mouse.up()
    page.wait_for_timeout(100)
    assert page.evaluate('() => getSelection().toString()') == 'A-17'
    # Clicked away, the next scan is caught again
    page.mouse.click(1500, 850)
    page.wait_for_selector('#scanField:focus')
    station.scan('B-42')
    station.wait_for(2)
    assert station.codes() == ['A-17', 'B-42']


def test_tab_in_the_empty_field_moves_on(station):
    page = station.page
    page.keyboard.press('Tab')
    assert page.evaluate('() => document.activeElement.id') == 'scanShow'
    assert station.none_for(200) == 0


def test_without_a_label_the_picker_opens(browser, server, app):
    context, page, station, errors = open_station(browser, server, app, {'width': 1600, 'height': 900}, '#scan')
    page.click(f'.scan-pick [data-name="{BOX}"]')
    page.wait_for_selector('#scanTitle:text-is("Box")')
    page.wait_for_selector('#scanField:focus')
    context.close()
    assert errors == []


def test_reached_from_the_saved_label_and_the_search(station, server):
    page = station.page
    page.goto(server + '/labeldesigner/#edit')
    page.wait_for_selector('#field .ln')
    page.click('#templates')
    page.click('.tpl-nav [data-cat="saved"]')
    page.click('.thumb:has-text("Box")')
    page.click('#tplScan')
    page.wait_for_function(f"() => location.hash === '#scan/{BOX}'")
    page.wait_for_selector('#scanField:focus')
    assert page.locator('.tpl').count() == 0
    page.evaluate("() => { location.hash = 'edit'; }")
    page.wait_for_selector('#field')
    page.keyboard.press('Control+k')
    page.wait_for_selector('.palette #palQ:focus')
    page.keyboard.type('>scan')
    page.wait_for_selector('.res.cmd:has-text("Scan to print")')
    page.keyboard.press('Enter')
    page.wait_for_selector('#scanField:focus')
    assert page.inner_text('#scanTitle') == 'Box'


def test_station_fits_a_phone(browser, server, app):
    context, page, station, errors = open_station(browser, server, app, {'width': 390, 'height': 844})
    page.wait_for_selector('#scanField:focus')
    station.scan('A-17')
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    assert page.evaluate('() => document.documentElement.scrollWidth') <= 390
    assert page.locator('#scanField').is_visible()
    page.locator('#scanResult').scroll_into_view_if_needed()
    assert page.locator('#scanResult').is_visible()
    assert not page.locator('#mainActs').is_visible()
    context.close()
    assert errors == []


def test_a_phone_keeps_its_keyboard_closed_until_the_field_is_tapped(browser, server, app):
    context, page, station, errors = open_station(browser, server, app, {'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    page.wait_for_selector('#scanField:focus')
    assert page.evaluate("() => matchMedia('(pointer: coarse)').matches")
    # A scanner types without the keyboard of the screen
    assert page.get_attribute('#scanField', 'inputmode') == 'none'
    station.scan('A-17')
    station.wait_for(1)
    # Tapped, the field takes typing by hand until the scan ends
    page.tap('#scanField')
    assert page.get_attribute('#scanField', 'inputmode') == 'text'
    station.scan('B-42')
    station.wait_for(2)
    page.wait_for_function("() => document.querySelector('#scanField').getAttribute('inputmode') === 'none'")
    assert station.codes() == ['A-17', 'B-42']
    context.close()
    assert errors == []
