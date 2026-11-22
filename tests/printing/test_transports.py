"""The connections several drivers share: Bluetooth LE against a fake
printer."""
import socket
import struct
import time

import pytest

from app.labeldesigner.drivers import transports
from tests.printing.test_niimbot import FakePrinter
from tests.printing.test_niimbot_ble import NIIMBOT, LePrinter

MAC = '00:00:5E:00:53:01'
DEVICE = f'bt://{MAC}'


class Refused(Exception):
    pass


class Gone(Refused):
    pass


def le_link(device=DEVICE, characteristic=NIIMBOT, notify=None):
    return transports.LeLink(device, characteristic, notify, error=Refused, no_answer=Gone)


@pytest.fixture
def le(monkeypatch):
    peer = LePrinter(FakePrinter())
    monkeypatch.setattr(transports.socket, 'socket', peer)
    monkeypatch.setattr(transports, '_kept', {})
    return peer


def test_bluetooth_le_raises_the_errors_of_the_driver(le, monkeypatch):
    with pytest.raises(Refused, match='no characteristic'):
        le_link(characteristic=bytes(16))
    assert le.closed == 1
    monkeypatch.setattr(le, 'recv', lambda size: b'')
    with pytest.raises(Gone):
        le_link()
    assert le.closed == 2


class SplitPrinter:
    """A printer on Bluetooth LE that takes what is written to one
    characteristic and answers it, reversed, with notifications of another."""

    WRITE, NOTIFY = bytes(range(16)), bytes(range(16, 32))

    def __init__(self):
        self.declarations = [(0x0010, 0x04, 0x0011, self.WRITE), (0x0013, 0x10, 0x0014, self.NOTIFY)]
        self.incoming, self.written, self.notifying = [], [], set()

    def __call__(self, family, kind, proto):
        return self

    def bind(self, address):
        pass

    def connect(self, address):
        pass

    def settimeout(self, timeout):
        pass

    def close(self):
        pass

    def send(self, pdu):
        op = pdu[0]
        if op == 0x02:
            self.incoming.append(bytes([0x03]) + struct.pack('<H', 100))
        elif op == 0x08:
            start = struct.unpack_from('<H', pdu, 1)[0]
            entries = [struct.pack('<HBH', h, p, v) + u for h, p, v, u in self.declarations if h >= start]
            self.incoming.append(bytes([0x09, 21]) + b''.join(entries) if entries else bytes([0x01, 0x08, 0, 0, 0x0a]))
        elif op == 0x04:
            start, end = struct.unpack_from('<HH', pdu, 1)
            told = [v + 1 for _, _, v, _ in self.declarations if start <= v + 1 <= end]
            self.incoming.append(bytes([0x05, 0x01]) + struct.pack('<HH', told[0], 0x2902) if told else bytes([0x01, 0x04, 0, 0, 0x0a]))
        elif op == 0x12:
            self.notifying.add(struct.unpack_from('<H', pdu, 1)[0] - 1)
            self.incoming.append(bytes([0x13]))
        elif op == 0x52:
            handle = struct.unpack_from('<H', pdu, 1)[0]
            self.written.append((handle, pdu[3:]))
            # Its notifications, the one of the written characteristic first
            self.incoming.append(bytes([0x1b]) + struct.pack('<H', 0x0011) + b'ignored')
            if 0x0014 in self.notifying:
                self.incoming.append(bytes([0x1b]) + struct.pack('<H', 0x0014) + pdu[3:][::-1])
        return len(pdu)

    def recv(self, size):
        if not self.incoming:
            raise socket.timeout('timed out')
        return self.incoming.pop(0)


def test_bluetooth_le_answers_on_another_characteristic(monkeypatch):
    peer = SplitPrinter()
    monkeypatch.setattr(transports.socket, 'socket', peer)
    link = le_link(characteristic=SplitPrinter.WRITE, notify=SplitPrinter.NOTIFY)
    assert peer.notifying == {0x0014}
    link.sendall(b'ping')
    assert peer.written == [(0x0011, b'ping')] and link.recv(64) == b'gnip'


def test_kept_bluetooth_le_link_is_taken_again(le):
    first = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    transports.release_le(DEVICE, first)
    # Also by another spelling of the device
    again = transports.kept_le(f'bt://{MAC.lower()}', NIIMBOT, error=Refused, no_answer=Gone)
    assert again is first and len(le.addresses) == 1
    transports.release_le(DEVICE, again)
    assert le.closed == 0
    again.timer.cancel()


def test_kept_bluetooth_le_link_is_closed_unused_only(le, monkeypatch):
    monkeypatch.setattr(transports, 'KEEP_LE', 0.1)
    link = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    transports.release_le(DEVICE, link)
    # Taken again before its time is up: kept while in use, its time
    # counted again once given back
    assert transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone) is link
    time.sleep(0.2)
    with transports._kept_lock:
        assert le.closed == 0 and link.timer is not None
    transports.release_le(DEVICE, link)
    deadline = time.monotonic() + 2
    while not le.closed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert le.closed == 1 and transports._kept == {}
    # Opened anew next time
    assert transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone) is not link
    assert len(le.addresses) == 2


def test_dropped_bluetooth_le_link_is_closed_when_given_back(le):
    link = transports.kept_le(DEVICE, NIIMBOT, error=Refused, no_answer=Gone)
    le.dropped = True
    with pytest.raises(OSError):
        link.sendall(b'x')
    transports.release_le(DEVICE, link)
    assert le.closed == 1 and transports._kept == {} and link.timer is None
