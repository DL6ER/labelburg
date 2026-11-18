"""The settings made in the printer settings of the label designer: the
label size set by hand, the cutting, which end of the labels leaves first
and the quality. They are kept on the
server, so that every browser, the API and the MCP tools use the same: a
print request takes what it leaves out from them. The roll is in the
printer, its settings belong to it rather than to a browser. The order of
copies and the date format stay in each browser.

They are kept in one file in the folder of the labels, the folder a Docker
setup keeps. A change names only the settings it changes, two browsers
changing different settings at the same time keep both."""
import json
import re

from .drivers import for_model
from .drivers.devices import device_id_fields, lock_key, usb_device_id
from .label_names import check_cut, check_label_size

# In the folder of the labels, hidden: saved labels never start with a dot
FILE = '.printers.json'
# The same for every printer
COMMON = ('quality',)
# Of each printer
OWN = ('label_size', 'cut', 'half_cut', 'chain', 'leaves_first')
QUALITIES = ('quality', 'high', 'fast')
CUTS = ('each', 'last', 'none')
ENDS = ('top', 'bottom')
# Printers come and go, a few hundred are a lot already
MAX_PRINTERS = 200
# What a print request names a setting by: one that names any of them is
# cut or printed in its own way
NAMED_BY = {'cut': ('cut', 'cut_once'), 'half_cut': ('cut', 'cut_once', 'half_cut'),
            'chain': ('cut', 'cut_once', 'chain'), 'quality': ('high_res', 'fast_printing')}

SETTINGS_MESSAGE = ('settings are label_size, cut (each, last or none), half_cut and chain (0 or 1) and '
                    'leaves_first (top or bottom) of a printer and quality (quality, high or fast), '
                    'an empty value removes one')
TOO_MANY_PRINTERS = f'Settings are kept for at most {MAX_PRINTERS} printers'
# The calibration of the print position: mm the labels of a printer are moved
# right and down, and the roll it was made on. Kept apart from the settings
# above, which a print request may name.
CALIBRATION = 'calibration'
MAX_OFFSET = 5
MAX_READING = 3
CALIBRATION_MESSAGE = ('across and along are millimeters in steps of 0.25 from -5 to 5, left and right in steps of '
                       '0.25 from -3 to 3, roll is the serial number of a roll')
READINGS_DISAGREE = ('Left and right do not fit together. Look at the label again; if they still do not fit, '
                     'put the roll in again and print a new calibration label.')
_ROLL = re.compile(r'[A-Za-z0-9]{1,32}')


def key(device: str) -> str:
    """The printer a setting belongs to: on USB by its serial number where
    its device ID tells one, as /dev/usb/lp<N> changes when printers are
    plugged in another order, else by its device. ?, a request without a
    printer where none is configured, prints on the simulator."""
    if device == '?':
        return 'simulation'
    fields = device_id_fields(usb_device_id(device))
    serial = fields.get('SN') or fields.get('SERN')
    return f'usb:{fields.get("MDL", "")}/{serial}' if serial else lock_key(device)


def parsed(content: str) -> dict:
    """The settings kept in the file, nothing of a broken one."""
    try:
        data = json.loads(content)
    except ValueError:
        return {'printers': {}}
    if not isinstance(data, dict) or not isinstance(data.get('printers'), dict):
        return {'printers': {}}
    return data


def _key(data: dict, device: str) -> str:
    """key(device), of a USB printer switched off, which tells no device ID,
    the one last seen on its device."""
    printer, seen = key(device), data.get('seen')
    off = isinstance(seen, dict) and not usb_device_id(device)
    return seen.get(printer, printer) if off else printer


def of_printer(data: dict, device: str) -> dict:
    """The settings of a printer, the common ones with its own."""
    own = data['printers'].get(_key(data, device))
    return {**{name: data[name] for name in COMMON if name in data},
            **{name: own[name] for name in OWN if isinstance(own, dict) and name in own}}


def checked(changes: dict, model: str) -> dict:
    """The changes as kept, ValueError for one the printer model cannot take."""
    if not changes or any(name not in COMMON + OWN or not isinstance(value, str) for name, value in changes.items()):
        raise ValueError(SETTINGS_MESSAGE)
    for name, value in changes.items():
        if not value:
            continue
        if name == 'label_size':
            check_label_size(value, model)
        elif name == 'cut':
            if value not in CUTS:
                raise ValueError(SETTINGS_MESSAGE)
            check_cut(value, model)
        elif name in ('half_cut', 'chain'):
            if value not in ('0', '1'):
                raise ValueError(SETTINGS_MESSAGE)
        elif name == 'leaves_first':
            if value not in ENDS:
                raise ValueError(SETTINGS_MESSAGE)
        elif value not in QUALITIES:
            raise ValueError(SETTINGS_MESSAGE)
    return changes


def _keeping(data: dict, device: str) -> str:
    """The printer a change of device is kept for, noted as seen there."""
    printer = _key(data, device)
    if printer.startswith('usb:'):
        seen = data['seen'] if isinstance(data.get('seen'), dict) else {}
        data['seen'] = {**seen, lock_key(device): printer}
    return printer


def changed(data: dict, device: str, changes: dict) -> dict:
    """data with the checked changes made, an empty value removes one."""
    printer = _keeping(data, device)
    own = data['printers'].get(printer)
    own = dict(own) if isinstance(own, dict) else {}
    for name, value in changes.items():
        target = data if name in COMMON else own
        if value:
            target[name] = value
        else:
            target.pop(name, None)
    if own:
        data['printers'][printer] = own
    else:
        data['printers'].pop(printer, None)
    if len(data['printers']) > MAX_PRINTERS:
        raise ValueError(TOO_MANY_PRINTERS)
    return data


def for_printing(data: dict, device: str, model: str, values: dict) -> dict:
    """values of a print request, what it leaves out as set for the printer:
    600 dpi and fast printing only where the model prints them."""
    kept = of_printer(data, device)

    def named(name):
        return any(values.get(k) not in (None, '') for k in NAMED_BY.get(name, (name,)))
    taken = {name: kept[name] for name in OWN if name in kept and not named(name)}
    if 'quality' in kept and not named('quality'):
        caps = for_model(model).capabilities(model)
        taken['high_res'] = '1' if kept['quality'] == 'high' and caps['high_res'] else '0'
        taken['fast_printing'] = '1' if kept['quality'] == 'fast' and 'fast' in caps['qualities'] else '0'
    return {**values, **taken}


def _in_steps(value, limit: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) <= limit
            and float(value * 4).is_integer())


def _roll(value) -> bool:
    return isinstance(value, str) and bool(_ROLL.fullmatch(value))


def calibration_of(data: dict, device: str) -> dict:
    """The calibration of a printer, none where nothing valid is kept."""
    own = data['printers'].get(_key(data, device))
    kept = own.get(CALIBRATION) if isinstance(own, dict) else None
    kept = kept if isinstance(kept, dict) else {}
    return {**{name: float(kept[name]) if _in_steps(kept.get(name), MAX_OFFSET) else 0.0 for name in ('across', 'along')},
            'roll': kept['roll'] if _roll(kept.get('roll')) else None}


def calibrated(data: dict, device: str, across: float, along: float, roll: str | None) -> dict:
    """data with the calibration of a printer kept, ValueError for values off
    the steps."""
    if not (_in_steps(across, MAX_OFFSET) and _in_steps(along, MAX_OFFSET)) or (roll is not None and not _roll(roll)):
        raise ValueError(CALIBRATION_MESSAGE)
    printer = _keeping(data, device)
    own = data['printers'].get(printer)
    own = dict(own) if isinstance(own, dict) else {}
    own[CALIBRATION] = {'across': float(across), 'along': float(along), 'roll': roll}
    data['printers'][printer] = own
    if len(data['printers']) > MAX_PRINTERS:
        raise ValueError(TOO_MANY_PRINTERS)
    return data


def from_readings(left: float, right: float) -> float:
    """How far the print sits right, in mm, by the numbers read where the
    black of the calibration label starts at the left and the right edge."""
    if not (_in_steps(left, MAX_READING) and _in_steps(right, MAX_READING)):
        raise ValueError(CALIBRATION_MESSAGE)
    # Moved sideways only, the two read the same number with opposite signs
    if abs(left + right) > 0.5:
        raise ValueError(READINGS_DISAGREE)
    return (right - left) / 2
