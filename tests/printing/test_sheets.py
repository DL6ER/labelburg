"""Rolls with several labels to a page: one label of a page, where each
label goes and the page put together of them."""
from PIL import Image

from app.labeldesigner.drivers.labels import FormFactor, Grid, Label
from app.labeldesigner.label import LabelContent
from app.labeldesigner.label_names import SIZES
from app.labeldesigner.sheets import (CUT_DOTS, CUT_GRAY, Sheet, compose, fill_state, is_paper, lying, page_view, per_sheet, piece,
                                      shifted, spots, with_cut_lines)


class Made:
    """A label as the sheets take it: its image as generated."""

    def __init__(self, image):
        self.image = image
        self.turned = False
        self.keep_generated = False
        self.label_content = LabelContent.TEXT_ONLY
        self.in_color = False
        self.generated_turned = []

    def generate(self):
        self.generated_turned.append(self.turned)
        image = self.image.rotate(180) if self.turned else self.image
        if self.keep_generated:
            self.generated = image
        return image


def dotted(width, height):
    """A white image with a black dot at its top left."""
    image = Image.new('L', (width, height), 255)
    image.putpixel((0, 0), 0)
    return image


def test_one_label_of_each_b1_sheet():
    assert [(piece(SIZES[i]).tape_size, piece(SIZES[i]).dots_printable) for i in
            ('niimbot15x8-2x3', 'niimbot13x35-2x1', 'niimbot30x15-1x2')] == \
        [((15, 8), (120, 64)), ((13, 35), (104, 280)), ((30, 15), (240, 120))]
    assert piece(SIZES['niimbot15x8-2x3']).pieces == (1, 1, 0)
    assert piece(SIZES['niimbot15x8-2x3']).identifier == 'niimbot15x8-2x3'
    assert piece(SIZES['niimbot50x30']) is SIZES['niimbot50x30']


def test_labels_to_a_sheet():
    assert [per_sheet(SIZES[i]) for i in ('niimbot15x8-2x3', 'niimbot13x35-2x1', 'niimbot30x15-1x2', 'niimbot50x30')] == [6, 2, 2, 1]


def test_a_label_lies_where_it_is_turned_into_its_lane():
    assert lying(SIZES['niimbot13x35-2x1'], Image.new('L', (280, 104)))
    assert not lying(SIZES['niimbot13x35-2x1'], Image.new('L', (104, 280)))
    assert not lying(SIZES['niimbot15x8-2x3'], Image.new('L', (120, 64)))


def test_labels_fill_rows_left_to_right():
    assert spots(SIZES['niimbot15x8-2x3'], False) == [(0, 0), (120, 0), (0, 64), (120, 64), (0, 128), (120, 128)]


def test_lying_labels_fill_lanes_as_read():
    assert spots(SIZES['niimbot13x35-2x1'], True) == [(0, 0), (120, 0)]
    # Text along the 30 x 15 mm labels: the first copy is the one leaving the printer second
    assert spots(SIZES['niimbot30x15-1x2'], True) == [(0, 120), (0, 0)]


def test_compose_turns_lying_labels_into_their_lane():
    sheet = compose(SIZES['niimbot13x35-2x1'], [dotted(280, 104), dotted(280, 104)])
    assert sheet.size == (224, 280)
    assert sheet.getpixel((0, 279)) == 0 and sheet.getpixel((120, 279)) == 0
    assert sheet.histogram()[0] == 2


def test_compose_turns_each_image_on_its_own():
    sheet = compose(SIZES['niimbot13x35-2x1'], [dotted(280, 104), dotted(104, 280)])
    assert sheet.getpixel((0, 279)) == 0 and sheet.getpixel((120, 0)) == 0
    assert sheet.histogram()[0] == 2


def test_missing_labels_stay_white():
    sheet = compose(SIZES['niimbot15x8-2x3'], [Image.new('L', (120, 64), 0)] * 2)
    assert sheet.size == (240, 192)
    assert sheet.crop((0, 0, 240, 64)).getextrema() == (0, 0)
    assert sheet.crop((0, 64, 240, 192)).getextrema() == (255, 255)


def test_sheet_turns_as_a_whole():
    images = [dotted(120, 64), Image.new('L', (120, 64), 0), dotted(120, 64)]
    labels = [Made(image) for image in images]
    for label in labels:
        label.turned = True
    sheet = Sheet(SIZES['niimbot15x8-2x3'], labels)
    assert sheet.turned
    assert sheet.generate().tobytes() == compose(SIZES['niimbot15x8-2x3'], images).rotate(180).tobytes()
    assert all(label.generated_turned == [False] for label in labels)


def test_sheet_is_kept_as_the_first_labels_image():
    labels = [Made(dotted(120, 64)), Made(dotted(120, 64))]
    labels[0].keep_generated = True
    sheet = Sheet(SIZES['niimbot15x8-2x3'], labels)
    image = sheet.generate()
    assert labels[0].generated is image
    assert image.size == (240, 192)
    assert sheet.labels == labels
    assert sheet.label_content == LabelContent.TEXT_ONLY and sheet.in_color is False


def test_page_shown_upright_with_the_first_label_top_left():
    # Lying 13 x 35 mm labels: the sheet turned back, the lanes one above the other
    page = page_view(SIZES['niimbot13x35-2x1'], [dotted(280, 104), dotted(280, 104)])
    assert page.size == (280, 224)
    assert page.getpixel((0, 0)) == 0 and page.getpixel((0, 120)) == 0
    assert page.histogram()[0] == 2
    upright = [dotted(120, 64)] * 3
    assert page_view(SIZES['niimbot15x8-2x3'], upright).tobytes() == compose(SIZES['niimbot15x8-2x3'], upright).tobytes()


# A sheet of paper of 50 x 30 mm at 10 dots per mm: 2 x 2 labels of 20 x 10 mm,
# 4 mm from the left, 3 from the top, 2 mm apart
PAPER = Label('test-paper', (50, 30), FormFactor.DIE_CUT, (500, 300), (500, 300), 0,
              grid=Grid((20, 10), 2, 2, left=4, top=3, step=(22, 12), corner=1, paper=True))


def test_labels_given_by_their_pieces_lie_on_a_grid_without_margins():
    page = Label('test20x30-2x1', (42, 30), FormFactor.DIE_CUT, (336, 240), (336, 240), 0, pieces=(2, 1, 2))
    assert page.grid == Grid((20, 30), 2, 1, step=(22, 30))
    assert SIZES['niimbot13x35-2x1'].grid == Grid((13, 35), 2, 1, step=(15, 35))
    assert SIZES['niimbot50x30'].grid is None and SIZES['niimbot50x30'].pieces == (1, 1, 0)


def test_pieces_follow_from_the_grid():
    assert PAPER.pieces == (2, 2, 2)
    assert per_sheet(PAPER) == 4
    assert is_paper(PAPER) and not is_paper(SIZES['niimbot15x8-2x3']) and not is_paper(None)


def test_one_label_of_a_sheet_of_paper():
    one = piece(PAPER)
    assert (one.tape_size, one.dots_printable, one.grid, one.pieces) == ((20, 10), (200, 100), None, (1, 1, 0))


def test_labels_of_paper_lie_at_their_margins_and_steps():
    assert spots(PAPER, False) == [(40, 30), (260, 30), (40, 150), (260, 150)]


def test_labels_of_paper_are_filled_as_read_also_lying():
    assert spots(PAPER, True) == spots(PAPER, False)


def test_spots_are_rounded_each_on_its_own():
    # 66.04 mm steps at 300 dpi: the third label is where its mm say, not two rounded steps on
    a4 = Label('test-a4', (210, 297), FormFactor.DIE_CUT, (2480, 3508), (2480, 3508), 0,
               grid=Grid((63.5, 38.1), 3, 7, left=7.25, top=15.15, step=(66.04, 38.1), paper=True))
    scale = 2480 / 210
    for (x, y), (col, row) in zip(spots(a4, False), [(c, r) for r in range(7) for c in range(3)]):
        assert abs(x - (7.25 + col * 66.04) * scale) <= 0.5 and abs(y - (15.15 + row * 38.1) * 3508 / 297) <= 0.5


def test_compose_skips_the_labels_used_before():
    sheet = compose(PAPER, [dotted(200, 100), dotted(200, 100)], skip=1)
    assert sheet.size == (500, 300)
    assert sheet.getpixel((260, 30)) == 0 and sheet.getpixel((40, 150)) == 0
    assert sheet.histogram()[0] == 2


def test_sheet_starts_at_its_skip():
    labels = [Made(dotted(200, 100))]
    labels[0].keep_generated = True
    sheet = Sheet(PAPER, labels, skip=3)
    image = sheet.generate()
    assert image.getpixel((260, 150)) == 0 and image.histogram()[0] == 1
    # The paper lies as it is, also as shown
    assert labels[0].shown.size == (500, 300)


def test_lying_labels_on_paper_turn_but_the_page_does_not():
    page = page_view(PAPER, [dotted(100, 200)])
    assert page.size == (500, 300)
    assert page.getpixel((40, 129)) == 0


def test_cut_lines_are_gray_around_each_label():
    page = with_cut_lines(PAPER, compose(PAPER, [Image.new('L', (200, 100), 0)]))
    assert page.mode == 'L'
    # Around the second label, where nothing is printed
    assert page.getpixel((360, 30)) == CUT_GRAY and page.getpixel((260, 80)) == CUT_GRAY
    assert page.getpixel((360, 31)) == 255 and page.getpixel((259, 80)) == 255
    # Printed black stays black, the corners are round
    assert page.getpixel((100, 30)) == 0
    assert page.getpixel((260, 30)) == 255


def test_cut_lines_on_black_dots_only_are_dotted():
    page = with_cut_lines(PAPER, Image.new('1', (500, 300), 1))
    assert page.mode == '1'
    top = [page.getpixel((x, 30)) for x in range(280, 440)]
    assert top.count(0) == len(top) // CUT_DOTS


def test_page_moved_by_its_offset():
    page = compose(PAPER, [dotted(200, 100)])
    moved = shifted(PAPER, page, (1, -0.5))
    assert moved.getpixel((50, 25)) == 0 and moved.histogram()[0] == 1
    assert shifted(PAPER, page, (0, 0)) is page


def test_fill_state_of_paper_tells_start_and_sheets():
    state = fill_state(PAPER, False, 3, 1, 100, start=2)
    assert state == {'size': [50, 30], 'labels': [[4, 3, 20, 10], [26, 3, 20, 10], [4, 15, 20, 10], [26, 15, 20, 10]],
                     'filled': 4, 'fill': None, 'start': 2, 'sheets': 1}
    assert fill_state(PAPER, True, 4, 1, 100, start=3) | {'labels': None} == {
        'size': [50, 30], 'labels': None, 'filled': 2, 'fill': 6, 'start': 3, 'sheets': 2}


def test_fill_state_where_no_number_of_copies_fills_the_page():
    # Two labels a copy from the second label on leave one free on every page
    assert fill_state(PAPER, False, 1, 2, 100, start=2)['fill'] is None
