"""Rolls with several labels to a page: one label of a page, where each
label goes and the page put together of them."""
from PIL import Image

from app.labeldesigner.label import LabelContent
from app.labeldesigner.label_names import SIZES
from app.labeldesigner.sheets import Sheet, compose, lying, page_view, per_sheet, piece, spots


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
