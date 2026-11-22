"""The label designer follows each of its print jobs by id until the
outcome is known: printed, not printed, or not known. The queue of the
printer chosen shows at the place of the print message."""
import contextlib
import os
from urllib.parse import parse_qs, unquote

import pytest

from tests.browser.helpers import png, with_tape
from tests.browser.test_editor import editor, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_shell import phone  # noqa: F401

START = 'start-1'
LP9, LP8 = 'file:///dev/usb/lp9', 'file:///dev/usb/lp8'


def answer_print(page, state='waiting', job='j1'):
    page.route('**/api/print', lambda route: route.fulfill(json={'success': True, 'queued': True, 'job': job, 'state': state, 'start': START}))


def answer_queue(page, replies, asked=None):
    """Each request gets the next reply, the last one stays."""
    left = list(replies)

    def handle(route):
        if asked is not None:
            asked.append(route.request.url)
        reply = left.pop(0) if len(left) > 1 else left[0]
        route.fulfill(status=reply.pop('status', 200), json=dict({'start': START}, **reply))
    page.route('**/api/queue/**', handle)


def job(state, **more):
    return dict({'id': 'j1', 'state': state, 'message': ''}, **more)


def history(page):
    return page.evaluate('() => labelEditor.S.history.map(h => [h.state, h.message, h.seen])')


def test_a_queued_label_is_followed_until_it_is_printed(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [job('waiting'), job('printing'), job('printed')])
    editor.click('#print')
    editor.wait_for_selector('#toast:has-text("Printed 1 label: Shelf")', timeout=15000)
    assert history(editor) == [['printed', '', 'printing']]


def test_a_label_removed_in_another_tab_is_not_printed(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [job('removed', message='Removed from the queue')])
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("Removed from the queue")', timeout=15000)
    assert history(editor) == [['failed', 'Removed from the queue', 'waiting']]
    # Listed as the server keeps it
    removed = {'id': 'j1', 'time': '2026-11-22T10:00:00Z', 'title': 'Shelf', 'labels': 1, 'copies': 1, 'from': '', 'from_detail': '',
               'printer_name': 'Demo printer', 'state': 'removed', 'message': 'Removed from the queue', 'preview': None,
               'again': True, 'again_asks': [], 'date_blanks': [], 'open': False}
    editor.route('**/api/history?*', lambda route: route.fulfill(json={'enabled': True, 'entries': [removed]}))
    editor.click('[role=alertdialog] [data-close]')
    editor.click('#templates')
    editor.click('.tpl-nav [data-cat="printed"]')
    editor.wait_for_selector('.thumb small:text-is("Not printed: Removed from the queue")')
    editor.click('.thumb')
    editor.wait_for_selector('#tplPrint:text-is("Print again")')


@pytest.mark.parametrize('seen,state,text', [
    ('waiting', 'failed', 'The label designer was restarted before it printed'),
    ('printing', 'unknown', 'The label designer was restarted while it printed')])
def test_a_restart_before_or_while_printing(editor, seen, state, text):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor, seen)
    answer_queue(editor, [{'status': 404, 'success': False, 'message': 'No such print job', 'start': 'start-2'}])
    editor.click('#print')
    editor.wait_for_selector(f'[role=alertdialog]:has-text("{text}")', timeout=15000)
    assert history(editor) == [[state, text, seen]]
    if state == 'unknown':
        assert editor.locator('[role=alertdialog] button:text-is("Print again")').count() == 1
        assert editor.locator('[role=alertdialog] button:text-is("Try again")').count() == 0


def test_a_job_the_server_kept_through_a_restart_is_followed_on(editor):  # noqa: F811
    # Found in the labels printed, or still waiting in the queue kept on disk
    type_label(editor, 'Shelf')
    answer_print(editor, 'printing')
    text = 'The label designer was restarted while it printed'
    answer_queue(editor, [job('waiting', start='start-2'), job('unknown', message=text, start='start-2')])
    editor.click('#print')
    editor.wait_for_selector(f'[role=alertdialog]:has-text("{text}")', timeout=15000)
    assert history(editor) == [['unknown', text, 'waiting']]


def test_an_outcome_asked_too_late_is_not_known(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [{'status': 404, 'success': False, 'message': 'No such print job'}])
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("Its outcome is no longer known")', timeout=15000)
    assert history(editor) == [['unknown', 'Its outcome is no longer known', 'waiting']]


def test_jobs_left_printing_are_resolved_when_the_page_opens_again(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor, 'printed')
    editor.click('#print')
    editor.wait_for_function("() => labelEditor.S.history[0] && labelEditor.S.history[0].state === 'printed'")
    editor.evaluate("""() => {
        const key = Object.keys(localStorage).find(k => k.endsWith('designer_history'));
        const h = JSON.parse(localStorage.getItem(key));
        Object.assign(h[0], { state: 'printing', job: 'j1', start: 'start-1', seen: 'printing' });
        localStorage.setItem(key, JSON.stringify(h));
    }""")
    answer_queue(editor, [job('printed')])
    editor.reload()
    editor.wait_for_selector('#field .ln')
    editor.wait_for_function("() => labelEditor.S.history[0] && labelEditor.S.history[0].state === 'printed'", timeout=15000)
    assert history(editor)[0][0] == 'printed'


@pytest.mark.parametrize('hidden', [False, True])
def test_the_outcome_is_asked_once_at_a_time_and_not_from_a_tab_in_the_background(editor, hidden):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    held = []
    editor.route('**/api/queue/**', lambda route: held.append(route))
    if hidden:
        editor.evaluate("() => Object.defineProperty(document, 'hidden', { value: true, configurable: true })")
    editor.click('#print')
    # Asked every 1.5 s, the first answer does not come
    editor.wait_for_timeout(3800)
    assert len(held) == (0 if hidden else 1)
    if hidden:
        editor.evaluate("() => { Object.defineProperty(document, 'hidden', { value: false, configurable: true }); document.dispatchEvent(new Event('visibilitychange')); }")
        editor.wait_for_timeout(300)
        assert len(held) == 1
    held[0].fulfill(json=dict(job('printed'), start=START))
    editor.wait_for_selector('#toast:has-text("Printed 1 label")')


def test_a_job_failed_at_once_shows_the_message_of_the_server(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor, 'failed')
    answer_queue(editor, [job('failed', message='The printer is out of tape')])
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("The printer is out of tape")', timeout=15000)
    assert history(editor) == [['failed', 'The printer is out of tape', 'failed']]


def test_a_job_failed_without_a_message_uses_the_fixed_text(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [job('failed')])
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("It was not printed")', timeout=15000)


def test_a_not_found_without_a_start_is_not_known(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    editor.route('**/api/queue/**', lambda route: route.fulfill(status=404, body='<html>Not found</html>', content_type='text/html'))
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("Its outcome is no longer known")', timeout=15000)
    assert history(editor)[0][0] == 'unknown'


def test_a_server_error_keeps_asking(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    replies = [lambda r: r.fulfill(status=500, body='oops'), lambda r: r.fulfill(json=dict(job('printed'), start=START))]
    editor.route('**/api/queue/**', lambda route: (replies.pop(0) if len(replies) > 1 else replies[0])(route))
    editor.click('#print')
    editor.wait_for_selector('#toast:has-text("Printed 1 label")', timeout=15000)


@pytest.mark.parametrize('reply', ['error', 'abort'])
def test_a_failed_answer_with_an_unreadable_job_read_ends_as_failed(editor, reply):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor, 'failed')
    editor.route('**/api/queue/**', lambda route: route.fulfill(status=500, body='oops') if reply == 'error' else route.abort())
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("It was not printed")', timeout=15000)
    assert history(editor) == [['failed', 'It was not printed', 'failed']]


# --- The queue of the printer chosen -------------------------------------------


def printer(path=LP9, **queue):
    """A printer on USB as /api/printer_status tells it, with the summary of
    its queue."""
    return with_tape({'path': path, 'name': '', 'model': 'QL-800', 'status_type': 'Waiting to receive', 'errors': [], 'settings': {},
                      'follow': False, 'queue': dict({'printing': False, 'waiting': 0, 'held': False}, **queue)})


def choose(page, *printers):
    """The printers as the server tells them, the first one chosen."""
    page.route('**/api/printer_status*', lambda route: route.fulfill(json={'printers': list(printers)}))
    page.evaluate("path => localStorage.setItem('designer_printer', JSON.stringify(path))", printers[0]['path'])
    page.evaluate('() => labelEditor.poll()')
    page.wait_for_function('path => labelEditor.S.printer.path === path', arg=printers[0]['path'])


def line(printing=None, waiting=(), held=None):
    return {'printing': printing, 'waiting': list(waiting), 'held': held}


def queued(job_id, title, labels=1, source='', state='waiting', **more):
    return dict({'id': job_id, 'title': title, 'labels': labels, 'from': source, 'from_detail': '', 'state': state, 'message': '',
                 'preview': f'/labeldesigner/api/queue/{job_id}/preview.png'}, **more)


def answer_lines(page, lines):
    """GET /api/queue?printer=<path> answers lines[path], else lines[None].
    The previews of its jobs are images."""
    def handle(route):
        path = unquote(route.request.url.split('printer=', 1)[1])
        route.fulfill(json=dict(lines.get(path, lines.get(None)) or line(), start=START))
    page.route(lambda url: '/api/queue?' in url, handle)
    page.route('**/api/queue/*/preview.png', lambda route: route.fulfill(body=png(120, 40), content_type='image/png'))


# Whether what is at the middle of the queue belongs to selector, i.e. is above it
COVERED = """sel => { const r = document.querySelector('#queue').getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return !!hit && !!hit.closest(sel); }"""
SHELF = queued('j1', 'Shelf 12', labels=3, state='printing', progress={'labels': 3, 'label': 2})
JAR, BOX = queued('j2', 'Spice jar'), queued('j3', 'Box 7', labels=2, source='Homebox')


def test_the_print_button_reads_print_while_the_printer_is_busy(editor):  # noqa: F811
    answer_lines(editor, {None: line(SHELF)})
    choose(editor, printer(printing=True))
    editor.wait_for_selector('#queue:not([hidden])')
    assert editor.locator('#print:text-is("Print")').count() == 1
    assert editor.get_attribute('#print use', 'href') == '#i-printer'


@pytest.mark.parametrize('hidden', [False, True])
def test_the_queue_is_read_while_it_has_jobs_from_a_tab_that_is_shown(editor, hidden):  # noqa: F811
    asked = []
    answer_lines(editor, {None: line(SHELF)})
    editor.on('request', lambda request: '/api/queue?' in request.url and asked.append(request.url))
    choose(editor, printer())
    editor.wait_for_timeout(3200)
    assert asked == []
    if hidden:
        editor.evaluate("() => Object.defineProperty(document, 'hidden', { value: true, configurable: true })")
    editor.evaluate("() => { const S = labelEditor.S; S.printer = Object.assign({}, S.printer, { queue: { printing: true, waiting: 0, held: false } }); }")
    editor.wait_for_timeout(3200)
    assert bool(asked) is not hidden


def test_printing_on_a_busy_printer_adds_the_label_to_the_queue(editor):  # noqa: F811
    answer_lines(editor, {None: line(SHELF)})
    choose(editor, printer(printing=True))
    type_label(editor, 'Spice jar')
    editor.wait_for_selector('#queue:not([hidden])')
    answer_print(editor, job='j2')
    answer_queue(editor, [job('waiting', id='j2')])
    editor.click('#print')
    editor.wait_for_selector('#toast:has-text("Added Spice jar to the queue")')


def test_the_queue_shows_what_prints_and_what_waits(editor):  # noqa: F811
    answer_lines(editor, {None: line(SHELF, [JAR, BOX])})
    choose(editor, printer(printing=True, waiting=2))
    editor.wait_for_selector('#queue:not([hidden])')
    assert editor.inner_text('#queue .q-line > span') == 'Printing Shelf 12, label 2 of 3'
    more = editor.locator('#queue [aria-expanded]')
    assert (more.inner_text(), more.get_attribute('aria-expanded')) == ('2 waiting', 'false')
    assert editor.locator('#queue .q-row').count() == 0
    more.click()
    editor.wait_for_selector('#queue .q-row >> nth=1')
    assert more.get_attribute('aria-expanded') == 'true'
    assert editor.locator('#queue .q-row img[src$="/preview.png"]').count() == 2
    assert editor.locator('#queue .q-row b').all_inner_texts() == ['Spice jar', 'Box 7']
    assert editor.locator('#queue .q-row small').all_inner_texts() == ['1 label', '2 labels, from Homebox']
    # Opened, it stays open as the queue is read again
    editor.wait_for_timeout(1700)
    assert editor.locator('#queue .q-row').count() == 2


def test_a_row_says_where_its_label_comes_from_in_plain_words(editor):  # noqa: F811
    # How it was sent shows on hover
    sent = queued('j4', 'Returns', source='another program', from_detail='Sent over the <API>')
    answer_lines(editor, {None: line(SHELF, [sent, BOX])})
    choose(editor, printer(printing=True, waiting=2))
    editor.click('#queue [aria-expanded]')
    editor.wait_for_selector('#queue .q-row >> nth=1')
    assert editor.locator('#queue .q-row small').all_inner_texts() == ['1 label, from another program', '2 labels, from Homebox']
    hover = editor.locator('#queue .q-row small [title]')
    assert (hover.count(), hover.inner_text(), hover.get_attribute('title')) == (1, 'another program', 'Sent over the <API>')


def test_a_waiting_label_is_removed_with_its_button(editor):  # noqa: F811
    lines = {None: line(SHELF, [JAR, BOX])}
    answer_lines(editor, lines)
    removed = []
    editor.route('**/api/queue/*/remove', lambda route: (removed.append(route.request), route.fulfill(json={'success': True})))
    choose(editor, printer(printing=True, waiting=2))
    editor.click('#queue [aria-expanded]')
    button = editor.locator('#queue .q-row >> nth=0 >> button')
    assert (button.get_attribute('title'), button.get_attribute('aria-label')) == ('Remove from the queue', 'Remove from the queue')
    lines[None] = line(SHELF, [BOX])
    button.click()
    editor.wait_for_function("() => document.querySelectorAll('#queue .q-row').length === 1")
    assert [(r.method, r.url.split('/api')[1], r.headers.get('x-requested-with')) for r in removed] == [
        ('POST', '/queue/j2/remove', 'XMLHttpRequest')]
    assert editor.locator('#queue .q-row b').all_inner_texts() == ['Box 7']
    assert editor.inner_text('#queue [aria-expanded]') == '1 waiting'


def test_a_held_queue_continues(editor):  # noqa: F811
    lines = {None: line(None, [JAR, BOX], 'The tape is used up')}
    answer_lines(editor, lines)
    continued = []
    editor.route('**/api/queue/continue', lambda route: (continued.append(route.request), route.fulfill(json={'success': True})))
    choose(editor, printer(waiting=2, held=True))
    editor.wait_for_selector('#queue:not([hidden])')
    assert editor.inner_text('#queue .q-line > span') == 'Not printed: The tape is used up. 2 waiting.'
    assert editor.locator('#print:text-is("Print")').count() == 1
    lines[None] = line(queued('j2', 'Spice jar', state='printing'), [BOX])
    editor.click('#queue button:text-is("Continue")')
    editor.wait_for_selector('#queue .q-line > span:text-is("Printing Spice jar")')
    assert [(r.method, parse_qs(r.post_data)['printer'], r.headers.get('x-requested-with')) for r in continued] == [
        ('POST', [LP9], 'XMLHttpRequest')]


def test_a_waiting_label_is_removed_from_a_held_line(editor):  # noqa: F811
    # Removing the last one ends the hold, as the server does
    lines = {None: line(None, [JAR, BOX], 'The tape is used up')}
    answer_lines(editor, lines)
    editor.route('**/api/queue/*/remove', lambda route: route.fulfill(json={'success': True}))
    choose(editor, printer(waiting=2, held=True))
    editor.wait_for_selector('#queue .held [aria-expanded="false"]:text-is("2 waiting")')
    editor.eval_on_selector('#queue [role=alert]', 'e => { e.dataset.kept = "1"; }')
    editor.click('#queue [aria-expanded]')
    editor.wait_for_selector('#queue .q-row >> nth=1')
    # Opened in place: the hold is not told again
    assert editor.eval_on_selector('#queue [role=alert]', 'e => e.dataset.kept') == '1'
    assert editor.locator('#queue button:text-is("Continue")').count() == 1
    lines[None] = line(None, [BOX], 'The tape is used up')
    editor.click('#queue [data-remove="j2"]')
    editor.wait_for_selector('#queue [role=alert]:text-is("Not printed: The tape is used up. 1 waiting.")')
    assert editor.locator('#queue .q-row b').all_inner_texts() == ['Box 7']
    lines[None] = line()
    editor.click('#queue [data-remove="j3"]')
    editor.wait_for_selector('#queue', state='hidden')
    assert editor.locator('#print:text-is("Print")').count() == 1


def test_a_print_on_another_printer_reads_no_queue(editor):  # noqa: F811
    # The queue shown is of the printer chosen, which has nothing to do
    asked = []
    answer_lines(editor, {})
    editor.on('request', lambda request: '/api/queue?' in request.url and asked.append(request.url))
    editor.route('**/api/print', lambda route: route.fulfill(json={'success': True}))
    choose(editor, printer(LP9), printer(LP8))
    editor.evaluate("() => { location.hash = 'settings/printers'; }")
    editor.click('[data-card="1"] [data-test]')
    editor.wait_for_selector('#toast:has-text("Printed 1 label: Which end first")')
    editor.wait_for_timeout(1700)
    assert asked == []


def test_the_not_printed_dialog_is_above_the_queue(editor):  # noqa: F811
    answer_lines(editor, {None: line(SHELF, [JAR])})
    choose(editor, printer(printing=True, waiting=1))
    type_label(editor, 'Jar 1')
    editor.wait_for_selector('#queue:not([hidden])')
    editor.route('**/api/print', lambda route: route.fulfill(status=500, json={'success': False, 'message': 'The printer is out of tape'}))
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("The printer is out of tape")')
    assert editor.locator('#queue').is_visible()
    assert editor.evaluate(COVERED, '#layer')


def test_a_remove_refused_says_why(editor):  # noqa: F811
    answer_lines(editor, {None: line(SHELF, [JAR])})
    editor.route('**/api/queue/*/remove', lambda route: route.fulfill(status=409, json={'success': False, 'message': 'It is printing already'}))
    choose(editor, printer(printing=True, waiting=1))
    editor.click('#queue [aria-expanded]')
    editor.click('#queue [data-remove="j2"]')
    editor.wait_for_selector('#toast:has-text("It is printing already")')
    assert editor.evaluate('() => labelEditor.S.history.length') == 0


def test_continue_works_again_after_it_did_not_reach_the_server(editor):  # noqa: F811
    answer_lines(editor, {None: line(None, [JAR], 'The tape is used up')})
    continued = []
    editor.route('**/api/queue/continue', lambda route: (continued.append(route.request), route.abort() if len(continued) == 1 else route.fulfill(json={'success': True})))
    choose(editor, printer(waiting=1, held=True))
    editor.click('#queue button:text-is("Continue")')
    editor.wait_for_selector('#toast:has-text("The label designer could not be reached")')
    with editor.expect_response('**/api/queue/continue'):
        editor.click('#queue button:text-is("Continue")', timeout=8000)
    assert len(continued) == 2


def watch_toasts(page):
    page.evaluate("""() => { window.toasts = [];
        const t = document.querySelector('#toast');
        new MutationObserver(() => { if (!t.hidden) window.toasts.push(t.querySelector('span').textContent); })
            .observe(t, { childList: true, subtree: true, attributes: true }); }""")


def test_only_the_last_of_several_labels_says_printed(editor):  # noqa: F811
    ids = iter(['j1', 'j2', 'j3'])
    editor.route('**/api/print', lambda route: route.fulfill(json={'success': True, 'queued': True, 'job': next(ids), 'state': 'waiting', 'start': START}))
    states = {'j1': 'waiting', 'j2': 'waiting', 'j3': 'waiting'}
    editor.route('**/api/queue/j*', lambda route: route.fulfill(json=job(states[route.request.url.rsplit('/', 1)[1]], start=START)))
    watch_toasts(editor)
    type_label(editor, 'Jar 1')
    editor.click('#print')
    for n in '23':
        editor.click('#field')
        editor.keyboard.press('End')
        editor.keyboard.press('Backspace')
        editor.keyboard.type(n)
        editor.wait_for_function('n => labelEditor.S.edit.design.text[0].text === "Jar " + n', arg=n)
        wait_for_preview(editor)
        editor.click('#print')
    editor.wait_for_function('() => labelEditor.S.history.filter(h => h.job).length === 3')
    for done in ('j1', 'j2'):
        states[done] = 'printed'
        editor.wait_for_function('n => labelEditor.S.history.filter(h => h.state === "printed").length === n', arg=int(done[1]), timeout=15000)
    editor.wait_for_timeout(300)
    states['j3'] = 'printed'
    editor.wait_for_selector('#toast:has-text("Printed 1 label: Jar 3")', timeout=15000)
    told = editor.evaluate('() => window.toasts')
    assert [t for t in told if t.startswith('Printed')] == ['Printed 1 label: Jar 3']
    assert 'Added Jar 2 to the queue' in told and 'Added Jar 3 to the queue' in told


def test_another_message_shows_over_the_queue_and_the_queue_comes_back(editor, app):  # noqa: F811
    answer_lines(editor, {None: line(SHELF)})
    choose(editor, printer(printing=True))
    editor.wait_for_selector('#queue:not([hidden])')
    type_label(editor, 'Pantry shelf')
    editor.click('#save')
    editor.fill('#saveName', 'Queue toast')
    try:
        editor.click('#saveGo')
        editor.wait_for_selector('#toast:has-text("Saved")')
        assert editor.locator('#queue').is_hidden()
        editor.wait_for_selector('#toast', state='hidden', timeout=8000)
        editor.wait_for_selector('#queue:not([hidden])')
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Queue_toast.json'))


@pytest.mark.parametrize('outcome', ['printed', 'failed'])
def test_the_queue_of_the_chosen_printer(editor, outcome):  # noqa: F811
    # Switched to another printer, the queue shown is its one; the jobs on
    # the first are followed on and end in the history
    lines = {LP9: line(SHELF, [queued('j2', 'Jar 1')]), LP8: line(queued('j5', 'Box 7', state='printing'))}
    answer_lines(editor, lines)
    choose(editor, printer(LP9, printing=True, waiting=1), printer(LP8, printing=True))
    type_label(editor, 'Jar 1')
    answer_print(editor, job='j2')
    states = {'j2': job('waiting', id='j2')}
    editor.route('**/api/queue/j2', lambda route: route.fulfill(json=dict(states['j2'], start=START)))
    editor.click('#print')
    editor.wait_for_selector('#toast:has-text("Added Jar 1 to the queue")')
    editor.click('#printerChip')
    editor.click('.pp [data-p="1"]')
    editor.keyboard.press('Escape')
    editor.wait_for_selector('#queue .q-line > span:text-is("Printing Box 7")')
    assert editor.locator('#queue [aria-expanded]').count() == 0
    if outcome == 'printed':
        states['j2'] = job('printed', id='j2')
        lines[LP9] = line(SHELF)
    else:
        states['j2'] = job('failed', id='j2', message='The tape is used up')
        lines[LP9] = line(None, [queued('j3', 'Jar 2')], 'The tape is used up')
    editor.wait_for_function('s => labelEditor.S.history[0].state === s', arg=outcome, timeout=15000)
    if outcome == 'failed':
        editor.wait_for_selector('[role=alertdialog]:has-text("The tape is used up")')
    assert editor.inner_text('#queue .q-line > span') == 'Printing Box 7'


def test_a_failed_label_is_told_by_the_held_queue_shown(editor):  # noqa: F811
    lines = {None: line(SHELF)}
    answer_lines(editor, lines)
    choose(editor, printer(printing=True))
    type_label(editor, 'Jar 1')
    answer_print(editor, job='j2')
    states = {'j2': job('waiting', id='j2')}
    editor.route('**/api/queue/j2', lambda route: route.fulfill(json=dict(states['j2'], start=START)))
    editor.click('#print')
    editor.wait_for_selector('#toast:has-text("Added Jar 1 to the queue")')
    states['j2'] = job('failed', id='j2', message='The tape is used up')
    lines[None] = line(None, [queued('j3', 'Jar 2')], 'The tape is used up')
    editor.wait_for_function("() => labelEditor.S.history[0].state === 'failed'", timeout=15000)
    editor.wait_for_selector('#queue .q-line > span:text-is("Not printed: The tape is used up. 1 waiting.")')
    editor.wait_for_timeout(300)
    assert editor.locator('[role=alertdialog]').count() == 0
    # Told to screen readers as the dialog would have been
    assert editor.inner_text('#queue [role=alert]') == 'Not printed: The tape is used up. 1 waiting.'


def test_a_label_removed_here_is_not_announced(editor):  # noqa: F811
    lines = {None: line(SHELF)}
    answer_lines(editor, lines)
    editor.route('**/api/queue/*/remove', lambda route: route.fulfill(json={'success': True}))
    choose(editor, printer(printing=True))
    type_label(editor, 'Jar 1')
    answer_print(editor, job='j2')
    states = {'j2': job('waiting', id='j2')}
    editor.route('**/api/queue/j2', lambda route: route.fulfill(json=dict(states['j2'], start=START)))
    editor.click('#print')
    lines[None] = line(SHELF, [queued('j2', 'Jar 1')])
    editor.click('#queue [aria-expanded]')
    editor.click('#queue [data-remove="j2"]')
    states['j2'] = job('removed', id='j2', message='Removed from the queue')
    lines[None] = line(SHELF)
    editor.wait_for_function("() => labelEditor.S.history[0].state === 'failed'", timeout=15000)
    editor.wait_for_timeout(300)
    assert editor.locator('[role=alertdialog]').count() == 0
    assert editor.evaluate('() => labelEditor.S.history[0].message') == 'Removed from the queue'


def test_the_demo_printer_leaves_no_queue_behind(editor, queued):  # noqa: F811
    # The demo printer prints each label as soon as it is in the queue
    type_label(editor, 'Shelf')
    watch_toasts(editor)
    editor.evaluate("() => { const print = document.querySelector('#print'); print.click(); print.click(); }")
    editor.wait_for_function("() => labelEditor.S.history.length === 2 && labelEditor.S.history.every(h => h.state === 'printed')", timeout=15000)
    editor.wait_for_selector('#toast:has-text("Printed 1 label: Shelf")')
    editor.wait_for_selector('#print:text-is("Print")')
    editor.wait_for_timeout(1700)
    assert editor.locator('#print:text-is("Print")').count() == 1
    assert editor.locator('#queue').is_hidden()
    assert [t for t in editor.evaluate('() => window.toasts') if t.startswith('Printed')] == ['Printed 1 label: Shelf']


def test_the_queue_on_the_phone(phone):  # noqa: F811
    answer_lines(phone, {None: line(SHELF, [JAR, BOX])})
    choose(phone, printer(printing=True, waiting=2))
    phone.wait_for_selector('#queue:not([hidden])')
    phone.tap('#queue [aria-expanded]')
    phone.wait_for_selector('#queue .q-row >> nth=1')
    bar, queue = phone.locator('#mainActs').bounding_box(), phone.locator('#queue').bounding_box()
    assert queue['y'] + queue['height'] <= bar['y']
    assert queue['x'] >= 16 and queue['x'] + queue['width'] <= 390 - 16
    assert phone.evaluate('() => document.documentElement.scrollWidth') == 390
    # Where the messages show
    assert phone.evaluate("() => getComputedStyle(document.querySelector('#queue')).bottom") == \
        phone.evaluate("() => getComputedStyle(document.querySelector('#toast')).bottom")


def test_the_printer_sheet_is_above_the_queue_on_the_phone(phone):  # noqa: F811
    answer_lines(phone, {None: line(SHELF, [JAR, BOX])})
    choose(phone, printer(printing=True, waiting=2))
    phone.wait_for_selector('#queue:not([hidden])')
    phone.tap('#printerChip')
    phone.wait_for_selector('.layer.sheet .pp .plist')
    assert phone.locator('#queue').is_visible()
    assert phone.evaluate(COVERED, '#layer')


def test_the_held_line_of_the_panel_is_an_alert(editor):  # noqa: F811
    answer_lines(editor, {None: line(None, [JAR], 'The tape is used up')})
    choose(editor, printer(held=True, waiting=1))
    editor.wait_for_selector('#queue .q-line.held [role=alert]')


def test_a_restart_after_a_while_without_a_read_may_have_printed(editor):  # noqa: F811
    # E.g. the tab was in the background: the job may have printed meanwhile
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [{'status': 404, 'success': False, 'message': 'No such print job', 'start': 'start-2'}])
    editor.click('#print')
    # Read 1.5 s after the answer at the earliest
    editor.wait_for_function("() => labelEditor.S.history[0].job === 'j1'")
    editor.evaluate('() => { labelEditor.S.history[0].seenAt -= 6000; }')
    text = 'The label designer was restarted while it printed'
    editor.wait_for_selector(f'[role=alertdialog]:has-text("{text}")', timeout=15000)
    assert history(editor) == [['unknown', text, 'waiting']]


def test_jobs_are_followed_by_the_reads_of_their_line(editor):  # noqa: F811
    # One read of the line tells them all, a job is asked for once it left it
    lines = {None: line()}
    answer_lines(editor, lines)
    choose(editor, printer())
    ids = iter(['j1', 'j2', 'j3'])
    editor.route('**/api/print', lambda route: route.fulfill(json={'success': True, 'queued': True, 'job': next(ids), 'state': 'waiting', 'start': START}))
    states = {'j1': 'printing', 'j2': 'waiting', 'j3': 'waiting'}
    editor.route('**/api/queue/j*', lambda route: route.fulfill(json=job(states[route.request.url.rsplit('/', 1)[1]], start=START)))
    asked = []
    editor.on('request', lambda request: '/api/queue' in request.url and not request.url.endswith('.png')
              and asked.append(request.url.split('/api/', 1)[1]))
    lines[None] = line(queued('j1', 'Jar', state='printing'), [queued('j2', 'Jar'), queued('j3', 'Jar')])
    type_label(editor, 'Jar')
    for _ in range(3):
        editor.click('#print')
    editor.wait_for_function('() => labelEditor.S.history.filter(h => h.job).length === 3')
    asked.clear()
    editor.wait_for_timeout(3200)
    assert [url for url in asked if url.startswith('queue/')] == []
    assert 1 <= len(asked) <= 3
    states['j1'] = 'printed'
    lines[None] = line(queued('j2', 'Jar', state='printing'), [queued('j3', 'Jar')])
    editor.wait_for_function("() => labelEditor.S.history.find(h => h.job === 'j1').state === 'printed'", timeout=15000)
    editor.wait_for_function("() => labelEditor.S.history.find(h => h.job === 'j2').seen === 'printing'", timeout=15000)
    assert sorted({url for url in asked if url.startswith('queue/')}) == ['queue/j1']


def _title(request):
    return parse_qs(request.post_data)['title'][0]


def test_prints_are_sent_one_after_the_other(editor):  # noqa: F811
    # The server queues them in the order they were printed
    held, sent = [], []

    def answer(route):
        sent.append(_title(route.request))
        # The first one waits for its answer
        if held:
            route.fulfill(json={'success': True})
        else:
            held.append(route)
    editor.route('**/api/print', answer)
    type_label(editor, 'Jar 1')
    with editor.expect_request('**/api/print'):
        editor.click('#print')
    editor.click('#field')
    editor.keyboard.press('End')
    editor.keyboard.press('Backspace')
    editor.keyboard.type('2')
    editor.wait_for_function('() => labelEditor.S.edit.design.text[0].text === "Jar 2"')
    wait_for_preview(editor)
    editor.click('#print')
    editor.wait_for_function('() => labelEditor.S.history.length === 2')
    editor.wait_for_timeout(500)
    assert sent == ['Jar 1']
    held[0].fulfill(json={'success': True})
    editor.wait_for_function("() => labelEditor.S.history.every(h => h.state === 'printed')")
    assert sent == ['Jar 1', 'Jar 2']


def test_prints_are_sent_in_order_per_printer(editor):  # noqa: F811
    held = []
    editor.route('**/api/print', lambda route: held.append(route))

    def until(count):
        while len(held) < count:
            editor.wait_for_timeout(10)

    def asked(title):
        return lambda r: r.url.endswith('/api/print') and f'title={title}&'.encode() in r.post_data_buffer
    with editor.expect_request(asked('A1')), editor.expect_request(asked('B1')):
        editor.evaluate("""async () => {
            const core = await import('/static/designer/core.js');
            const on = path => Object.assign({}, core.S.printer, { path });
            const job = title => ({ title, design: core.S.edit.design, values: {}, copies: 1 });
            window.sent = [core.printJob(job('A1'), null, on('file:///dev/usb/lp8')), core.printJob(job('A2'), null, on('file:///dev/usb/lp8')),
                core.printJob(job('B1'), null, on('file:///dev/usb/lp9'))];
        }""")
    # The first of printer 8 and the one of printer 9 are out, the second of 8 waits for the first
    until(2)
    held[1].fulfill(json={'success': True})
    editor.evaluate('() => new Promise(done => setTimeout(done))')
    assert len(held) == 2
    with editor.expect_request(asked('A2')):
        held[0].fulfill(json={'success': True})
    until(3)
    held[2].fulfill(json={'success': True})
    editor.evaluate('() => Promise.all(window.sent)')
