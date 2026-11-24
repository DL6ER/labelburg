"""Characters the font of a line has no glyph for, e.g. emoji, check marks
and stars, drawn with the first fallback font that has them."""

import io
import json

import pytest
from PIL import Image, ImageOps

from app.fonts import EMOJI_FONT_PATH
from app.labeldesigner import icons, label

from tests.helpers import EXAMPLE_FORMDATA, make_client

FAMILY = '\U0001F468\u200d\U0001F469\u200d\U0001F467'
FLAG = '\U0001F1E9\U0001F1EA'


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


@pytest.fixture
def fonts(client):
    return client.application.extensions['fonts']


def preview(client, text, font='Liberation Sans,Regular', **settings) -> Image.Image:
    data = dict(EXAMPLE_FORMDATA, border_thickness='0', **settings,
                text=json.dumps([{'font': font, 'text': text, 'size': '60', 'align': 'center'}]))
    response = client.post('/labeldesigner/api/preview?return_format=png', data=data)
    assert response.status_code == 200
    return Image.open(io.BytesIO(response.data)).convert('L')


def ink(image: Image.Image) -> tuple | None:
    return ImageOps.invert(image).point(lambda p: 255 if p > 127 else 0).getbbox()


def width(client, text) -> int:
    box = ink(preview(client, text))
    return box[2] - box[0]


@pytest.mark.parametrize('char', ['\u2713', '\u2605', '\u2600', '\u26a1', '\U0001F525', '\U0001F600'])
def test_a_character_the_font_lacks_is_not_its_missing_glyph(client, char):
    # U+0378 is in no font: Liberation Sans draws its missing glyph
    missing = preview(client, '\u0378')
    drawn = preview(client, char)
    assert ink(drawn) and drawn.tobytes() != missing.tobytes()


def test_the_characters_go_to_the_first_font_that_has_them(fonts):
    liberation, dejavu = fonts.get_path('Liberation Sans,Regular'), fonts.get_path('DejaVu Sans,Book')
    flash = icons.icon('flash')
    assert label._font_runs(f'{flash} Hot \U0001F525\u2713 food', liberation, fonts.fallbacks()) == (
        (flash, icons.FONT_PATH), (' Hot ', liberation), ('\U0001F525', EMOJI_FONT_PATH), ('\u2713', dejavu), (' food', liberation))
    # Sequences stay together with the font that has all of them, without
    # Raqm their joiners are left out
    assert label._font_runs(f'{FAMILY}{FLAG}', dejavu, fonts.fallbacks()) == ((FAMILY.replace('\u200d', '') + FLAG, EMOJI_FONT_PATH),)
    # So are the tags of a flag like that of England and the variation
    # selectors of the supplement
    england = '\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F'
    assert label._font_runs(f'{england}\U000E0101', dejavu, fonts.fallbacks()) == (('\U0001F3F4', EMOJI_FONT_PATH),)


def test_a_font_with_the_character_keeps_its_own(client, fonts):
    # FreeSerif has a check mark, Liberation Sans has none
    free_serif = fonts.get_path('FreeSerif,Regular')
    assert label._font_runs('Done \u2713', free_serif, fonts.fallbacks()) == (('Done \u2713', free_serif),)
    with_fallbacks = {font: preview(client, 'Done \u2713', font).tobytes() for font in ('FreeSerif,Regular', 'Liberation Sans,Regular')}
    fonts._fallbacks = ()
    assert preview(client, 'Done \u2713', 'FreeSerif,Regular').tobytes() == with_fallbacks['FreeSerif,Regular']
    assert preview(client, 'Done \u2713', 'Liberation Sans,Regular').tobytes() != with_fallbacks['Liberation Sans,Regular']


@pytest.mark.parametrize('font', ['Liberation Sans,Regular', 'DejaVu Sans,Book'])
@pytest.mark.parametrize('text', ['A', '\u2714', '\U0001F525'])
@pytest.mark.parametrize('joiner', ['\ufe0f', '\u200d'])
def test_joiners_and_selectors_draw_nothing(client, font, text, joiner):
    # Liberation Sans has no U+FE0F, DejaVu Sans has
    assert preview(client, text + joiner, font).tobytes() == preview(client, text, font).tobytes()


# Pillow draws an emoji sequence as one glyph only with Raqm, which needs
# libfribidi, not in the image: there it prints its parts side by side

@pytest.mark.parametrize('sequence, parts', [(FAMILY, 3), (FLAG, 2)], ids=['family', 'flag'])
def test_an_emoji_sequence_prints_its_parts(client, sequence, parts):
    # The family its people, the flag its two letters, each a glyph
    missing = preview(client, '\u0378').tobytes()
    people = sequence.replace('\u200d', '')
    assert preview(client, sequence).tobytes() == preview(client, people).tobytes()
    assert all(ink(preview(client, part)) and preview(client, part).tobytes() != missing for part in people)
    assert width(client, sequence) > (parts - 0.5) * width(client, '\U0001F600')


def test_paragraph_separators_stay_with_the_font_of_the_line(client, fonts):
    # Raqm refuses to lay them out, also next to emoji
    liberation = fonts.get_path('Liberation Sans,Regular')
    text = '\U0001F525\u200d\u2029 \x85\x1c\x1d\x1e\u2029'
    assert all(font == liberation for run, font in label._font_runs(text, liberation, fonts.fallbacks()))
    assert ink(preview(client, text))


def test_the_characters_drawn_as_one_stay_together():
    text = f'a{FAMILY}{FLAG}\U0001F1EBr\U0001F44D\U0001F3FDe\u0301#\ufe0f\u20e3\U0001F3F4\U000E0067\U000E0062\U000E007F'
    assert label._clusters(text) == ['a', FAMILY, FLAG, '\U0001F1EB', 'r', '\U0001F44D\U0001F3FD', 'e\u0301', '#\ufe0f\u20e3',
                                     '\U0001F3F4\U000E0067\U000E0062\U000E007F']


@pytest.mark.parametrize('settings', [{'text_wrap': '1'}, {'text_fit': 'shrink'}], ids=['wrapped', 'shrunk'])
def test_emoji_are_measured_as_drawn(client, settings):
    # A line of emoji, wider than the label, wraps or shrinks within its margins
    image = preview(client, ' '.join(['\U0001F525\U0001F600\u2605'] * 8), **settings)
    left, top, right, bottom = ink(image)
    margin_left, margin_right = int(EXAMPLE_FORMDATA['margin_left']), int(EXAMPLE_FORMDATA['margin_right'])
    assert left >= margin_left and right <= image.width - margin_right


def test_emoji_on_a_bold_line_are_bolder(client):
    def black(font):
        return preview(client, '\U0001F525', font).histogram()[0]
    assert black('DejaVu Sans,Bold') > 1.1 * black('DejaVu Sans,Book')
