"""The label designer follows each of its print jobs by id until the
outcome is known: printed, not printed, or not known."""
import pytest

from tests.browser.test_editor import editor, type_label  # noqa: F401

START = 'start-1'


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
        editor.click('[role=alertdialog] [data-close]')
        editor.click('#templates')
        editor.click('.tpl-nav [data-cat="printed"]')
        editor.wait_for_selector(f'.thumb small:text-is("Maybe not printed: {text}")')
        editor.click('.thumb')
        editor.wait_for_selector('#tplPrint:text-is("Print again")')


def test_an_outcome_asked_too_late_is_not_known(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    answer_print(editor)
    answer_queue(editor, [{'status': 404, 'success': False, 'message': 'No such print job'}])
    editor.click('#print')
    editor.wait_for_selector('[role=alertdialog]:has-text("Its outcome is no longer known")', timeout=15000)
    assert history(editor) == [['unknown', 'Its outcome is no longer known', 'waiting']]


def test_jobs_left_printing_are_resolved_when_the_page_opens_again(editor):  # noqa: F811
    type_label(editor, 'Shelf')
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
