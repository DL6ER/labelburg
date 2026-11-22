"""Label sizes, as each driver describes those of its printers."""

from dataclasses import dataclass
from enum import IntEnum


def whole(mm: float) -> int | float:
    """mm as a whole number where it is one, e.g. 15 for 15.0."""
    return int(mm) if mm == int(mm) else mm


class FormFactor(IntEnum):
    DIE_CUT = 1
    ENDLESS = 2
    ROUND_DIE_CUT = 3
    # Endless P-touch tape
    PTOUCH_ENDLESS = 4


class Color(IntEnum):
    BLACK_WHITE = 0
    # Black and red on white, e.g. DK-22251
    BLACK_RED_WHITE = 1


@dataclass(frozen=True)
class Grid:
    """Labels laid out on a page, in mm: the size of each, width and length,
    how many across (columns) and along (rows), the top left of the first
    one, the step to the next one across and along, and the radius of their
    corners. paper: a sheet of labels an office printer prints as a page."""
    size: tuple[float, float]
    columns: int
    rows: int
    left: float = 0
    top: float = 0
    step: tuple[float, float] = (0, 0)
    corner: float = 0
    paper: bool = False


@dataclass(frozen=True)
class Label:
    """A label size. tape_size is (width, length) in mm, the length 0 for
    endless tape. dots_total and dots_printable are (across, along) in dots of
    the printer, the length 0 for endless tape. offset_r moves the print this
    many dots off the right edge, so that it is centered, feed_margin is fed
    before and after it. models lists the printers it is made for, empty for
    all printers of its driver. grid lays out the labels of a page of
    several, e.g. of a Niimbot roll, half cut apart, or a sheet of labels.
    pieces follow from it: how many across (lanes), how many along (rows),
    and the gap between lanes in mm; given without a grid, they make one
    without margins. gap is the paper in mm between die-cut labels on
    their roll."""
    identifier: str
    tape_size: tuple[int, int]
    form_factor: FormFactor
    dots_total: tuple[int, int]
    dots_printable: tuple[int, int]
    offset_r: int
    feed_margin: int = 0
    models: tuple[str, ...] = ()
    color: Color = Color.BLACK_WHITE
    pieces: tuple[int, int, float] | None = None
    gap: float = 0
    grid: Grid | None = None

    def __post_init__(self):
        grid, pieces = self.grid, self.pieces
        if grid is None and pieces and pieces[0] * pieces[1] > 1:
            across, along, gap = pieces
            size = (whole((self.tape_size[0] - gap * (across - 1)) / across), whole(self.tape_size[1] / along))
            grid = Grid(size, across, along, step=(whole(size[0] + gap), size[1]))
            object.__setattr__(self, 'grid', grid)
        if grid is not None:
            pieces = (grid.columns, grid.rows, whole(round(grid.step[0] - grid.size[0], 3)) if grid.columns > 1 else 0)
        object.__setattr__(self, 'pieces', pieces if grid is not None else (1, 1, 0))

    @property
    def name(self) -> str:
        if self.form_factor == FormFactor.DIE_CUT:
            name = '{0}mm x {1}mm die-cut'.format(*self.tape_size)
        elif self.form_factor == FormFactor.ROUND_DIE_CUT:
            name = f'{self.tape_size[0]}mm round die-cut'
        else:
            name = f'{self.tape_size[0]}mm endless'
        return name + (' (black/red/white)' if self.color == Color.BLACK_RED_WHITE else '')
