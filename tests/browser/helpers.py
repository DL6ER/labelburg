"""Helpers shared by the browser tests."""

import io

from PIL import Image

from app.labeldesigner import tape


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


def shown_again(page):
    """The page hidden, e.g. behind another tab, and shown again, as the
    browser tells it. Waits until it read the label settings again."""
    with page.expect_response(lambda r: r.url.endswith('/api/settings/labels') and r.request.method == 'GET'):
        page.evaluate("""() => [true, false].forEach(hidden => {
            Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
            Object.defineProperty(document, 'visibilityState', { value: hidden ? 'hidden' : 'visible', configurable: true });
            document.dispatchEvent(new Event('visibilitychange', { bubbles: true }));
        })""")
