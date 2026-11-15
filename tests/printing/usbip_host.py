"""A fake host that shares USB printers over USB/IP like Linux's usbip-host:
it lists and imports its devices, answers their descriptors and the device
ID, and passes the data of the printer interface to the printer behind it,
a fake printer at the other end of a socket pair."""

import select
import socket
import struct
import threading

from app.labeldesigner.drivers import usbip

QL1100_ID = 'MFG:Brother;CMD:PT-CBP;MDL:QL-1100;CLS:PRINTER;CID:Brother LabelPrinter TypeD;'
VC500W_ID = 'MFG:Brother;MDL:VC-500W;CID:Brother_VC-500W;CMD:LABEL;CLS:PRINTER;SN:000000000000000'


def _interface(number, cls, protocol, out, in_):
    endpoints = [struct.pack('<BBBBHB', 7, 5, address, 2, 64, 0) for address in (out, in_) if address is not None]
    return struct.pack('<BBBBBBBBB', 9, 4, number, 0, len(endpoints), cls, 1, protocol, 0) + b''.join(endpoints)


class SharedDevice:
    """A device of the host. interfaces: (class, protocol) of each; the
    printer behind the first bidirectional printer interface, at the
    endpoints 0x02 and 0x81, is the other end of the socket 'printer'.
    'requests' are the control requests it got, 'resets' how often a client
    let it go, after which Linux resets it. configuration 0: not configured
    yet."""

    def __init__(self, busid='1-1', product=0x20A7, device_id=QL1100_ID, interfaces=((7, 2),), configuration=1,
                 device_class=0):
        self.busid, self.product, self.device_id = busid, product, device_id
        self.configuration, self.device_class = configuration, device_class
        self.interfaces = interfaces
        self.requests, self.resets, self.imports = [], 0, 0
        self.used = False
        self.plug_in()
        printer = interfaces.index((7, 2)) if (7, 2) in interfaces else -1
        parts = b''.join(_interface(i, cls, protocol, *((0x02, 0x81) if i == printer else (0x03 + i, 0x83 + i)))
                         for i, (cls, protocol) in enumerate(interfaces))
        self.descriptor = struct.pack('<BBHBBBBB', 9, 2, 9 + len(parts), len(interfaces), 1, 0, 0xC0, 0) + parts

    def record(self):
        return usbip.DEVICE.pack(f'/sys/devices/usb1/{self.busid}'.encode(), self.busid.encode(), 1, 2, 2,
                                 0x04F9, self.product, 0x0100, self.device_class, 0, 0, self.configuration, 1,
                                 len(self.interfaces))

    def unplug(self):
        """Switch the printer off: its transfers fail, the host lets the
        client go and shares it no longer."""
        self.present = False
        try:
            self.printer.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Switched off before
            pass
        self.printer.close()

    def plug_in(self):
        self.present = True
        self._host_end, self.printer = socket.socketpair()


class FakeHost:
    """Shares its devices on localhost. With 'silent', the printer takes no
    data and writes are never answered. 'extra' is sent after what the
    printer sends, more than was asked for."""

    def __init__(self, *shared):
        self.devices = list(shared)
        self.silent = False
        self.extra = b''
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port = self.server.getsockname()[1]
        self.connections = []
        threading.Thread(target=self._serve, daemon=True).start()

    def address(self, busid=''):
        return f'usbip://127.0.0.1:{self.port}' + (f'/{busid}' if busid else '')

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            self.connections.append(connection)
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection):
        with connection:
            try:
                _, code, _ = usbip.OP.unpack(_receive(connection, usbip.OP.size))
                if code == usbip.OP_REQ_DEVLIST:
                    free = [device for device in self.devices if device.present and not device.used]
                    connection.sendall(usbip.OP.pack(usbip.VERSION, usbip.OP_REP_DEVLIST, 0) + struct.pack('>I', len(free))
                                       + b''.join(device.record() + b''.join(usbip.INTERFACE.pack(cls, 1, protocol)
                                                                             for cls, protocol in device.interfaces)
                                                  for device in free))
                    return
                busid = _receive(connection, 32).rstrip(b'\0').decode()
                device = next((device for device in self.devices if device.busid == busid and device.present), None)
                status = usbip.ST_NODEV if device is None else usbip.ST_NA if device.used else 0
                connection.sendall(usbip.OP.pack(usbip.VERSION, usbip.OP_REP_IMPORT, status) + (b'' if status else device.record()))
                if status:
                    return
                device.used, device.imports = True, device.imports + 1
                try:
                    self._transfers(connection, device)
                finally:
                    device.used, device.resets = False, device.resets + 1
            except OSError:
                pass

    def _transfers(self, connection, device):
        reading = None
        while True:
            watched = [connection] + ([device._host_end] if reading else [])
            ready, _, _ = select.select(watched, [], [])
            if device._host_end in ready:
                data = device._host_end.recv(reading[1])
                if not data:
                    # Switched off: the read fails, the host closes the connection
                    connection.sendall(_answer(reading[0], -108))
                    return
                connection.sendall(_answer(reading[0], 0, data + self.extra))
                reading = None
            if connection not in ready:
                continue
            command, seqnum, _, direction, endpoint, rest = usbip.HEADER.unpack(_receive(connection, usbip.HEADER.size))
            if command != usbip.CMD_SUBMIT:
                return
            _, length, _, _, _, setup = usbip.SUBMIT.unpack(rest)
            data = _receive(connection, length) if direction == usbip.OUT and length else b''
            if endpoint == 0:
                connection.sendall(self._control(device, seqnum, setup))
            elif endpoint != (2 if direction == usbip.OUT else 1):
                connection.sendall(_answer(seqnum, -32))
            elif direction == usbip.OUT:
                if self.silent:
                    continue
                device._host_end.sendall(data)
                connection.sendall(_answer(seqnum, 0, length=len(data)))
            else:
                reading = (seqnum, length)

    def _control(self, device, seqnum, setup):
        request_type, request, value, index, length = struct.unpack('<BBHHH', setup)
        device.requests.append((request_type, request, value, index))
        if (request_type, request, value) == (0x80, 6, 0x0200):
            return _answer(seqnum, 0, device.descriptor[:length])
        if (request_type, request) == (0xA1, 0):
            text = device.device_id.encode()
            return _answer(seqnum, 0, (len(text) + 2).to_bytes(2, 'big') + text)
        if (request_type, request) in ((0x00, 9), (0x01, 11)):
            return _answer(seqnum, 0)
        # A stall
        return _answer(seqnum, -32)

    def close(self):
        self.server.close()
        for connection in self.connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _answer(seqnum, status, data=b'', length=None):
    return usbip.HEADER.pack(usbip.RET_SUBMIT, seqnum, 0, 0, 0, struct.pack(
        '>iiiii8x', status, len(data) if length is None else length, 0, 0, 0)) + data


def _receive(connection, size):
    data = b''
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise OSError('closed')
        data += chunk
    return data
