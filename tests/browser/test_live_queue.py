"""The label designer with the queue of the live server, which prints on
the demo printer."""
import contextlib
import os
import threading

from app.labeldesigner import printer
from tests.browser.test_editor import editor, type_label  # noqa: F401
from tests.browser.test_gallery import open_saved, save_new
from tests.browser.test_print_queue import watch_toasts


def gate(monkeypatch, failures=()):
    """Each job prints once release is set, the n-th one fails with
    failures[n] where that is given."""
    release, count = threading.Event(), [0]
    original = printer.PrinterQueue.process_queue

    def held(self):
        n = count[0]
        count[0] += 1
        release.wait(20)
        if n < len(failures) and failures[n]:
            self._print_queue.clear()
            return failures[n]
        return original(self)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', held)
    return release


def printed(page) -> list:
    return [t for t in page.evaluate('() => window.toasts') if t.startswith('Printed')]


def test_a_label_printed_while_another_prints_waits_in_the_queue(editor, queued, monkeypatch):  # noqa: F811
    release = gate(monkeypatch)
    try:
        type_label(editor, 'Shelf')
        watch_toasts(editor)
        editor.click('#print')
        editor.wait_for_function('() => labelEditor.S.history.length === 1 && labelEditor.S.history[0].job')
        editor.click('#print')
        editor.wait_for_selector('#queue .q-line:has-text("Printing Shelf")', timeout=10000)
        editor.wait_for_selector('#queue [data-more]:has-text("1 waiting")', timeout=10000)
        assert editor.locator('#print:text-is("Print")').count() == 1
    finally:
        release.set()
    editor.wait_for_function("() => labelEditor.S.history.length === 2 && labelEditor.S.history.every(h => h.state === 'printed')", timeout=15000)
    editor.wait_for_selector('#print:text-is("Print")', timeout=5000)
    editor.wait_for_selector('#queue', state='hidden', timeout=5000)
    assert printed(editor) == ['Printed 1 label: Shelf']


def test_a_label_that_did_not_print_holds_the_queue_until_continue(editor, queued, monkeypatch):  # noqa: F811
    release = gate(monkeypatch, failures=['The tape is used up'])
    try:
        type_label(editor, 'Shelf')
        editor.click('#print')
        editor.wait_for_function('() => labelEditor.S.history.length === 1 && labelEditor.S.history[0].job')
        editor.click('#print')
        editor.wait_for_function('() => labelEditor.S.history.length === 2 && labelEditor.S.history.every(h => h.job)')
    finally:
        release.set()
    editor.wait_for_selector('#queue .q-line.held:has-text("Not printed: The tape is used up. 1 waiting.")', timeout=10000)
    assert editor.locator('[role=alertdialog]').count() == 0
    editor.click('#queue [data-continue]')
    editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')", timeout=15000)
    assert editor.evaluate('() => labelEditor.S.history.map(h => [h.state, h.message])') == [['printed', ''], ['failed', 'The tape is used up']]
    editor.wait_for_selector('#print:text-is("Print")', timeout=5000)


def test_printed_is_told_while_a_label_waits_on_another_printer(editor, queued):  # noqa: F811
    # A label of this browser waits on another printer, e.g. its queue is held
    editor.route('**/api/queue/other', lambda route: route.fulfill(json={'id': 'other', 'state': 'waiting', 'message': '', 'start': 'x'}))
    editor.evaluate("""() => labelEditor.S.history.unshift({ id: 'o1', time: Date.now(), title: 'Other', design: { text: [] }, values: {},
        copies: 1, source: null, printer: 'QL-800', printerName: '', path: 'file:///dev/usb/lp9', state: 'printing', message: '',
        job: 'other', start: 'x', seen: 'waiting' })""")
    type_label(editor, 'Shelf')
    watch_toasts(editor)
    editor.click('#print')
    editor.wait_for_function("() => labelEditor.S.history[0].state === 'printed'", timeout=15000)
    editor.wait_for_selector('#toast:has-text("Printed 1 label: Shelf")', timeout=5000)


def test_the_gallery_takes_the_next_name_while_the_first_waits(editor, queued, monkeypatch, app):  # noqa: F811
    release = gate(monkeypatch)
    field = '#tplFields [data-blank="Name"]'
    try:
        save_new(editor, 'Badge queue', 'Hello [Name]')
        open_saved(editor, 'Badge queue')
        for n, name in enumerate(('Anna', 'Ben'), 1):
            editor.fill(field, name)
            editor.press(field, 'Enter')
            editor.wait_for_function('n => labelEditor.S.history.filter(h => h.job).length === n', arg=n, timeout=5000)
            # Accepted into the queue: emptied for the next name
            editor.wait_for_function('sel => document.querySelector(sel).value === ""', arg=field, timeout=5000)
        release.set()
        editor.wait_for_function("() => labelEditor.S.history.every(h => h.state === 'printed')", timeout=15000)
        assert editor.evaluate('() => labelEditor.S.history.map(h => h.values.Name)') == ['Ben', 'Anna']
        assert editor.input_value(field) == ''
    finally:
        release.set()
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Badge_queue.json'))
