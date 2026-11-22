"""Sheets of labels for office printers, A4 first, then US Letter, and a
whole A4 page, e.g. for signs. Each layout is as its maker's spec sheet
gives it: the size of a label, how many across and down, the top left of
the first one, the step to the next one across and down and the radius of
the corners, in mm; inches as Avery gives them for US Letter, see inch().
Products of the same layout are one sheet.

The layouts were started from and checked against the label templates of
gLabels (https://github.com/jimevins/glabels-qt), under this license:

    Copyright (C) 2001-2026  Jaye Evins

    Permission is hereby granted, free of charge, to any person
    obtaining a copy of this software and associated documentation
    files (the "Software"), to deal in the Software without
    restriction, including without limitation the rights to use,
    copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the
    Software is furnished to do so, subject to the following
    conditions:

    The above copyright notice and this permission notice shall be
    included in all copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
    EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
    OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
    HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
    WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
    FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
    OTHER DEALINGS IN THE SOFTWARE."""

import re
from typing import NamedTuple

from ..labels import FormFactor, Grid, Label, whole


def inch(value: float) -> float:
    """value inches in mm."""
    return round(value * 25.4, 4)


# Width and length in mm, and the names the label designer groups them by
PAGES = {'A4': (210, 297), 'Letter': (inch(8.5), inch(11))}
PAGE_NAMES = {'A4': 'A4', 'Letter': 'US Letter'}
DPI = 300


class Layout(NamedTuple):
    """A sheet of labels: its maker and products, the page, the size of a
    label, how many across and down, the top left of the first label, the
    step to the next one across and down, and the radius of the corners,
    all in mm. Without a maker the page itself is the label."""
    maker: str
    products: tuple[str, ...]
    page: str
    label: tuple[float, float]
    columns: int
    rows: int
    left_top: tuple[float, float]
    step: tuple[float, float]
    corner: float = 0


# Avery gives no margins for its A4 sheets: as in its Word templates, the
# labels are centered across. It gives no corners either, those are as in
# gLabels where it has the sheet, else square. Avery's US corners are as in
# its PDF templates.
LAYOUTS = (
    Layout('', ('A4',), 'A4', (210, 297), 1, 1, (0, 0), (210, 297)),
    Layout('Avery', ('L7160', 'J8160'), 'A4', (63.5, 38.1), 3, 7, (7.23, 15.15), (66.02, 38.1), 1.76),
    Layout('Avery', ('L7159', 'J8159'), 'A4', (63.5, 33.86), 3, 8, (7.23, 13.06), (66.02, 33.86)),
    Layout('Avery', ('L7161', 'J8161'), 'A4', (63.5, 46.56), 3, 6, (7.23, 8.82), (66.02, 46.56), 2.47),
    Layout('Avery', ('L7162', 'J8162'), 'A4', (99.06, 33.87), 2, 8, (4.67, 13.02), (101.6, 33.87), 1.76),
    Layout('Avery', ('L7163', 'J8163'), 'A4', (99.06, 38.1), 2, 7, (4.67, 15.15), (101.6, 38.1), 1.76),
    Layout('Avery', ('L7165', 'J8165'), 'A4', (99.06, 67.73), 2, 4, (4.67, 13.04), (101.6, 67.73)),
    Layout('Avery', ('L7166', 'J8166'), 'A4', (99.06, 93.13), 2, 3, (4.67, 8.81), (101.6, 93.13)),
    Layout('Avery', ('L7167', 'J8167'), 'A4', (199.6, 289.05), 1, 1, (5.2, 3.97), (199.6, 289.05)),
    Layout('Avery', ('L7168', 'J8168'), 'A4', (199.6, 143.53), 1, 2, (5.2, 4.97), (199.6, 143.53)),
    Layout('Avery', ('L7169', 'J8169'), 'A4', (99.1, 139), 2, 2, (4.63, 9.5), (101.64, 139), 2.12),
    Layout('Avery', ('L7173', 'J8173'), 'A4', (99.06, 57), 2, 5, (4.67, 6), (101.6, 57)),
    Layout('Avery', ('L7651', 'J8651'), 'A4', (38.1, 21.17), 5, 13, (4.71, 10.89), (40.62, 21.17), 1.76),
    Layout('Avery', ('L7656',), 'A4', (46, 11.1), 4, 21, (6.04, 15.93), (50.64, 12.68)),
    # Archive serial numbers for Paperless-ngx
    Layout('Avery', ('L4731REV', 'L4731'), 'A4', (25.4, 10), 7, 27, (8.53, 13.5), (27.92, 10)),
    Layout('Avery', ('L4732',), 'A4', (35.6, 16.9), 5, 16, (11, 13.05), (38.1, 16.9), 1.5),
    Layout('Avery', ('L4736',), 'A4', (45.7, 21.17), 4, 12, (9.79, 21.48), (48.24, 21.17)),
    Layout('Avery', ('L4737',), 'A4', (63.5, 29.6), 3, 9, (7.23, 15.1), (66.02, 29.6)),
    Layout('Avery Zweckform', ('3474',), 'A4', (70, 37), 3, 8, (0, 0), (70, 37)),
    Layout('Avery Zweckform', ('3475', '3490'), 'A4', (70, 36), 3, 8, (0, 4.5), (70, 36)),
    Layout('Avery Zweckform', ('3481',), 'A4', (70, 41), 3, 7, (0, 5), (70, 41)),
    Layout('Avery Zweckform', ('3483',), 'A4', (105, 148), 2, 2, (0, 0), (105, 148)),
    Layout('Avery Zweckform', ('3425',), 'A4', (105, 57), 2, 5, (0, 6), (105, 57)),
    Layout('Avery Zweckform', ('4780',), 'A4', (48.5, 25.4), 4, 10, (8, 21.5), (48.5, 25.4)),
    Layout('Herma', ('4226',), 'A4', (48.26, 16.93), 4, 16, (8.48, 13.06), (48.26, 16.93)),
    Layout('Herma', ('4360',), 'A4', (70, 36), 3, 8, (0, 4.5), (70, 36)),
    Layout('Herma', ('10001',), 'A4', (25.4, 10), 7, 27, (8.48, 13.5), (27.94, 10), 1.1),
    Layout('Avery', ('5160', '8160', '5260', '5960', '18160', '6460'), 'Letter', (inch(2.625), inch(1)), 3, 10,
           (inch(0.1875), inch(0.5)), (inch(2.75), inch(1)), inch(0.1)),
    Layout('Avery', ('5161',), 'Letter', (inch(4), inch(1)), 2, 10, (inch(0.167), inch(0.5)), (inch(4.1875), inch(1)), inch(0.1)),
    Layout('Avery', ('5162',), 'Letter', (inch(4), inch(4 / 3)), 2, 7, (inch(0.156), inch(0.833)), (inch(4.188), inch(4 / 3)), inch(0.1)),
    Layout('Avery', ('5163',), 'Letter', (inch(4), inch(2)), 2, 5, (inch(0.156), inch(0.5)), (inch(4.188), inch(2)), inch(0.2)),
    Layout('Avery', ('5164',), 'Letter', (inch(4), inch(10 / 3)), 2, 3, (inch(0.156), inch(0.5)), (inch(4.188), inch(10 / 3)), inch(0.19)),
    Layout('Avery', ('5167',), 'Letter', (inch(1.75), inch(0.5)), 4, 20, (inch(0.3), inch(0.5)), (inch(2.05), inch(0.5)), inch(0.05)),
    # As in Avery's templates of the sheet, rows of 0.66 in
    Layout('Avery', ('5195',), 'Letter', (inch(1.75), inch(0.66)), 4, 15, (inch(0.3), inch(0.55)), (inch(2.05), inch(0.66)), inch(0.094)),
    Layout('Avery', ('5168',), 'Letter', (inch(3.5), inch(5)), 2, 2, (inch(0.5), inch(0.5)), (inch(4), inch(5)), inch(0.1)),
    Layout('Avery', ('5165',), 'Letter', (inch(8.5), inch(11)), 1, 1, (0, 0), (inch(8.5), inch(11))),
)


def identifier(layout: Layout) -> str:
    """The id of its label size, e.g. sheet-l7160."""
    return f'sheet-{layout.products[0].lower()}'


def _mm(value: float) -> str:
    return f'{whole(round(value, 1))}'


def name(layout: Layout) -> str:
    """e.g. Avery L7160 / J8160 - 21 labels, 63.5 x 38.1 mm."""
    if not layout.maker:
        return f'Whole {PAGE_NAMES[layout.page]} page'
    count = layout.columns * layout.rows
    return (f"{layout.maker} {' / '.join(layout.products)} - {count} label{'s' if count > 1 else ''}, "
            f'{_mm(layout.label[0])} x {_mm(layout.label[1])} mm')


def short(layout: Layout) -> str:
    """The sheet as the label designer names it in a line, e.g. Avery L7160."""
    return f'{layout.maker} {layout.products[0]}' if layout.maker else f'{PAGE_NAMES[layout.page]} page'


def label(layout: Layout, model: str) -> Label:
    """The label size of the sheet, the page at DPI, for printers of model."""
    page = PAGES[layout.page]
    dots = (round(page[0] * DPI / 25.4), round(page[1] * DPI / 25.4))
    grid = Grid(layout.label, layout.columns, layout.rows, *layout.left_top, layout.step, layout.corner, paper=True)
    return Label(identifier(layout), tuple(whole(mm) for mm in page), FormFactor.DIE_CUT, dots, dots, 0,
                 models=(model,), grid=grid)


def of(label_size: str) -> Layout | None:
    """The sheet of a label size, None for another one."""
    return _BY_ID.get(label_size)


def _plain(text: str) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(text).upper())


_BY_ID = {identifier(layout): layout for layout in LAYOUTS}
# The sheets by each of their products, also as "Avery L7160" or "l 7160"
_BY_PRODUCT = {_plain(product): identifier(layout) for layout in LAYOUTS for product in layout.products}
# Longer first: Avery Zweckform before Avery
_MAKERS = sorted({_plain(layout.maker) for layout in LAYOUTS if layout.maker}, key=len, reverse=True)


def named(text) -> str | None:
    """The label size of the sheet a product number names, e.g. L7160,
    J8160 or Avery 5160, None for none."""
    plain = _plain(text)
    for maker in _MAKERS:
        if plain.startswith(maker) and plain != maker:
            plain = plain.removeprefix(maker)
            break
    return _BY_PRODUCT.get(plain)
