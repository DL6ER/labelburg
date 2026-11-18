"""Calibrating a Niimbot B1 in the printer panel: the hint after a roll change
and the calibration dialog, which changes nothing until it is saved. The
server is stubbed."""

import json
from urllib.parse import parse_qs

import pytest

HINT = 'A new roll is in the printer. Print a calibration label so the labels sit straight on it.'


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


def _panel(designer, model='Niimbot/B1', roll='PZ1TEST00002'):
    designer.evaluate(f"""() => {{ S.printers = [Object.assign({{}}, S.printer, {{ model: '{model}', status_type: 'Reply to status request',
        roll: '{roll}', calibration: {{ across: 0, along: 0, roll: 'PZ1TEST00001' }} }})]; S.printer = S.printers[0]; openPrinterPanel(); }}""")


def _dialog(designer, wide=False):
    _panel(designer)
    designer.click('#calOpen')
    designer.wait_for_selector('[data-move="left"]' if wide else '#calLeft')


def _sent_without(sent, *names):
    return {k: v for k, v in sent.items() if k not in names}


def test_calibration_is_offered_only_where_the_printer_has_it(designer, calibration):
    _panel(designer, model='QL-800')
    assert designer.locator('#calOpen').count() == 0
    designer.evaluate('() => closeLayer(true)')
    _panel(designer)
    assert designer.locator('#calOpen').is_visible()


def test_calibrate_printer_is_named_so(designer, calibration):
    _panel(designer)
    assert designer.inner_text('#calOpen') == 'Calibrate printer'
    designer.click('#calOpen')
    designer.wait_for_selector('#calLeft')
    assert designer.inner_text('#layer h2') == 'Calibrate printer'


def test_a_new_roll_is_pointed_out_and_not_now_hides_it(designer, calibration):
    sent, _ = calibration
    _panel(designer)
    assert HINT in designer.inner_text('.pp')
    designer.click('#calNotNow')
    designer.wait_for_function('() => !document.querySelector("#calNotNow")')
    assert sent == [('/calibration', {'printer': designer.evaluate('() => S.printer.path'), 'model': 'Niimbot/B1', 'roll': 'PZ1TEST00002'})]
    assert HINT not in designer.inner_text('.pp')


def test_the_same_roll_is_not_pointed_out(designer, calibration):
    _panel(designer, roll='PZ1TEST00001')
    assert HINT not in designer.inner_text('.pp')


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
    assert (sent[1][1]['across'], sent[1][1]['along']) == ('-1', '0')


def test_cancel_keeps_what_was_saved(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    designer.click('#calCancel')
    designer.wait_for_function('() => document.querySelector("#layer").hidden')
    assert all(body.get('save') == '0' for _, body in sent)
    assert designer.evaluate('() => S.printer.calibration.across') == 0


def test_save_keeps_the_working_correction(designer, calibration):
    sent, _ = calibration
    _dialog(designer)
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    designer.click('#calSave')
    _until(designer, lambda: len(sent) == 2)
    designer.wait_for_function('() => document.querySelector("#layer").hidden')
    assert _sent_without(sent[1][1], 'printer') == {'model': 'Niimbot/B1', 'across': '-1', 'along': '0', 'roll': 'PZ1TEST00002'}
    assert designer.evaluate('() => S.printer.calibration.across') == -1


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


def test_one_button_prints_right_under_the_example(designer, calibration):
    _dialog(designer)
    assert designer.locator('#layer [data-cal-print]').count() == 1
    assert designer.evaluate("() => document.querySelector('#layer .cal-example').nextElementSibling.matches('[data-cal-print]')")
    assert 'btn-print' in designer.get_attribute('#layer [data-cal-print]', 'class')


def test_cancel_and_save_end_the_dialog_side_by_side(designer, calibration):
    _dialog(designer)
    cancel, save = designer.locator('#calCancel'), designer.locator('#calSave')
    assert (cancel.inner_text(), save.inner_text()) == ('Cancel', 'Save')
    assert 'bad' in cancel.get_attribute('class') and cancel.locator('.fa-xmark').count() == 1
    assert 'ok' in save.get_attribute('class') and save.locator('.fa-floppy-disk').count() == 1
    # One row at the bottom, Cancel left of Save
    assert designer.evaluate("""() => {
        const c = document.querySelector('#calCancel'), s = document.querySelector('#calSave');
        return c.parentElement === s.parentElement && c.nextElementSibling === s && !s.parentElement.nextElementSibling; }""")


def test_buttons_side_by_side_on_the_screen(designer, calibration):
    _dialog(designer)
    sheet = designer.locator('#layer .sheet').bounding_box()
    for first, second in (('#calCancel', '#calSave'), ('[data-move="left"]', '[data-move="right"]'), ('[data-move="up"]', '[data-move="down"]')):
        a, b = designer.locator(first).bounding_box(), designer.locator(second).bounding_box()
        # One row, the first on the left, neither as wide as the dialog
        assert abs(a['y'] - b['y']) < 2 and a['x'] + a['width'] <= b['x']
        assert a['width'] < sheet['width'] / 2 and b['width'] < sheet['width'] / 2


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
    sent, _ = calibration
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


def test_new_roll_prints_from_the_dialog_once(designer, calibration):
    sent, _ = calibration
    pending = []
    designer.route('**/api/calibration/print', lambda route: pending.append(route))
    _panel(designer)
    designer.click('#calPrintNew')
    designer.wait_for_selector('[data-cal-print]')
    _until(designer, lambda: len(pending) == 1)
    assert designer.eval_on_selector('[data-cal-print]', 'b => b.disabled')
    assert designer.inner_text('#calResult') == 'Printing the calibration label.'
    pending[0].fulfill(content_type='application/json', body='{"success": true}')
    designer.wait_for_function('() => !document.querySelector("[data-cal-print]").disabled')
    assert len(pending) == 1
