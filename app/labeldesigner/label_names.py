"""Label sizes in the label designer: their names and which printer models
print them. Each driver (drivers/) has the label sizes of its printers."""

import functools
import math

from .drivers.labels import Color, Label

from . import drivers
from .drivers import LABELS, MODELS
from .drivers.base import shown


def label_name(label: Label) -> str:
    """Name of a label size, made of its size where its driver does not name
    it."""
    driver = drivers.for_label(label.identifier)
    return driver.label_names.get(label.identifier, label.name) if driver else label.name


def label_fits(label: Label, model: str) -> bool:
    """Whether a printer model can print a label size: one of the label
    sizes of its driver that the driver lets it print."""
    driver = drivers.for_model(model)
    return drivers.for_label(label.identifier) is driver and driver.fits(label, model)


def label_palette(label: Label) -> str:
    """The colors a label can show: 'color' on the tapes of the VC-500W,
    'red' (black and red) on black/red/white labels, 'black' on all others."""
    return drivers.for_label(label.identifier).palette(label)


def label_dpi(label_size: str, high_res: bool) -> int:
    """The resolution labels of a size are made with."""
    driver = drivers.for_label(label_size) or drivers.BROTHER
    label = next((label for label in LABELS if label.identifier == label_size), None)
    if label is None:
        return driver.dpi(high_res)
    return driver.label_dpi(label)[1 if high_res else 0]


def label_printers(label: Label) -> list:
    """Printer models the label designer offers a label size for."""
    if label.identifier in drivers.for_label(label.identifier).hidden_labels:
        return []
    return [model for model in MODELS if label_fits(label, model)]


@functools.cache
def _offered() -> dict[str, list]:
    return {label.identifier: label_printers(label) for label in LABELS}


def printer_label_size(printer: dict, default: str) -> tuple[str, bool]:
    """The label size a printer of /api/printer_status prints on, and whether
    it is assumed: the tape the printer reports, else default, else the first
    label size its model takes. Printers of no known model take every label
    size offered. DK and TZe tape of the same size are told apart by the
    model, and only the QL-800 series tells a black and red roll."""
    model = printer.get('model')
    offered = _offered()
    known = any(model in models for models in offered.values())
    takes = [label for label in LABELS if (model in offered[label.identifier] if known else offered[label.identifier])]
    if printer.get('media_width'):
        tape = (printer['media_width'], printer.get('media_length') or 0)
        red = printer.get('red_roll') is True or 'red' in str(printer.get('media_type') or '').lower()
        found = next((label for label in takes if label.tape_size == tape and (label.color == Color.BLACK_RED_WHITE) == red), None)
        if found:
            return found.identifier, False
    assumed = next((label for label in takes if label.identifier == default), None) or next(iter(takes), None)
    return (assumed.identifier if assumed else default), True


def label_size_problem(label_size: str, model: str) -> str:
    """Why the printer model cannot print the label size, or an empty string
    if it can. Unknown models are left to the Brother driver."""
    label = next((label for label in LABELS if label.identifier == label_size), None)
    if label is None:
        return f'Unknown label size: {label_size}'
    if model in MODELS and not label_fits(label, model):
        name = shown(model)
        return f"The label size {label_name(label)} cannot be printed on {'an' if name[:1] in tuple('AEIOU') else 'a'} {name}"
    return ''


def label_size_problems() -> list[str]:
    """Every problem of a known label size on a known printer model."""
    return [problem for label in LABELS for model in MODELS
            if (problem := label_size_problem(label.identifier, model))]


def check_label_size(label_size: str, model: str):
    """Raise ValueError if the printer model cannot print the label size."""
    problem = label_size_problem(label_size, model)
    if problem:
        raise ValueError(problem)


def cut_problems() -> list[str]:
    """Every problem of a way of cutting on a known printer model."""
    return [problem for model in MODELS for cut in ('each', 'last', 'none')
            if (problem := drivers.for_model(model).cut_problem(cut, model))]


def check_cut(cut: str, model: str):
    """Raise ValueError if the printer model cannot cut labels this way."""
    problem = drivers.for_model(model).cut_problem(cut, model)
    if problem:
        raise ValueError(problem)


def length_limits(label: Label) -> tuple[float, float, float] | None:
    """The shortest and the longest an endless label of the size is as cut,
    the shortest rounded up to a tenth, and the paper fed before and after
    its print, each, in mm. None where its labels have no length to set:
    die-cut labels and tapes of printers whose labels grow with their
    content."""
    driver = drivers.for_label(label.identifier)
    rules = driver.length_rules(label) if driver else None
    if rules is None:
        return None
    margin, shortest, longest = rules
    return math.ceil((shortest + 2 * margin) * 10) / 10, longest, margin


def label_length_problem(label: Label, length: float) -> str:
    """Why an endless label of the size cannot be length mm long as cut, or
    an empty string if it can."""
    shortest, longest, _ = length_limits(label)
    if length < shortest:
        return f"label_length must be at least {shortest:g} mm on this tape"
    if length > longest:
        return f"label_length must not be longer than {longest:g} mm on this tape"
    return ''


def label_length_problems() -> list[str]:
    """Every message a length of an endless label is refused with."""
    return list(dict.fromkeys(problem for label in LABELS if length_limits(label)
                              for length in (0, math.inf) if (problem := label_length_problem(label, length))))
