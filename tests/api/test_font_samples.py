"""The name of a font family drawn in its own font, for the font menu of the
label designer."""

import io
import logging

from PIL import Image

from app.fonts import Fonts
from tests.helpers import make_client
from tests.test_fonts import os2, sfnt, win

URL = '/labeldesigner/api/fonts/sample'


def test_a_family_is_drawn_in_its_regular_style(tmp_path):
    client = make_client(tmp_path)
    r = client.get(URL, query_string={'family': 'DejaVu Sans Mono'})
    assert r.status_code == 200 and r.mimetype == 'image/png'
    # Black on transparent, the page colors it for its theme. The height is
    # the same for each family, the text stands on the same line.
    with Image.open(io.BytesIO(r.data)) as image:
        assert image.mode == 'LA' and image.height == 56 and image.width > 300
        assert image.getchannel('L').getextrema() == (0, 0) and image.getchannel('A').getextrema() == (0, 255)
    # Kept by the browser, a family installed stays as it is
    assert r.cache_control.public and r.cache_control.max_age == 86400
    assert client.get(URL, query_string={'family': 'DejaVu Sans Mono'}, headers={'If-None-Match': r.headers['ETag']}).status_code == 304
    assert client.get(URL, query_string={'family': 'DejaVu Serif'}).data != r.data


def test_a_family_that_cannot_draw_its_name_has_no_sample(tmp_path):
    # Noto Sans Symbols 2 has no letters, the menu shows the name as text
    r = make_client(tmp_path).get(URL, query_string={'family': 'Noto Sans Symbols 2'})
    assert r.status_code == 204 and not r.data


def test_a_family_not_installed_has_no_sample(tmp_path):
    client = make_client(tmp_path)
    for query in ({'family': 'No Such Font'}, {}, {'family': 'DejaVu Sans,Bold'}):
        r = client.get(URL, query_string=query)
        assert r.status_code == 404, query
        assert r.get_json() == {'success': False, 'message': 'Not found'}


def test_a_broken_font_file_has_no_sample(tmp_path):
    # Its names and OS/2 table read, it cannot draw: the menu shows its name
    # as text, the server tells it once
    folder = tmp_path / 'fonts'
    folder.mkdir()
    (folder / 'broken.ttf').write_bytes(sfnt([win(1, 'Broken Sans'), win(2, 'Regular')], os2=os2(400, 5)))
    client = make_client(tmp_path)
    client.application.extensions['fonts'] = Fonts(logging.getLogger('test'), additional_path=str(folder), system_fonts=False)
    for _ in range(2):
        r = client.get(URL, query_string={'family': 'Broken Sans'})
        assert r.status_code == 204 and not r.data

