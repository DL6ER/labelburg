"""The Settings part "Print queue": every printer that prints or has labels
waiting, with the rows of the print message."""
from urllib.parse import parse_qs

from tests.browser.helpers import png
from tests.browser.test_editor import editor  # noqa: F401
from tests.browser.test_print_queue import BOX, JAR, LP8, LP9, SHELF, START, choose, printer, queued
from tests.browser.test_shell import phone  # noqa: F401

LP7 = 'file:///dev/usb/lp7'


def answer_all(page, printers, asked=None):
    """GET /api/queue without a printer answers printers[0], a mutable list."""
    def handle(route):
        if asked is not None:
            asked.append(route.request.url)
        route.fulfill(json={'printers': printers[0], 'start': START})
    page.route(lambda url: url.split('?')[0].endswith('/api/queue'), handle)
    page.route('**/api/queue/*/preview.png', lambda route: route.fulfill(body=png(120, 40), content_type='image/png'))


def two_lines():
    return [{'printer': LP9, 'printing': SHELF, 'waiting': [JAR, BOX], 'held': None},
            {'printer': LP8, 'printing': None, 'waiting': [queued('j4', 'Returns', labels=4, source='the webhook')], 'held': 'The cover is open.'}]


def named(*paths):
    names = {LP9: 'Shelf printer', LP8: 'Returns printer', LP7: 'Cable printer'}
    return [dict(printer(p), name=names[p]) for p in paths or names]


def open_page(page, lines, *printers):
    answer_all(page, [lines])
    choose(page, *(printers or named()))
    page.evaluate("() => { location.hash = 'settings/queue'; }")


def test_the_print_queue_page_lists_every_printer_that_prints_or_waits(editor):  # noqa: F811
    open_page(editor, two_lines())
    editor.wait_for_selector('.q-card >> nth=1')
    assert editor.inner_text('.set-nav a.on') == 'Print queue'
    assert editor.inner_text('#setPart h2') == 'Print queue'
    cards = editor.locator('.q-card')
    assert cards.locator('.set-card-h small').all_inner_texts() == ['Printing', 'Stopped']
    assert cards.nth(0).locator('.q-row b').all_inner_texts() == ['Printing Shelf 12, label 2 of 3', 'Spice jar', 'Box 7']
    assert cards.nth(0).locator('.q-row small').all_inner_texts() == ['1 label', '2 labels, from Homebox']
    assert cards.locator('img[src$="/preview.png"]').count() == 4
    assert cards.nth(1).locator('.q-held span').inner_text() == 'Not printed: The cover is open. 1 waiting.'
    assert cards.nth(1).locator('.q-held button').inner_text() == 'Continue'
    assert cards.nth(1).locator('.q-row small').inner_text() == '4 labels, from the webhook'
    assert editor.locator('.set-card-h b').all_inner_texts() == ['Shelf printer', 'Returns printer']
    assert editor.inner_text('#setQueue > p') == 'Cable printer: nothing printing or waiting.'


def test_nothing_printing_says_so(editor):  # noqa: F811
    open_page(editor, [])
    editor.wait_for_selector('#setQueue p')
    assert editor.inner_text('#setQueue') == 'Nothing is printing or waiting.'


def test_a_waiting_label_is_removed_on_the_page(editor):  # noqa: F811
    lines = [two_lines()[0]]
    answer_all(editor, [lines])
    removed = []
    editor.route('**/api/queue/*/remove', lambda route: (removed.append(route.request), route.fulfill(json={'success': True})))
    choose(editor, printer())
    editor.evaluate("() => { location.hash = 'settings/queue'; }")
    editor.wait_for_selector('.q-card .q-row >> nth=2')
    lines[0] = dict(lines[0], waiting=[BOX])
    editor.click('.q-card [data-remove="j2"]')
    editor.wait_for_function("() => document.querySelectorAll('.q-card .q-row').length === 2")
    assert [(r.method, r.url.split('/api')[1], r.headers.get('x-requested-with')) for r in removed] == [('POST', '/queue/j2/remove', 'XMLHttpRequest')]


def test_continue_on_the_page_names_the_printer(editor):  # noqa: F811
    answer_all(editor, [two_lines()])
    continued = []
    editor.route('**/api/queue/continue', lambda route: (continued.append(route.request), route.fulfill(json={'success': True})))
    choose(editor, printer(), printer(LP8))
    editor.evaluate("() => { location.hash = 'settings/queue'; }")
    editor.click('.q-card >> nth=1 >> [data-continue]')
    editor.wait_for_timeout(300)
    assert [(r.method, parse_qs(r.post_data)['printer'], r.headers.get('x-requested-with')) for r in continued] == [
        ('POST', [LP8], 'XMLHttpRequest')]


def test_the_page_reads_the_queue_only_while_it_is_shown(editor):  # noqa: F811
    asked = []
    answer_all(editor, [two_lines()], asked)
    choose(editor, printer())
    editor.evaluate("() => { location.hash = 'settings/queue'; }")
    editor.wait_for_selector('.q-card')
    editor.wait_for_timeout(1700)
    assert len(asked) >= 2
    editor.evaluate("() => { location.hash = 'settings/labels'; }")
    editor.wait_for_timeout(300)
    n = len(asked)
    editor.wait_for_timeout(3200)
    assert len(asked) == n
    editor.evaluate("() => { location.hash = 'settings/queue'; }")
    editor.wait_for_timeout(300)
    n = len(asked)
    editor.evaluate("() => Object.defineProperty(document, 'hidden', { value: true, configurable: true })")
    editor.wait_for_timeout(3200)
    assert len(asked) == n


def test_the_print_queue_page_on_the_phone(phone):  # noqa: F811
    open_page(phone, two_lines())
    phone.wait_for_selector('.q-card >> nth=1')
    assert phone.evaluate('() => document.documentElement.scrollWidth') == 390
    for box in phone.locator('.q-card').all():
        b = box.bounding_box()
        assert b['x'] >= 0 and b['x'] + b['width'] <= 390
