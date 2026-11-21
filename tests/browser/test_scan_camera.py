"""Codes read with the camera at the scan station: decoded in the browser
with the vendored reader, which is loaded from the server only when the
camera is switched on."""
import contextlib
import io
import os

import pytest
from PIL import Image

from app.labeldesigner import codes, printer_settings
from tests.browser.test_scan import BOX, Station, save_box

LINK = 'https://example.org/r/AbC9'
WASM = '/static/designer/vendor/zxing-wasm/zxing_reader.wasm'


def code_png(code_type, data, scale=8, quiet=4):
    rows, _ = codes.matrix(code_type, data, 'M' if code_type == 'QR' else None)
    size = len(rows[0]) + 2 * quiet, len(rows) + 2 * quiet
    image = Image.new('L', size, 255)
    for y, row in enumerate(rows):
        for x, dot in enumerate(row):
            if dot:
                image.putpixel((x + quiet, y + quiet), 0)
    out = io.BytesIO()
    image.resize((size[0] * scale, size[1] * scale), Image.NEAREST).save(out, format='PNG')
    return out.getvalue()


def serve_codes(page, server):
    pictures = {'qr.png': code_png('QR', LINK), 'dm.png': code_png('DATAMATRIX', 'A-17')}
    page.route(server + '/test-codes/*', lambda route: route.fulfill(content_type='image/png', body=pictures[route.request.url.rsplit('/', 1)[1]]))


# getUserMedia answers with the picture of a canvas showing the QR code, or
# what window.cameraShows(src) puts in view, or fails with the error named in
# window.cameraFails
FAKE_CAMERA = """
window.cameraAsked = 0;
window.cameraTracks = [];
if (navigator.mediaDevices) navigator.mediaDevices.getUserMedia = async constraints => {
    window.cameraAsked++;
    window.cameraConstraints = constraints;
    if (window.cameraFails) throw new DOMException('No', window.cameraFails);
    const img = new Image();
    img.src = window.cameraPicture || '/test-codes/qr.png';
    await img.decode();
    window.cameraShows = async src => { const next = new Image(); next.src = src; await next.decode(); window.cameraImg = next; };
    window.cameraImg = img;
    const canvas = document.createElement('canvas');
    canvas.width = 480;
    canvas.height = 360;
    const g = canvas.getContext('2d');
    const paint = () => { const p = window.cameraImg; g.fillStyle = '#fff'; g.fillRect(0, 0, 480, 360); g.drawImage(p, (480 - p.width) / 2, (360 - p.height) / 2); };
    paint();
    setInterval(paint, 50);
    const stream = canvas.captureStream(20);
    window.cameraTracks.push(...stream.getTracks());
    return stream;
};
"""


@pytest.fixture
def camera_station(browser, server, app):
    """The station with a camera that sees a QR code; the URLs the page loads recorded."""
    opened = []

    def start(viewport=None, init='', **options):
        with contextlib.suppress(FileNotFoundError):
            os.remove(os.path.join(app.config['LABEL_REPOSITORY_DIR'], printer_settings.FILE))
        context = browser.new_context(viewport=viewport or {'width': 1600, 'height': 900}, locale='en-US', **options)
        page = context.new_page()
        errors, loaded = [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: loaded.append(request.url))
        save_box(page, server)
        station = Station(page)
        serve_codes(page, server)
        page.add_init_script(FAKE_CAMERA + init)
        page.goto(f'{server}/labeldesigner/#scan/{BOX}')
        page.wait_for_selector('#scanField:focus')
        opened.append((context, errors))
        return page, station, loaded

    yield start
    for context, errors in opened:
        context.close()
        assert errors == []


def ended(page):
    return page.evaluate('() => window.cameraTracks.map(track => track.readyState)')


def test_decode_reads_a_qr_and_a_datamatrix(camera_station, server):
    page, _, loaded = camera_station()
    texts = page.evaluate("""async () => {
        const { decode } = await import('/static/designer/camera.js');
        const drawn = async src => {
            const img = new Image();
            img.src = src;
            await img.decode();
            const canvas = document.createElement('canvas');
            canvas.width = img.width + 60;
            canvas.height = img.height + 40;
            const g = canvas.getContext('2d');
            g.fillStyle = '#fff';
            g.fillRect(0, 0, canvas.width, canvas.height);
            g.drawImage(img, 30, 20);
            return canvas;
        };
        const qr = await drawn('/test-codes/qr.png'), dm = await drawn('/test-codes/dm.png');
        const blank = document.createElement('canvas');
        blank.width = blank.height = 200;
        return [await decode(qr), await decode(dm.getContext('2d').getImageData(0, 0, dm.width, dm.height)), await decode(blank)];
    }""")
    assert texts == [LINK, 'A-17', None]
    # The decoder comes from the server, nothing from elsewhere
    assert server + WASM in loaded
    assert [url for url in loaded if not url.startswith(server + '/') and not url.startswith('data:')] == []


def test_same_code_in_view_prints_once(camera_station):
    page, _, _ = camera_station()
    taken = page.evaluate("""async () => {
        const { camera } = await import('/static/designer/camera.js');
        const frames = [['X', 0], ['X', 500], ['X', 2900], ['Y', 3000], ['X', 3100], ['X', 6200]];
        let now = 0, i = 0;
        const taken = [], video = document.createElement('video');
        return new Promise(done => {
            const cam = camera(video, text => taken.push([text, now]), {
                clock: { now: () => now },
                read: async () => {
                    if (i === frames.length) { cam.stop(); done(taken); return null; }
                    const [text, at] = frames[i++];
                    now = at;
                    return text;
                },
            });
        });
    }""")
    assert taken == [['X', 0], ['Y', 3000], ['X', 6200]]


def test_camera_scan_prints_once_and_stops(camera_station, server):
    page, station, loaded = camera_station()
    assert not page.locator('#scanCam video').is_visible()
    page.click('#scanCamera')
    page.wait_for_selector('#scanCamera:has-text("Stop the camera")')
    assert page.evaluate('() => window.cameraConstraints') == {'video': {'facingMode': 'environment'}}
    station.wait_for(1)
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    assert page.locator('#scanCam video').is_visible()
    # The code stays in view: printed once
    assert station.none_for(800) == 1
    assert station.codes() == [LINK]
    assert 'preview_only' not in station.requests[0]
    assert page.evaluate('() => document.activeElement.id') == 'scanField'
    page.click('#scanCamera')
    page.wait_for_selector('#scanCamera:has-text("Use the camera")')
    assert ended(page) == ['ended']
    assert not page.locator('#scanCam video').is_visible()
    assert [url for url in loaded if not url.startswith(server + '/') and not url.startswith('data:')] == []


def test_show_only_applies_to_the_camera(camera_station):
    page, station, _ = camera_station()
    page.click('#scanShow')
    page.click('#scanCamera')
    station.wait_for(1)
    assert station.requests[0]['preview_only'] == '1'
    page.wait_for_selector('#scanResult:has-text("Shown, not printed")')


def test_leaving_the_station_stops_the_camera(camera_station):
    page, station, _ = camera_station()
    page.click('#scanCamera')
    station.wait_for(1)
    assert ended(page) == ['live']
    page.evaluate("() => { location.hash = 'edit'; }")
    page.wait_for_selector('#field')
    page.wait_for_function("() => window.cameraTracks.every(track => track.readyState === 'ended')")


def test_camera_reads_nothing_while_a_dialog_is_open(camera_station):
    page, station, _ = camera_station()
    page.click('#scanCamera')
    station.wait_for(1)
    page.click('#scanChange')
    page.wait_for_selector('.scan-pick')
    page.evaluate("() => window.cameraShows('/test-codes/dm.png')")
    assert station.none_for(800) == 1
    page.keyboard.press('Escape')
    page.wait_for_selector('.scan-pick', state='detached')
    station.wait_for(2)
    assert station.codes() == [LINK, 'A-17']


def test_camera_stopped_before_it_answers_stays_off(camera_station):
    # The browser asks for permission; the station is left meanwhile
    page, station, _ = camera_station(init="""
        const ask = navigator.mediaDevices.getUserMedia;
        navigator.mediaDevices.getUserMedia = c => new Promise(done => { window.allowCamera = () => done(ask(c)); });
    """)
    page.click('#scanCamera')
    page.wait_for_function('() => !!window.allowCamera')
    page.click('#scanCamera')
    page.wait_for_selector('#scanCamera:has-text("Use the camera")')
    page.evaluate('() => window.allowCamera()')
    page.wait_for_function('() => window.cameraTracks.length === 1')
    page.wait_for_function("() => window.cameraTracks[0].readyState === 'ended'")
    assert station.none_for(500) == 0
    assert not page.locator('#scanCam video').is_visible()


@pytest.mark.parametrize('error, message', [
    ('NotAllowedError', 'The browser did not allow the camera. Allow it in the address bar and try again.'),
    ('NotFoundError', 'No camera found.'),
])
def test_camera_refused_or_missing_says_so(camera_station, error, message):
    page, station, _ = camera_station(init=f"window.cameraFails = '{error}';")
    page.click('#scanCamera')
    page.wait_for_selector(f'#scanCamNote:has-text("{message}")')
    page.wait_for_selector('#scanCamera:has-text("Use the camera")')
    assert not page.locator('#scanCam video').is_visible()
    # The keyboard scanner still works
    station.scan('A-17')
    station.wait_for(1)
    assert station.codes() == ['A-17']


def test_insecure_page_explains_the_camera(camera_station, server):
    page, station, loaded = camera_station(init="Object.defineProperty(window, 'isSecureContext', { value: false });")
    assert page.evaluate('() => isSecureContext') is False
    page.get_by_role('button', name='Use the camera').click()
    note = page.locator('#scanCamNote')
    note.wait_for()
    assert note.inner_text() == 'The camera works only over a secure address (https). The guide shows how to set one up.'
    assert note.locator('a').get_attribute('href') == 'https://github.com/DL6ER/labelburg/blob/master/docs/reverse-proxy.md'
    assert page.evaluate('() => window.cameraAsked') == 0
    assert not page.locator('#scanCam video').is_visible()
    assert [url for url in loaded if 'vendor/zxing-wasm/' in url] == []


def test_no_wasm_without_the_camera(camera_station):
    page, station, loaded = camera_station()
    station.scan('A-17')
    station.wait_for(1)
    page.wait_for_selector('#scanResult:has-text("Printed: Box")')
    assert page.locator('#scanCamera').is_visible()
    assert [url for url in loaded if 'vendor/zxing-wasm/' in url] == []
    assert page.evaluate('() => window.cameraAsked') == 0


def test_camera_on_a_phone_keeps_the_keyboard_of_the_screen_closed(camera_station):
    page, station, _ = camera_station({'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    page.tap('#scanField')
    assert page.get_attribute('#scanField', 'inputmode') == 'text'
    page.tap('#scanCamera')
    page.wait_for_selector('#scanCamera:has-text("Stop the camera")')
    station.wait_for(1)
    # The field has the focus again for a keyboard scanner, without the keyboard of the screen
    page.wait_for_function("() => document.activeElement.id === 'scanField'")
    assert page.get_attribute('#scanField', 'inputmode') == 'none'
    assert page.evaluate('() => document.documentElement.scrollWidth') <= 390
    assert page.locator('#scanCam video').is_visible()


def test_camera_waits_for_a_saved_label(camera_station, server):
    page, station, _ = camera_station()
    page.click('#scanCamera')
    station.wait_for(1)
    page.evaluate("() => { location.hash = 'scan/Gone.json'; }")
    page.wait_for_selector('.scan-label:has-text("is no longer saved")')
    page.wait_for_function("() => window.cameraTracks.every(track => track.readyState === 'ended')")
    assert page.is_disabled('#scanCamera')
    assert page.inner_text('#scanCamera') == 'Use the camera'
