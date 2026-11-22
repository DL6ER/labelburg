"""Which addresses printers added in the Settings may have, by ADD_PRINTERS,
and connections to them that keep to the addresses checked."""
import socket
import threading
import time

import pytest

from app.labeldesigner import ipp, snmp
from app.labeldesigner.drivers import devices, reach, usbip
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.drivers.escpos import protocol as escpos
from app.labeldesigner.drivers.tspl import protocol as tspl
from app.labeldesigner.drivers.vc500w import protocol as vc500w
from app.labeldesigner.drivers.zpl import protocol as zpl
from tests.helpers import within


class Names(dict):
    """Host names that resolve to the addresses the test gives them, a list
    for each name, or a function giving it, for an answer that changes.
    'asked' counts the questions for each name."""

    def __init__(self):
        super().__init__()
        self.asked = {}


@pytest.fixture
def names(monkeypatch):
    table = Names()
    real = socket.getaddrinfo

    def getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host not in table:
            return real(host, port, family, type, proto, flags)
        table.asked[host] = table.asked.get(host, 0) + 1
        told = table[host]() if callable(table[host]) else table[host]
        if not told:
            raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
        return [(socket.AF_INET6, type or socket.SOCK_STREAM, 6, '', (a, int(port or 0), 0, 0)) if ':' in a
                else (socket.AF_INET, type or socket.SOCK_STREAM, 6, '', (a, int(port or 0))) for a in told]
    monkeypatch.setattr(socket, 'getaddrinfo', getaddrinfo)
    return table


@pytest.fixture(autouse=True)
def nothing_watched(monkeypatch):
    monkeypatch.setattr(reach, '_watched', {})


@pytest.mark.parametrize('address, local, anywhere', [
    ('127.0.0.1', True, True),
    ('127.10.0.1', True, True),
    ('10.1.2.3', True, True),
    ('172.16.0.5', True, True),
    ('172.31.255.254', True, True),
    ('192.168.2.113', True, True),
    # Shared address space, e.g. Tailscale's addresses
    ('100.64.1.2', True, True),
    ('100.127.255.254', True, True),
    ('::1', True, True),
    ('fd12:3456::1', True, True),
    ('::ffff:192.168.1.5', True, True),
    ('8.8.8.8', False, True),
    ('172.32.0.1', False, True),
    ('100.128.0.1', False, True),
    ('2001:4860:4860::8888', False, True),
    ('64:ff9b::808:808', False, True),
    # Never, in any mode
    ('169.254.169.254', False, False),
    ('fe80::1', False, False),
    ('fe80::1%eth0', False, False),
    ('224.0.0.251', False, False),
    ('ff02::fb', False, False),
    ('255.255.255.255', False, False),
    ('0.0.0.0', False, False),
    ('::', False, False),
    ('240.0.0.1', False, False),
    ('192.0.2.1', False, False),
    ('2001:db8::1', False, False),
    ('::ffff:169.254.169.254', False, False),
    ('64:ff9b::a9fe:a9fe', False, False),
])
def test_addresses_each_mode_allows(address, local, anywhere):
    assert reach.allowed(address, 'local') is local
    assert reach.allowed(address, 'any') is anywhere
    assert reach.allowed(address, 'off') is False


@pytest.mark.parametrize('resolved, local, anywhere', [
    (['192.168.1.20'], '', ''),
    (['192.168.1.20', 'fd00::20'], '', ''),
    (['127.0.0.1'], '', ''),
    (['8.8.8.8'], 'outside', ''),
    # Every address counts, a name may resolve to several
    (['192.168.1.20', '8.8.8.8'], 'outside', ''),
    (['192.168.1.20', '2001:4860:4860::8888'], 'outside', ''),
    (['192.168.1.20', '169.254.1.1'], 'never', 'never'),
    (['8.8.8.8', 'fe80::1'], 'never', 'never'),
    ([], 'unknown', 'unknown'),
])
def test_host_names_by_every_address(names, resolved, local, anywhere):
    names['printer.test'] = resolved
    assert reach.refusal('printer.test', 'local') == local
    assert reach.refusal('printer.test', 'any') == anywhere
    assert reach.refusal('printer.test', 'off') == 'off'


@pytest.mark.parametrize('host, local', [
    ('192.168.1.20', ''), ('[fd00::20]', ''), ('fd00::20', ''), ('[2001:4860:4860::8888]', 'outside'),
    ('169.254.1.1', 'never'),
    # Other spellings of an address, as the system takes them
    ('2130706433', ''), ('0x7f.0.0.1', ''), ('0xa9.254.169.254', 'never'),
])
def test_addresses_as_hosts(host, local):
    assert reach.refusal(host, 'local') == local


class Listener:
    """A server on localhost that keeps the connections it gets."""

    def __init__(self):
        self.server = socket.create_server(('127.0.0.1', 0))
        self.address = self.server.getsockname()
        self.port = self.address[1]
        self.accepted = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                self.accepted.append(self.server.accept()[0])
            except OSError:
                return

    def close(self):
        self.server.close()
        for connection in self.accepted:
            connection.close()


@pytest.fixture
def listener():
    server = Listener()
    yield server
    server.close()


def test_connections_to_hosts_not_watched_are_left_alone(monkeypatch):
    # Configured printers and those a request names keep working as before
    made = []
    monkeypatch.setattr(socket, 'create_connection', lambda to, timeout=None: made.append((to, timeout)) or 'socket')
    assert reach.create_connection(('printer.test', 9100), timeout=10) == 'socket'
    assert made == [(('printer.test', 9100), 10)]


def test_watched_host_outside_the_mode_is_not_connected_to(names, listener):
    names['printer.test'] = ['8.8.8.8']
    reach.watch(['printer.test'], 'local')
    with pytest.raises(reach.NotAllowed) as refused:
        reach.create_connection(('printer.test', listener.port), timeout=1)
    assert refused.value.reason == 'outside'
    assert devices.unanswered(refused.value) == reach.NOT_ALLOWED
    assert listener.accepted == []


def test_watched_host_connects_to_the_address_checked(names, listener):
    # A name that resolves to another address the next time is asked once
    answers = iter([['127.0.0.1'], ['8.8.8.8']])
    names['printer.test'] = lambda: next(answers)
    reach.watch(['PRINTER.test'], 'local')
    with reach.create_connection(('printer.test', listener.port), timeout=1) as connection:
        assert connection.getpeername() == listener.address
    assert names.asked == {'printer.test': 1}


def test_each_connection_checks_again(names, listener):
    names['printer.test'] = ['127.0.0.1']
    reach.watch(['printer.test'], 'local')
    reach.create_connection(('printer.test', listener.port), timeout=1).close()
    names['printer.test'] = ['127.0.0.1', '8.8.8.8']
    with pytest.raises(reach.NotAllowed):
        reach.create_connection(('printer.test', listener.port), timeout=1)
    assert names.asked == {'printer.test': 2}


def test_connections_while_checking_are_checked(names, listener):
    names['printer.test'] = ['8.8.8.8']
    with reach.checking('local'):
        with pytest.raises(reach.NotAllowed):
            reach.create_connection(('printer.test', 9100), timeout=1)
        with pytest.raises(reach.NotAllowed):
            reach.getaddrinfo('printer.test', 161, socket.SOCK_DGRAM)
    # Only in that context
    assert reach.getaddrinfo('printer.test', 161, socket.SOCK_DGRAM)[0][4] == ('8.8.8.8', 161)


def test_checking_does_not_reach_other_threads(names):
    names['printer.test'] = ['8.8.8.8']
    told = []
    with reach.checking('local'):
        thread = threading.Thread(target=lambda: told.append(reach.getaddrinfo('printer.test', 161, socket.SOCK_DGRAM)))
        thread.start()
        thread.join()
    assert told[0][0][4] == ('8.8.8.8', 161)


def _usbip(host):
    return usbip.shared_devices(host, 3240, timeout=1)


def _ipp(host):
    return ipp.get_printer_attributes(host, 631, timeout=1)


def _snmp(host):
    return snmp.get_values(host, ['1.3.6.1.2.1.1.1.0'], timeout=1)


def _brother(host):
    return network.send(b'job', f'tcp://{host}')


def _vc500w(host):
    with vc500w.Connection(host):
        pass


def _escpos(host):
    return escpos.connect(f'tcp://{host}')


def _zpl_job(host):
    return zpl.write(f'tcp://{host}', b'^XA^XZ')


def _zpl_status(host):
    return zpl.host_status(f'tcp://{host}')


def _tspl_job(host):
    return tspl.write(f'tcp://{host}', b'CLS\r\n')


def _tspl_status(host):
    return tspl.ask(f'tcp://{host}', name=True)


@pytest.mark.parametrize('connect', [_usbip, _ipp, _snmp, _brother, _vc500w, _escpos, _zpl_job, _zpl_status, _tspl_job, _tspl_status])
def test_every_connection_to_a_printer_is_checked(names, connect):
    names['printer.test'] = ['8.8.8.8']
    reach.watch(['printer.test'], 'local')
    with pytest.raises(reach.NotAllowed):
        connect('printer.test')
    assert names.asked == {'printer.test': 1}


def test_tests_reach_only_this_host(only_this_host):
    # A check that let an address through would make the tests connect there
    with pytest.raises(OSError):
        socket.create_connection(('192.0.2.1', 9100), timeout=1)
    with pytest.raises(OSError), socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as udp:
        udp.connect(('2001:db8::1', 161))
    assert only_this_host == [('192.0.2.1', 9100), ('2001:db8::1', 161)]
    only_this_host.clear()
    with socket.create_server(('127.0.0.1', 0)) as server, socket.create_connection(server.getsockname(), timeout=1):
        pass


@pytest.mark.parametrize('address', ['100.100.100.200', 'fd00:ec2::254', '::ffff:100.100.100.200'])
def test_metadata_addresses_of_cloud_providers_are_never_allowed(address):
    # Inside 100.64/10 and fc00::/7, which local allows otherwise
    assert not reach.allowed(address, 'local') and not reach.allowed(address, 'any')
    assert reach.allowed('100.100.100.199', 'local') and reach.allowed('fd00:ec2::253', 'local')


class Holes(set):
    """Addresses whose hosts drop connection attempts, which then run into
    their timeout. 'tried' are the addresses connected to, in their order."""

    def __init__(self):
        super().__init__()
        self.tried = []


@pytest.fixture
def blackholed(monkeypatch):
    holes = Holes()
    real = socket.socket.connect

    def connect(sock, address):
        if sock.type == socket.SOCK_STREAM:
            holes.tried.append(address[0])
            if address[0] in holes:
                time.sleep(sock.gettimeout() or 1)
                raise TimeoutError('timed out')
        return real(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', connect)
    return holes


MANY = [f'127.0.0.{i}' for i in range(2, 8)]


def ends_within(seconds: float, connect):
    started = time.monotonic()
    with pytest.raises(OSError):
        within(10, connect)
    return time.monotonic() - started < seconds


def test_a_connection_ends_by_its_timeout_however_many_addresses_a_name_has(names, blackholed):
    names['many.test'] = MANY
    blackholed.update(MANY)
    reach.watch(['many.test'], 'local')
    assert ends_within(1.5, lambda: reach.create_connection(('many.test', 9300), timeout=1))
    assert len(blackholed.tried) <= reach.MAX_ADDRESSES


def test_one_address_of_each_family_is_tried(names, blackholed):
    names['many.test'] = ['fd00::1', 'fd00::2', '127.0.0.2', '127.0.0.3']
    blackholed.update(['fd00::1', '127.0.0.2'])
    reach.watch(['many.test'], 'local')
    assert ends_within(1.5, lambda: reach.create_connection(('many.test', 9300), timeout=1))
    assert blackholed.tried == ['fd00::1', '127.0.0.2']


def test_the_second_address_gets_what_the_first_left(names, blackholed, listener):
    names['two.test'] = ['127.0.0.2', '127.0.0.1']
    blackholed.add('127.0.0.2')
    reach.watch(['two.test'], 'local')
    with reach.create_connection(('two.test', listener.port), timeout=1) as connection:
        assert connection.getpeername() == listener.address
        # Its timeout is the one asked for, the connection is used with it
        assert connection.gettimeout() == 1


def test_the_import_of_a_shared_printer_ends_with_connecting(names, blackholed, monkeypatch):
    monkeypatch.setattr(usbip, 'HANDSHAKE_TIMEOUT', 1)
    names['many.test'] = MANY
    blackholed.update(MANY)
    reach.watch(['many.test'], 'local')
    assert ends_within(1.5, lambda: usbip.connect('usbip://many.test:3299'))


def test_ipp_ends_with_connecting(names, blackholed):
    names['many.test'] = MANY
    blackholed.update(MANY)
    reach.watch(['many.test'], 'local')
    assert ends_within(1, lambda: ipp.get_printer_attributes('many.test', 3299, timeout=0.5))


def test_a_slow_name_leaves_the_connection_its_timeout(names, listener):
    names['slow.test'] = lambda: time.sleep(1.2) or ['127.0.0.1']
    reach.watch(['slow.test'], 'local')
    with reach.create_connection(('slow.test', listener.port), timeout=0.5) as connection:
        assert connection.getpeername() == listener.address
