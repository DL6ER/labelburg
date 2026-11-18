"""The calibration labels of the B1: pages as wide as its printhead with the
label in the middle, as the driver then moves them.

On rolls narrow enough for the printhead to reach past both edges, a
triangle at each side runs onto the label: its straight side lies outside
the label and is never seen, its slanted side crosses the edge where the
print sits. The number next to that point tells how far. On wider rolls a
frame inside the printhead's edges shows the white strips beside it. Lines
1 mm inside the top and bottom show the white strips there."""

import io

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import protocol

MM = 8
# Kept clear of the rounded ends of a label
CORNER = 2
# The printhead has to reach this far past each edge of a narrow roll
REACH = 3


def is_narrow(width_mm: float, head: int = protocol.B1_HEAD) -> bool:
    return width_mm * MM <= head - 2 * REACH * MM


def _reach(length_mm: float) -> float:
    """mm the triangles read each way: 3 on labels long enough for it."""
    return 3 if length_mm - 2 * CORNER >= 20 else 2


def _rows(length_mm: float) -> tuple[int, int]:
    """The first and the last row of the triangles."""
    return CORNER * MM, round((length_mm - CORNER) * MM) - 1


def row_of(v: float, length_mm: float) -> int:
    """The row where the triangles read v mm."""
    r, (y0, y1) = _reach(length_mm), _rows(length_mm)
    return round(y0 + (v + r) / (2 * r) * (y1 - y0))


def _title(img: Image.Image, x: int, top: int, bottom: int):
    """CALIBRATION turned, in the middle of x between top and bottom."""
    word = Image.new('L', (bottom - top, 22), 255)
    ImageDraw.Draw(word).text(((bottom - top) // 2, 11), 'CALIBRATION', font=ImageFont.load_default(size=16), fill=0, anchor='mm')
    word = word.rotate(90, expand=True)
    img.paste(word, (x - word.width // 2, top), ImageOps.invert(word))


def narrow(width_mm: float, length_mm: float, head: int = protocol.B1_HEAD) -> Image.Image:
    """The calibration label of a narrow roll, black on white."""
    height, width = round(length_mm * MM), round(width_mm * MM)
    left = (head - width) // 2
    r, (y0, y1) = _reach(length_mm), _rows(length_mm)
    img = Image.new('L', (head, height), 255)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=11)
    # The outermost dots of the label, left and right
    for side, edge in ((-1, left), (1, left + width - 1)):
        def slant(y):
            return edge - side * (-r + 2 * r * (y - y0) / (y1 - y0)) * MM
        outer = edge + side * r * MM
        d.polygon([(outer, y0), (outer, y1), (slant(y1), y1)], fill=0)
        for k in range(round(8 * r) + 1):
            v = -r + k / 4
            y = row_of(v, length_mm)
            # Whole, half and quarter millimeters
            reach = 1.0 if k % 4 == 0 else 0.7 if k % 2 == 0 else 0.4
            d.line((outer, y, slant(y), y), fill=255, width=1)
            d.line((slant(y), y, edge - side * (r + reach) * MM, y), fill=0, width=1)
            if k % 2 == 0 and abs(v) < r:
                d.text((edge - side * (r + 1.2) * MM, y), f'{v:+g}' if v else '0', font=font, fill=0,
                       anchor='lm' if side < 0 else 'rm')
    x_a, x_b = left + (r + 4.5) * MM, left + width - (r + 4.5) * MM
    for y in (MM, height - 1 - MM):
        d.line((x_a, y, x_b, y), fill=0, width=2)
    _title(img, left + width // 2, CORNER * MM, height - CORNER * MM)
    return img


def wide(width_mm: float, length_mm: float, head: int = protocol.B1_HEAD) -> Image.Image:
    """The calibration label of a roll wider than that: a frame 1 mm inside
    the printhead's edges and the label's top and bottom."""
    height = round(length_mm * MM)
    img = Image.new('L', (head, height), 255)
    ImageDraw.Draw(img).rectangle((MM, MM, head - 1 - MM, height - 1 - MM), outline=0, width=2)
    _title(img, head // 2, CORNER * MM, height - CORNER * MM)
    return img


def example(kind: str) -> bytes:
    """A calibration label printed 1 mm too far right, as a PNG for the
    label designer: on a 40 x 30 mm roll with the reading points marked, or
    on a 50 x 30 mm roll with its uneven white strips."""
    if kind not in ('narrow', 'wide'):
        raise ValueError('kind must be narrow or wide')
    width_mm, length_mm, shift, zoom, pad = (40 if kind == 'narrow' else 50), 30, MM, 3, 5 * MM
    page = narrow(width_mm, length_mm) if kind == 'narrow' else wide(width_mm, length_mm)
    width, height = width_mm * MM, length_mm * MM
    left = (page.width - width) // 2
    # The backing around the label, which takes no print
    picture = Image.new('RGB', (width + 2 * pad, height + 2 * pad), (214, 214, 206))
    picture.paste((252, 252, 248), (pad, pad, pad + width, pad + height))
    for y in range(height):
        for x in range(width):
            printed = left + x - shift
            if 0 <= printed < page.width and page.getpixel((printed, y)) < 128:
                picture.putpixel((pad + x, pad + y), (20, 20, 20))
    picture = picture.resize((picture.width * zoom, picture.height * zoom), Image.Resampling.NEAREST)
    if kind == 'narrow':
        d = ImageDraw.Draw(picture)
        for x, v in ((pad, -1), (pad + width, 1)):
            cx, cy = x * zoom, (pad + row_of(v, length_mm)) * zoom
            d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), outline=(220, 30, 30), width=4)
    out = io.BytesIO()
    picture.save(out, 'PNG')
    return out.getvalue()
