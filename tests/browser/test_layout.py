"""The label designer at /labeldesigner/ in windows of any width: the top
bar keeps Print in view, the toolbar stays in its column and Advanced stays
put beside the label."""
import pytest

from tests.browser.test_editor import editor, type_label, wait_for_preview  # noqa: F401

RECT = "sel => { const r = document.querySelector(sel).getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }"


@pytest.mark.parametrize('width', [360, 700, 800, 880, 1024])
def test_nothing_scrolls_sideways_and_print_is_in_view(editor, width):  # noqa: F811
    editor.set_viewport_size({'width': width, 'height': 700})
    editor.wait_for_timeout(100)
    assert editor.evaluate('() => document.documentElement.scrollWidth <= innerWidth')
    right = editor.evaluate(RECT, '#print')[2]
    assert right <= width


@pytest.mark.parametrize('width', [720, 800])
def test_the_toolbar_stays_in_its_column_beside_advanced(editor, width):  # noqa: F811
    editor.set_viewport_size({'width': width, 'height': 700})
    type_label(editor, 'Shelf 3', 'spices')
    editor.click('#advBtn')
    editor.wait_for_function("() => getComputedStyle(document.querySelector('#center')).marginRight === '360px'")
    editor.wait_for_timeout(400)
    bar, center, card = editor.evaluate(RECT, '#ltb'), editor.evaluate(RECT, '#center'), editor.evaluate(RECT, '#adv')
    assert bar[0] >= center[0] and bar[2] <= card[0]


def test_advanced_stays_in_view_while_the_stage_scrolls(editor):  # noqa: F811
    editor.set_viewport_size({'width': 1000, 'height': 600})
    type_label(editor, *[f'Line {n}' for n in range(1, 9)])
    editor.click('#advBtn')
    editor.wait_for_selector('#adv [data-auto]')
    editor.evaluate("() => { const s = document.querySelector('#stage'); s.scrollTop = s.scrollHeight; }")
    card = editor.evaluate(RECT, '#adv')
    assert card[1] == 56 and card[3] == 600
    close = editor.evaluate(RECT, '[data-close-adv]')
    assert 56 <= close[1] and close[3] <= 600
    # Templates and New beside it, not under it
    assert editor.evaluate(RECT, '.stage-acts')[2] <= card[0]
