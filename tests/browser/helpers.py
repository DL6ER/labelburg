"""Helpers shared by the browser tests and their fixtures."""

import io
import time

from PIL import Image

from app.labeldesigner import tape


def type_line(page, row, text):
    page.fill(f'[data-row="{row}"] .tx', text)
    wait_for_preview(page)


def kept(page):
    """Wait for the pause after a change: it is a step to undo then, and
    kept for the next visit."""
    page.wait_for_function('() => S.hist.last === snapshot(S.edit)')


def fits_note(page) -> str:
    return page.inner_text('#edFits')


def preview_size(page, stage='#edStage') -> tuple:
    return tuple(page.eval_on_selector(f'{stage} .paper img', 'img => [img.naturalWidth, img.naturalHeight]'))


def printed(page, count=1):
    page.wait_for_function("count => S.history.length === count && S.history.every(h => h.state !== 'printing')", arg=count)
    return page.evaluate('() => S.history.map(h => h.state)')


def png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new('L', (width, height), 'black').save(out, format='PNG')
    return out.getvalue()


def with_tape(printer: dict, default: str = '62') -> dict:
    """A printer as /api/printer_status tells it, with the tape the server
    finds in it and the label sizes it takes."""
    model = printer.get('model')
    manual = (printer.get('settings') or {}).get('label_size', '')
    return {**printer, 'tape': tape.describe(tape.in_printer(printer, model, manual, default)), 'takes': tape.takes(model)}


def fake_printer(page, manual='', **status):
    """Answer the status queries with a QL-800 that reports the given
    status, e.g. the loaded labels, and load the page again. Its settings
    are those the server keeps for it, manual a tape set by hand where they
    have none."""
    printer = {'errors': [], 'model': 'QL-800', 'path': 'simulation', 'media_width': None, 'media_length': 0, **status}

    def answer(route):
        real = next((p for p in route.fetch().json().get('printers', []) if p['path'] == printer['path']), {})
        settings = {**({'label_size': manual} if manual else {}), **real.get('settings', {}), **status.get('settings', {})}
        faked = with_tape({**printer, 'settings': settings})
        route.fulfill(json={**faked, 'printers': [faked], 'selected': printer['path']})

    page.route('**/api/printer_status*', answer)
    page.reload()
    wait_for_preview(page)


def set_tape(page, size):
    """Set the tape in the printer by hand in the printer panel, from the
    editor. Its label is placed on that tape."""
    page.click('#edTapeChange')
    with page.expect_response('**/api/settings') as answer:
        page.select_option('#setTape', size)
    assert answer.value.ok, f"Tape {size} refused: {answer.value.json().get('message')}"
    page.wait_for_function('size => S.tapeManual && S.tape === size', arg=size)
    page.keyboard.press('Escape')
    page.wait_for_selector('.pp', state='detached')
    wait_for_preview(page)


def save_as(page, name):
    page.click('#edSave')
    page.fill('#saveName', name)
    page.press('#saveName', 'Enter')


def wait_for_held(page, held, count=1, timeout=30):
    """Wait until a route has held count requests in held, failing after
    timeout seconds as the waits of Playwright do."""
    deadline = time.monotonic() + timeout
    while len(held) < count:
        assert time.monotonic() < deadline, f'{len(held)} of {count} requests held'
        page.wait_for_timeout(50)


def wait_for_preview(page, stage='#edStage'):
    """The label designer: wait until the stage shows the newest preview.
    Every change marks the stage as busy right away."""
    page.wait_for_function("stage => { const el = document.querySelector(stage); return el && !el.classList.contains('busy'); }", arg=stage)
