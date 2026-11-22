"""The RD rolls of Brother's TD printers and which model prints which, after
the raster command references of the TD-2000, TD-2300D and TD-4D series
(TD2000, TD2300 and TD4000 in models.py). Die-cut labels are printed in their
print area as on the QL printers, the printer keeps the margins along them
itself; a continuous roll is fed a margin before and after the print."""

from .. import diecut
from ..labels import FormFactor, Label
from .models import TD_SERIES

# Each series' rolls of each resolution: width and length in mm as the status
# tells them, the length 0 of a continuous roll, the dots of the paper across
# and along, those of the print area, and the pins right of it, which the
# first byte of a raster line holds (TD2000 2.3.2 and 2.3.5, TD2300 and
# TD4000 4.3.2 and 4.3.5). The linerless rolls are left out with their models.
ROLLS = {
    ('td2', 203): (((57, 0), (457, 0), (432, 0), 8), ((58, 0), (464, 0), (440, 0), 4),
                   ((30, 30), (240, 240), (216, 192), 116), ((40, 40), (320, 320), (296, 272), 76),
                   ((40, 50), (320, 400), (296, 352), 76), ((40, 60), (320, 480), (296, 432), 76),
                   ((50, 30), (400, 240), (376, 192), 36), ((51, 26), (406, 205), (382, 157), 33),
                   ((60, 60), (480, 480), (448, 432), 0)),
    ('td2', 300): (((57, 0), (675, 0), (638, 0), 17), ((58, 0), (685, 0), (648, 0), 12),
                   ((30, 30), (354, 354), (318, 283), 177), ((40, 40), (472, 472), (436, 401), 118),
                   ((40, 50), (472, 591), (436, 519), 118), ((40, 60), (472, 709), (436, 638), 118),
                   ((50, 30), (591, 354), (554, 283), 59), ((51, 26), (600, 302), (564, 231), 54),
                   ((60, 60), (709, 709), (660, 638), 6)),
    ('td23', 203): (((57, 0), (456, 0), (432, 0), 20), ((58, 0), (464, 0), (440, 0), 16),
                    ((51, 26), (406, 205), (382, 156), 45)),
    ('td23', 300): (((57, 0), (672, 0), (637, 0), 29), ((58, 0), (684, 0), (648, 0), 24),
                    ((51, 26), (599, 302), (563, 230), 66)),
    ('td4', 203): (((58, 0), (464, 0), (440, 0), 196), ((76, 0), (607, 0), (583, 0), 124),
                   ((90, 0), (719, 0), (695, 0), 68), ((102, 0), (812, 0), (788, 0), 22),
                   ((51, 26), (406, 205), (382, 156), 225), ((76, 26), (609, 205), (585, 156), 123),
                   ((102, 50), (812, 399), (788, 351), 22), ((102, 152), (812, 1218), (788, 1170), 22)),
    ('td4', 300): (((58, 0), (685, 0), (649, 0), 315), ((76, 0), (897, 0), (861, 0), 209),
                   ((90, 0), (1062, 0), (1027, 0), 126), ((102, 0), (1200, 0), (1164, 0), 58),
                   ((51, 26), (600, 302), (564, 232), 358), ((76, 26), (900, 302), (864, 232), 208),
                   ((102, 50), (1200, 589), (1164, 519), 58), ((102, 152), (1200, 1800), (1164, 1728), 58)),
}
# Dots fed before and after the print on a continuous roll, 3 mm: 24 and 35
# dots (TD2000 2.1 and 2.3.3), 24 and 36 dots (TD2300 and TD4000 4.1)
MARGINS = {'td2': {203: 24, 300: 35}, 'td23': {203: 24, 300: 36}, 'td4': {203: 24, 300: 36}}
# The shortest print on a continuous roll in mm (TD2000 2.3.4, TD2300 and
# TD4000 4.3.4), and in lines with the optional cutter attached, 20 mm
SHORTEST_MM = {'td2': 12, 'td23': 6.4, 'td4': 12}
CUTTER_SHORTEST = {203: 160, 300: 236}


def _label(series: str, dpi: int, tape: tuple, total: tuple, printable: tuple, right: int) -> Label:
    width, length = tape
    size = f'{width}x{length}' if length else f'{width}'
    return Label(f"{series}{'-300' if dpi == 300 else ''}-{size}", tape, FormFactor.DIE_CUT if length else FormFactor.ENDLESS,
                 total, printable, right, feed_margin=0 if length else MARGINS[series][dpi],
                 models=tuple(TD_SERIES[series][dpi]))


LABELS = tuple(_label(series, dpi, *roll) for (series, dpi), rolls in ROLLS.items() for roll in rolls)
BY_ID = {label.identifier: label for label in LABELS}
MODELS = frozenset(model for resolutions in TD_SERIES.values() for models in resolutions.values() for model in models)
SHORTEST = {label.identifier: SHORTEST_MM[label.identifier.partition('-')[0]] for label in LABELS}
# Brother's codes of the rolls, as its pages name them, e.g.
# https://www.brother.co.uk/supplies/mobile-printing/label-rolls/rd/rds02e1, RD-S07E5 as
# https://www.brother.ee/labelling-and-receipts/td-2120n does; RD for the others
CODES = {(102, 152): 'RD-S02E1', (102, 50): 'RD-S03E1', (76, 26): 'RD-S04E1', (51, 26): 'RD-S05E1',
         (102, 0): 'RD-S01E2', (58, 0): 'RD-S07E5'}
NAMES = {label.identifier: (f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut' if label.tape_size[1]
                            else f'{label.tape_size[0]} mm endless') + f" ({CODES.get(label.tape_size, 'RD')})"
         for label in LABELS}
# A label of another driver's die-cut size, or another TD series', prints on
# the same paper here
diecut.note_papers(LABELS)
# Brother's DK labels of the same paper: DK-11241 of 102 x 152 mm, 153 mm
# long in labels.py, and the 102 mm DK-22243
DK_PAPERS = {'102x152': (102, 152), '102': (102, 0)}


def label_for(label_size: str, model: str) -> str:
    """The label size of the TD model for the paper of label_size, e.g.
    td4-102x152 for zpl102x152, else label_size."""
    paper = DK_PAPERS.get(label_size)
    if paper is None:
        return diecut.label_for(label_size, LABELS, model)
    return next((label.identifier for label in LABELS if label.tape_size == paper and model in label.models), label_size)
