"""Devices nearby on Bluetooth LE through the kernel's management interface,
as mdns.discover() finds devices on the network. No BlueZ service is needed,
but the right to control the adapters, e.g. in a privileged container. Only
Bluetooth LE is searched: printers on classic Bluetooth are paired with the
computer before they print, and set up by their address then."""

import logging
import select
import socket
import struct
import time

logger = logging.getLogger(__name__)

# The management channel, for no controller in particular
HCI_DEV_NONE = 0xffff
HCI_CHANNEL_CONTROL = 3
READ_INDEX_LIST, READ_INFO, START_DISCOVERY, STOP_DISCOVERY = 0x0003, 0x0004, 0x0023, 0x0024
COMMAND_COMPLETE, COMMAND_STATUS, DEVICE_FOUND, DISCOVERING = 0x0001, 0x0002, 0x0012, 0x0013
# Public and random LE addresses
LE = 0x06
# The settings an adapter is searched with
POWERED, LOW_ENERGY = 1 << 0, 1 << 9

# The adapter that heard each device best at the last search that found it,
# by the device's address
heard_by: dict[str, str] = {}


def _command(op: int, index: int, params: bytes = b'') -> bytes:
    return struct.pack('<HHH', op, index, len(params)) + params


def _fields(data: bytes) -> dict[int, list[bytes]]:
    """The fields of advertising data by their type. Any device nearby may
    advertise anything: a field running past the data ends the fields."""
    fields, i = {}, 0
    while i + 1 < len(data) and data[i] and i + 1 + data[i] <= len(data):
        fields.setdefault(data[i + 1], []).append(data[i + 2:i + 1 + data[i]])
        i += 1 + data[i]
    return fields


def _address(data: bytes) -> str:
    return ':'.join(f'{b:02X}' for b in data[5::-1])


def adapter_for(address: str) -> str:
    """The address of the adapter to reach a device through, any one for a
    device no search heard."""
    return heard_by.get(address.upper(), socket.BDADDR_ANY)


def _device(params: bytes) -> dict:
    """A device of a Device Found event: its address, its name and the
    128-bit UUIDs of its services, as far as it advertises them."""
    fields = _fields(params[14:14 + struct.unpack_from('<H', params, 12)[0]])
    name = (fields.get(0x09) or fields.get(0x08) or [b''])[0].decode('utf-8', 'replace').strip('\x00 ')
    services = [block[i:i + 16] for block in fields.get(0x06, []) + fields.get(0x07, []) for i in range(0, len(block) - 15, 16)]
    return {'address': _address(params), 'name': name,
            'le': params[6] in (1, 2), 'services': services}


def discover(timeout: float = 5.0) -> list[dict]:
    """Search every adapter that is on for timeout seconds, the devices
    found by address. Errors, e.g. without an adapter or the right to
    search, find nothing."""
    found, heard = {}, {}
    adapters, unasked, searching = {}, None, set()
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as sock:
            sock.bind((HCI_DEV_NONE, HCI_CHANNEL_CONTROL))
            sock.send(_command(READ_INDEX_LIST, HCI_DEV_NONE))
            deadline = time.monotonic() + timeout
            try:
                while (remaining := deadline - time.monotonic()) > 0:
                    if not select.select([sock], [], [], remaining)[0]:
                        break
                    data = sock.recv(1024)
                    if len(data) < 6:
                        continue
                    code, at, length = struct.unpack_from('<HHH', data)
                    params = data[6:6 + length]
                    if code in (COMMAND_COMPLETE, COMMAND_STATUS) and len(params) >= 3:
                        op, status = struct.unpack_from('<HB', params)
                        if op == READ_INDEX_LIST and code == COMMAND_COMPLETE:
                            count = struct.unpack_from('<H', params, 3)[0] if not status else 0
                            indexes = struct.unpack_from(f'<{count}H', params, 5)
                            unasked = set(indexes)
                            for index in indexes:
                                sock.send(_command(READ_INFO, index))
                        elif op == READ_INFO and unasked and at in unasked:
                            unasked.discard(at)
                            settings = struct.unpack_from('<I', params, 16)[0] if not status and len(params) >= 20 else 0
                            if settings & POWERED and settings & LOW_ENERGY:
                                adapters[at] = _address(params[3:9])
                                sock.send(_command(START_DISCOVERY, at, bytes([LE])))
                                searching.add(at)
                        elif op == START_DISCOVERY and status and at in searching:
                            logger.info('Bluetooth printers are not looked for on %s: the search was refused (%#04x)', adapters[at], status)
                            searching.discard(at)
                            del adapters[at]
                    # One too short for its header is left out
                    elif code == DEVICE_FOUND and at in searching and len(params) >= 14:
                        device = _device(params)
                        known = found.setdefault(device['address'], device)
                        known['name'] = device['name'] or known['name']
                        known['services'] = known['services'] + [s for s in device['services'] if s not in known['services']]
                        signal = struct.unpack_from('<b', params, 7)[0]
                        if signal > heard.get(device['address'], (-129, ''))[0]:
                            heard[device['address']] = (signal, adapters[at])
                    elif code == DISCOVERING and at in searching and len(params) > 1 and not params[1]:
                        searching.discard(at)
                    if unasked is not None and not unasked and not searching:
                        break
            finally:
                for index in adapters:
                    sock.send(_command(STOP_DISCOVERY, index, bytes([LE])))
    except OSError as e:
        logger.debug('No Bluetooth search: %s', e)
        return []
    heard_by.update({address: adapter for address, (_, adapter) in heard.items()})
    return list(found.values())
