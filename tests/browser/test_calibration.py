"""Lining up the labels of a Niimbot B1 in the printer panel: the hint after a
roll change and the calibration dialog, the server stubbed."""

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


def test_calibration_is_offered_only_where_the_printer_has_it(designer, calibration):
    _panel(designer, model='QL-800')
    assert designer.locator('#calOpen').count() == 0
    designer.evaluate('() => closeLayer(true)')
    _panel(designer)
    assert designer.locator('#calOpen').is_visible()


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


def test_readings_are_sent_and_the_result_is_shown(designer, calibration):
    sent, _ = calibration
    _panel(designer)
    designer.click('#calOpen')
    designer.wait_for_selector('#calLeft')
    designer.select_option('#calLeft', '-1')
    designer.select_option('#calRight', '1')
    designer.click('#calSave')
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.inner_text('#calResult') == 'The printer prints 1 mm too far right. Labels are moved 1 mm to the left from now on.'
    assert sent[-1][1] | {'printer': ''} == {'printer': '', 'model': 'Niimbot/B1', 'left': '-1', 'right': '1', 'roll': 'PZ1TEST00002'}


def test_refused_readings_tell_why(designer, calibration):
    _, answers = calibration
    answers['POST'] = (400, {'success': False, 'message': 'Left and right do not fit together.'})
    _panel(designer)
    designer.click('#calOpen')
    designer.wait_for_selector('#calLeft')
    designer.select_option('#calLeft', '-1.5')
    designer.select_option('#calRight', '-1')
    designer.click('#calSave')
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.inner_text('#calResult') == 'Left and right do not fit together.'
    # Kept to be corrected
    assert designer.eval_on_selector('#calLeft', 'e => e.value') == '-1.5'


def test_fine_tuning_moves_from_the_kept_offsets(designer, calibration):
    sent, answers = calibration
    answers['GET'] = {'across': 0.5, 'along': -0.25, 'roll': 'PZ1TEST00001', 'kind': 'wide'}
    answers['POST'] = (200, {'success': True, 'shift': None, 'calibration': {'across': 0.25, 'along': -0.25, 'roll': 'PZ1TEST00002'}})
    _panel(designer)
    designer.click('#calOpen')
    designer.wait_for_selector('[data-move="left"]')
    assert 'Compare the white strips left and right of the frame.' in designer.inner_text('#layer')
    designer.click('[data-move="left"]')
    _until(designer, lambda: len(sent) == 1)
    # 0.25 mm on a wide roll, from what the server keeps
    assert (sent[0][1]['across'], sent[0][1]['along']) == ('0.25', '-0.25')
    designer.click('#calReset')
    _until(designer, lambda: len(sent) == 2)
    assert (sent[1][1]['across'], sent[1][1]['along']) == ('0', '0')


def _dialog(designer, calibration):
    _panel(designer)
    designer.click('#calOpen')
    designer.wait_for_selector('#calLeft')


def test_save_needs_a_number_on_each_side(designer, calibration):
    sent, _ = calibration
    _dialog(designer, calibration)
    designer.select_option('#calLeft', '-1')
    designer.click('#calSave')
    assert designer.inner_text('#calResult') == 'Choose the number on each side.'
    assert sent == []


def test_numbers_are_cleared_once_saved(designer, calibration):
    # Saved twice, the labels would be moved twice
    sent, _ = calibration
    _dialog(designer, calibration)
    designer.select_option('#calLeft', '-1')
    designer.select_option('#calRight', '1')
    designer.click('#calSave')
    _until(designer, lambda: len(sent) == 1)
    designer.wait_for_function('() => document.querySelector("#calResult").textContent')
    assert designer.eval_on_selector('#calLeft', 'e => e.value') == designer.eval_on_selector('#calRight', 'e => e.value') == ''


def test_example_tells_what_it_shows(designer, calibration):
    _dialog(designer, calibration)
    assert 'where the black starts' in designer.get_attribute('.cal-example', 'alt')


def test_printing_waits_for_the_printer(designer, calibration):
    pending = []
    designer.route('**/api/calibration/print', lambda route: pending.append(route))
    _dialog(designer, calibration)
    designer.click('[data-cal-print]')
    _until(designer, lambda: len(pending) == 1)
    assert designer.eval_on_selector_all('[data-cal-print]', 'all => all.every(b => b.disabled)')
    assert designer.inner_text('#calResult') == 'Printing the calibration label.'
    pending[0].fulfill(content_type='application/json', body='{"success": true}')
    designer.wait_for_function('() => [...document.querySelectorAll("[data-cal-print]")].every(b => !b.disabled)')
