"""The order of image, code and text in the label designer at
/labeldesigner/: chips to drag or move with the keyboard, buttons to
swap them, and the direction."""
import json

from tests.browser.helpers import png
from tests.browser.test_editor import editor, sent, type_label, wait_for_preview  # noqa: F401
from tests.browser.test_gallery import search_and_open
from tests.browser.test_media import RECORD, changed, part

URL = 'https://example.com'


def chips(page) -> list:
    return page.eval_on_selector_all('#ltb [data-opart]', 'els => els.map(e => e.dataset.opart)')


def direction(page) -> str:
    return page.get_attribute('#ltb [data-odir].on', 'data-odir')


def with_all_parts(page):
    """Text, an image and a code; the code picked."""
    page.evaluate(RECORD)
    type_label(page, 'DEF')
    with page.expect_file_chooser() as chooser:
        page.click('#addImage')
    changed(page, lambda: chooser.value.set_files(files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png(200, 200)}]))
    changed(page, lambda: page.click('#addCode'))
    page.keyboard.type(URL)
    wait_for_preview(page)
    page.wait_for_function('() => document.querySelectorAll("#ltb [data-opart]").length === 3')


def along(page, *names) -> list:
    """The parts of the preview from left to right, or from top to bottom."""
    boxes = {name: part(page, name) for name in names}
    return sorted(boxes, key=lambda name: (boxes[name][0], boxes[name][1]))


def test_drag_a_part_to_reorder(editor):  # noqa: F811
    with_all_parts(editor)
    # As the server lays it out: the image left of the text, the code right
    assert chips(editor) == ['image', 'text', 'code'] and direction(editor) == 'row'
    source = editor.locator('#ltb [data-opart="text"]').bounding_box()
    target = editor.locator('#ltb [data-opart="code"]').bounding_box()

    def drag():
        editor.mouse.move(source['x'] + source['width'] / 2, source['y'] + source['height'] / 2)
        editor.mouse.down()
        editor.mouse.move(target['x'] + target['width'] * .9, target['y'] + target['height'] / 2, steps=8)
        editor.mouse.up()
    request, _ = changed(editor, drag)
    assert (request['parts_order'], request['parts_direction'], request['image_position']) == ('image,code,text', 'row', '')
    assert chips(editor) == ['image', 'code', 'text']
    # The preview follows: the text right of the code
    assert along(editor, 'image', 'code', 'line') == ['image', 'code', 'line']
    # A step to undo
    editor.click('#undo')
    editor.wait_for_function('() => labelEditor.S.edit.design.parts_order === ""')
    wait_for_preview(editor)
    # The widest chip dragged far past the end stays within the chips, over
    # neither Row nor Column, and lands last
    chips_box = editor.locator('#ltb .ochips').bounding_box()
    source = editor.locator('#ltb [data-opart="image"]').bounding_box()
    editor.mouse.move(source['x'] + source['width'] / 2, source['y'] + source['height'] / 2)
    editor.mouse.down()
    editor.mouse.move(source['x'] + 600, source['y'] + source['height'] / 2, steps=10)
    lifted = editor.locator('#ltb .ochip.lifted').bounding_box()
    assert chips_box['x'] <= lifted['x'] and lifted['x'] + lifted['width'] <= chips_box['x'] + chips_box['width'] + 1
    editor.mouse.up()
    editor.wait_for_function('() => labelEditor.S.edit.design.parts_order === "text,code,image"')
    # And back to the start the same way
    wait_for_preview(editor)
    source = editor.locator('#ltb [data-opart="image"]').bounding_box()
    editor.mouse.move(source['x'] + source['width'] / 2, source['y'] + source['height'] / 2)
    editor.mouse.down()
    editor.mouse.move(source['x'] - 600, source['y'] + source['height'] / 2, steps=10)
    lifted = editor.locator('#ltb .ochip.lifted').bounding_box()
    assert chips_box['x'] - 1 <= lifted['x']
    editor.mouse.up()
    editor.wait_for_function('() => labelEditor.S.edit.design.parts_order === "image,text,code"')


def test_move_a_part_with_the_keyboard_and_swap_with_a_button(editor):  # noqa: F811
    with_all_parts(editor)
    editor.focus('#ltb [data-opart="image"]')
    request, _ = changed(editor, lambda: editor.keyboard.press('ArrowRight'))
    assert request['parts_order'] == 'text,image,code'
    # The part keeps the focus, it moves on
    assert editor.evaluate('() => document.activeElement.dataset.opart') == 'image'
    assert editor.get_attribute('#ltb [data-opart="image"]', 'aria-label') == 'Image, 2 of 3'
    request, _ = changed(editor, lambda: editor.keyboard.press('ArrowRight'))
    assert request['parts_order'] == 'text,code,image'
    # Not past the end
    editor.keyboard.press('ArrowRight')
    editor.wait_for_timeout(200)
    assert chips(editor) == ['text', 'code', 'image']
    # Back to an order previewed before, without asking the server again
    editor.keyboard.press('ArrowLeft')
    editor.wait_for_function('() => labelEditor.S.edit.design.parts_order === "text,image,code"')
    wait_for_preview(editor)
    # On a phone a button between two parts swaps them
    request, _ = changed(editor, lambda: editor.click('#ltb [data-oswap="0"]'))
    assert request['parts_order'] == 'image,text,code'
    assert along(editor, 'image', 'code', 'line') == ['image', 'line', 'code']


def test_switch_the_direction(editor):  # noqa: F811
    with_all_parts(editor)
    request, _ = changed(editor, lambda: editor.click('#ltb [data-odir="column"]'))
    assert (request['parts_order'], request['parts_direction']) == ('image,text,code', 'column')
    assert direction(editor) == 'column'
    image, line, code = part(editor, 'image'), part(editor, 'line'), part(editor, 'code')
    assert image[3] <= line[1] and line[3] <= code[1]
    # The image's toolbar has the same order
    editor.click('#picks .pk[data-part="image"]')
    editor.wait_for_selector('#ltb [data-imode]')
    assert chips(editor) == ['image', 'text', 'code'] and direction(editor) == 'column'


def test_the_order_is_named_on_the_phone_too(editor):  # noqa: F811
    with_all_parts(editor)
    assert editor.inner_text('#ltb .order .olabel') == 'Order'
    editor.set_viewport_size({'width': 390, 'height': 844})
    editor.wait_for_timeout(300)
    assert editor.is_visible('#ltb .order .olabel')
    # The word and the chips in one row within the screen
    word, chips_box = editor.locator('#ltb .order .olabel').bounding_box(), editor.locator('#ltb .ochips').bounding_box()
    assert abs((word['y'] + word['height'] / 2) - (chips_box['y'] + chips_box['height'] / 2)) <= 2
    assert word['x'] >= 0 and chips_box['x'] + chips_box['width'] <= 390


def test_the_order_needs_two_parts(editor):  # noqa: F811
    editor.evaluate(RECORD)
    # A code alone has no order, with text it has one
    changed(editor, lambda: editor.click('#addCode'))
    editor.wait_for_selector('#ltb [data-kind]')
    assert chips(editor) == []
    editor.click('#field')
    editor.keyboard.type('Pantry')
    wait_for_preview(editor)
    editor.click('#picks .pk[data-part="code"]')
    editor.wait_for_selector('#ltb [data-kind]')
    assert sorted(chips(editor)) == ['code', 'text']


def test_a_saved_label_keeps_its_order(editor):  # noqa: F811
    with_all_parts(editor)
    changed(editor, lambda: editor.click('#ltb [data-oswap="1"]'))
    editor.click('#save')
    editor.fill('#saveName', 'Ordered parts')
    editor.click('#saveGo')
    editor.wait_for_function('n => labelEditor.S.saved.some(s => s.title === n)', arg='Ordered parts')
    editor.wait_for_selector('.dialog', state='detached')
    editor.click('#newLabel')
    editor.wait_for_function('() => labelEditor.S.edit.title !== "Ordered parts"')
    search_and_open(editor, 'Ordered parts')
    editor.wait_for_function('() => labelEditor.S.edit.title === "Ordered parts"')
    wait_for_preview(editor)
    assert editor.evaluate('() => labelEditor.S.edit.design.parts_order') == 'image,code,text'
    editor.click('#picks .pk[data-part="code"]')
    editor.wait_for_selector('#ltb [data-opart]')
    assert chips(editor) == ['image', 'code', 'text']
    assert along(editor, 'image', 'code', 'line') == ['image', 'code', 'line']


def test_a_label_with_a_side_shows_its_order(editor, server):  # noqa: F811
    for side, order, way in (('right', ['text', 'code'], 'row'), ('below', ['text', 'code'], 'column'), ('above', ['code', 'text'], 'column')):
        title = f'Code {side}'
        editor.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                            data=json.dumps({'name': f'Code_{side}.json', 'title': title, 'label_size': '62', 'orientation': 'standard',
                                             'print_type': 'qrcode_text', 'code_text': URL, 'image_position': side,
                                             'text': [{'text': 'Pantry', 'size': '40', 'font': 'DejaVu Sans,Book', 'align': 'left'}]}))
        editor.click('#newLabel')
        search_and_open(editor, title)
        editor.wait_for_function('t => labelEditor.S.edit.title === t', arg=title)
        wait_for_preview(editor)
        editor.click('#picks .pk[data-part="code"]')
        editor.wait_for_selector('#ltb [data-opart]')
        editor.wait_for_function('o => [...document.querySelectorAll("#ltb [data-opart]")].map(e => e.dataset.opart).join() === o', arg=','.join(order))
        assert direction(editor) == way
        # Shown, not changed
        assert editor.evaluate('() => labelEditor.S.edit.design.image_position') == side


def test_a_label_opened_shows_its_own_order_before_its_preview(editor, server):  # noqa: F811
    # The label before had another order and direction
    with_all_parts(editor)
    changed(editor, lambda: editor.click('#ltb [data-oswap="1"]'))
    editor.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                        data=json.dumps({'name': 'Code_below.json', 'title': 'Code below', 'label_size': '62', 'orientation': 'standard',
                                         'print_type': 'qrcode_text', 'code_text': URL, 'image_position': 'below',
                                         'text': [{'text': 'Below', 'size': '40', 'font': 'DejaVu Sans,Book', 'align': 'left'}]}))
    # Its previews wait until they are let through
    held, released = [], []
    editor.route('**/api/preview', lambda route: route.continue_() if released else held.append(route))
    search_and_open(editor, 'Code below')
    editor.click('#newDiscard')
    editor.wait_for_function('() => labelEditor.S.edit.title === "Code below"')
    editor.click('#addCode')
    editor.wait_for_selector('#ltb [data-opart]')
    assert chips(editor) == ['text', 'code'] and direction(editor) == 'column'
    released.append(True)
    for route in held:
        route.continue_()
    wait_for_preview(editor)
    editor.unroute('**/api/preview')
    assert chips(editor) == ['text', 'code'] and direction(editor) == 'column'

