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


def key(device: str, device_id: str | None = None) -> str:
    """The printer a setting belongs to: on USB by its serial number where
    its device ID tells one, as /dev/usb/lp<N> changes when printers are
    plugged in another order, else by its device. ?, a request without a
    printer where none is configured, prints on the simulator. device_id
    is the one read already, if it was."""
    if device == '?':
        return 'simulation'
    fields = device_id_fields(usb_device_id(device) if device_id is None else device_id)
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
    the one last seen on its device. Its device ID is read once, over
    USB/IP each read is a connection."""
    device_id = usb_device_id(device)
    printer, seen = key(device, device_id), data.get('seen')
    last = seen.get(printer) if isinstance(seen, dict) and not device_id else None
    # The file may be edited by hand, anything but text names no printer
    return last if isinstance(last, str) else printer


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


def changed(data: dict, device: str, changes: dict) -> dict:
    """data with the checked changes made, an empty value removes one."""
    printer = _key(data, device)
    if printer.startswith('usb:'):
        seen = data['seen'] if isinstance(data.get('seen'), dict) else {}
        data['seen'] = {**seen, lock_key(device): printer}
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
