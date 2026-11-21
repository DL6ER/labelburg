"""Values with markup, e.g. in a label edited by hand or shared, and in the
address of the label designer at /labeldesigner/ are shown as text: none of
them becomes part of the page."""
import json

from tests.browser.test_editor import editor, wait_for_preview  # noqa: F401

MARKUP = '"><img src="x" onerror="window.injected = 1">'


def untouched(page):
    """Nothing of MARKUP made it into the page as markup."""
    return page.evaluate('() => [window.injected, document.querySelectorAll(\'img[src="x"]\').length]') == [None, 0]


def test_a_color_with_markup_is_no_color(editor):  # noqa: F811
    assert editor.evaluate("""async markup => {
        const colors = await import('/static/designer/colors.js');
        return [colors.hexOf(markup), colors.hexOf('#2563EB'), colors.hexOf('red'), colors.hexOf(['#2563eb'])];
    }""", MARKUP) == ['#000000', '#2563eb', '#ff0000', '#000000']
    # Shown as a swatch and in the menu of colors of a color tape
    custom = editor.evaluate("""async markup => {
        const colors = await import('/static/designer/colors.js');
        const box = document.createElement('div');
        box.innerHTML = colors.swatchHTML('data-test-swatch', markup, { label_size: 'vc25' }, 'the line');
        document.body.append(box);
        const btn = box.querySelector('[data-test-swatch]');
        colors.openColors(btn, { color: markup, d: { label_size: 'vc25' }, what: 'the line', pick: () => {}, box: document.body });
        return document.querySelector('.color-menu [data-custom]').value;
    }""", MARKUP)
    assert custom == '#000000'
    assert untouched(editor)


def test_label_values_with_markup_are_text(editor):  # noqa: F811
    design = {f'margin_{side}': MARKUP for side in ('left', 'right', 'top', 'bottom')}
    design.update(text=[{'text': 'Jar {{blank:What}}', 'size': '40', 'font': 'DejaVu Sans,Book', 'align': 'left', 'color': MARKUP}],
                  fields={'What': {'type': MARKUP}}, border_color=MARKUP, image_text_gap=MARKUP)
    editor.evaluate("""async design => {
        const core = await import('/static/designer/core.js');
        const ed = await import('/static/designer/editor.js');
        ed.openEditor({ title: 'Markup', design: Object.assign(core.baseDesign('62'), design) });
    }""", json.loads(json.dumps(design)))
    editor.wait_for_selector('#fill input[data-blank="What"]')
    # A kind of blank it does not know is guessed by the name
    assert editor.eval_on_selector('#fill input[data-blank="What"]', 'e => [e.type, [...e.attributes].map(a => a.name).sort()]') == [
        'text', ['autocomplete', 'class', 'data-blank', 'id', 'placeholder', 'value']]
    editor.click('#advBtn')
    editor.click('#adv [data-custom]')
    editor.wait_for_selector('#adv [data-margin="left"]')
    editor.click('#field .ln >> nth=0')
    editor.wait_for_selector('#ltb [data-sizein]')
    assert untouched(editor)


def test_an_address_naming_no_part_of_the_settings_shows_the_printers(editor):  # noqa: F811
    for part in ('__proto__', 'constructor', MARKUP):
        editor.evaluate('part => { location.hash = "settings/" + part; }', part)
        editor.wait_for_selector('#setPart[data-part="printers"]')
        assert editor.locator('.set-nav [data-part="printers"].on').count() == 1
        editor.evaluate('() => { location.hash = "edit"; }')
        editor.wait_for_selector('#field .ln')
    assert untouched(editor)
