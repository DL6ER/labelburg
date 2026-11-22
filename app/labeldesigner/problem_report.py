"""The details of a problem report as the bug form on GitHub asks for them:
the version, each printer with its connection and its labels, in plain
words. Only what is built here is told, never addresses, host names, serial
numbers, the names printers were given, files or label texts."""

import re
from urllib.parse import quote, urlencode

from .drivers import MODELS, devices, for_model, usbip
from .label_names import SIZES, label_name
from .printer import is_simulation

FORM = 'https://github.com/DL6ER/labelburg/issues/new'
TEMPLATE = 'bug_report.yml'
# The fields of the form by their id, with the names the form gives them
FIELDS = (('version', 'Version'), ('printer', 'Printer'), ('label_size', 'Labels'))
# Characters of a field, further printers are counted: the address stays far
# below GitHub's limit even with every character written as %XX
LONGEST = 700
# What a printer reports where no label size matches, see tape.Tape.unknown
UNKNOWN_TAPE = re.compile(r'[0-9.]+( x [0-9.]+)? mm( long)?')


def version(build: dict) -> str:
    """The version as the Settings show it under About."""
    parts = [build.get('release'), f"commit {build['commit'][:7]}" if build.get('commit') else '',
             f"built {build['date']}" if build.get('date') else '']
    return ', '.join(part for part in parts if part) or 'A development version'


def _model(printer: dict) -> str:
    model = printer.get('model')
    return model.replace('/', ' ') if model in MODELS else 'Unknown model'


def _connection(path: str) -> str:
    if is_simulation(path):
        return 'as the demo printer'
    if usbip.is_address(path):
        return 'over USB on another device'
    if path.startswith('bt://'):
        return 'over Bluetooth'
    if path.startswith(('tcp://', 'vc500w://')):
        return 'over the network'
    if path.startswith(('usb://', '/dev/usb/', 'file:///dev/usb/')):
        return 'over USB'
    return 'over another connection'


def _answered(printer: dict) -> bool:
    # Not for errors the printer itself reports, e.g. its cover open
    return printer.get('status_type') != devices.FAILED_STATUS_TYPE


def _printer(printer: dict) -> str:
    told = f"{_model(printer)} {_connection(printer.get('path') or '')}"
    return told if _answered(printer) else f'{told}, not reachable'


def _labels(printer: dict) -> str:
    """The labels a printer prints on and where that is known from, see
    tape.describe()."""
    tape = printer.get('tape') or {}
    size = SIZES.get(tape.get('id')) if isinstance(tape.get('id'), str) else None
    name = label_name(size) if size else 'an unknown label size'
    if tape.get('manual'):
        return f'{name}, chosen in the Settings'
    if tape.get('known'):
        return f'{name}, reported by the printer'
    unknown = tape.get('unknown')
    if isinstance(unknown, str) and UNKNOWN_TAPE.fullmatch(unknown):
        return f'{unknown} reported by the printer, unknown to Labelburg'
    if is_simulation(printer.get('path')) or not for_model(printer.get('model')).reports_tape:
        return f'{name}, assumed as the printer cannot report it'
    if not _answered(printer):
        return f'{name}, the printer did not answer'
    return f'{name}, not reported by the printer'


def _who(printer: dict) -> str:
    return 'Demo printer' if is_simulation(printer.get('path')) else _model(printer)


def _fitting(parts: list[str]) -> int:
    """How many of the parts fit into a field, with room to count the others."""
    length = 0
    for i, part in enumerate(parts):
        length += len(part) + 2
        if i and length > LONGEST - len('; and 999 more'):
            return i
    return len(parts)


def _joined(parts: list[str], count: int) -> str:
    more = [f'and {len(parts) - count} more'] if count < len(parts) else []
    return '; '.join(parts[:count] + more)


def details(build: dict, printers: list[dict]) -> dict:
    """The fields of the bug form by their id, from the build info and the
    printers as /api/printer_status tells them."""
    told = [_printer(p) for p in printers]
    labels = [f'{_who(p)}: {_labels(p)}' for p in printers] if len(printers) > 1 else [_labels(p) for p in printers]
    # Both fields name the same printers
    count = min(_fitting(told), _fitting(labels))
    return {
        'version': version(build),
        'printer': _joined(told, count) or 'No printer found',
        'label_size': _joined(labels, count),
    }


def text(details: dict) -> str:
    """The details to copy, named as in the form."""
    return '\n'.join(f'{name}: {details[key]}' for key, name in FIELDS if details.get(key))


def url(details: dict) -> str:
    """The bug form on GitHub with the details filled in."""
    values = {'template': TEMPLATE, **{key: details[key] for key, _ in FIELDS if details.get(key)}}
    return f'{FORM}?{urlencode(values, safe="", quote_via=quote)}'
