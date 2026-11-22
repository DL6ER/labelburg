"""Label printers the search finds that no driver prints on, e.g. a DYMO
LabelWriter on USB or a Niimbot B21 on Bluetooth, and their report for the
form on GitHub. A report tells the kind of printer only: the version, the
connection, the USB IDs, the fields of the device ID that name the model,
the Bluetooth name without its serial number and the services it
advertises. Never serial numbers, addresses, host names, paths or names
printers were given. Nothing is sent to a printer to find out more."""

import re
import uuid
from urllib.parse import quote, urlencode

from .adding import FIELDS
from .drivers import MODELS, devices
from .drivers.brother import models as brother_models
from .drivers.niimbot.protocol import NIIMBOT_SERVICE

FORM = 'https://github.com/DL6ER/labelburg/issues/new'
TEMPLATE = 'new_printer.yml'
# Makers of label printers by their USB vendor ID as usb.ids names them,
# Niimbot by its B1. Their devices without a printer interface are found
# too, e.g. a DYMO LabelManager PnP.
LABEL_MAKERS = {0x0922: 'DYMO', 0x1203: 'TSC', 0x3513: 'Niimbot', 0x0a5f: 'Zebra', 0x0b0b: "Datamax-O'Neil",
                0x0828: 'SATO', 0x0985: 'cab', 0x0d8a: 'King Jim'}
# Their products usb.ids names that have no printer interface
PRODUCTS = {(0x0922, 0x0013): 'LabelManager 400', (0x0922, 0x1001): 'LabelManager PnP',
            (0x0d8a, 0x0101): 'TEPRA PRO', (0x0d8a, 0x4001): 'KL-P1000'}
# DYMO's postal scales
NOT_PRINTERS = {0x0922: range(0x8000, 0x10000)}
# Commands of office printers, which are no label printers: PCL, PostScript,
# PDF, AirPrint, IPP Everywhere, Epson and Canon inkjets, Canon lasers
OFFICE = re.compile(r'(PCL|POSTSCRIPT|PDF|URF|PWG|ESCPL2|ESCPR|BJ|LIPS)', re.IGNORECASE)
# Niimbot's models by niimbluelib (MIT) that no driver prints on. They tell
# <model>-<serial number> as their name; models of two characters count
# only with Niimbot's service, other devices may be named so.
NIIMBOT_MODELS = frozenset((
    'A1_PRO', 'A20', 'A203', 'A63', 'A8', 'A8_P', 'B1_PRO', 'B1_SE', 'B11', 'B16', 'B18', 'B18S', 'B2', 'B2_PRO',
    'B203', 'B21', 'B21_PRO', 'B21_C2B', 'B21_L2B', 'B21S', 'B21S_C2B', 'B3', 'B31', 'B32', 'B32R', 'B3S', 'B3S_A',
    'B3S_P', 'B4', 'B4_PRO', 'B50', 'B50W', 'C1', 'D101', 'D11', 'D11_H', 'D11_PRO', 'D11S', 'EP1C', 'EP2M_H', 'EP3M',
    'ET10', 'H1', 'H1S', 'JC_M90', 'K2', 'K3', 'K3_ITD', 'K3_W', 'K4', 'M2_H', 'M3', 'MP3K', 'MP3K_W', 'N1', 'P1', 'P18',
    'P1S', 'S1', 'S3', 'S6', 'S6_P', 'T2S', 'T6', 'T7', 'T8', 'T8S', 'TP2M_H', 'Z401'))
NIIMBOT_NAMES = frozenset(model for model in NIIMBOT_MODELS if len(model) > 2)
NIIMBOT_NAME = re.compile(r'(?P<model>[A-Z0-9_]+)-(?=[A-Z]*[0-9])[A-Z0-9]{4,}', re.IGNORECASE)
# Brother's printers tell their model and serial number, e.g.
# PT-P710BT<serial>: P-touch printers of other models by this
PTOUCH_NAME = re.compile(r'(?P<model>PT-[A-Z][0-9]{3}BT)[A-Z0-9]*', re.IGNORECASE)
# What a field must not tell: everything after a word that names a serial
# number, an address or an owner, MAC addresses as 12 hex digits with or
# without separators, IPv4 and IPv6 addresses, host names, paths
_NAMED = re.compile(r'\b(SN|S/N|SERIAL|MAC|IP|HOST|OWNER)(?![A-Z0-9-]).*', re.IGNORECASE | re.DOTALL)
_IPV4 = re.compile(r'\d{1,3}(\.\d{1,3}){3}')
_ADDRESS = re.compile(r'(?<![0-9A-Z])[0-9A-F]{2}([:. -]?[0-9A-F]{2}){5}(?![0-9A-Z])'
                      r'|(?<![\w:])[0-9A-F]{0,4}(:[0-9A-F]{0,4}){2,7}(?![\w:])'
                      r'|[A-Z0-9-]+(\.[A-Z0-9-]+)*\.[A-Z]{2,}\b|(?<![\w/])/\S*|\b[A-Z]:\\\S*', re.IGNORECASE)
_WORD = re.compile(r'[A-Z0-9]+', re.IGNORECASE)
# Characters of a field and services told at most
LONGEST = 120
MOST_SERVICES = 8


def _serial(word: str) -> bool:
    return len(word) >= 6 and sum(c.isdigit() for c in word) >= 5


def clean(text: str) -> str:
    """A field as a report tells it: without addresses and serial numbers,
    at most LONGEST characters."""
    text = _ADDRESS.sub('', _IPV4.sub('', _NAMED.sub('', str(text or ''))))
    text = _WORD.sub(lambda m: '' if _serial(m.group()) else m.group(), text)
    return re.sub(r'\s+', ' ', text).strip(' -_:;,/')[:LONGEST].strip()


def office(fields: dict) -> bool:
    """Whether a device ID, by its fields, is one of an office printer."""
    return any(OFFICE.match(command.strip()) for command in fields.get('CMD', '').split(','))


def brother(fields: dict) -> bool:
    """Whether a device ID names Brother as the maker."""
    return fields.get('MFG', '').strip().upper().startswith('BROTHER')


def label_maker(ids: tuple[int, int]) -> bool:
    """Whether a USB device is one of a maker of label printers, see
    LABEL_MAKERS, and no other device of theirs."""
    vendor, product = ids
    return vendor in LABEL_MAKERS and product not in NOT_PRINTERS.get(vendor, ())


def on_usb(device_id: str, ids: tuple[int, int] | None) -> dict:
    """A printer on USB no driver prints on by its device ID, empty where it
    tells none, and its vendor and product ID, None where unknown."""
    fields = devices.device_id_fields(device_id)
    told = {name: value for name in FIELDS if (value := clean(fields.get(name, '')))}
    maker, model = told.get('MFG', ''), told.get('MDL', '')
    # A description is free text: told only where it repeats maker and model
    named = {word.upper() for word in _WORD.findall(f'{maker} {model}')}
    if not {word.upper() for word in _WORD.findall(told.get('DES', ''))} <= named:
        del told['DES']
    if not maker and ids:
        maker = LABEL_MAKERS.get(ids[0], '')
        model = model or PRODUCTS.get(ids, '')
    if model and maker.upper() not in model.upper():
        model = f'{maker} {model}'
    return {'where': 'usb', 'name': model or (f'{maker} printer' if maker else 'Printer on USB'),
            'usb_id': f'{ids[0]:04x}:{ids[1]:04x}' if ids else '', 'device_id': told}


def _brother(name: str) -> tuple[str, bool] | None:
    """The model of a Brother printer a Bluetooth name tells and whether
    Labelburg prints on it, None for other names."""
    known = next((model for model in sorted(brother_models.MODELS, key=len, reverse=True)
                  if re.fullmatch(re.escape(model) + '[A-Z0-9]*', name, re.IGNORECASE)), None)
    if known:
        return known, True
    ptouch = PTOUCH_NAME.fullmatch(name)
    return (ptouch['model'].upper(), False) if ptouch else None


def on_bluetooth(device: dict) -> dict | None:
    """A label printer on Bluetooth as bluetooth.discover() found it that no
    driver prints on there, by its name, or by Niimbot's service and a name
    Niimbot gives; None for other devices."""
    name = str(device.get('name') or '').strip()
    niimbot, brother = NIIMBOT_NAME.fullmatch(name), _brother(name)
    service = NIIMBOT_SERVICE in device.get('services', [])
    model = niimbot['model'].upper() if niimbot else ''
    shown = ''
    if niimbot and (model in NIIMBOT_NAMES or service and model in NIIMBOT_MODELS):
        maker = 'Niimbot'
    elif niimbot and service:
        # A model Labelburg prints on, e.g. a D110_M on classic Bluetooth
        if any(known.upper().startswith(f'NIIMBOT/{model}') for known in MODELS):
            return None
        maker, model = 'Niimbot', ''
    elif brother:
        maker, (model, known) = 'Brother', brother
        if known and brother_models.MODELS[model].bluetooth:
            return None
        # One Labelburg prints on over USB or the network only
        shown = ' over Bluetooth' if known else ''
    else:
        return None
    model = clean(model)
    kinds = [kind for kind, on in (('Bluetooth LE', device.get('le')), ('classic Bluetooth', device.get('classic'))) if on]
    services = [str(uuid.UUID(bytes=bytes(service[::-1]))) for service in device.get('services', [])
                if len(service) == 16][:MOST_SERVICES]
    return {'where': 'bluetooth', 'name': (f'{maker} {model}' if model else f'{maker} printer') + shown,
            'connection': ' and '.join(kinds) or 'Bluetooth', 'bluetooth_name': model, 'services': services}


def version(build: dict) -> str:
    """The version as the Settings show it under About."""
    parts = [build.get('release'), f"commit {build['commit'][:7]}" if build.get('commit') else '',
             f"built {build['date']}" if build.get('date') else '']
    return ', '.join(part for part in parts if part) or 'A development version'


def _found(found: dict) -> list[str]:
    """What was found of the printer, a line each."""
    if found['where'] == 'bluetooth':
        lines = [f"Connection: {found['connection']}"]
        if found['bluetooth_name']:
            lines.append(f"Bluetooth name: {found['bluetooth_name']}")
        if found['services']:
            lines.append(f"Services: {', '.join(found['services'])}")
        return lines
    lines = ['Connection: USB']
    if found['usb_id']:
        lines.append(f"USB ID: {found['usb_id']}")
    if found['device_id']:
        lines.append('Device ID: ' + ''.join(f'{name}:{value};' for name, value in found['device_id'].items()))
    return lines


def report(found: dict, build: dict) -> str:
    """The report to copy."""
    return '\n'.join([f'Version: {version(build)}', f"Printer: {found['name']}", *_found(found)])


def report_url(found: dict, build: dict) -> str:
    """The form for new printers on GitHub with the report filled in."""
    values = {'template': TEMPLATE, 'title': f"New printer: {found['name']}", 'version': version(build),
              'printer': found['name'], 'found': '\n'.join(_found(found))}
    return f'{FORM}?{urlencode(values, safe="", quote_via=quote)}'


def entry(found: dict, build: dict) -> dict:
    """A printer found as /api/printer_status tells it in unknown."""
    return {'where': found['where'], 'name': found['name'], 'report': report(found, build),
            'report_url': report_url(found, build)}
