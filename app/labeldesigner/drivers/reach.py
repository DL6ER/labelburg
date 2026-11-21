"""The addresses the server connects to for printers added in the Settings, as
ADD_PRINTERS allows: local (the default), any or off. Labelburg has no login,
so whoever reaches it could add a printer anywhere. Every address a host
name resolves to is checked when a printer is added and again at each
connection to it, which then goes to an address that was checked.
Standard library only."""

import contextlib
import contextvars
import errno
import ipaddress
import socket
import time

MODES = ('local', 'any', 'off')
# Loopback, private networks and the shared address space 100.64/10, where
# overlay networks like Tailscale give the devices of a user their addresses
LOCAL = tuple(ipaddress.ip_network(network) for network in (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10', '::1/128', 'fc00::/7'))
# IPv4 addresses inside IPv6 ones, connected to as IPv4 addresses
NAT64 = ipaddress.ip_network('64:ff9b::/96')
# Metadata services of cloud providers, also inside the local networks:
# the common one, that of AWS containers, Alibaba Cloud's and the IPv6 one
# of AWS
METADATA = tuple(ipaddress.ip_address(address) for address in (
    '169.254.169.254', '169.254.170.2', '100.100.100.200', 'fd00:ec2::254'))

NOT_ALLOWED = 'The address of the printer is not allowed on this server (ADD_PRINTERS)'
# A connection tries this many addresses, the first of each family first:
# a name may resolve to dozens, each would hold the thread for its timeout
MAX_ADDRESSES = 2

# Hosts of the added printers, by lower case name, and the mode they are
# checked in, see watch()
_watched: dict[str, str] = {}
# The mode the connections of a request are checked in, e.g. while a
# printer is identified, see checking()
_checking: contextvars.ContextVar[str | None] = contextvars.ContextVar('checking', default=None)


class NotAllowed(OSError):
    """A connection the mode does not allow, reason is 'off', 'never' for an
    address no printer has, or 'outside' for one outside the local network."""

    def __init__(self, reason: str):
        super().__init__(errno.EACCES, NOT_ALLOWED)
        self.reason = reason


def _plain(address: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        return ip.ipv4_mapped
    if ip.version == 6 and ip in NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def _never(ip) -> bool:
    """Link-local, multicast, broadcast, unspecified and reserved addresses
    and those of METADATA, in every mode. Python counts ::1 as reserved."""
    if ip.version == 6 and ip.scope_id:
        ip = ipaddress.ip_address(str(ip).split('%')[0])
    return ip in METADATA or not ip.is_loopback and (ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved)


def _reason(address: str, mode: str) -> str:
    ip = _plain(address)
    if mode not in ('local', 'any'):
        return 'off'
    if _never(ip):
        return 'never'
    if any(ip in network for network in LOCAL) or mode == 'any' and ip.is_global:
        return ''
    return 'outside' if ip.is_global else 'never'


def allowed(address: str, mode: str) -> bool:
    """Whether a printer may have the IP address in the mode."""
    return not _reason(address, mode)


def addresses(host: str, port: int, mode: str, kind: int = socket.SOCK_STREAM) -> list:
    """What socket.getaddrinfo() tells for host, NotAllowed unless the mode
    allows every address."""
    if mode not in ('local', 'any'):
        raise NotAllowed('off')
    told = socket.getaddrinfo(host.strip('[]'), port, type=kind)
    reasons = {_reason(info[4][0], mode) for info in told} - {''}
    if reasons:
        raise NotAllowed('never' if 'never' in reasons else 'outside')
    return told


def refusal(host: str, mode: str) -> str:
    """Why a printer at host cannot be added in the mode: 'off', 'unknown'
    for a name that is not found, 'never' or 'outside', see NotAllowed, or
    an empty string."""
    try:
        addresses(host, 0, mode)
    except NotAllowed as e:
        return e.reason
    except (socket.gaierror, UnicodeError):
        return 'unknown'
    return ''


def watch(hosts, mode: str):
    """Check every connection to the hosts in the mode from now on."""
    global _watched
    _watched = {host.lower(): mode for host in hosts}


def check(mode: str | None) -> contextvars.Token:
    """Check every connection of this thread in the mode until stop() of
    what it returns, with None only those to the watched hosts."""
    return _checking.set(mode)


def stop(token: contextvars.Token):
    _checking.reset(token)


def checking_mode() -> str | None:
    """The mode every connection of this thread is checked in, see check(),
    None where only the watched hosts are checked."""
    return _checking.get()


@contextlib.contextmanager
def checking(mode: str | None):
    """Check every connection made within, in the same thread, in the mode."""
    token = check(mode)
    try:
        yield
    finally:
        stop(token)


def _mode(host: str) -> str | None:
    return _checking.get() or _watched.get(str(host).strip('[]').lower())


def getaddrinfo(host: str, port: int, kind: int = socket.SOCK_STREAM) -> list:
    """socket.getaddrinfo() of host, checked where it is watched."""
    mode = _mode(host)
    if mode is None:
        return socket.getaddrinfo(host, port, type=kind)
    return addresses(host, port, mode, kind)


def _to_try(told: list) -> list:
    """MAX_ADDRESSES of what getaddrinfo() told, the first of each family first."""
    first, families = [], set()
    for info in told:
        if info[0] not in families:
            families.add(info[0])
            first.append(info)
    return (first + [info for info in told if info not in first])[:MAX_ADDRESSES]


def create_connection(address: tuple, timeout: float | None, source_address=None,
                      deadline: float | None = None) -> socket.socket:
    """socket.create_connection(), connected by deadline, a time.monotonic(),
    where it is given. To a watched host only at an address it resolved to
    and the mode allows, MAX_ADDRESSES of them within the timeout. The
    connection then has the timeout."""
    host, port = address[:2]
    wait = timeout
    if deadline is not None and timeout is not None:
        wait = max(0.01, min(timeout, deadline - time.monotonic()))
    mode = _mode(host)
    if mode is None:
        kwargs = {} if source_address is None else {'source_address': source_address}
        if wait == timeout:
            return socket.create_connection(address, timeout=timeout, **kwargs)
        connection = socket.create_connection(address, timeout=wait, **kwargs)
        connection.settimeout(timeout)
        return connection
    tried = _to_try(addresses(host, port, mode))
    # From now on, the resolver has its own limits
    end = None if timeout is None else time.monotonic() + timeout
    if deadline is not None:
        end = deadline if end is None else min(end, deadline)
    error = OSError(errno.EHOSTUNREACH, f'No address of {host}')
    for i, (family, kind, protocol, _, sockaddr) in enumerate(tried):
        left = None if end is None else end - time.monotonic()
        if left is not None and left <= 0:
            error = TimeoutError(errno.ETIMEDOUT, f'No address of {host} answered in time')
            break
        connection = socket.socket(family, kind, protocol)
        try:
            # What is left is shared with the addresses after it
            connection.settimeout(None if left is None else left / (len(tried) - i))
            if source_address is not None:
                connection.bind(source_address)
            connection.connect(sockaddr)
            connection.settimeout(timeout)
            return connection
        except OSError as e:
            connection.close()
            error = e
    raise error

