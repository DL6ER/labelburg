"""The labels printed, from every browser and program, as the server keeps
them: listed under Settings > Printed labels and in Templates, printed
again, asked only where the date or number would change, and switched off
or cleared."""
import contextlib
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from app.labeldesigner import printer_settings
from tests.browser.helpers import clear_printed, png
from tests.browser.test_editor import sent, type_label, wait_for_preview
from tests.browser.test_settings import open_settings


@pytest.fixture
def page(browser, server, app):
    def no_settings():
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    no_settings()
    clear_printed(server)
    context = browser.new_context(viewport={'width': 1600, 'height': 924}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    no_settings()
    assert errors == []


def print_over_the_api(server, *lines, **values) -> str:
    """A label printed by another program on the demo printer, its job."""
    text = json.dumps([{'text': line, 'size': '40'} for line in lines])
    with urlopen(f'{server}/labeldesigner/api/print', urlencode({'printer': 'simulation', 'text': text, 'label_size': '62', **values}).encode()) as r:
        return json.load(r)['job']


def rows(page) -> list[str]:
    page.wait_for_selector('#setHist .set-hrow, #setHist .set-empty')
    return [row.inner_text() for row in page.locator('#setHist .set-hrow').all()]


def test_labels_printed_anywhere_are_listed_and_printed_again_at_once(page, server):
    print_over_the_api(server, 'Flour')
    type_label(page, 'Sugar')
    page.click('#print')
    page.wait_for_selector('#toast:has-text("Printed 1 label")')
    open_settings(page, 'printed')
    assert page.inner_text('#setPart .set-h p') == 'From every device and program.'
    listed = rows(page)
    assert [row.split('\n')[0] for row in listed] == ['Sugar', 'Flour']
    assert 'Demo printer' in listed[0] and 'from' not in listed[0]
    assert 'from another program' in listed[1]
    page.wait_for_selector('#setHist .set-hrow >> nth=0 >> .set-thumb.shown')
    # Neither a date nor a number changes, it is printed without a question
    with page.expect_request('**/api/history/*/print') as request:
        page.click('#setHist .set-hrow >> nth=1 >> [data-again]')
    assert sent(request.value) == {'as_before': '1', 'source': 'designer', 'printer': 'simulation', 'model': 'QL-800'}
    page.wait_for_selector('#toast:has-text("Printed 1 label: Flour")')
    page.wait_for_function("() => document.querySelectorAll('#setHist .set-hrow').length === 3")
    assert page.locator('#layer:visible').count() == 0


@pytest.mark.parametrize('asks, same, other', [
    (['date'], 'Same date', "Today's date"),
    (['asn'], 'Same number', 'Next number'),
    (['date', 'asn'], 'Same date and number', "Today's date, next number")])
def test_print_again_asks_only_what_would_change(page, server, asks, same, other):
    job = print_over_the_api(server, 'Jar')

    def listed(route):
        with urlopen(server + '/labeldesigner/api/history?limit=60') as r:
            told = json.load(r)
        for entry in told['entries']:
            entry['again_asks'] = asks
        route.fulfill(json=told)
    page.route('**/api/history?*', listed)
    open_settings(page, 'printed')
    rows(page)
    page.click('#setHist [data-again]')
    page.wait_for_selector('[role=alertdialog][aria-label="Print again"]')
    assert page.inner_text('[role=alertdialog] h2') == 'Print again'
    assert page.locator('[role=alertdialog] .acts button').all_inner_texts() == [other, same]
    assert page.evaluate('() => document.activeElement.id') == 'againSame'
    with page.expect_request(f'**/api/history/{job}/print') as request:
        page.click(f'[role=alertdialog] button:text-is("{other}")')
    assert sent(request.value)['as_before'] == '0'
    page.wait_for_function("() => document.querySelectorAll('#setHist .set-hrow').length === 2")
    page.click('#setHist .set-hrow >> nth=-1 >> [data-again]')
    with page.expect_request(f'**/api/history/{job}/print') as request:
        page.click(f'[role=alertdialog] button:text-is("{same}")')
    assert sent(request.value)['as_before'] == '1'
    page.wait_for_function("() => document.querySelectorAll('#setHist .set-hrow').length === 3")
    # Closed, nothing is printed
    page.click('#setHist .set-hrow >> nth=-1 >> [data-again]')
    page.click('[role=alertdialog] [data-close]')
    page.wait_for_selector('[role=alertdialog]', state='detached')
    assert len(rows(page)) == 3


def test_a_label_printed_with_a_date_asks_for_it(page, server):
    # As the server tells it, without a stand-in
    print_over_the_api(server, 'Opened {{datetime:%d.%m.%Y}}')
    open_settings(page, 'printed')
    rows(page)
    page.click('#setHist [data-again]')
    assert page.locator('[role=alertdialog] .acts button').all_inner_texts() == ["Today's date", 'Same date']


def test_labels_printed_are_not_remembered_while_switched_off(page, server):
    print_over_the_api(server, 'Flour')
    open_settings(page, 'printed')
    assert len(rows(page)) == 1
    assert page.inner_text('#setPart .set-grid').startswith('Remember printed labels')
    assert 'Everyone on your network can see them.' in page.inner_text('#setPart .set-grid')
    # Those remembered go, it asks first
    page.click('[data-k="history"][data-v="0"]')
    page.wait_for_selector('[role=alertdialog] h2:text-is("Stop remembering printed labels?")')
    assert page.inner_text('[role=alertdialog] p') == 'The list is cleared.'
    page.click('[role=alertdialog] button:text-is("Cancel")')
    assert page.locator('[data-k="history"][data-v="1"].on').count() == 1 and len(rows(page)) == 1
    page.click('[data-k="history"][data-v="0"]')
    with page.expect_response('**/api/settings/labels'):
        page.click('[role=alertdialog] button:text-is("Turn off")')
    page.wait_for_selector('#setHist .set-empty:text-is("Printed labels are not remembered.")')
    page.wait_for_selector('#toast:has-text("Printed labels are not remembered")')
    print_over_the_api(server, 'Sugar')
    # Templates have no group of them
    page.click('#tabs [data-tab="edit"]')
    with page.expect_response('**/api/history?*'):
        page.click('#templates')
    page.wait_for_selector('.tpl-nav [data-cat="saved"]')
    assert page.locator('.tpl-nav [data-cat="printed"]').count() == 0
    page.keyboard.press('Escape')
    open_settings(page, 'printed')
    with page.expect_response('**/api/settings/labels'):
        page.click('[data-k="history"][data-v="1"]')
    page.wait_for_selector('#setHist .set-empty:text-is("Nothing printed yet. Labels you print show up here.")')
    # Off without any, it asks nothing
    with page.expect_response('**/api/settings/labels'):
        page.click('[data-k="history"][data-v="0"]')
    page.wait_for_selector('#setHist .set-empty:text-is("Printed labels are not remembered.")')


def test_the_list_is_cleared_after_asking(page, server):
    print_over_the_api(server, 'Flour')
    open_settings(page, 'printed')
    assert len(rows(page)) == 1
    page.click('#setClear')
    page.wait_for_selector('[role=alertdialog] h2:text-is("Clear all printed labels?")')
    page.click('[role=alertdialog] button:text-is("Cancel")')
    assert len(rows(page)) == 1
    page.click('#setClear')
    with page.expect_response('**/api/history/clear'):
        page.click('#clearGo')
    page.wait_for_selector('#setHist .set-empty:text-is("Nothing printed yet. Labels you print show up here.")')


def test_a_printed_label_opens_in_the_editor_with_its_values(page, server):
    print_over_the_api(server, 'Jar {{blank:What}}', blanks=json.dumps({'What': 'Beans'}))
    open_settings(page, 'printed')
    rows(page)
    page.click('#setHist [data-open]')
    page.wait_for_selector('#field .ln')
    assert page.evaluate('() => [labelEditor.S.edit.design.text[0].text, labelEditor.S.edit.values.What]') == ['Jar {{blank:What}}', 'Beans']


def open_label(page, text, fields, blanks=None):
    """A label with blanks opened in the editor, as from a saved label."""
    page.evaluate("""async ([text, fields, blanks]) => {
        const core = await import('/static/designer/core.js'), editor = await import('/static/designer/editor.js');
        const design = Object.assign(core.baseDesign('62'), { text: [core.L(text, 40)], fields }, blanks ? { blanks } : {});
        editor.openEditor({ title: 'Jar', design, values: {}, origin: null });
    }""", [text, fields, blanks])
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)


@pytest.mark.parametrize('blanks, fields, sent_blanks, asks', [
    # Today, and 5 days from today, follow the day the server prints on
    (None, {'Day': {'type': 'date'}}, {}, ['date']),
    (None, {'Day': {'type': 'date', 'default': '+5d'}}, {}, ['date']),
    ({'Day': '+5d'}, {'Day': {'type': 'date'}}, {'Day': '+5d'}, ['date']),
    # A date of its own stays
    ({'Day': '2026-11-03'}, {'Day': {'type': 'date'}}, {'Day': '2026-11-03'}, [])])
def test_a_date_blank_printed_from_the_editor_follows_the_day(page, server, blanks, fields, sent_blanks, asks):
    with page.expect_request(lambda r: r.url.endswith('/api/preview') and 'Opened' in (r.post_data or '')) as previewed:
        open_label(page, 'Opened {{blank:Day}}', fields, blanks)
    with page.expect_request('**/api/print') as request:
        page.click('#print')
    assert json.loads(sent(request.value)['blanks']) == sent_blanks
    # The preview asks the same, so that both show the same day
    assert json.loads(sent(previewed.value)['blanks']) == sent_blanks
    page.wait_for_selector('#toast:has-text("Printed 1 label")')
    with urlopen(server + '/labeldesigner/api/history?limit=1') as r:
        assert json.load(r)['entries'][0]['again_asks'] == asks


def test_a_label_printed_opens_as_the_saved_label_it_came_from(page, server):
    from tests.browser.test_gallery import save_new
    save_new(page, 'Pantry jar', 'Pantry')
    name = page.evaluate("() => labelEditor.S.edit.origin.id")
    with page.expect_request('**/api/print'):
        page.click('#print')
    page.wait_for_selector('#toast:has-text("Printed 1 label")')
    # Printed elsewhere by its name
    with urlopen(f'{server}/labeldesigner/api/repository/print', urlencode({'name': name, 'printer': 'simulation'}).encode()) as r:
        assert json.load(r)['success']
    page.click('#newLabel')
    open_settings(page, 'printed')
    assert len(rows(page)) == 2
    for row in (0, 1):
        open_settings(page, 'printed')
        rows(page)
        page.click(f'#setHist .set-hrow >> nth={row} >> [data-open]')
        page.wait_for_selector('#field .ln')
        assert page.evaluate('() => [labelEditor.S.edit.origin && labelEditor.S.edit.origin.kind, labelEditor.S.edit.origin && labelEditor.S.edit.origin.id]') == ['saved', name]


def test_labels_of_other_programs_are_printed_again_but_not_opened(page, server):
    with urlopen(server + '/labeldesigner/api/homebox', png(200, 100)) as r:
        assert json.load(r)['success']
    print_over_the_api(server, 'Flour')
    open_settings(page, 'printed')
    assert [row.split('\n')[0] for row in rows(page)] == ['Flour', 'Label']
    assert page.locator('#setHist .set-hrow >> nth=0 >> [data-open]').count() == 1
    homebox = page.locator('#setHist .set-hrow >> nth=1')
    assert 'from Homebox' in homebox.inner_text()
    assert homebox.locator('[data-open]').count() == 0 and homebox.locator('[data-again]').count() == 1


def test_more_labels_printed_are_shown_when_asked(page, server):
    def entry(number):
        return {'id': f'e{number}', 'time': '2026-11-22T10:00:00Z', 'title': f'Jar {number}', 'labels': 1, 'copies': 1, 'from': '',
                'from_detail': '', 'printer_name': 'Demo printer', 'state': 'printed', 'message': '', 'preview': None,
                'again': True, 'again_asks': [], 'date_blanks': [], 'open': True}
    asked = []

    def listed(route):
        asked.append(route.request.url)
        older = 'before=e59' in route.request.url
        route.fulfill(json={'enabled': True, 'entries': [entry(60)] if older else [entry(n) for n in range(60)]})
    page.route('**/api/history?*', listed)
    open_settings(page, 'printed')
    assert len(rows(page)) == 60
    page.click('#setMore')
    page.wait_for_function("() => document.querySelectorAll('#setHist .set-hrow').length === 61")
    assert page.locator('#setMore').count() == 0 and asked[-1].endswith('limit=60&before=e59')


def test_labels_printed_are_listed_by_their_saved_name_else_by_their_first_line(page, server):
    from tests.browser.test_gallery import print_jar, save_new
    save_new(page, 'Rice jar', 'Rice')
    with page.expect_request('**/api/print'):
        page.click('#print')
    page.wait_for_selector('#toast:has-text("Printed 1 label")')
    page.click('#newLabel')
    print_jar(page, 'Beans')
    open_settings(page, 'printed')
    assert [row.split('\n')[0] for row in rows(page)] == ['Jar Beans', 'Rice jar']
