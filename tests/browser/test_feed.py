"""Feed and cut on the printer card of the Settings of the label designer at
/labeldesigner/: offered where the printer has it, named for what it does
with the tape in the printer, as the server tells it, and sent through the
print queue. On the demo printer as each model."""
import contextlib
import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

import pytest

from app.labeldesigner import printer_settings
from tests.browser.test_editor import sent, wait_for_preview
from tests.browser.test_settings import open_settings

CARD = '#setPrinters [data-card="0"]'
CUTS_OFF = 'Cuts off the last label, e.g. after printing without a cut.'


@pytest.fixture
def designer(browser, server, app):
    kept = os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE)
    with contextlib.suppress(FileNotFoundError):
        os.remove(kept)
    context = browser.new_context(viewport={'width': 1600, 'height': 924}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page
    context.close()
    # The model chosen here does not reach other tests
    with contextlib.suppress(FileNotFoundError):
        os.remove(kept)
    assert errors == []


def _demo(page, server, **settings):
    """The Settings of the demo printer, kept as given first, one after the
    other: a tape is checked against the model kept before."""
    for name, value in settings.items():
        with urlopen(f'{server}/labeldesigner/api/settings', urlencode({'printer': 'simulation', name: value}).encode()) as r:
            assert json.load(r)['success']
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    open_settings(page)
    page.wait_for_selector(f'{CARD} [data-k="leaves_first"]')


@pytest.mark.parametrize('model, label_size, button, hint', [
    ('QL-800', '62', 'Feed and cut', CUTS_OFF),
    ('QL-800', '62x29', 'Feed one label', CUTS_OFF + ' This uses one empty label.'),
    ('PT-E550W', 'pt12', 'Feed and cut', CUTS_OFF),
    ('Epson/ESC-POS', 'escpos80', 'Feed and cut', CUTS_OFF),
    ('Zebra/ZPL', 'zpl57x32', 'Feed one label', 'Feeds one empty label, e.g. to tear off the last one.'),
])
def test_the_button_says_what_it_does_with_the_tape(designer, server, model, label_size, button, hint):
    _demo(designer, server, demo_model=model, label_size=label_size)
    feed = designer.locator(f'{CARD} [data-feed]')
    assert feed.inner_text() == button
    assert designer.inner_text(f'{CARD} [data-feed] + small') == hint
    with designer.expect_response('**/api/feed') as answer:
        feed.click()
    assert answer.value.ok
    assert sent(answer.value.request) == {'printer': 'simulation', 'model': model}
    # The demo printer feeds nothing, and says nothing went wrong
    designer.wait_for_function('() => !document.querySelector("[data-feed]").disabled')
    assert designer.is_hidden('#toast')


@pytest.mark.parametrize('model', ['QL-500', 'Niimbot/B1', 'PUQU/Q00', 'VC-500W'])
def test_not_offered_where_the_printer_has_no_such_thing(designer, server, model):
    _demo(designer, server, demo_model=model)
    assert designer.locator(f'{CARD} [data-feed]').count() == 0


def test_another_roll_names_the_button_anew(designer, server):
    _demo(designer, server, demo_model='QL-800', label_size='62')
    assert designer.inner_text(f'{CARD} [data-feed]') == 'Feed and cut'
    designer.select_option(f'{CARD} select[data-k="label_size"]', '62x29')
    designer.wait_for_function(f'() => document.querySelector({json.dumps(CARD + " [data-feed]")}).textContent === "Feed one label"')


def test_it_waits_once_at_a_time_and_tells_why_it_failed(designer, server):
    _demo(designer, server, demo_model='QL-800')
    pending = []
    designer.route('**/api/feed', lambda route: pending.append(route))
    designer.route('**/api/queue/f1', lambda route: route.fulfill(json={'start': 's1', 'state': 'failed', 'message': 'The cover is open'}))
    designer.click(f'{CARD} [data-feed]')
    designer.wait_for_function('() => document.querySelector("#toast").textContent.includes("Feeding")')
    assert designer.eval_on_selector(f'{CARD} [data-feed]', 'b => b.disabled')
    for _ in range(100):
        if pending:
            break
        designer.wait_for_timeout(20)
    pending[0].fulfill(json={'success': True, 'queued': True, 'job': 'f1', 'state': 'waiting', 'start': 's1'})
    designer.wait_for_function('() => document.querySelector("#toast").textContent.includes("The cover is open")')
    assert designer.inner_text('#toast button') == 'Try again'
    designer.wait_for_function('() => !document.querySelector("[data-feed]").disabled')
    assert len(pending) == 1


def test_a_refusal_is_told(designer, server):
    _demo(designer, server, demo_model='QL-800')
    designer.route('**/api/feed', lambda route: route.fulfill(status=400, json={'success': False, 'message': 'This printer offers no feed and cut'}))
    designer.click(f'{CARD} [data-feed]')
    designer.wait_for_function('() => document.querySelector("#toast").textContent.includes("This printer offers no feed and cut")')
