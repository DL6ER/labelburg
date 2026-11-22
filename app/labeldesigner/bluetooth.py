"""Devices nearby on Bluetooth LE through the kernel's management interface,
as mdns.discover() finds devices on the network. No BlueZ service is needed,
but the right to control the adapter, e.g. in a privileged container. Only
Bluetooth LE is searched unless classic Bluetooth is asked for: printers
there are paired with the computer before they print, and set up by their
address then."""

import logging
import select
import socket
import struct
import time

logger = logging.getLogger(__name__)

# The management channel, for no controller in particular
HCI_DEV_NONE = 0xffff
HCI_CHANNEL_CONTROL = 3
READ_INDEX_LIST, START_DISCOVERY, STOP_DISCOVERY = 0x0003, 0x0023, 0x0024
COMMAND_COMPLETE, COMMAND_STATUS, DEVICE_FOUND, DISCOVERING = 0x0001, 0x0002, 0x0012, 0x0013
# Public and random LE addresses, and classic Bluetooth (BR/EDR)
LE, CLASSIC = 0x06, 0x01
# The advertising field of the class of device
CLASS_OF_DEVICE = 0x0d
# Searching both, the kernel searches LE for 5.12 s, then classic Bluetooth
# for as long
CLASSIC_SEARCH = 11.0


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


def _device(params: bytes, classic: bool = False) -> dict:
    """A device of a Device Found event: its address, its name and the
    128-bit UUIDs of its services, as far as it advertises them. With
    classic, also whether it is on classic Bluetooth and its class of
    device, None where it tells none."""
    fields = _fields(params[14:14 + struct.unpack_from('<H', params, 12)[0]])
    name = (fields.get(0x09) or fields.get(0x08) or [b''])[0].decode('utf-8', 'replace').strip('\x00 ')
    services = [block[i:i + 16] for block in fields.get(0x06, []) + fields.get(0x07, []) for i in range(0, len(block) - 15, 16)]
    device = {'address': ':'.join(f'{b:02X}' for b in params[5::-1]), 'name': name,
              'le': params[6] in (1, 2), 'services': services}
    if classic:
        told = (fields.get(CLASS_OF_DEVICE) or [b''])[0]
        device.update(classic=params[6] == 0, device_class=int.from_bytes(told, 'little') if len(told) == 3 else None)
    return device


def discover(timeout: float = 5.0, classic: bool = False) -> list[dict]:
    """Search for timeout seconds, the devices found by address. With
    classic on classic Bluetooth too, for CLASSIC_SEARCH seconds at least,
    LE alone where the adapter refuses that. Errors, e.g. without an adapter
    or the right to search, find nothing."""
    found, kind = {}, (LE | CLASSIC) if classic else LE
    try:
        with socket.socket(socket.AF_BLUETOOTH, socket.SOCK_RAW, socket.BTPROTO_HCI) as sock:
            sock.bind((HCI_DEV_NONE, HCI_CHANNEL_CONTROL))
            sock.send(_command(READ_INDEX_LIST, HCI_DEV_NONE))
            index, deadline = None, time.monotonic() + (max(timeout, CLASSIC_SEARCH) if classic else timeout)
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
                            if not count:
                                return []
                            index = struct.unpack_from('<H', params, 5)[0]
                            sock.send(_command(START_DISCOVERY, index, bytes([kind])))
                        elif op == START_DISCOVERY and status and kind != LE:
                            # An adapter without classic Bluetooth
                            logger.debug('Classic Bluetooth is not searched: the search was refused (%#04x)', status)
                            kind, deadline = LE, time.monotonic() + timeout
                            sock.send(_command(START_DISCOVERY, index, bytes([kind])))
                        elif op == START_DISCOVERY and status:
                            logger.info('Bluetooth printers are not looked for: the search was refused (%#04x)', status)
                            index = None
                            return []
                    # One too short for its header is left out
                    elif code == DEVICE_FOUND and at == index and len(params) >= 14:
                        device = _device(params, classic)
                        known = found.setdefault(device['address'], device)
                        known['name'] = device['name'] or known['name']
                        known['services'] = known['services'] + [s for s in device['services'] if s not in known['services']]
                        if classic:
                            # A device on both is found on each
                            known['le'], known['classic'] = known['le'] or device['le'], known['classic'] or device['classic']
                            if known['device_class'] is None:
                                known['device_class'] = device['device_class']
                    elif code == DISCOVERING and at == index and len(params) > 1 and not params[1]:
                        break
            finally:
                if index is not None:
                    sock.send(_command(STOP_DISCOVERY, index, bytes([kind])))
    except OSError as e:
        logger.debug('No Bluetooth search: %s', e)
        return []
    return list(found.values())
