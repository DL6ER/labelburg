"""Pages of several labels: rolls with several labels to a page, e.g. of a
Niimbot B1, and sheets of labels an office printer prints. The page is
printed as a whole, its labels lie as its grid (drivers/labels.Grid) lays
them out. A label is designed and made for one label of the page, the
copies fill the pages one after the other."""

from dataclasses import replace

from PIL import Image, ImageChops, ImageDraw

from .drivers.labels import Label, whole

# The gray of the lines plain paper is cut along, and of a page of black
# dots only, every how many dots of them are printed
CUT_GRAY = 190
CUT_DOTS = 4


def per_sheet(label: Label) -> int:
    across, along, _ = label.pieces
    return across * along


def is_paper(label: Label | None) -> bool:
    """Whether a label size is a sheet of labels an office printer prints."""
    return bool(label and label.grid and label.grid.paper)


def _scale(label: Label) -> tuple[float, float]:
    """Dots per mm of the page across and along."""
    return label.dots_total[0] / label.tape_size[0], label.dots_total[1] / label.tape_size[1]


def piece(label: Label) -> Label:
    """One label of a page as a label size of its own, the label size
    itself where it has no grid."""
    grid = label.grid
    if grid is None:
        return label
    across, along = _scale(label)
    dots = (round(grid.size[0] * across), round(grid.size[1] * along))
    return replace(label, tape_size=tuple(whole(mm) for mm in grid.size), dots_total=dots, dots_printable=dots,
                   grid=None, pieces=None)


def lying(label: Label, image: Image.Image) -> bool:
    """Whether a label is turned by 90 degrees to stand in its lane, as the
    drivers turn a label of that size printed alone."""
    return image.width != piece(label).dots_printable[0]


def _cells(label: Label) -> list[tuple[int, int]]:
    """The top left of each label of the page in dots, row by row."""
    grid = label.grid
    if grid is None:
        return [(0, 0)]
    across, along = _scale(label)
    return [(round((grid.left + col * grid.step[0]) * across), round((grid.top + row * grid.step[1]) * along))
            for row in range(grid.rows) for col in range(grid.columns)]


def spots(label: Label, is_lying: bool) -> list[tuple[int, int]]:
    """The top left of each label on the page in dots, across and along
    from the end printed first, in the order the copies fill them: as read
    with the label upright, lying labels with the page turned clockwise. A
    sheet of paper is read as it lies, whichever way its labels are turned."""
    cells = _cells(label)
    if is_paper(label):
        return cells
    return sorted(cells, key=(lambda spot: (spot[0], -spot[1])) if is_lying else (lambda spot: (spot[1], spot[0])))


def compose(label: Label, images: list[Image.Image], skip: int = 0) -> Image.Image:
    """The page with the images of its labels, at most one page of them,
    from its label skip + 1 on, the labels left over white. Each image is
    turned into its lane on its own, the order follows the first."""
    sheet = Image.new(images[0].mode, label.dots_printable, 'white')
    for spot, image in zip(spots(label, lying(label, images[0]))[skip:], images):
        sheet.paste(image.rotate(90, expand=True) if lying(label, image) else image, spot)
    return sheet


def page_view(label: Label, images: list[Image.Image], skip: int = 0) -> Image.Image:
    """The page as the label designer shows it: as the labels are seen
    upright, the first one top left; a sheet of paper as it lies."""
    sheet = compose(label, images, skip)
    return sheet.rotate(-90, expand=True) if lying(label, images[0]) and not is_paper(label) else sheet


def with_cut_lines(label: Label, page: Image.Image) -> Image.Image:
    """The page with a thin line around each label to cut plain paper along,
    light gray over what is printed there, on a page of black dots only
    dotted."""
    across, along = _scale(label)
    size = piece(label).dots_printable
    radius = round(label.grid.corner * across) if label.grid else 0
    lines = Image.new('L', page.size, 255)
    draw = ImageDraw.Draw(lines)
    for x, y in _cells(label):
        draw.rounded_rectangle((x, y, x + size[0] - 1, y + size[1] - 1), radius, outline=CUT_GRAY)
    if page.mode == '1':
        dotted = ImageChops.lighter(lines.point(lambda value: 0 if value < 255 else 255), _dots(page.size))
        return ImageChops.darker(page.convert('L'), dotted).convert('1', dither=Image.Dither.NONE)
    return ImageChops.darker(page.convert('L'), lines)


def _dots(size: tuple[int, int]) -> Image.Image:
    """White, black where x + y is a multiple of CUT_DOTS: a line drawn
    through it keeps every CUT_DOTS-th dot."""
    rows = [bytes(0 if (x + y) % CUT_DOTS == 0 else 255 for x in range(size[0])) for y in range(CUT_DOTS)]
    return Image.frombytes('L', size, b''.join(rows[y % CUT_DOTS] for y in range(size[1])))


def shifted(label: Label, page: Image.Image, offset: tuple[float, float]) -> Image.Image:
    """The page moved offset mm right and down, white where it moved from."""
    across, along = _scale(label)
    dx, dy = round(offset[0] * across), round(offset[1] * along)
    if not dx and not dy:
        return page
    moved = Image.new(page.mode, page.size, 'white')
    moved.paste(page, (dx, dy))
    return moved


class Sheet:
    """Labels of a page as the drivers print them, like a single label,
    from its label skip + 1 on. It is turned as a whole, so that the labels
    keep their order."""

    def __init__(self, label: Label, labels: list, skip: int = 0):
        self.label = label
        self.labels = labels
        self.skip = skip
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
        images = [made.generate() for made in self.labels]
        image = compose(self.label, images, self.skip)
        if self.turned:
            image = image.rotate(180)
        # The print routes return the image of the first label, as printed,
        # the queue shows the page as the label designer does
        if getattr(self.labels[0], 'keep_generated', False):
            self.labels[0].generated = image
            self.labels[0].shown = page_view(self.label, images, self.skip)
        return image


def _mm(value: float) -> int | float:
    return whole(round(value, 1))


def _boxes(label: Label, is_lying: bool) -> tuple[tuple, list[tuple]]:
    """The page and its labels in mm as the label designer draws them, in
    the order they are filled, see fill_state()."""
    width, length = piece(label).tape_size
    sheet = label.tape_size
    grid = label.grid
    if is_paper(label):
        return sheet, [(grid.left + col * grid.step[0], grid.top + row * grid.step[1], width, length)
                       for row in range(grid.rows) for col in range(grid.columns)]
    scale = sheet[0] / label.dots_total[0]
    boxes = [(x * scale, y * scale, width, length) for x, y in spots(label, is_lying)]
    if is_lying:
        # Turned clockwise, the end printed first is on the right
        boxes = [(sheet[1] - y - h, x, h, w) for x, y, w, h in boxes]
        sheet = sheet[::-1]
    return sheet, boxes


def fill_state(label: Label, is_lying: bool, copies: int, per_copy: int, limit: int, start: int = 1) -> dict | None:
    """How the copies fill the last page, for the label designer to draw:
    the page and its labels in mm as the label is seen upright, in the
    order they are filled, how many of them are filled, and the copies that
    fill it, None where it is full or that would be more than limit labels.
    The copies start at label start of the first page; of a sheet of paper
    also where they start and on how many sheets they are. None where a
    label size has one label to a page."""
    per = per_sheet(label)
    if per == 1:
        return None
    sheet, boxes = _boxes(label, is_lying)
    before = start - 1
    filled = (before + copies * per_copy - 1) % per + 1
    fill = None
    if filled < per:
        # The page is full after a whole number of copies, or never is
        fill = next((more for more in range(copies + 1, copies + per + 1) if (before + more * per_copy) % per == 0), None)
        if fill is not None and fill * per_copy > limit:
            fill = None
    state = {'size': [_mm(v) for v in sheet], 'labels': [[_mm(v) for v in box] for box in boxes], 'filled': filled, 'fill': fill}
    if is_paper(label):
        state.update(start=start, sheets=(before + copies * per_copy - 1) // per + 1)
    return state
