"""Draw the fastener symbols of {{fastener:<name>}} as SVGs in
tools/symbols/fasteners, from which tools/symbols_font.py builds the font.

The shapes are those of gflabel by Nicholas Devenish
(https://github.com/ndevenish/gflabel, BSD-3-Clause, see the LICENSE there),
drawn with skia-pathops instead of build123d:

    pip install -r requirements-symbols.txt
    python3 tools/fastener_svgs.py
"""
import math
import os

import pathops
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'symbols', 'fasteners')

# Drawn 20 high, as the viewBox; the font leaves room around them as
# Material Design Icons have
H = 20
BOX = H
K = 0.5522847498  # control points of a circle of four cubic curves


def polygon(points):
    path = pathops.Path()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.close()
    return path


def circle(r, cx=0.0, cy=0.0):
    path = pathops.Path()
    path.moveTo(cx + r, cy)
    for (x0, y0), (x1, y1) in (((1, 0), (0, 1)), ((0, 1), (-1, 0)), ((-1, 0), (0, -1)), ((0, -1), (1, 0))):
        path.cubicTo(cx + r * (x0 + K * x1), cy + r * (y0 + K * y1), cx + r * (x1 + K * x0), cy + r * (y1 + K * y0), cx + r * x1, cy + r * y1)
    path.close()
    return path


def ellipse(rx, ry, cx=0.0, cy=0.0):
    path = pathops.Path()
    circle(1).draw(TransformPen(path.getPen(), (rx, 0, 0, ry, cx, cy)))
    return path


def rotated(points, degrees, cx=0.0, cy=0.0):
    a = math.radians(degrees)
    return [(cx + x * math.cos(a) - y * math.sin(a), cy + x * math.sin(a) + y * math.cos(a)) for x, y in points]


def rectangle(w, h, cx=0.0, cy=0.0, degrees=0):
    return polygon(rotated([(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)], degrees, cx, cy))


def regular_polygon(r, sides, degrees=0):
    return polygon([(r * math.cos(math.radians(degrees + 360 * i / sides)), r * math.sin(math.radians(degrees + 360 * i / sides))) for i in range(sides)])


def slot(separation, width, degrees):
    """A rounded slot: two half circles separation apart."""
    return union(rectangle(separation, width, degrees=degrees),
                 *(circle(width / 2, *rotated([(x, 0)], degrees)[0]) for x in (-separation / 2, separation / 2)))


def union(*paths):
    result = pathops.Path()
    for path in paths:
        result = pathops.op(result, path, pathops.PathOp.UNION)
    return result


def minus(a, *b):
    for path in b:
        a = pathops.op(a, path, pathops.PathOp.DIFFERENCE)
    return a


def scaled(path, s):
    result = pathops.Path()
    path.draw(TransformPen(result.getPen(), (s, 0, 0, s, 0, 0)))
    return result


def drive(shape, cut_radius):
    """A drive of gflabel's drive_shape, 1 wide; cut_radius is how far a
    slot reaches, to cut through the head. Returns the cut and the pin of a
    security drive."""
    if shape == 'phillips':
        return union(rectangle(1, .2), rectangle(.2, 1), rectangle(.4, .4, degrees=45))
    if shape == 'pozidrive':
        return union(rectangle(1, .2), rectangle(.2, 1), rectangle(.4, .4, degrees=45),
                     rectangle(1, .1, degrees=45), rectangle(1, .1, degrees=-45))
    if shape == 'slot':
        return rectangle(cut_radius, .2)
    if shape == 'hex':
        return regular_polygon(.5, 6)
    if shape == 'cross':
        return union(rectangle(1, .2), rectangle(.2, 1))
    if shape == 'phillips-slot':
        return union(drive('phillips', cut_radius), rectangle(cut_radius, .2))
    if shape == 'square':
        return rectangle(.6, .6, degrees=45)
    if shape == 'triangle':
        # Equilateral, sides .95, its box centered
        h = .95 * math.sqrt(3) / 2
        return polygon([(-.475, -h / 2), (.475, -h / 2), (0, h / 2)])
    if shape == 'torx':
        lobes = union(circle(.37), *(slot(.82, .19, 120 * i) for i in range(3)))
        return minus(lobes, *(circle(.11, *rotated([(.41, 0)], 30 + 60 * i)[0]) for i in range(6)))
    raise ValueError(shape)


def head(*drives, outer=None, radius=.7 * H / 2):
    """A screw head from above: a circle, or a hexagon, with its drives."""
    outer = outer or circle(H / 2)
    cut = union(*(scaled(drive(d, (H / 2) / radius), 2 * radius) for d in drives if d != 'security'))
    if 'security' in drives:
        cut = minus(cut, circle(.1 * 2 * radius))
    return minus(outer, cut)


def bolt(headshape, tapping=False, flanged=False, slotted=False, length=2 * H):
    """A bolt from the side, as gflabel's {bolt} of a fixed length: head to
    the left, centered."""
    lw = H / 2.25
    hw = length / 2
    head_h = H / 2 - (lw / 3 if flanged else 0)
    tip = [(hw - lw / 2, lw / 2), (hw, 0), (hw - lw / 2, -lw / 2)] if tapping else [(hw, lw / 2), (hw, -lw / 2)]
    shank = polygon([(-hw + lw, lw / 2), *tip, (-hw + lw, -lw / 2)])
    if headshape == 'pan':
        r = lw / 4
        head_ = union(rectangle(lw - r, 2 * head_h, -hw + r + (lw - r) / 2),
                      rectangle(r, 2 * head_h - 2 * r, -hw + r / 2),
                      circle(r, -hw + r, head_h - r), circle(r, -hw + r, -head_h + r))
    elif headshape == 'socket':
        head_ = rectangle(lw, 2 * head_h, -hw + lw / 2)
    elif headshape == 'wafer':
        head_ = rectangle(lw / 3, 2 * head_h, -hw + lw * 5 / 6)
    elif headshape == 'countersunk':
        head_ = polygon([(-hw, -head_h), (-hw, head_h), (-hw + lw, lw / 2), (-hw + lw, -lw / 2)])
    elif headshape == 'round':
        head_ = minus(ellipse(lw, head_h, -hw + lw), rectangle(lw, 2 * head_h, -hw + lw * 1.5))
    else:
        raise ValueError(headshape)
    shape = union(head_, shank)
    if slotted:
        shape = minus(shape, rectangle(lw / 2, lw / 2, -hw + lw / 4))
    if flanged:
        shape = union(shape, rectangle(lw / 4, H, -hw + lw - lw / 8))
    return shape


def threaded_insert():
    """gflabel's threaded insert, 10 high: a knurled sleeve with a collar."""
    def outline():
        quarter = [(-3, 0), (-3, 1.25), (-4, 1.25), (-4, 3.75)]
        return polygon(quarter + [(-x, y) for x, y in reversed(quarter)] + [(-x, -y) for x, y in quarter] + [(x, -y) for x, y in reversed(quarter)])
    body = union(outline(), rectangle(6, 2.5, 0, -3.76 - 2.5 / 2))
    trapezoid = [(-1.074, .65), (-.226, .65), (1.074, -.65), (.226, -.65)]
    cuts = [polygon([(x + cx, y + cy) for x, y in trapezoid]) for cx in (-2.4375, -.8125, .8125, 2.4375) for cy in (-2.5, 2.5)]
    body = minus(body, *cuts)
    moved = pathops.Path()
    body.draw(TransformPen(moved.getPen(), (H / 10, 0, 0, H / 10, 0, 2.5 / 2 * H / 10)))
    return moved


def nut_profile(lock=False):
    width = H / 2.25
    cut_h, cut_y = H / 10, H / 4
    if lock:
        width *= 1.5
        cy = H * .2
        shape = union(rectangle(width * 2 / 3, H), circle(width / 3, -width / 3, cy), circle(width / 3, -width / 3, -cy),
                      rectangle(width / 3, 2 * cy, -width / 2))
        return minus(shape, rectangle(width * 2 / 3, cut_h, 0, cut_y), rectangle(width * 2 / 3, cut_h, 0, -cut_y))
    return minus(rectangle(width, H), rectangle(width, cut_h, 0, cut_y), rectangle(width, cut_h, 0, -cut_y))


def lockwasher():
    inner = .55
    y = H / 2 * (inner + 1) / 2
    cut = pathops.Path()
    rectangle(H / 2 * inner / 2, y * 2, H * .1, y).draw(TransformPen(cut.getPen(), (math.cos(math.pi / 4), math.sin(math.pi / 4), -math.sin(math.pi / 4), math.cos(math.pi / 4), 0, 0)))
    return minus(circle(H / 2), circle(H / 2 * inner), cut)


DRIVES = ('phillips', 'pozidrive', 'slot', 'phillips-slot', 'cross', 'hex', 'torx', 'square', 'triangle')

SYMBOLS = {
    **{f'head-{d}': lambda d=d: head(d) for d in DRIVES},
    'head-torx-security': lambda: head('torx', 'security'),
    'head-hex-security': lambda: head('hex', 'security'),
    'hexhead': lambda: regular_polygon(H / 2, 6),
    **{f'hexhead-{d}': lambda d=d: head(d, outer=regular_polygon(H / 2, 6), radius=.6 * H / 2) for d in ('slot', 'phillips', 'cross')},
    'nut': lambda: minus(regular_polygon(H / 2, 6), circle(H / 2 * .4)),
    'nut-square': lambda: minus(rectangle(H, H), circle(H / 2 * .55)),
    'nut-t': lambda: minus(union(rectangle(H * .6 - 2 * H / 7, H), rectangle(H * .6, H - 2 * H / 7),
                                 *(circle(H / 7, sx * (H * .3 - H / 7), sy * (H / 2 - H / 7)) for sx in (-1, 1) for sy in (-1, 1))),
                           circle(H * .2)),
    'nut-profile': lambda: nut_profile(),
    'locknut-profile': lambda: nut_profile(lock=True),
    'washer': lambda: minus(circle(H / 2), circle(H / 2 * .55)),
    'washer-lock': lockwasher,
    'threaded-insert': threaded_insert,
    **{f'bolt-{shape}': lambda shape=shape: bolt(shape) for shape in ('pan', 'socket', 'countersunk', 'round', 'wafer')},
    **{f'bolt-{shape}-tapping': lambda shape=shape: bolt(shape, tapping=True) for shape in ('pan', 'socket', 'countersunk', 'round', 'wafer')},
    'bolt-pan-flanged': lambda: bolt('pan', flanged=True),
    'bolt-pan-flanged-tapping': lambda: bolt('pan', flanged=True, tapping=True),
    'bolt-countersunk-slotted': lambda: bolt('countersunk', slotted=True),
}


def svg(path, width):
    """The path, y up around (0, 0), as an SVG of the width, H high."""
    pen = SVGPathPen(None, ntos=lambda v: f'{round(v, 3):g}')
    path.draw(TransformPen(pen, (1, 0, 0, -1, width / 2, BOX / 2)))
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {BOX}"><path d="{pen.getCommands()}"/></svg>\n'


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    for name, make in SYMBOLS.items():
        path = make()
        path.simplify()
        width = 2 * BOX if name.startswith('bolt-') else BOX
        with open(os.path.join(OUT, f'{name}.svg'), 'w', encoding='utf-8') as f:
            f.write(svg(path, width))
    print(f'{len(SYMBOLS)} fasteners written to {OUT}')
