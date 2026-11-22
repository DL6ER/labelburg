"""Codes and images in the label designer at /labeldesigner/: adding
them, their toolbars, and several files or PDF pages."""
import io
import json

import pytest
from PIL import Image

from tests.browser.helpers import png
from tests.browser.test_editor import editor, type_label, wait_for_preview  # noqa: F401

PREVIEW = '**/api/preview'


def pdf(*sizes) -> bytes:
    pages = [Image.new('L', size, 'black') for size in sizes]
    out = io.BytesIO()
    pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return out.getvalue()


# What the page sends to the server, as it hands it to fetch(): over CDP,
# Chrome leaves out the body of some requests with files
RECORD = """() => {
    window.sentForms = [];
    const original = window.fetch;
    window.fetch = (url, options = {}) => {
        const body = options.body;
        if (body && body.entries) {
            const fields = {}, files = [];
            for (const [key, value] of body.entries()) {
                if (value instanceof Blob) files.push(value.name);
                else fields[key] = value;
            }
            sentForms.push({ path: String(url).replace(/^.*\\/api/, ''), fields, files });
        }
        return original(url, options);
    };
}"""


@pytest.fixture
def media(editor):  # noqa: F811
    editor.evaluate(RECORD)
    return editor


def sent_to(page, path, action) -> tuple[dict, list]:
    """The fields and the names of the files the first request to path
    after an action has."""
    count = page.evaluate('() => sentForms.length')
    with page.expect_request(lambda r: r.url.endswith('/api' + path) and r.method == 'POST'):
        action()
    sent = page.evaluate('([count, path]) => sentForms.slice(count).find(f => f.path === path)', [count, path])
    return sent['fields'], sent['files']


def changed(page, action) -> tuple[dict, list]:
    """The preview request an action leads to."""
    fields, files = sent_to(page, '/preview', action)
    wait_for_preview(page)
    return fields, files


def part(page, name):
    return page.evaluate('name => labelEditor.editorUI.last.parts.find(p => p.part === name).box', name)


def design(page, key):
    return page.evaluate('key => labelEditor.S.edit.design[key]', key)


def test_add_a_code_and_change_its_kind_and_order(media):
    type_label(media, 'Shelf 3', 'spices')
    request, _ = changed(media, lambda: media.click('#addCode'))
    assert (request['print_type'], request['barcode_type']) == ('qrcode_text', 'QR')
    assert media.inner_text('#addCode') == 'Code added'
    # Its content is typed right away, a blank in it as in the text
    assert media.evaluate("() => document.activeElement.hasAttribute('data-ctext')")
    media.keyboard.type('https://example.com/[Shelf]')
    media.wait_for_function("() => labelEditor.S.edit.design.code_text === 'https://example.com/{{blank:Shelf}}'")
    wait_for_preview(media)
    request, _ = changed(media, lambda: media.select_option('#ltb [data-kind]', 'DATAMATRIX'))
    assert (request['barcode_type'], request['code_text']) == ('DATAMATRIX', 'https://example.com/{{blank:Shelf}}')
    assert 'Shelf' in json.loads(request['fields'])
    assert media.inner_text('#ltb .fsel span') == 'DataMatrix'
    assert media.eval_on_selector_all('#ltb [data-opart]', 'els => els.map(e => e.dataset.opart)') == ['code', 'text']
    request, _ = changed(media, lambda: media.click('#ltb [data-odir="column"]'))
    assert (request['parts_order'], request['parts_direction']) == ('code,text', 'column')
    assert part(media, 'code')[3] <= part(media, 'line')[1]
    # A click on a line of the label picks it, one on the code the code
    media.click('#picks .pk[data-part="line"][data-i="1"]')
    media.wait_for_selector('#ltb [data-font]')
    media.click('#picks .pk[data-part="code"]')
    media.wait_for_selector('#ltb [data-kind]')
    # Error correction where the kind of code has it
    media.click('#ltb [data-more]')
    assert 'set their own error correction' in media.inner_text('.media-menu')
    changed(media, lambda: media.select_option('#ltb [data-kind]', 'QR'))
    request, _ = changed(media, lambda: media.click('.media-menu [data-level="H"]'))
    assert request['qrcode_correction'] == 'H'
    # Removed, the label is one of text again, undone it has the code
    request, _ = changed(media, lambda: media.click('.media-menu [data-cremove]'))
    assert request['print_type'] == 'text'
    assert media.inner_text('#addCode') == 'Code'
    media.click('#undo')
    media.wait_for_function("() => labelEditor.S.edit.design.print_type === 'qrcode_text'")


def test_the_kinds_of_codes_come_with_the_page(editor, server):  # noqa: F811
    # The same the API lists, without asking it
    codes = editor.request.get(server + '/labeldesigner/api/barcodes').json()['codes']
    assert editor.evaluate('() => labelEditor.S.codes') == codes


def test_a_code_fitted_to_the_label_grows_from_its_size(media):
    type_label(media, 'Box 7')
    changed(media, lambda: media.click('#addCode'))
    fitted = part(media, 'code')
    media.click('#ltb [data-csize="1"]')
    media.wait_for_function("() => labelEditor.S.edit.design.image_fit === '0'")
    wait_for_preview(media)
    box = part(media, 'code')
    # One dot larger than fitted, not the size of a dot it had before
    assert fitted[2] - fitted[0] < box[2] - box[0] < (fitted[2] - fitted[0]) * 1.2
    assert media.input_value('#ltb [data-csizein]') == design(media, 'qrcode_size')


def test_a_code_too_small_to_scan_is_said_until_it_grows(media):
    media.set_viewport_size({'width': 1600, 'height': 924})
    type_label(media, 'Box 7')
    changed(media, lambda: media.click('#addCode'))
    assert 'Everything fits' in media.inner_text('#fits')
    # Dots of 2 dots of the printer are too small
    request, _ = changed(media, lambda: (media.fill('#ltb [data-csizein]', '2'), media.press('#ltb [data-csizein]', 'Enter')))
    assert (request['image_fit'], request['qrcode_size']) == ('0', '2')
    # Of a size set, less text would not make it larger
    assert media.inner_text('#fits').strip() == 'This code may be too small to scan. Make it larger.'
    request, _ = changed(media, lambda: media.click('#ltb [data-csize="1"]'))
    assert request['qrcode_size'] == '3'
    assert 'Everything fits' in media.inner_text('#fits')
    assert 'too small' not in media.inner_text('#fits')


def test_add_an_image_and_change_its_colors_and_size(media):
    type_label(media, 'Shelf 3')
    with media.expect_file_chooser() as chooser:
        media.click('#addImage')
    request, files = changed(media, lambda: chooser.value.set_files(files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png(400, 200)}]))
    assert (request['print_type'], files) == ('image', ['black.png'])
    assert media.inner_text('#addImage') == 'Image added'
    media.wait_for_selector('#ltb [data-imode]')
    request, _ = changed(media, lambda: media.click('#ltb [data-imode="black_and_white"]'))
    assert request['image_mode'] == 'black_and_white'
    # Red and color need black and red tape
    assert media.is_disabled('#ltb [data-imode="red_and_black"]') and media.is_disabled('#ltb [data-imode="colored"]')
    # Scaled, it starts as large as it was fitted
    fitted = part(media, 'image')
    media.click('#ltb [data-fit="0"]')
    media.wait_for_function("() => labelEditor.S.edit.design.image_fit === '0'")
    wait_for_preview(media)
    assert abs(part(media, 'image')[2] - fitted[2]) <= (fitted[2] - fitted[0]) * .02
    scale = int(design(media, 'image_scaling_factor'))
    request, _ = changed(media, lambda: media.click('#ltb [data-scale="1"]'))
    assert (request['image_fit'], request['image_scaling_factor']) == ('0', str(scale + 10))
    request, _ = changed(media, lambda: media.click('#ltb [data-turn]'))
    assert request['image_rotation'] == '90'
    media.click('#ltb [data-more]')
    request, _ = changed(media, lambda: media.click('.media-menu [data-crop]'))
    assert request['image_crop'] == '1'


def test_a_pdf_of_two_pages_steps_through_them(media):
    media.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    request, files = changed(media, lambda: media.set_input_files('#imageFile', files=[
        {'name': 'two.pdf', 'mimeType': 'application/pdf', 'buffer': pdf((200, 100), (100, 200))}]))
    assert (request['print_type'], files) == ('image', ['two.pdf'])
    assert media.inner_text('#addImage') == 'PDF added'
    # Its first page only, as before pages are chosen
    assert media.is_hidden('#ltb .unit-step')
    media.click('#ltb [data-more]')
    media.wait_for_selector('.media-menu:has-text("This PDF has 2 pages")')
    request, _ = changed(media, lambda: media.click('.media-menu [data-allpages]'))
    assert request['pdf_pages'] == 'all'
    media.wait_for_selector('#ltb [data-unitof]:text-is("1 of 2")')
    first = media.eval_on_selector('#labelImg', 'img => [img.naturalWidth, img.naturalHeight]')
    request, files = changed(media, lambda: media.click('#ltb [data-unit="1"]'))
    assert (request['pdf_pages'], files) == ('2', ['two.pdf'])
    assert media.inner_text('#ltb [data-unitof]') == '2 of 2'
    assert media.eval_on_selector('#labelImg', 'img => [img.naturalWidth, img.naturalHeight]') != first
    fields, files = sent_to(media, '/print', lambda: media.click('#print'))
    assert (fields['pdf_pages'], files) == ('all', ['two.pdf'])


@pytest.mark.parametrize('way', ['paste', 'drop'])
def test_several_images_dropped_or_pasted(media, way):
    type_label(media, 'Box 7')
    media.evaluate("""async way => {
        const data = new DataTransfer();
        for (const [name, width] of [['one.png', 200], ['two.png', 100], ['three.png', 300]]) {
            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = 100;
            canvas.getContext('2d').fillRect(10, 10, width - 20, 80);
            const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
            data.items.add(new File([blob], name, { type: 'image/png' }));
        }
        if (way === 'paste') document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        else document.querySelector('#stage').dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    }""", way)
    media.wait_for_selector('#ltb [data-unitof]:text-is("1 of 3")')
    wait_for_preview(media)
    assert media.inner_text('#addImage') == '3 images'
    _, files = changed(media, lambda: media.click('#ltb [data-unit="1"]'))
    assert files == ['two.png']
    # The file shown stays shown when another one is removed
    media.click('#ltb [data-more]')
    media.click('.media-menu [data-unfile="0"]')
    media.wait_for_selector('#ltb [data-unitof]:text-is("1 of 2")')
    assert media.evaluate('() => labelEditor.S.edit.design.image.name') == 'two.png'
    assert media.inner_text('#addImage') == '2 images'


def test_a_light_image_offers_stronger_contrast(media):
    # The server tells so for light images on color tape
    def faint(route):
        response = route.fetch()
        route.fulfill(response=response, headers={**response.headers, 'x-image-faint': '1'})
    media.route(PREVIEW, faint)
    media.set_input_files('#imageFile', files=[{'name': 'light.png', 'mimeType': 'image/png', 'buffer': png()}])
    media.wait_for_selector('#fits:has-text("This image looks light")')
    media.unroute(PREVIEW)
    request, _ = changed(media, lambda: media.click('#fits [data-fix="contrast"]'))
    assert request['image_contrast'] == '1'
    assert 'Everything fits' in media.inner_text('#fits')
