"""The settings made in the printer settings of the label designer: the
label size set by hand, the cutting, which end of the labels leaves first
and the quality. They are kept on the
server, so that every browser, the API and the MCP tools use the same: a
print request takes what it leaves out from them. The roll is in the
printer, its settings belong to it rather than to a browser. The settings
of labels, how dates are written, the order of copies of several files and
the font of new labels, are kept for everyone as well.

They are kept in one file in the folder of the labels, the folder a Docker
setup keeps. A change names only the settings it changes, two browsers
changing different settings at the same time keep both. The printers
added in the Settings are kept there too, see adding.py."""
import json
import re

from .drivers import MODELS, devices, for_model
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
# The printers added in the Settings, each with its address, model and name
ADDED = 'added'
MAX_ADDED = 20
TOO_MANY_ADDED = f'At most {MAX_ADDED} printers can be added'
BROKEN = f'The file {FILE} in the folder of the labels is not valid, fix or remove it, it was left as it is'


class Broken(Exception):
    """The file of the settings holds no settings, see readable()."""
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
NO_CALIBRATION = 'This printer offers no calibration'
OFFSET_LIMIT = 'The labels cannot be moved further than 5 mm.'
_ROLL = re.compile(r'[A-Za-z0-9]{1,32}')


def key(device: str, device_id: str | None = None) -> str:
    """The printer a setting belongs to: on USB by its serial number where
    its device ID tells one, as /dev/usb/lp<N> changes when printers are
    plugged in another order, by the serial number its status told, e.g. a
    Niimbot B1 on USB and on Bluetooth, else by its device. ?, a request
    without a printer where none is configured, prints on the simulator.
    device_id is the one read already, if it was."""
    if device == '?':
        return 'simulation'
    fields = device_id_fields(usb_device_id(device) if device_id is None else device_id)
    serial = fields.get('SN') or fields.get('SERN')
    if serial:
        return f'usb:{fields.get("MDL", "")}/{serial}'
    told = devices.status_cache.get(lock_key(device), {})
    if told.get('serial') and told.get('model'):
        return f'serial:{told["model"]}/{told["serial"]}'
    return lock_key(device)


def parsed(content: str) -> dict:
    """The settings kept in the file, nothing of a broken one."""
    try:
        data = json.loads(content)
    except ValueError:
        return {'printers': {}}
    if not isinstance(data, dict) or not isinstance(data.setdefault('printers', {}), dict):
        return {'printers': {}}
    return data


def readable(content: str) -> bool:
    """Whether the file holds settings or nothing yet, so that a change may
    replace it."""
    if not content.strip():
        return True
    try:
        data = json.loads(content)
    except ValueError:
        return False
    return isinstance(data, dict) and isinstance(data.get('printers', {}), dict)


def _key(data: dict, device: str) -> str:
    """key(device), of a USB printer switched off that tells no device ID,
    or of one whose serial number is not told yet, the one last seen on its
    device. Settings kept before the serial number was told stay its own.
    Its device ID is read once. A printer shared over USB/IP is not imported
    for it, a slow host would hold the request: the device ID it told last
    is taken."""
    device_id = usb_device_id(device, connect=False)
    printer, seen = key(device, device_id), data.get('seen')
    if printer.startswith('serial:') and printer not in data['printers'] and lock_key(device) in data['printers']:
        return lock_key(device)
    last = seen.get(printer) if isinstance(seen, dict) else None
    # The file may be edited by hand, anything but text names no printer
    if not isinstance(last, str):
        return printer
    # A B1 on USB tells a device ID without its serial number
    return last if not device_id or (printer == lock_key(device) and last.startswith('serial:')) else printer


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
    printer = key(device)
    if printer.startswith('serial:') and printer not in data['printers'] and lock_key(device) in data['printers']:
        data['printers'][printer] = data['printers'].pop(lock_key(device))
    printer = _key(data, device)
    if printer.startswith(('usb:', 'serial:')):
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


def for_printing(kept: dict, model: str, values: dict) -> dict:
    """values of a print request, what it leaves out as set for the printer
    in kept, its settings as of_printer() gives them: 600 dpi and fast
    printing only where the model prints them."""
    def named(name):
        return any(values.get(k) not in (None, '') for k in NAMED_BY.get(name, (name,)))
    taken = {name: kept[name] for name in OWN if name in kept and not named(name)}
    if 'quality' in kept and not named('quality'):
        caps = for_model(model).capabilities(model)
        taken['high_res'] = '1' if kept['quality'] == 'high' and caps['high_res'] else '0'
        taken['fast_printing'] = '1' if kept['quality'] == 'fast' and 'fast' in caps['qualities'] else '0'
    return {**values, **taken}


# Of the labels, not of a printer
LABEL_SETTINGS = ('date_format', 'collate', 'font_family')
# Those the label designer offers, e.g. 2026-11-01 or 01.11.2026
DATE_FORMATS = ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%m/%d/%Y')
LABEL_SETTINGS_MESSAGE = ('label settings are date_format (%Y-%m-%d, %d.%m.%Y, %d/%m/%Y or %m/%d/%Y), '
                          'collate (0 or 1) and font_family (a font family of the server or of the font menu of the label designer), '
                          'an empty value removes one')


def _label_setting(name: str, value, families) -> bool:
    """Whether value can be kept for the label setting name."""
    if not isinstance(value, str):
        return False
    if name == 'date_format':
        return value in DATE_FORMATS
    if name == 'collate':
        return value in ('0', '1')
    return value in families


def label_settings(data: dict, families) -> dict:
    """All label settings kept in data, empty where none is set. One that
    cannot be kept, e.g. a font family no longer installed or a value
    written wrong by hand, is empty too."""
    kept = data.get('labels')
    kept = kept if isinstance(kept, dict) else {}
    return {name: kept[name] if _label_setting(name, kept.get(name), families) else '' for name in LABEL_SETTINGS}


def checked_label_settings(changes: dict, families) -> dict:
    """The changes as kept, ValueError for one that cannot be kept. families
    are those font_family takes: of the server and of the font menu of the
    label designer, see Fonts.family_names()."""
    if not changes or any(name not in LABEL_SETTINGS or value != '' and not _label_setting(name, value, families)
                          for name, value in changes.items()):
        raise ValueError(LABEL_SETTINGS_MESSAGE)
    return changes


def changed_label_settings(data: dict, changes: dict) -> dict:
    """data with the checked changes made, an empty value removes one."""
    kept = data.get('labels')
    kept = dict(kept) if isinstance(kept, dict) else {}
    for name, value in changes.items():
        if value:
            kept[name] = value
        else:
            kept.pop(name, None)
    if kept:
        data['labels'] = kept
    else:
        data.pop('labels', None)
    return data


def added(data: dict) -> list[dict]:
    """The printers added in the Settings, each with its address, model and
    name, empty if it has none. An entry a hand edit broke is left out."""
    entries = data.get(ADDED)
    if not isinstance(entries, list):
        return []
    return [{'address': entry['address'], 'model': entry['model'],
             'name': entry['name'] if isinstance(entry.get('name'), str) else ''}
            for entry in entries
            if isinstance(entry, dict) and isinstance(entry.get('address'), str) and entry.get('model') in MODELS]


def with_added(data: dict, address: str, model: str, name: str) -> dict:
    """data with the printer added, ValueError for more than MAX_ADDED."""
    entries = added(data)
    if len(entries) >= MAX_ADDED:
        raise ValueError(TOO_MANY_ADDED)
    data[ADDED] = entries + [{'address': address, 'model': model, 'name': name}]
    return data


def without_added(data: dict, address: str) -> dict:
    """data without the printer added at address."""
    data[ADDED] = [entry for entry in added(data) if entry['address'] != address]
    return data


def _in_steps(value, limit: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) <= limit
            and abs(value) != float('inf') and float(value * 4).is_integer())


def _roll(value) -> bool:
    return isinstance(value, str) and bool(_ROLL.fullmatch(value))


def calibration_of(data: dict, device: str) -> dict:
    """The calibration of a printer, none where nothing valid is kept."""
    own = data['printers'].get(_key(data, device))
    kept = own.get(CALIBRATION) if isinstance(own, dict) else None
    kept = kept if isinstance(kept, dict) else {}
    return {**{name: float(kept[name]) if _in_steps(kept.get(name), MAX_OFFSET) else 0.0 for name in ('across', 'along')},
            'roll': kept['roll'] if _roll(kept.get('roll')) else None}


def checked_offsets(across: float, along: float, roll: str | None = None):
    """ValueError for offsets off the steps or further than 5 mm, or a roll
    told otherwise than in letters and digits."""
    if not (_in_steps(across, float('inf')) and _in_steps(along, float('inf'))) or (roll is not None and not _roll(roll)):
        raise ValueError(CALIBRATION_MESSAGE)
    if abs(across) > MAX_OFFSET or abs(along) > MAX_OFFSET:
        raise ValueError(OFFSET_LIMIT)


def calibrated(data: dict, device: str, across: float, along: float, roll: str | None) -> dict:
    """data with the calibration of a printer kept, ValueError for values off
    the steps."""
    checked_offsets(across, along, roll)
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
