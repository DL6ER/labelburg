"""Die-cut labels of printers that print a label as an image as large as it
is, e.g. those on ZPL: their common sizes in the dots of a printer, the
labels a printer measured, and how a label is turned for it. The printer
finds the labels on the roll by the gaps between them or by black marks on
their back, see SENSINGS."""

from PIL import Image

from .labels import FormFactor, Label

# Width x length in mm of common labels, metric ones and inch ones, e.g.
# 105 x 148 mm (A6) and 102 x 152 mm (4 x 6 in) shipping labels. The printer
# does not tell which are loaded.
SIZES = ((105, 148),
         (102, 152), (102, 102), (102, 76), (102, 51), (102, 25),
         (100, 150), (100, 100), (100, 70), (100, 50), (100, 30),
         (80, 50), (76, 51), (76, 25), (60, 40), (58, 40), (57, 32),
         (51, 25), (50, 30), (50, 25), (40, 30), (40, 20), (38, 25), (30, 20))
# mm between the labels of most rolls
GAP = 3
# How the printer finds the start of each label: by the gaps between the
# labels, by black marks on their back, or not at all on a continuous roll
SENSINGS = ('gap', 'mark', 'endless')
# The roll of each, as the label designer offers and names it
ROLLS = {'gap': 'Labels with gaps', 'mark': 'Black marks on the back', 'endless': 'Continuous roll'}
# The roll a status names, of labels with gaps or where nothing is set
MEDIA_TYPE = 'Die-cut labels'
# A measured length further off than this from all sizes is not taken
LENGTH_TOLERANCE = 2
# Printers of 203 dpi, which some name 200 dpi, have 8 dots per mm
EIGHT_PER_MM = (200, 203)
# The paper of each label size labels() made, by its name, so that a label
# made for one driver's size prints on the same paper of another's
_PAPERS: dict[str, tuple[int, int]] = {}


def dots(mm: float, dpi: int) -> int:
    """mm in dots of a printer of dpi, see EIGHT_PER_MM."""
    return round(mm * 8) if dpi in EIGHT_PER_MM else round(mm * dpi / 25.4)


def millimeters(length: int, dpi: int) -> float:
    """A length in dots of a printer of dpi in mm, see dots()."""
    return length / 8 if dpi in EIGHT_PER_MM else length * 25.4 / dpi


def labels(prefix: str, dpi: int, head_dots: int, models: tuple, sizes: tuple) -> tuple[Label, ...]:
    """The label sizes of the sizes for printers of the models, of dpi and
    with a printhead head_dots wide, e.g. <prefix>102x152. Of a wider label
    the rest is not printed."""
    def label(width, length):
        total = (dots(width, dpi), dots(length, dpi))
        return Label(f'{prefix}{width}x{length}', (width, length), FormFactor.DIE_CUT, total,
                     (min(total[0], head_dots), total[1]), 0, models=tuple(models), gap=GAP)
    made = tuple(label(width, length) for width, length in sizes)
    _PAPERS.update((size.identifier, size.tape_size) for size in made)
    return made


def label_for(label_size: str, labels: tuple, model: str) -> str:
    """The label size of labels that a printer of the model prints a label
    made for label_size on: the same paper, also of another driver's die-cut
    labels, e.g. tspl102x152 for zpl102x152, else label_size."""
    paper = _PAPERS.get(label_size)
    return next((label.identifier for label in labels if label.tape_size == paper and model in label.models), label_size)


def measured(length: int, dpi: int = 203) -> int | None:
    """The length in mm of the labels of a length the printer measured in
    dots, of dpi, e.g. 235 dots at 203 dpi 30 mm, None if no labels have
    about that length or it is as near to two lengths."""
    mm = millimeters(length, dpi)
    nearest = min(abs(size[1] - mm) for size in SIZES)
    lengths = {size[1] for size in SIZES if abs(size[1] - mm) == nearest}
    return lengths.pop() if nearest <= LENGTH_TOLERANCE and len(lengths) == 1 else None


def loaded(length: int, dpi: int = 203) -> tuple[int, int] | None:
    """Width and length in mm of the labels of a length the printer measured
    in dots, of dpi, e.g. 1214 dots at 203 dpi (152 mm) 102 x 152 mm. None
    if labels of another width have that length too, the printer does not
    tell their width."""
    mm = measured(length, dpi)
    sizes = [size for size in SIZES if size[1] == mm]
    return sizes[0] if len(sizes) == 1 else None


def as_set(state: dict, sensing: str | None) -> dict:
    """The status of a printer told how it finds the labels, see SENSINGS:
    its roll named as set, MEDIA_TYPE for labels with gaps and where nothing
    is set. On a continuous roll it measured no labels: the length it tells,
    and a width that comes with it, are of the labels it was sent last. A
    width told without a length is the one of the roll."""
    state = dict(state, media_type=ROLLS[sensing] if sensing in ('mark', 'endless') else MEDIA_TYPE)
    if sensing == 'endless' and state.get('media_length'):
        state.update(media_width=None, media_length=0)
    return state


def oriented(img: Image.Image, across: int, top_first: bool) -> Image.Image:
    """A label as the printer gets it, across dots wide: its top leaves the
    printer first, of one longer than wide, which the label designer draws
    lying, its end, as on the other printers. top_first: whether the first
    row of an image leaves the printer first, else its last one."""
    if img.width != across:
        return img.rotate(90 if top_first else -90, expand=True)
    return img if top_first else img.rotate(180, expand=True)
