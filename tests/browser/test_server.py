"""The label designer reads, previews and prints labels as the server does:
dates moved from today, flags written in any way, saved labels, lengths,
blanks and what it sends where a label is placed on other tape."""
import base64
import datetime
import io
import json
import os
from urllib.parse import parse_qs

import pytest
from PIL import Image

from app.labeldesigner.label import shift_date
from tests.browser.helpers import with_tape
from tests.browser.test_editor import editor, sent, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_stage import status_is

# The modules of the page, as loaded by it
CORE = "() => import('/static/designer/core.js')"


@pytest.mark.parametrize('day, offset, moved', [
    ('2024-01-31', '+1m', '2024-02-29'), ('2023-01-31', '+1m', '2023-02-28'), ('2024-03-31', '-1m', '2024-02-29'),
    ('2024-02-29', '+1y', '2025-02-28'), ('2024-12-30', '+5d', '2025-01-04'), ('2024-01-31', '-2w', '2024-01-17'),
    ('2024-11-15', '+14m', '2026-01-15'), ('2024-01-15', '-13m', '2022-12-15'),
])
def test_a_date_blank_moves_from_today_as_the_server_moves_dates(editor, day, offset, moved):  # noqa: F811
    # The field starts at the browser's date, the server moves the others
    assert shift_date(datetime.date.fromisoformat(day), offset).isoformat() == moved
    assert editor.evaluate(f'async ([day, offset]) => (await ({CORE})()).shiftDate(day, offset)', [day, offset]) == moved


@pytest.mark.parametrize('zone', ['Pacific/Kiritimati', 'Pacific/Pago_Pago'])
def test_date_blanks_start_at_the_local_date(browser, server, zone):
    # 25 hours apart, one of them is always on another date than UTC
    context = browser.new_context(locale='en-US', timezone_id=zone)
    page = context.new_page()
    page.goto(server + '/labeldesigner/')
    page.wait_for_selector('#field .ln')
    assert page.evaluate(f"async () => (await ({CORE})()).today() === new Date().toLocaleDateString('sv-SE')")
    context.close()


@pytest.mark.parametrize('value, on', [('2', True), (' 1 ', True), ('TRUE', True), ('0', False), ('false', False), ('-1', False)])
def test_a_stored_cable_flag_is_read_as_the_server_reads_it(editor, value, on):  # noqa: F811
    assert editor.evaluate(f'async value => (await ({CORE})()).flagOn({{ cable_flag: value }})', value) == on


def test_flags_of_a_saved_label_are_read_as_the_server_reads_them(editor, app):  # noqa: F811
    # true and false as text are what the server reads them as. One the
    # server refuses stays as it is, the preview tells why.
    repo = app.config['LABEL_REPOSITORY_DIR']
    labels = {
        'Flags1.json': {'image_fit': 'true', 'image_crop': 'true', 'shrink_text': 'true'},
        'Flags2.json': {'image_fit': 'false', 'image_crop': 'false', 'shrink_text': 'FALSE'},
        'Flags3.json': {'image_fit': ' 2 ', 'image_crop': 1, 'shrink_text': '-1'},
        'Flags4.json': {'image_fit': '', 'image_crop': None, 'shrink_text': ''},
        'Flags5.json': {'image_fit': True, 'image_crop': False, 'shrink_text': True},
        'Flags6.json': {'text_fit': 'Shrink'},
        'Flags7.json': {'shrink_text': 'yes'},
    }
    for name, flags in labels.items():
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            json.dump(dict({'title': name, 'label_size': '62', 'text': [{'text': 'Shelf', 'size': '40'}]}, **flags), f)
    try:
        read = editor.evaluate(f"""async names => {{
            const core = await ({CORE})();
            await core.loadSaved();
            return Promise.all(names.map(async id => {{
                const d = await core.itemDesign({{ kind: 'saved', id }});
                const told = await core.preview(d, {{}}).then(() => '', e => e.message);
                return [d.image_fit, d.image_crop, d.text_fit, d.shrink_text === undefined ? null : d.shrink_text, told];
            }}));
        }}""", list(labels))
        assert read == [['1', '1', 'shrink', None, ''], ['0', '0', 'none', None, ''], ['1', '1', 'shrink', None, ''], ['1', '0', 'none', None, ''],
                        ['1', '0', 'shrink', None, ''], ['1', '0', 'Shrink', None, 'text_fit must be none, shrink or fill'],
                        ['1', '0', '', 'yes', 'shrink_text must be 0 or 1']]
    finally:
        for name in labels:
            os.remove(os.path.join(repo, name))


def test_saved_label_looks_the_same_through_the_api(editor, server):  # noqa: F811
    # The server fills in the blanks for both: a saved label previewed by
    # name looks as in the label designer, its date in the format set there
    result = editor.evaluate(f"""async () => {{
        const core = await ({CORE})();
        await core.saveLabelSettings({{ date_format: '%d.%m.%Y' }});
        const design = Object.assign(core.baseDesign('62'), {{ print_type: 'qrcode_text', code_text: 'box/{{{{blank:Box}}}}',
            text: [core.L('{{{{blank:What}}}} {{{{blank:Code}}}}', 60), core.L('Frozen on {{{{blank:Frozen on}}}}', 40)],
            fields: {{ Box: {{ type: 'number' }}, Code: {{ type: 'text' }} }} }});
        const values = {{ What: 'Chili', Box: '4', Code: '2026-11-02', 'Frozen on': '2026-11-01' }};
        const sent = JSON.parse(core.formFor(design, 1, false, values).get('fields'));
        const name = await core.saveLabel('Same through the API', design);
        const own = await (await fetch((await core.preview(design, values)).url)).arrayBuffer();
        const api = await (await fetch(core.API + '/repository/preview?name=' + encodeURIComponent(name) + '&blanks=' + encodeURIComponent(JSON.stringify(values)))).arrayBuffer();
        const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
        return {{ sent, name, own: b64(own), api: b64(api) }};
    }}""")
    try:
        assert result['sent']['Frozen on'] == {'type': 'date'}
        assert result['sent']['Code'] == {'type': 'text'}
        own, api = (Image.open(io.BytesIO(base64.b64decode(result[k]))) for k in ('own', 'api'))
        assert own.size == api.size and own.convert('RGB').tobytes() == api.convert('RGB').tobytes()
    finally:
        editor.request.post(server + '/labeldesigner/api/repository/delete', form={'name': result['name']})
        editor.request.post(server + '/labeldesigner/api/settings/labels', form={'date_format': ''})


def test_the_length_is_limited_as_the_server_says(editor):  # noqa: F811
    type_label(editor, 'Shelf')
    editor.click('#advBtn')
    editor.click('[data-len="fixed"]')
    assert editor.get_attribute('[data-length]', 'min') == '18.7'
    assert editor.get_attribute('[data-length]', 'max') == '3000'


def test_blank_left_empty_is_printed_empty(editor):  # noqa: F811
    # Its default, or a date of today, is not put in instead
    blanks = editor.evaluate(f"""async () => {{
        const core = await ({CORE})();
        const design = Object.assign(core.baseDesign('62'), {{ text: [core.L('{{{{blank:Note}}}} {{{{blank:Due}}}}', 40)],
            fields: {{ Note: {{ default: 'n/a' }}, Due: {{ type: 'date' }} }} }});
        return JSON.parse(core.formFor(design, 1, true, {{ Note: '', Due: '' }}).get('blanks'));
    }}""")
    assert blanks == {'Note': '', 'Due': ''}


def test_code_of_empty_blanks_takes_the_text(editor):  # noqa: F811
    # Like a code left empty, the server puts the text of the label into it.
    # It gets the blanks as they are, with their values.
    with editor.expect_request('**/api/print') as request:
        editor.evaluate(f"""async () => {{
            const core = await ({CORE})();
            core.printJob({{ title: 'Link', values: {{}}, copies: 1,
                design: Object.assign(core.baseDesign('62'), {{ print_type: 'qrcode_text', code_text: '{{{{blank:URL}}}}', text: [core.L('Shelf', 40)] }}) }});
        }}""")
    form = parse_qs(request.value.post_data, keep_blank_values=True)
    assert form['code_text'] == ['{{blank:URL}}'] and form['blanks'] == ['{}']
    editor.wait_for_function("() => labelEditor.S.history.length === 1 && labelEditor.S.history[0].state === 'printed'")


def test_a_cable_flag_on_loaded_die_cut_labels_prints_without_it(editor):  # noqa: F811
    status_is(editor, with_tape({'errors': [], 'model': 'QL-800', 'path': 'simulation', 'media_width': 29, 'media_length': 90, 'settings': {}}))
    editor.evaluate(f"""async () => {{
        const core = await ({CORE})();
        const editor = await import('/static/designer/editor.js');
        editor.openEditor({{ title: 'Router', design: Object.assign(core.baseDesign('62'), {{ text: [core.L('Router', 40)], cable_flag: '1' }}) }});
    }}""")
    wait_for_preview(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.label_size') == '29x90'
    with editor.expect_request('**/api/print') as request:
        editor.click('#print')
    assert sent(request.value)['cable_flag'] == '0'
    editor.wait_for_function("() => labelEditor.S.history.every(h => h.state !== 'printing')")


def test_a_wrong_check_digit_is_told_and_not_printed(editor):  # noqa: F811
    # It is not replaced by the right one: a typo would print another valid code
    editor.evaluate(f"""async () => {{
        const core = await ({CORE})();
        const editor = await import('/static/designer/editor.js');
        editor.openEditor({{ title: 'EAN', design: Object.assign(core.baseDesign('62'), {{ print_type: 'qrcode_text', barcode_type: 'EAN13',
            code_text: '4006381333932', text: [core.L('Jam', 40)] }}) }});
    }}""")
    wait_for_preview(editor)
    assert 'The check digit of EAN-13 must be 1' in editor.inner_text('#fits')
    assert editor.is_disabled('#print')


def test_a_font_that_is_not_installed_is_said_and_kept(editor, app):  # noqa: F811
    repo = app.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    for line in label['text']:
        line['font'] = 'Not Installed,Regular'
    path = os.path.join(repo, 'Other-Fonts.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(label, f)
    try:
        editor.keyboard.press('Control+k')
        editor.wait_for_selector('.palette #palQ:focus')
        editor.keyboard.type('Other Fonts')
        editor.click('#palRes .res:has-text("Other Fonts")')
        editor.wait_for_function('() => labelEditor.S.edit.title === "Other Fonts"')
        wait_for_preview(editor)
        assert 'The font Not Installed (Regular) is not installed' in editor.inner_text('#fits')
        assert editor.evaluate('() => labelEditor.S.edit.design.text[0].font') == 'Not Installed,Regular'
        assert editor.is_enabled('#print')
    finally:
        os.remove(path)


def test_instances_below_one_host_keep_their_labels_apart(editor, server):  # noqa: F811
    # Served below different paths behind a reverse proxy, they keep their
    # label for the next visit, the print history and the printer apart
    type_label(editor, 'Of the root')
    editor.wait_for_function("() => (JSON.parse(localStorage.getItem('designer_next_edit') || 'null') || { design: { text: [] } }).design.text.some(l => l.text === 'Of the root')")
    editor.evaluate("() => localStorage.setItem('designer_printer', JSON.stringify('file:///dev/usb/lp7'))")
    editor.route('**/labeldesigner/', lambda route: route.fulfill(
        response=route.fetch(), body=route.fetch().text().replace('"storage_prefix": ""', '"storage_prefix": "/labels/"')))
    editor.reload()
    editor.wait_for_selector('#field .ln')
    wait_for_preview(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.text.map(l => l.text)') == ['']
    assert not editor.is_visible('#toast:has-text("/dev/usb/lp7 is not connected")')
    type_label(editor, 'Below /labels')
    editor.wait_for_function("() => (JSON.parse(localStorage.getItem('/labels/designer_next_edit') || 'null') || { design: { text: [] } }).design.text.some(l => l.text === 'Below /labels')")
    assert editor.evaluate("() => JSON.parse(localStorage.getItem('designer_next_edit')).design.text[0].text") == 'Of the root'
