"""The test sheet of an office printer, printed on a sheet of labels: it
tells how far the printer prints off the labels. A scale across crosses
the cut right of the first label, a scale down the cut below it, each in
mm from -3 to 3 with 0 where the cut is meant to be; on a sheet of one
column the scale across crosses the left edge of the labels, on one of one
row the scale down their top edge. Where the cut crosses a scale, the
number there is how far the labels are to be moved. A line of 100 mm tells
whether the printer prints the page as large as it is."""

import functools
import io

from PIL import Image, ImageDraw, ImageFont

from ..labels import Label

KIND = 'sheet'
KIND_MESSAGE = 'kind must be sheet'
# mm each scale reaches either way, between its ticks and to the cut of a
# scale of half its length
REACH = 3
TICK = 0.5
LINE = 100


def _scale(label: Label) -> tuple[float, float]:
    return label.dots_total[0] / label.tape_size[0], label.dots_total[1] / label.tape_size[1]


def cuts(label: Label) -> tuple[float, float, float, float]:
    """Where the scales go, in mm: the cut the scale across crosses and the
    middle of the first row it runs at, the middle of the first column the
    scale down runs at and the cut it crosses."""
    grid = label.grid
    width, length = grid.size
    across = grid.left + width if grid.columns > 1 else grid.left
    down = grid.top + length if grid.rows > 1 else grid.top
    return across, grid.top + length / 2, grid.left + width / 2, down


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _ruler(draw: ImageDraw.ImageDraw, label: Label, at: float, line: float, turned: bool):
    """A scale from -REACH to REACH mm with 0 at mm at, running across at
    line, or down where turned. The numbers of whole mm take turns on
    either side of it, so that they keep apart."""
    across, along = _scale(label)
    per_mm = along if turned else across
    font = _font(round(1.6 * per_mm))

    def point(v: float, off: float) -> tuple[float, float]:
        # v along the scale, off mm beside it
        return ((line + off) * across, (at + v) * along) if turned else ((at + v) * across, (line + off) * along)
    draw.line((*point(-REACH, 0), *point(REACH, 0)), fill=0, width=1)
    for k in range(-round(REACH / TICK), round(REACH / TICK) + 1):
        v = k * TICK
        whole = v == int(v)
        side = 1 if int(v) % 2 else -1
        reach = 2.4 if v == 0 else 1.6 if whole else 0.8
        draw.line((*point(v, 0), *point(v, side * reach if whole else -reach)), fill=0, width=3 if v == 0 else 1)
        if whole:
            draw.text(point(v, side * (reach + 1.3)), f'{v:+g}' if v else '0', font=font, fill=0, anchor='mm')


def test_sheet(label: Label) -> Image.Image:
    """The test sheet for a sheet of labels, gray, as large as its page."""
    page = Image.new('L', label.dots_printable, 255)
    draw = ImageDraw.Draw(page)
    across, row, column, down = cuts(label)
    _ruler(draw, label, across, row, turned=False)
    _ruler(draw, label, down, column, turned=True)
    per_mm = _scale(label)[0]
    middle = (label.tape_size[0] / 2, label.tape_size[1] / 2)
    start, end = round((middle[0] - LINE / 2) * per_mm), round((middle[0] + LINE / 2) * per_mm)
    y = round(middle[1] * _scale(label)[1])
    draw.line((start, y, end - 1, y), fill=0, width=3)
    for x in (start, end - 1):
        draw.line((x, y - round(per_mm), x, y + round(per_mm)), fill=0, width=3)
    draw.text(((start + end) / 2, y - round(2 * per_mm)), f'This line is {LINE} mm', font=_font(round(3 * per_mm)), fill=0, anchor='ms')
    return page


@functools.cache
def example(label: Label) -> bytes:
    """A test sheet printed 1 mm too far right and 0.5 mm too far down, as
    a PNG for the label designer: where each scale crosses its cut, marked,
    the labels light on their backing, their cuts drawn."""
    across, along = _scale(label)
    shift = (round(1 * across), round(0.5 * along))
    printed = Image.new('L', label.dots_printable, 255)
    printed.paste(test_sheet(label), shift)
    grid = label.grid
    backing = Image.new('RGB', label.dots_printable, (214, 214, 206))
    paper = ImageDraw.Draw(backing)
    for row in range(grid.rows):
        for col in range(grid.columns):
            x, y = (grid.left + col * grid.step[0]) * across, (grid.top + row * grid.step[1]) * along
            paper.rounded_rectangle((x, y, x + grid.size[0] * across - 1, y + grid.size[1] * along - 1),
                                    round(grid.corner * across), fill=(252, 252, 248), outline=(150, 150, 140), width=3)
    backing.paste((20, 20, 20), (0, 0), printed.point(lambda v: 255 - v))
    cut_across, row_mm, column_mm, cut_down = cuts(label)
    marks = ImageDraw.Draw(backing)
    for x, y in ((cut_across * across, (row_mm + 0.5) * along), ((column_mm + 1) * across, cut_down * along)):
        marks.ellipse((x - 24, y - 24, x + 24, y + 24), outline=(220, 30, 30), width=5)
    # Both scales and the cuts around them
    reach = (REACH + 3) * across
    parts = [backing.crop((round(cut_across * across - reach), round(row_mm * along - reach),
                           round(cut_across * across + reach), round(row_mm * along + reach))),
             backing.crop((round(column_mm * across - reach), round(cut_down * along - reach),
                           round(column_mm * across + reach), round(cut_down * along + reach)))]
    gap = 16
    picture = Image.new('RGB', (parts[0].width * 2 + gap, parts[0].height), (255, 255, 255))
    picture.paste(parts[0], (0, 0))
    picture.paste(parts[1], (parts[0].width + gap, 0))
    picture = picture.resize((picture.width * 3 // 2, picture.height * 3 // 2), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    picture.save(out, 'PNG')
    return out.getvalue()
