"""Rolls with several labels to a page, e.g. of a Niimbot B1: the page is a
sheet of labels the printer prints as a whole. A label is designed and made
for one label of the sheet, the copies fill the sheets one after the other."""

from dataclasses import replace

from PIL import Image

from .drivers.labels import Label


def per_sheet(label: Label) -> int:
    across, along, _ = label.pieces
    return across * along


def _whole(mm: float) -> int | float:
    return int(mm) if mm == int(mm) else mm


def _gap_dots(label: Label) -> int:
    return round(label.pieces[2] * label.dots_total[0] / label.tape_size[0])


def piece(label: Label) -> Label:
    """One label of a sheet as a label size of its own, the label size
    itself where it has one label to a page."""
    across, along, gap = label.pieces
    if across * along == 1:
        return label
    gap_dots = _gap_dots(label)

    def one(size: tuple) -> tuple[int, int]:
        return (size[0] - gap_dots * (across - 1)) // across, size[1] // along
    tape = (_whole((label.tape_size[0] - gap * (across - 1)) / across), _whole(label.tape_size[1] / along))
    return replace(label, tape_size=tape, dots_total=one(label.dots_total), dots_printable=one(label.dots_printable), pieces=(1, 1, 0))


def lying(label: Label, image: Image.Image) -> bool:
    """Whether a label is turned by 90 degrees to stand in its lane, as the
    drivers turn a label of that size printed alone."""
    return image.width != piece(label).dots_printable[0]


def spots(label: Label, lying: bool) -> list[tuple[int, int]]:
    """The top left of each label on the sheet in dots, across and along
    from the end printed first, in the order the copies fill them: as read
    with the label upright, lying labels with the sheet turned clockwise."""
    across, along, _ = label.pieces
    width, length = piece(label).dots_printable
    step = width + _gap_dots(label)
    cells = [(col * step, row * length) for row in range(along) for col in range(across)]
    return sorted(cells, key=(lambda spot: (spot[0], -spot[1])) if lying else (lambda spot: (spot[1], spot[0])))


def compose(label: Label, images: list[Image.Image]) -> Image.Image:
    """The sheet with the images of its labels, at most one sheet of them,
    the labels left over white. Each image is turned into its lane on its
    own, the order follows the first."""
    sheet = Image.new(images[0].mode, label.dots_printable, 'white')
    for spot, image in zip(spots(label, lying(label, images[0])), images):
        sheet.paste(image.rotate(90, expand=True) if lying(label, image) else image, spot)
    return sheet


class Sheet:
    """Labels of a sheet as the drivers print them, like a single label. It
    is turned as a whole, so that the labels keep their order."""

    def __init__(self, label: Label, labels: list):
        self.label = label
        self.labels = labels
        self.turned = labels[0].turned
        for made in labels:
            made.turned = False

    @property
    def label_content(self):
        return self.labels[0].label_content

    @property
    def in_color(self) -> bool:
        return self.labels[0].in_color

    def generate(self) -> Image.Image:
        image = compose(self.label, [made.generate() for made in self.labels])
        if self.turned:
            image = image.rotate(180)
        # The print routes return the image of the first label, as printed
        if getattr(self.labels[0], 'keep_generated', False):
            self.labels[0].generated = image
        return image


def _mm(value: float) -> int | float:
    return _whole(round(value, 1))


def fill_state(label: Label, lying: bool, copies: int, per_copy: int, limit: int) -> dict | None:
    """How the copies fill the last sheet, for the label designer to draw:
    the sheet and its labels in mm as the label is seen upright, in the
    order they are filled, how many of them are filled, and the copies that
    fill it, None where it is full or that would be more than limit labels.
    None where a label size has one label to a page."""
    per = per_sheet(label)
    if per == 1:
        return None
    width, length = piece(label).tape_size
    sheet = label.tape_size
    scale = sheet[0] / label.dots_total[0]
    boxes = [(x * scale, y * scale, width, length) for x, y in spots(label, lying)]
    if lying:
        # Turned clockwise, the end printed first is on the right
        boxes = [(sheet[1] - y - h, x, h, w) for x, y, w, h in boxes]
        sheet = sheet[::-1]
    filled = (copies * per_copy - 1) % per + 1
    fill = None
    if filled < per:
        fill = copies + 1
        while fill * per_copy % per:
            fill += 1
        if fill * per_copy > limit:
            fill = None
    return {'size': [_mm(v) for v in sheet], 'labels': [[_mm(v) for v in box] for box in boxes], 'filled': filled, 'fill': fill}
