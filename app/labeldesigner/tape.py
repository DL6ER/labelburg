"""The tape in a printer and where labels made for another tape go on it."""

from dataclasses import dataclass, field

from PIL import Image, ImageColor

from . import drivers, sheets
from .drivers.labels import FormFactor
from .label_names import LABELS, SIZES, label_printers, label_size_problem

TAKEN_BY = {label.identifier: label_printers(label) for label in LABELS}
# The models some label size names
_LISTED = set().union(*TAKEN_BY.values())
ENDLESS = (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS)


@dataclass(frozen=True)
class Tape:
    id: str
    assumed: bool
    manual: bool = False
    # What the printer reports where no label size matches, e.g. '40 mm',
    # or the length it measured where it tells no width, e.g. '50 mm long'
    unknown: str = ''
    red_unknown: bool = False
    # The label sizes of the length it measured where it tells no width
    candidates: tuple = ()
    # The model of the printer, for its size of the same paper, see
    # same_paper(). Left out of equality: the tape, not the printer, is compared.
    model: str | None = field(default=None, compare=False)

    @property
    def known(self) -> bool:
        return not self.assumed and self.id in SIZES


def _takes(size_id: str, model) -> bool:
    """Whether a printer takes a label size, any size that has printers if
    no label lists its model."""
    known = bool(model) and model in _LISTED
    return model in TAKEN_BY[size_id] if known else bool(TAKEN_BY[size_id])


def takes(model) -> list[str]:
    """The label sizes a printer takes, for the label designer."""
    return [s for s in SIZES if _takes(s, model)]


def same_paper(size_id: str | None, model) -> str | None:
    """The label size a printer of the model prints a label made for size_id
    on: the same paper of another model is the same tape, e.g. escpos80-180
    for escpos80 on a 180 dpi ESC/POS printer, see Driver.label_for()."""
    if not model or not isinstance(size_id, str) or size_id not in SIZES:
        return size_id
    return drivers.for_model(model).label_for(size_id, model)


def _red(state: dict) -> bool | None:
    """Whether the roll is a black and red one, None where the printer does
    not tell."""
    # The color of the text on TZe tape is no black and red roll
    if state.get('red_roll') is True or 'red' in (state.get('media_type') or '').lower():
        return True
    # Only the QL-800 series tells whether a roll is black and red
    return None if state.get('red_roll') is None else False


def _reported(state: dict) -> list[str]:
    """The label sizes of the tape a status tells: both 62 and 62red where
    the printer does not tell whether its roll is black and red, every
    width of the length where it tells no width, e.g. a ZPL printer that
    measured its labels."""
    width, length = state.get('media_width'), state.get('media_length') or 0
    if not width and not length:
        return []
    red = _red(state)
    found = [s for s, label in SIZES.items()
             if _takes(s, state.get('model')) and label.tape_size[1] == length
             and (not width or label.tape_size[0] == width) and red in (None, label.color == 1)]
    # Round and square labels of the same size, e.g. of a B1, by what the
    # printer tells
    round_ = 'round' in (state.get('media_type') or '').lower()
    return sorted(found, key=lambda s: (SIZES[s].form_factor == FormFactor.ROUND_DIE_CUT) != round_)


def detect(state: dict, manual: str, default_size: str) -> Tape:
    """The tape in a printer from its status, else the one set by hand. Of a
    printer that measures only the length of its labels, the one set by hand
    of that length, else the only label size of it."""
    model = state.get('model')
    found = None
    unknown = ''
    red_unknown = False
    candidates = ()
    width, length = state.get('media_width'), state.get('media_length') or 0
    if width:
        red = _red(state)
        # Black where the printer does not tell whether its roll has red
        found = next((s for s in _reported(state) if red or SIZES[s].color != 1), None)
        red_unknown = bool(found) and red is None
        if not found:
            unknown = f'{width} x {length} mm' if length else f'{width} mm'
    elif length:
        candidates = tuple(_reported(state))
        if manual in candidates:
            return Tape(manual, assumed=False, manual=True, candidates=candidates, model=model)
        if len(candidates) == 1 and not manual:
            found = candidates[0]
        else:
            # One set by hand of another length is for other labels
            unknown, manual = f'{length} mm long', ''
    if found:
        return Tape(found, assumed=False, red_unknown=red_unknown, candidates=candidates, model=model)
    if manual in SIZES:
        return Tape(manual, assumed=False, manual=True, model=model)
    return Tape(assumed_size(model, default_size), assumed=True, unknown=unknown, candidates=candidates, model=model)


def assumed_size(model, default_size: str) -> str:
    """The label size a printer of the model that does not tell its tape is
    assumed to have: the default size as the printer's size of its paper,
    or the first one the printer takes."""
    default_size = same_paper(default_size, model)
    if default_size in SIZES and _takes(default_size, model):
        return default_size
    return next((s for s in SIZES if _takes(s, model)), default_size)


def hand_set(manual: str, model: str) -> str:
    """The tape set by hand if the model prints on it, its size of the same
    paper where it was set for another model, see same_paper(), else none,
    e.g. one set for another printer at the same address. The settings file
    may be edited by hand, anything but text counts as none."""
    manual = same_paper(manual, model)
    if isinstance(manual, str) and manual in SIZES and not label_size_problem(manual, model):
        return manual
    return ''


def _told(state: dict, model: str) -> dict:
    """The status as it tells the tape of a printer of the model. Named for
    the model, the status may be of another printer at the address or tell
    no model, so a tape the model cannot print counts as none, as does any
    tape where the driver does not tell it."""
    return {**state, 'model': model} if drivers.for_model(model).reports_tape else {'model': model}


def in_printer(state: dict, model: str, manual: str, default_size: str) -> Tape:
    """The tape a printer of the model prints on: the one of its status,
    else the one set by hand, else unknown, see _told()."""
    return detect(_told(state, model), hand_set(manual, model), default_size)


def loaded(state: dict, model: str) -> list[str]:
    """The label sizes a printer of the model may have loaded as its status
    tells, see _told() and _reported(). Empty if it tells none."""
    return _reported(_told(state, model))


def _die_cut(size_id: str) -> bool:
    return size_id in SIZES and SIZES[size_id].form_factor not in ENDLESS


def _long_along_tape(size_id: str) -> bool:
    # Of a page of several labels, the label designed
    return size_id in SIZES and sheets.piece(SIZES[size_id]).tape_size[1] > sheets.piece(SIZES[size_id]).tape_size[0]


def _runs_along(design: dict) -> bool:
    # Anything but rotated is standard, as the label is rendered
    return (design.get('orientation') != 'rotated') == _long_along_tape(design['label_size'])


def _set_wide(design: dict, wide: bool) -> dict:
    design['orientation'] = 'standard' if wide == _die_cut(design['label_size']) else 'rotated'
    return design


def _set_runs_along(design: dict, along: bool) -> dict:
    design['orientation'] = 'standard' if along == _long_along_tape(design['label_size']) else 'rotated'
    return design


def set_size(design: dict, size_id: str) -> dict:
    """Another label size: die-cut labels start wide, on endless tape the
    text keeps its direction."""
    along = _runs_along(design)
    design['label_size'] = size_id
    if _die_cut(size_id):
        design['cable_flag'] = '0'
        return _set_wide(design, True)
    return _set_runs_along(design, along)


def fits(size_id: str, tape: Tape) -> bool:
    """Whether a label of this size is on the tape as it is: of the same
    size, a black one also on a black and red roll, one for black and red
    where the printer cannot tell whether its roll has red, also one for the
    same paper of another model."""
    if size_id == tape.id:
        return True
    a, b = SIZES.get(size_id), SIZES.get(tape.id)
    return bool(a and b and a.form_factor == b.form_factor
                and tuple(a.tape_size) == tuple(b.tape_size) and (a.color != 1 or tape.red_unknown))


def moves(size_id: str, tape: Tape) -> bool:
    """Whether a label of this size is put on the tape, where it is not known,
    on the printer's size of the same paper, see same_paper(). One of the
    size of the tape only takes its name, one for black and red keeps it
    where the printer cannot tell whether its roll has red."""
    if size_id not in SIZES:
        return False
    if not tape.known:
        return same_paper(size_id, tape.model) != size_id
    return size_id != tape.id and not (tape.red_unknown and fits(size_id, tape))


def placed(size_id: str, tape: Tape) -> bool:
    return tape.known and not fits(size_id, tape)


def on_tape(design: dict, tape: Tape) -> dict:
    """The label placed on the tape, a shallow copy so that images stay
    shared."""
    if not moves(design['label_size'], tape):
        return design
    copy = dict(design)
    if not tape.known:
        copy['label_size'] = same_paper(design['label_size'], tape.model)
    elif fits(design['label_size'], tape):
        copy['label_size'] = tape.id
    else:
        return set_size(copy, tape.id)
    return copy


def describe(tape: Tape) -> dict:
    """The tape for the printer status, with where each label size goes."""
    moving = [s for s in SIZES if moves(s, tape)]
    place = {}
    for size_id in moving:
        for orientation in ('standard', 'rotated'):
            # The flag stays None unless placing sets it
            result = on_tape({'label_size': size_id, 'orientation': orientation, 'cable_flag': None}, tape)
            entry = {'label_size': result['label_size'], 'orientation': result['orientation']}
            if result['cable_flag'] is not None:
                entry['cable_flag'] = result['cable_flag']
            place[f'{size_id}|{orientation}'] = entry
    return {
        'id': tape.id, 'known': tape.known, 'assumed': tape.assumed, 'manual': tape.manual,
        'unknown': tape.unknown, 'red_unknown': tape.red_unknown, 'candidates': list(tape.candidates),
        'fits': [s for s in SIZES if fits(s, tape)], 'moves': moving, 'place': place,
    }


RED = (255, 0, 0)


def in_colors(im: Image.Image, tape_color: str, ink_color: str) -> Image.Image:
    """A preview im shown on tape of tape_color printed in ink_color, both
    #rrggbb, the tape also #rrggbbaa: white is the tape, black the ink, red
    stays red, the shades between them in between. Clear tape is see-through."""
    *tape, opaque = ImageColor.getcolor(tape_color, 'RGBA')
    ink = ImageColor.getrgb(ink_color)[:3]
    im = im.convert('RGB')
    # Of a pixel (r, g, b) of white, black and red, g is the white, r - g the
    # red and 255 - r the black: each channel shown is linear in r and g,
    # premultiplied by the alpha of the tape
    matrix = []
    for c in range(3):
        matrix += [(RED[c] - ink[c]) / 255, (tape[c] * opaque / 255 - RED[c]) / 255, 0, ink[c]]
    shown = im.convert('RGB', tuple(matrix))
    if opaque == 255:
        return shown
    alpha = im.getchannel('G').point(lambda g: round(255 - g * (255 - opaque) / 255))
    return Image.merge('RGBa', (*shown.split(), alpha)).convert('RGBA')
