"""Die-cut labels several drivers share: their sizes in the dots of a
printer, the size a printer measured and how a label is turned for it."""
import pytest
from PIL import Image

from app.labeldesigner.drivers import diecut
from app.labeldesigner.drivers.labels import FormFactor


@pytest.mark.parametrize('mm, dpi, dots', [(102, 203, 816), (152, 203, 1216), (3, 203, 24), (104, 203, 832),
                                           (102, 200, 816), (102, 300, 1205), (152, 300, 1795), (3, 300, 35),
                                           (104, 300, 1228), (102, 600, 2409)])
def test_dots(mm, dpi, dots):
    # 8 dots per mm at 203 dpi, as the printers count them, also where named 200 dpi
    assert diecut.dots(mm, dpi) == dots


@pytest.mark.parametrize('length, dpi, mm', [(1216, 203, 152), (1216, 200, 152), (235, 203, 30), (1795, 300, 152), (354, 300, 30),
                                             (1214, 300, 102), (500, 203, None), (808, 203, None)])
def test_measured(length, dpi, mm):
    # 808 dots at 203 dpi are 101 mm, as near to 100 as to 102 mm
    assert diecut.measured(length, dpi) == mm


@pytest.mark.parametrize('length, dpi, size', [(1214, 203, (102, 152)), (1184, 203, (105, 148)), (1795, 300, (102, 152)),
                                               (1748, 300, (105, 148)), (235, 203, None), (354, 300, None)])
def test_loaded(length, dpi, size):
    # 30 mm long labels are 100, 50 and 40 mm wide, the width stays unknown
    assert diecut.loaded(length, dpi) == size


def test_measured_at_203_dpi_unless_told():
    assert diecut.measured(1214) == 152 and diecut.loaded(1214) == (102, 152)


def test_labels_in_the_dots_of_the_printer():
    labels = diecut.labels('t', 300, diecut.dots(104, 300), ('Maker/Model',), ((105, 148), (50, 30)))
    assert [label.identifier for label in labels] == ['t105x148', 't50x30']
    wide, small = labels
    assert (wide.tape_size, wide.form_factor, wide.models) == ((105, 148), FormFactor.DIE_CUT, ('Maker/Model',))
    # Wider than the printhead, the rest is not printed
    assert (wide.dots_total, wide.dots_printable) == ((1240, 1748), (1228, 1748))
    assert (small.dots_total, small.dots_printable, small.gap) == ((591, 354), (591, 354), diecut.GAP)


def test_a_label_size_has_no_gap_unless_told():
    from app.labeldesigner.drivers.labels import Label
    assert Label('62', (62, 0), FormFactor.ENDLESS, (696, 0), (696, 0), 12).gap == 0


def _drawn(width, height):
    """A label as drawn, a black square in its top left corner."""
    img = Image.new('1', (width, height), 1)
    img.paste(0, (0, 0, 4, 4))
    return img


@pytest.mark.parametrize('top_first, corner', [(True, (0, 0)), (False, (39, 19))])
def test_the_top_of_a_label_leaves_first(top_first, corner):
    # 40 dots across the printhead, the label as wide
    img = diecut.oriented(_drawn(40, 20), 40, top_first)
    assert img.size == (40, 20)
    assert img.getpixel(corner) == 0 and img.getpixel((39 - corner[0], 19 - corner[1])) == 1


# Its start, the top left corner as drawn, and its end in the row that leaves
# first: the first where top_first, else the last
@pytest.mark.parametrize('top_first, start, white', [(True, (0, 59), (19, 59)), (False, (19, 0), (0, 0))])
def test_the_end_of_a_label_drawn_lying_leaves_first(top_first, start, white):
    # 20 dots across the printhead, the label drawn 60 wide and 20 high
    img = diecut.oriented(_drawn(60, 20), 20, top_first)
    assert img.size == (20, 60)
    # Turned, not mirrored
    assert img.getpixel(start) == 0 and img.getpixel(white) == 1


@pytest.mark.parametrize('sensing, roll', [(None, 'Die-cut labels'), ('gap', 'Die-cut labels'), ('mark', 'Labels with black marks on the back')])
def test_the_roll_is_named_as_set(sensing, roll):
    state = {'media_type': 'Die-cut labels', 'media_width': None, 'media_length': 152}
    assert diecut.as_set(state, sensing) == dict(state, media_type=roll)


def test_a_continuous_roll_has_no_labels_measured():
    # The length a printer on it tells is the one it was sent last
    state = {'media_type': 'Die-cut labels', 'media_width': 102, 'media_length': 152, 'errors': []}
    assert diecut.as_set(state, 'endless') == {'media_type': 'Continuous roll', 'media_width': None, 'media_length': 0, 'errors': []}
    assert state['media_length'] == 152
