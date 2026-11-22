"""Printers the search does not find, added in the Settings, e.g. one another
device shares over USB/IP or a network printer elsewhere. Before a printer is
added, it is asked what it is: over USB/IP for its device ID, on the network
over SNMP and IPP. Nothing goes to the port it prints on before it is known
as a printer of a model this server prints on, as a laser printer prints
whatever arrives there. A printer on IPP no other driver prints on is added
as "Other printer", which prints over IPP, never on the raw port. Where its
address may be is up to ADD_PRINTERS, see drivers/reach.py."""

import errno
import http.client
from urllib.parse import urlsplit

from app import PRINTER_NAME

from . import ipp, snmp
from .drivers import DEVICE_PATTERNS, MODELS, described_model, devices, for_model, reach, told_model, usbip
from .drivers import ipp as other
from .drivers.brother import network
from .drivers.vc500w import tapes

# Ports printers take jobs on: raw printing, also the ports after 9100 of
# print servers with several, LPD, IPP and the second raw port of Zebra
# printers. A USB/IP host is never asked on them.
PRINT_PORTS = frozenset((515, 631, 6101, *range(9100, 9200)))
MAX_LENGTH = 200
# What a network printer is asked over IPP, also whether it is one for
# "Other printer"
IDENTITY = ('printer-make-and-model', 'printer-device-id')
ASKED_OVER_IPP = tuple(dict.fromkeys((*IDENTITY, *other.OFFERED, *other.LABEL_SIGNS)))
# The fields of a device ID that are told, not its serial number
FIELDS = ('MFG', 'MDL', 'CMD', 'CLS', 'DES')

NOT_AN_ADDRESS = 'Give the address of the printer, e.g., usbip://192.168.1.50, tcp://192.168.1.60 or vc500w://192.168.1.70'
BLUETOOTH = 'Printers on Bluetooth cannot be added here, name them in PRINTERS'
PRINT_PORT = 'Printers take jobs on this port, give the port of USB/IP, 3240 unless it was changed'
RAW_PORT = 'Printers take raw jobs on this port, give the port of IPP, 631 unless it was changed'
OFF = 'Adding printers is switched off on this server (ADD_PRINTERS)'
ONLY_LOCAL = 'Only printers in your local network can be added (ADD_PRINTERS)'
NEVER = 'No printer can be added at this address'
UNKNOWN_NAME = 'No device has this name, check the address'
NO_ANSWER = 'Nothing answers at this address'
NOT_SHARED = 'The device at this address shares no printer'
SHARED_BUSY = 'The printer is used by another computer'
NO_PRINTER = 'No label printer this server knows answers at this address, name others in PRINTERS'
NO_IPP = 'No printer answers over IPP at this address, check its path, e.g., /ipp/print'
NAME_REFUSED = 'A name has up to 40 letters and digits, also spaces, dots, hyphens and underscores between them'
NAME_TAKEN = 'Another printer has this name'
ALREADY = 'This printer is in the list already'
NOT_ADDED = 'Only printers added in the Settings can be removed'
NOT_ASKED = 'The printer could not be asked what it is, the log of the server names the reason'
# For a printer on the network a request names that the server was not told about
NAMED_OFF = ('Only printers of the configuration, found by the search or added in the Settings print here, '
             'ADD_PRINTERS=local or any allows others')
NAMED_OUTSIDE = 'Printers outside the local network print here only when they are configured, ADD_PRINTERS=any allows them'
NAMED_NEVER = 'No printer prints at this address unless it is named in PRINTERS'
MESSAGES = (NOT_AN_ADDRESS, BLUETOOTH, PRINT_PORT, RAW_PORT, OFF, ONLY_LOCAL, NEVER, UNKNOWN_NAME, NO_ANSWER, NOT_SHARED,
            SHARED_BUSY, NO_PRINTER, NO_IPP, other.NO_FORMAT, other.LOGIN, other.TLS, NAME_REFUSED, NAME_TAKEN, ALREADY,
            NOT_ADDED, NOT_ASKED, NAMED_OFF, NAMED_OUTSIDE, NAMED_NEVER)

# By the reasons of reach.refusal()
_REFUSALS = {'off': OFF, 'outside': ONLY_LOCAL, 'never': NEVER, 'unknown': UNKNOWN_NAME}
# What the Settings tell of each mode
ALLOWS = {
    'local': 'Only printers in your local network can be added (ADD_PRINTERS).',
    'any': 'Printers in your network and elsewhere on the internet can be added (ADD_PRINTERS).',
    'off': 'Adding printers is switched off on this server, printers added before are not used (ADD_PRINTERS).',
}


def address_of(text: str) -> str:
    """The address of a printer as it is kept: as typed, the scheme in lower
    case, tcp:// in front of a host alone. ValueError for one that is no
    address of a printer that can be added."""
    address = str(text or '').strip()
    scheme, separator, rest = address.partition('://')
    if separator:
        address = f'{scheme.lower()}://{rest}'
    elif not address.startswith('/'):
        if address.lower() in ('', '?', 'simulation'):
            raise ValueError(NOT_AN_ADDRESS)
        address = f'tcp://{address}'
    if len(address) > MAX_LENGTH:
        raise ValueError(NOT_AN_ADDRESS)
    if address.startswith('bt://'):
        raise ValueError(BLUETOOTH)
    if usbip.is_address(address):
        if not usbip.ADDRESS.fullmatch(address) or not 0 < usbip.parse(address)[1] < 65536:
            raise ValueError(NOT_AN_ADDRESS)
        if usbip.parse(address)[1] in PRINT_PORTS:
            raise ValueError(PRINT_PORT)
        return address
    if not any(pattern.fullmatch(address) for pattern in DEVICE_PATTERNS):
        raise ValueError(NOT_AN_ADDRESS)
    if ipp.is_address(address):
        try:
            port = ipp.printer_at(address).port
        except ValueError:
            raise ValueError(NOT_AN_ADDRESS) from None
        if port in PRINT_PORTS - {ipp.PORT}:
            raise ValueError(RAW_PORT)
    return address


def name_of(text: str, taken) -> str:
    """The name a printer is added with, empty for none. ValueError for one
    PRINTERS would refuse or another printer has, of the names taken."""
    name = str(text or '').strip()
    if not name:
        return ''
    if not PRINTER_NAME.fullmatch(name) or name.casefold() == 'simulation':
        raise ValueError(NAME_REFUSED)
    if name.casefold() in {other.casefold() for other in taken}:
        raise ValueError(NAME_TAKEN)
    return name


def check_named(device: str, mode: str):
    """A printer on the network a request names that is neither configured,
    added nor found by the search: ValueError unless the mode allows every
    address of its host, as for adding it. USB and Bluetooth printers have
    no host."""
    host = host_of(device)
    if host is None:
        return
    reason = 'off' if mode not in ('local', 'any') else reach.refusal(host, mode)
    if reason:
        raise ValueError({'off': NAMED_OFF, 'outside': NAMED_OUTSIDE, 'never': NAMED_NEVER, 'unknown': UNKNOWN_NAME}[reason])


def host_of(device: str) -> str | None:
    """The host of a printer on the network or shared over USB/IP, None for
    others."""
    if usbip.is_address(device):
        try:
            return usbip.parse(device)[0]
        except ValueError:
            return None
    if device.startswith(('tcp://', 'vc500w://')):
        return urlsplit(device).hostname
    if ipp.is_address(device):
        try:
            return ipp.printer_at(device).host
        except ValueError:
            return None
    return None


def identify(text: str, mode: str, keep: bool = False) -> dict:
    """What answers at the address: its kind (usbip, network or usb), model,
    the fields of its device ID, its state as its status tells it once it is
    known, and its address as kept, which on the network follows the model,
    vc500w:// for a VC-500W, ipp:// for an "Other printer". ValueError with
    a plain refusal where the mode does not allow the address or no printer
    this server prints on answers. A printer shared over USB/IP is let go
    again unless keep, or it was imported before."""
    if mode not in ('local', 'any'):
        raise ValueError(OFF)
    address = address_of(text)
    host = host_of(address)
    if host is not None and (reason := reach.refusal(host, mode)):
        raise ValueError(_REFUSALS[reason])
    try:
        # Every connection goes to an address checked, also when the name
        # resolves to another one meanwhile
        with reach.checking(mode):
            if usbip.is_address(address):
                return _shared(address, keep)
            if ipp.is_address(address):
                return _on_ipp(address)
            if host is not None:
                return _on_network(address, host)
            return _on_usb(address)
    except reach.NotAllowed as e:
        raise ValueError(_REFUSALS[e.reason]) from e


def _found(address: str, kind: str, model: str, device_id: str, state: dict) -> dict:
    fields = devices.device_id_fields(device_id)
    return {'address': address, 'kind': kind, 'model': model,
            'device_id': {name: fields[name] for name in FIELDS if fields.get(name)}, 'state': state}


def _state(address: str, model: str) -> dict:
    """The status of a printer known to be of the model, as far as it answers."""
    driver = for_model(model)
    try:
        return driver.status(address, model) or {}
    except driver.errors as e:
        return devices.failed_printer_state(address, e, model)


def _shared(address: str, keep: bool) -> dict:
    imported = usbip.connected(address)
    try:
        # Its descriptors and device ID only, nothing goes to its printer interface
        printer = usbip.connect(address)
    except reach.NotAllowed:
        raise
    except OSError as e:
        raise ValueError(NOT_SHARED if e.errno in (errno.ENODEV, errno.EPROTO) else
                         SHARED_BUSY if e.errno == errno.EBUSY else NO_ANSWER) from e
    try:
        model = told_model(printer.device_id)
        if model is None or not for_model(model).takes_usbip:
            raise ValueError(NO_PRINTER)
        found = _found(address, 'usbip', model, printer.device_id, _state(address, model))
    except BaseException:
        # Not held for one that is refused, another computer may use it
        if not imported:
            usbip.disconnect(address)
        raise
    if not keep and not imported:
        usbip.disconnect(address)
    return found


def _ipp_identity(host: str) -> dict:
    try:
        return ipp.get_printer_attributes(host, network.IPP_PORT, attributes=ASKED_OVER_IPP)
    except reach.NotAllowed:
        raise
    except (OSError, ipp.IPPError, http.client.HTTPException):
        return {}


def _described(host: str) -> str | None:
    """The model the SNMP system description of a device names, e.g. of a
    TSC printer."""
    try:
        description = snmp.get(host, snmp.SYSTEM_DESCRIPTION, port=network.SNMP_PORT, timeout=network.SNMP_TIMEOUT)
    except reach.NotAllowed:
        raise
    except (OSError, snmp.SNMPError):
        return None
    return described_model(description.decode('utf-8', 'replace')) if isinstance(description, bytes) else None


def _on_network(address: str, host: str) -> dict:
    where = urlsplit(address).netloc
    try:
        # A Brother printer tells its model and tape over SNMP or IPP
        state = network.query_network_status(f'tcp://{where}', 'Unknown', label_printers_only=True)
    except reach.NotAllowed:
        raise
    except OSError as e:
        raise ValueError(NO_ANSWER) from e
    device_id = ''
    if not state or state['model'] == 'Unknown':
        state = None
        told = _ipp_identity(host)
        device_id = str(told.get('printer-device-id') or '')
        named = str(told.get('printer-make-and-model') or '').removeprefix('Brother ').strip()
        model = told_model(device_id) or (named if named in MODELS else None) or _described(host)
        if model is None:
            return _other_printer(host, told, device_id)
    else:
        model = state['model']
    found = f"{'vc500w' if model == tapes.MODEL else 'tcp'}://{where}"
    if not any(pattern.fullmatch(found) for pattern in for_model(model).device_patterns):
        raise ValueError(NO_PRINTER)
    return _found(found, 'network', model, device_id, state or _state(found, model))


def _other_printer(host: str, told: dict, device_id: str) -> dict:
    """A network printer no driver claims that told over IPP that it prints
    labels and takes a format of "Other printer", at the address it
    answered at. An office printer is one only given as ipp://."""
    model = other.offered(told) if told and other.tells_labels(told) else None
    if model is None:
        raise ValueError(NO_PRINTER)
    port = '' if network.IPP_PORT == ipp.PORT else f':{network.IPP_PORT}'
    address = f"ipp://{f'[{host}]' if ':' in host else host}{port}{ipp.DEFAULT_PATH}"
    return _found(address, 'network', model, device_id, other.told_status(address, model, told))


def _on_ipp(address: str) -> dict:
    """The printer at an address on IPP, see ipp.printer_at(), added as an
    "Other printer" whatever it is."""
    try:
        told = other.ask(address, ASKED_OVER_IPP)
    except reach.NotAllowed:
        raise
    except ipp.IPPError as e:
        raise ValueError(other.asked_for(e) or NO_IPP) from e
    except (OSError, http.client.HTTPException) as e:
        raise ValueError(NO_ANSWER) from e
    model = other.offered(told)
    if model is None:
        raise ValueError(other.NO_FORMAT)
    return _found(address, 'network', model, str(told.get('printer-device-id') or ''), other.told_status(address, model, told))


def _on_usb(address: str) -> dict:
    device_id = devices.usb_device_id(address)
    model = told_model(device_id)
    if model is None or not any(pattern.fullmatch(address) for pattern in for_model(model).device_patterns):
        raise ValueError(NO_PRINTER)
    return _found(address, 'usb', model, device_id, _state(address, model))
