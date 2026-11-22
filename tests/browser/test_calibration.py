"""Calibrating a Niimbot B1 at the printer chip and in the Settings of the
label designer at /labeldesigner/: the hint after a roll change and the
calibration dialog, which changes nothing until it is saved. The server is
stubbed."""

import json
from urllib.parse import parse_qs

import pytest

from tests.browser.helpers import with_tape
from tests.browser.test_editor import editor  # noqa: F401

HINT = 'A new roll is in the printer. Calibrate the printer so the labels sit straight on it.'
GLOWS = '() => document.querySelector("[data-cal-open]").classList.contains("attention")'


@pytest.fixture
def designer(editor):  # noqa: F811
    return editor


@pytest.fixture
def calibration(designer):
    """The requests to the calibration routes, answered as the server would."""
    sent, answers = [], {'GET': {'across': 0.0, 'along': 0.0, 'roll': 'PZ1TEST00001', 'kind': 'narrow'},
                         'POST': (200, {'success': True, 'shift': 1.0, 'calibration': {'across': -1.0, 'along': 0.0, 'roll': 'PZ1TEST00002'}})}

    def handle(route):
        request = route.request
        if '/example.png' in request.url:
            return route.fulfill(status=404, body='')
        if request.method == 'GET':
            return route.fulfill(content_type='application/json', body=json.dumps(answers['GET']))
        sent.append((request.url.split('/api')[-1], {k: v[0] for k, v in parse_qs(request.post_data or '').items()}))
        if request.url.endswith('/print'):
            return route.fulfill(content_type='application/json', body='{"success": true}')
        status, body = answers['POST']
        route.fulfill(status=status, content_type='application/json', body=json.dumps(body))
    designer.route('**/api/calibration**', handle)
    return sent, answers


def _until(designer, condition):
    # Route handlers answer while the page waits
    for _ in range(100):
        if condition():
            return
        designer.wait_for_timeout(20)
    raise AssertionError('not reached')


def _printer(designer, model='Niimbot/B1', roll='PZ1TEST00002', path='simulation', follow=False, width=50, length=30):
    """The printer chosen as the server tells it, by default a B1 with a
    50 x 30 mm roll calibrated on another one. One to follow is asked on its
    own, as the server tells of one on USB."""
    printer = with_tape({'path': path, 'name': '', 'model': model, 'status_type': 'Reply to status request', 'errors': [], 'settings': {},
                         'roll': roll, 'calibration': {'across': 0, 'along': 0, 'roll': 'PZ1TEST00001'},
                         'media_width': width, 'media_length': length, 'follow': follow})
    designer.evaluate('p => { labelEditor.S.printers = [p]; labelEditor.S.printer = p; }', printer)


def _panel(designer, **printer):
    """The printers at the printer chip."""
    _printer(designer, **printer)
    designer.click('#printerChip')
    designer.wait_for_selector('.pp .plist')


def _dialog(designer, wide=False):
    _panel(designer)
    designer.click('.pp [data-cal-open]')
    designer.wait_for_selector('[data-move="left"]' if wide else '#calLeft')


def _sent_without(sent, *names):
    return {k: v for k, v in sent.items() if k not in names}


def test_calibration_is_offered_only_where_the_printer_has_it(designer, calibration):
    _panel(designer, model='QL-800')
    assert designer.locator('[data-cal-open]').count() == 0
    designer.keyboard.press('Escape')
    _panel(designer)
    assert designer.locator('.pp [data-cal-open]').is_visible()


def test_calibrate_printer_is_named_so(designer, calibration):
    _panel(designer)
    assert designer.inner_text('.pp [data-cal-open]') == 'Calibrate printer'
    designer.click('.pp [data-cal-open]')
    designer.wait_for_selector('#calLeft')
    assert designer.inner_text('#layer h2') == 'Calibrate printer'


def test_a_new_roll_is_pointed_out_and_dismiss_hides_it(designer, calibration):
    sent, _ = calibration
    _panel(designer)
    # One button to calibrate, which draws the eye
    assert HINT in designer.inner_text('.pp') and designer.evaluate(GLOWS)
    assert designer.locator('.pp .cal button').count() == 2
    assert designer.inner_text('[data-cal-dismiss]') == 'Dismiss'
    designer.click('[data-cal-dismiss]')
    designer.wait_for_function('() => !document.querySelector("[data-cal-dismiss]")')
    assert sent == [('/calibration', {'printer': 'simulation', 'model': 'Niimbot/B1', 'roll': 'PZ1TEST00002'})]
    assert HINT not in designer.inner_text('.pp') and not designer.evaluate(GLOWS)


def test_the_same_roll_is_not_pointed_out(designer, calibration):
    _panel(designer, roll='PZ1TEST00001')
    assert HINT not in designer.inner_text('.pp') and not designer.evaluate(GLOWS)


def test_calibrate_printer_glows_without_pulsing(designer, calibration):
    _panel(designer)
    assert designer.eval_on_selector('[data-cal-open]', 'b => getComputedStyle(b).animationName') == 'none'
    assert designer.eval_on_selector('[data-cal-open]', 'b => getComputedStyle(b).boxShadow') != 'none'


def test_a_round_roll_compares_the_white_ring(designer, calibration):
    _, answers = calibration
    answers['GET'] = dict(answers['GET'], kind='round')
    _panel(designer)
    with designer.expect_request('**/calibration/example.png*') as request:
        designer.click('.pp [data-cal-open]')
    assert 'kind=round' in request.value.url
    text = designer.inner_text('#layer')
    assert 'Compare the white ring left and right of the circle.' in text
    assert 'Compare the white ring above the circle with the one below it.' in text
    assert designer.locator('#calLeft').count() == 0 and designer.locator('[data-move="left"]').count() == 1


def test_a_roll_the_designer_does_not_offer_is_not_pointed_out(designer, calibration):
    # No calibration label can be printed on it
    _panel(designer, width=30, length=15)
    assert 'reports 30 x 15 mm tape' in designer.inner_text('.pp')
    assert HINT not in designer.inner_text('.pp')


def test_a_roll_put_in_a_printer_on_usb_is_pointed_out_within_seconds(designer, calibration):
    # Asked on its own every 2 s, without "Look for printers"
    swapped = []
    designer.route('**/api/printer_status?printer=*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({'printers': swapped})))
    _panel(designer, roll='PZ1TEST00001', path='file:///dev/usb/lp9', follow=True)
    assert HINT not in designer.inner_text('.pp')
    swapped.append(dict(designer.evaluate('() => labelEditor.S.printer'), roll='PZ1TEST00002'))
    designer.wait_for_function(f'() => document.querySelector(".pp").innerText.includes({json.dumps(HINT)})', timeout=3000)


def test_the_settings_of_the_printer_calibrate_it_too(designer, calibration):
    sent, _ = calibration
    _printer(designer)
    designer.click('#tabs [data-tab="settings"]')
    card = designer.locator('#setPrinters [data-card="0"]')
    card.locator('[data-cal-open]').wait_for()
    assert HINT in card.inner_text() and 'Position' in card.inner_text()
    card.locator('[data-cal-open]').click()
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    designer.wait_for_function('() => !document.querySelector("#calSave").disabled')
    designer.click('#calSave')
    designer.wait_for_function('() => document.querySelector("#layer").hidden')
    # Kept for the roll in the printer, the hint is gone
    assert sent[1][1]['roll'] == 'PZ1TEST00002'
    designer.wait_for_function(f'() => !document.querySelector("#setPrinters").innerText.includes({json.dumps(HINT)})')
    assert card.locator('[data-cal-open]').count() == 1


def test_readings_are_applied_and_the_result_is_shown(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.select_option('#calLeft', '-1')
    designer.select_option('#calRight', '1')
    designer.click('#calApply')
    _until(designer, lambda: len(sent) == 1)
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.inner_text('#calResult') == 'The printer prints 1 mm too far right. Labels are moved 1 mm to the left once you save.'
    assert designer.inner_text('#calNow') == 'Labels are moved 1 mm to the left.'
    # Counted from the correction the label was printed with, not saved yet
    assert _sent_without(sent[0][1], 'printer') == {'model': 'Niimbot/B1', 'left': '-1', 'right': '1', 'across': '0', 'along': '0', 'save': '0'}
    # Applied twice, the labels would be moved twice
    assert designer.eval_on_selector('#calLeft', 'e => e.value') == designer.eval_on_selector('#calRight', 'e => e.value') == ''


def test_refused_readings_tell_why_and_stay_chosen(designer, calibration):
    _, answers = calibration
    answers['POST'] = (400, {'success': False, 'message': 'Left and right do not fit together.'})
    _dialog(designer)
    designer.select_option('#calLeft', '-1.5')
    designer.select_option('#calRight', '-1')
    designer.click('#calApply')
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.inner_text('#calResult') == 'Left and right do not fit together.'
    assert designer.eval_on_selector('#calLeft', 'e => e.value') == '-1.5'


def test_apply_needs_a_number_on_each_side(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.select_option('#calLeft', '-1')
    designer.click('#calApply')
    assert designer.inner_text('#calResult') == 'Choose the number on each side.'
    assert sent == []


def test_moves_count_from_the_working_correction(designer, calibration):
    sent, answers = calibration
    answers['GET'] = {'across': 0.5, 'along': -0.25, 'roll': 'PZ1TEST00001', 'kind': 'wide'}
    answers['POST'] = (200, {'success': True, 'shift': None, 'calibration': {'across': 0.25, 'along': -0.25, 'roll': 'PZ1TEST00001'}})
    _dialog(designer, wide=True)
    assert 'Compare the white strips left and right of the frame.' in designer.inner_text('#layer')
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    # 0.25 mm on a wide roll, tried without saving
    assert _sent_without(sent[0][1], 'printer', 'model') == {'across': '0.25', 'along': '-0.25', 'save': '0'}
    designer.wait_for_function('() => document.querySelector("#calNow").textContent.includes("0.25 mm to the right")')
    designer.click('#calReset')
    _until(designer, lambda: len(sent) == 2)
    assert _sent_without(sent[1][1], 'printer', 'model') == {'across': '0', 'along': '0', 'save': '0'}


def test_calibration_label_prints_with_the_working_correction(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.select_option('#calLeft', '-1')
    designer.select_option('#calRight', '1')
    designer.click('#calApply')
    _until(designer, lambda: len(sent) == 1)
    designer.click('[data-cal-print]')
    _until(designer, lambda: len(sent) == 2)
    assert sent[1][0] == '/calibration/print'
    assert (sent[1][1]['across'], sent[1][1]['along'], sent[1][1]['label_size']) == ('-1', '0', 'niimbot50x30')


def test_cancel_keeps_what_was_saved(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    designer.click('#calCancel')
    designer.wait_for_function('() => document.querySelector("#layer").hidden')
    assert all(body.get('save') == '0' for _, body in sent)
    assert designer.evaluate('() => labelEditor.S.printer.calibration.across') == 0


def test_save_keeps_the_working_correction(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    designer.wait_for_function('() => !document.querySelector("#calSave").disabled')
    designer.click('#calSave')
    _until(designer, lambda: len(sent) == 2)
    designer.wait_for_function('() => document.querySelector("#layer").hidden')
    assert _sent_without(sent[1][1], 'printer') == {'model': 'Niimbot/B1', 'across': '-1', 'along': '0', 'roll': 'PZ1TEST00002'}
    assert designer.evaluate('() => labelEditor.S.printer.calibration.across') == -1


def test_example_tells_what_it_shows(designer, calibration):
    _dialog(designer)
    assert 'where the black starts' in designer.get_attribute('.cal-example', 'alt')
    # Shown smaller than drawn, smoothed rather than with dots left out
    assert designer.eval_on_selector('.cal-example', 'e => getComputedStyle(e).imageRendering') != 'pixelated'


def test_printing_waits_for_the_printer(designer, calibration):
    pending = []
    designer.route('**/api/calibration/print', lambda route: pending.append(route))
    _dialog(designer)
    designer.click('[data-cal-print]')
    _until(designer, lambda: len(pending) == 1)
    assert designer.eval_on_selector('[data-cal-print]', 'b => b.disabled')
    assert designer.inner_text('#calResult') == 'Printing the calibration label.'
    pending[0].fulfill(content_type='application/json', body='{"success": true}')
    designer.wait_for_function('() => !document.querySelector("[data-cal-print]").disabled')


def test_the_calibration_label_waits_in_the_queue(designer, calibration):
    replies = [{'state': 'waiting'}, {'state': 'printing'}, {'state': 'printed'}]
    designer.route('**/api/calibration/print', lambda route: route.fulfill(
        json={'success': True, 'queued': True, 'job': 'c1', 'state': 'waiting', 'start': 's1'}))
    designer.route('**/api/queue/c1', lambda route: route.fulfill(json=dict({'start': 's1'}, **(replies.pop(0) if len(replies) > 1 else replies[0]))))
    _dialog(designer)
    designer.evaluate('() => { labelEditor.S.printer.queue = { printing: true, waiting: 0, held: false }; }')
    designer.click('[data-cal-print]')
    designer.wait_for_function('() => document.querySelector("#calResult").textContent === "The calibration label waits in the queue."')
    assert designer.eval_on_selector('[data-cal-print]', 'b => b.disabled')
    designer.wait_for_function('() => document.querySelector("#calResult").textContent === "Printing the calibration label."')
    designer.wait_for_function('() => !document.querySelector("[data-cal-print]").disabled')
    assert designer.inner_text('#calResult') == ''


def test_a_calibration_label_that_fails_in_the_queue_says_why(designer, calibration):
    designer.route('**/api/calibration/print', lambda route: route.fulfill(
        json={'success': True, 'queued': True, 'job': 'c1', 'state': 'waiting', 'start': 's1'}))
    designer.route('**/api/queue/c1', lambda route: route.fulfill(json={'start': 's1', 'state': 'failed', 'message': 'The cover is open'}))
    _dialog(designer)
    designer.click('[data-cal-print]')
    designer.wait_for_function('() => !document.querySelector("[data-cal-print]").disabled')
    assert designer.inner_text('#calResult') == 'The cover is open'


def test_one_button_prints_right_under_the_example(designer, calibration):
    _dialog(designer)
    assert designer.locator('#layer [data-cal-print]').count() == 1
    assert designer.evaluate("() => document.querySelector('#layer .cal-example').nextElementSibling.matches('[data-cal-print]')")
    assert 'primary' in designer.get_attribute('#layer [data-cal-print]', 'class')


def test_cancel_and_save_end_the_dialog_side_by_side(designer, calibration):
    _dialog(designer)
    cancel, save = designer.locator('#calCancel'), designer.locator('#calSave')
    assert (cancel.inner_text(), save.inner_text()) == ('Cancel', 'Save')
    assert 'primary' in save.get_attribute('class') and 'primary' not in cancel.get_attribute('class')
    # One row at the bottom, Cancel left of Save
    assert designer.evaluate("""() => {
        const c = document.querySelector('#calCancel'), s = document.querySelector('#calSave');
        return c.parentElement === s.parentElement && c.nextElementSibling === s && !s.parentElement.nextElementSibling; }""")


def test_buttons_side_by_side_on_the_screen(designer, calibration):
    _dialog(designer)
    dialog = designer.locator('#layer .dialog').bounding_box()
    for first, second in (('#calCancel', '#calSave'), ('[data-move="left"]', '[data-move="right"]'), ('[data-move="up"]', '[data-move="down"]')):
        a, b = designer.locator(first).bounding_box(), designer.locator(second).bounding_box()
        # One row, the first on the left, neither as wide as the dialog
        assert abs(a['y'] - b['y']) < 2 and a['x'] + a['width'] <= b['x']
        assert a['width'] < dialog['width'] / 2 and b['width'] < dialog['width'] / 2


def test_readings_count_from_the_correction_the_label_was_printed_with(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    # Moved after the label was printed
    designer.click('[data-move="right"]')
    _until(designer, lambda: len(sent) == 1)
    designer.wait_for_function('() => !document.querySelector("#calApply").disabled')
    designer.select_option('#calLeft', '-1')
    designer.select_option('#calRight', '1')
    designer.click('#calApply')
    _until(designer, lambda: len(sent) == 2)
    assert sent[1][1]['across'] == '0'
    # Told from the label as printed
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.inner_text('#calResult') == 'The printer prints 1 mm too far right. Labels are moved 1 mm to the left once you save.'


def test_steps_wait_for_the_one_before(designer, calibration):
    pending = []
    designer.route('**/api/calibration', lambda route: pending.append(route) if route.request.method == 'POST' else route.fallback())
    _dialog(designer)
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(pending) == 1)
    assert designer.eval_on_selector_all('[data-move], #calReset, #calApply, #calSave', 'all => all.every(b => b.disabled)')
    pending[0].fulfill(content_type='application/json', body=json.dumps(
        {'success': True, 'shift': None, 'calibration': {'across': -0.5, 'along': 0.0, 'roll': 'PZ1TEST00001'}}))
    designer.wait_for_function('() => !document.querySelector("[data-move=left]").disabled')
    assert designer.inner_text('#calNow') == 'Labels are moved 0.5 mm to the left.'


def test_calibration_label_prints_once_at_a_time(designer, calibration):
    pending = []
    designer.route('**/api/calibration/print', lambda route: pending.append(route))
    _panel(designer)
    designer.click('.pp [data-cal-open]')
    designer.click('[data-cal-print]')
    _until(designer, lambda: len(pending) == 1)
    assert designer.eval_on_selector('[data-cal-print]', 'b => b.disabled')
    assert designer.inner_text('#calResult') == 'Printing the calibration label.'
    pending[0].fulfill(content_type='application/json', body='{"success": true}')
    designer.wait_for_function('() => !document.querySelector("[data-cal-print]").disabled')
    assert len(pending) == 1
