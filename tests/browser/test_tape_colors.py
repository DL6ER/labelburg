"""The label designer at /labeldesigner/ shows the label on the tape in the
printer, in the colors the printer reports, and names them with the tape."""
import contextlib
import os

import pytest

from app.labeldesigner import printer_settings
from app.labeldesigner.drivers import devices
from app.labeldesigner.drivers.brother.status import parse_status
from tests.browser.test_editor import sent, type_label, wait_for_preview
from tests.browser.test_settings import printers_are
from tests.printing.test_tape_colors import ptouch_status

PRINTER = 'tcp://192.0.2.10'
# The pixel of the label in a corner and the colors in it, as the browser shows it
SHOWN = """() => {
    const img = document.querySelector('#labelImg'), c = document.createElement('canvas');
    [c.width, c.height] = [img.naturalWidth, img.naturalHeight];
    const g = c.getContext('2d');
    g.drawImage(img, 0, 0);
    const data = g.getImageData(0, 0, c.width, c.height).data, colors = new Set();
    for (let i = 0; i < data.length; i += 4) colors.add(data.slice(i, i + 4).join());
    return { corner: [...data.slice(0, 4)], colors: [...colors] };
}"""
PAPER = "() => getComputedStyle(document.querySelector('#paper')).backgroundColor"


@pytest.fixture(autouse=True)
def found(monkeypatch):
    # The network printer is known, as if the search found it
    monkeypatch.setattr('app.labeldesigner.routes.found_devices', lambda: {PRINTER})


@pytest.fixture(params=['light'])
def designer(request, browser, server, app):
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    context = browser.new_context(viewport={'width': 1600, 'height': 924}, locale='en-US')
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(server + '/labeldesigner/')
    # The theme chosen in the Settings, the next load takes it
    page.evaluate('theme => localStorage.setItem("designer_theme", theme)', request.param)
    page.wait_for_selector('#field .ln')
    wait_for_preview(page)
    yield page
    context.close()
    with contextlib.suppress(FileNotFoundError):
        os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
    assert errors == []


def reports(page, server, monkeypatch, tape_code, ink_code):
    """A PT-P750W with 24 mm TZe tape of these colors, its status as the
    server keeps it and as the label designer reads it."""
    state = parse_status(ptouch_status(tape_code, ink_code))
    monkeypatch.setitem(devices.status_cache, devices.lock_key(PRINTER), state)
    page.evaluate("path => localStorage.setItem('designer_printer', JSON.stringify(path))", PRINTER)
    printers_are(page, server, {**state, 'path': PRINTER, 'name': 'Workshop'})


def shown_label(page, text='Shelf'):
    with page.expect_request('**/api/preview') as asked:
        type_label(page, text)
    return sent(asked.value), page.evaluate(SHOWN)


def test_the_label_on_white_on_black_tape(designer, server, monkeypatch):
    reports(designer, server, monkeypatch, 0x08, 0x01)
    asked, shown = shown_label(designer)
    assert asked['tape_colors'] == '1' and asked['printer'] == PRINTER
    # White print on black tape, the tape around it black too
    assert shown['corner'] == [0, 0, 0, 255] and '255,255,255,255' in shown['colors']
    assert designer.evaluate(PAPER) == 'rgb(0, 0, 0)'
    assert designer.inner_text('#printerChip small') == '24 mm, white on black'
    assert '24 mm (TZe tape), white on black' in designer.get_attribute('#printerChip', 'title')
    designer.click('#printerChip')
    assert '24 mm (TZe tape) in the printer, white on black' in designer.inner_text('.pp [data-p="0"]')
    designer.click('#toSettings')
    designer.wait_for_selector('#setPart[data-part="printers"]')
    assert designer.inner_text('[data-card="0"] .set-val') == '24 mm (TZe tape), white on black'


def test_the_label_on_another_tape_and_without_colors(designer, server, monkeypatch):
    reports(designer, server, monkeypatch, 0x06, 0x08)
    shown = shown_label(designer)[1]
    assert shown['corner'] == [255, 212, 0, 255]
    assert designer.evaluate(PAPER) == 'rgb(255, 212, 0)'
    # A tape the printer names no colors of is shown white, and is not asked for
    reports(designer, server, monkeypatch, 0xF1, 0xF1)
    asked, shown = shown_label(designer, 'Bin')
    assert 'tape_colors' not in asked
    assert shown['corner'] == [255, 255, 255, 255]
    assert designer.evaluate(PAPER) == 'rgb(255, 255, 255)'
    assert designer.get_attribute('#labelbox', 'class').split() == ['labelbox']
    assert designer.inner_text('#printerChip small') == '24 mm'


@pytest.mark.parametrize('designer', ['light', 'dark'], indirect=True)
def test_clear_tape_shows_the_stage(designer, server, monkeypatch, request):
    reports(designer, server, monkeypatch, 0x03, 0x08)
    shown = shown_label(designer)[1]
    assert shown['corner'][3] == 0 and '0,0,0,255' in shown['colors']
    assert designer.inner_text('#printerChip small') == '24 mm, black on clear'
    # Black print on clear tape: the stage shows through where it is light,
    # in the dark the stage of the light theme is behind it
    theme = request.node.callspec.params['designer']
    assert designer.evaluate('() => document.documentElement.dataset.theme') == theme
    dark = theme == 'dark'
    assert designer.evaluate(PAPER) == ('rgb(233, 235, 239)' if dark else 'rgba(0, 0, 0, 0)')
    # White print on clear tape, the other way round
    reports(designer, server, monkeypatch, 0x09, 0x01)
    shown = shown_label(designer, 'Bin')[1]
    assert shown['corner'][3] == 0 and '255,255,255,255' in shown['colors']
    assert designer.evaluate(PAPER) == ('rgba(0, 0, 0, 0)' if dark else 'rgb(58, 63, 69)')
