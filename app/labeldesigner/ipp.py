"""Status of network printers over IPP (RFC 8010, 8011).

Network printers do not answer status requests on the raw printing port
9100, but many answer IPP on port 631: whether they are ready, why not
(e.g. "cover-open") and which labels are loaded. This module sends a
Get-Printer-Attributes request for these and decodes the answer, with the
standard library only."""

import socket
import struct
import threading
import http.client
from itertools import count

# Operation and the tags of RFC 8010 that are needed here
GET_PRINTER_ATTRIBUTES = 0x000B
TAG_OPERATION = 0x01
TAG_END = 0x03
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_RANGE = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MEMBER_NAME = 0x4A

STATUS_ATTRIBUTES = (
    'printer-make-and-model',
    'printer-state',
    'printer-state-reasons',
    'media-col-ready',
)

# printer-state
IDLE, PROCESSING, STOPPED = 3, 4, 5

# Messages for the reasons a printer gives over IPP why it cannot print
# (printer-state-reasons, RFC 8011), without the -error suffix
REASON_MESSAGES = {
    'cover-open': 'The cover is open',
    'media-empty': 'There are no labels in the printer',
    'media-needed': 'There are no labels in the printer',
    'media-jam': 'The labels are jammed',
    'offline': 'The printer is offline',
    'paused': 'The printer is paused',
    'shutdown': 'The printer is shutting down',
    # e.g. a QL-810W after a job for labels other than the loaded ones
    'other': 'See the lights or display of the printer',
}

# media-type values of endless rolls (PWG 5100.7)
ENDLESS_MEDIA_TYPES = {'continuous', 'continuous-long', 'continuous-short', 'roll'}

# A longer answer is refused, the attributes asked for take a few KB
MAX_ANSWER = 256 * 1024

_request_ids = count(1)


class IPPError(Exception):
    """The printer answered, but not with the attributes."""


def loaded_media(media) -> tuple:
    """Width across the print head and length of the loaded labels in mm
    (length 0 for endless rolls), and their type, from media-col-ready.
    IPP gives the size of the label as x by y, and with
    long-edge-first the longer side runs across the print head, e.g.
    29 x 62 mm for the 62 mm wide DK-11209."""
    if isinstance(media, list):
        media = media[0] if media else None
    if not isinstance(media, dict):
        return None, 0, None
    # Values of other types than asked for tell nothing
    size, source = media.get('media-size'), media.get('media-source-properties')
    size = size if isinstance(size, dict) else {}
    x, y = size.get('x-dimension'), size.get('y-dimension')
    if not isinstance(x, int) or x <= 0:
        return None, 0, None
    feed = source.get('media-source-feed-direction') if isinstance(source, dict) else None
    kind = media.get('media-type')
    endless = isinstance(kind, str) and kind in ENDLESS_MEDIA_TYPES or not isinstance(y, int) or y <= 0
    if endless:
        return round(x / 100), 0, 'Continuous length tape'
    across, along = (max(x, y), min(x, y)) if feed == 'long-edge-first' else (x, y)
    return round(across / 100), round(along / 100), 'Die-cut labels'


def state_errors(attributes: dict) -> list:
    """Messages for the reasons why the printer cannot print. Reasons with
    the suffix -report or -warning do not stop it, the others do. A
    QL-810W is briefly "stopped" without a reason at the end of every job,
    so only reasons count."""
    reasons = attributes.get('printer-state-reasons') or []
    if not isinstance(reasons, list):
        reasons = [reasons]
    errors = []
    for reason in reasons:
        if not isinstance(reason, str) or reason == 'none' or reason.endswith(('-report', '-warning')):
            continue
        keyword = reason.removesuffix('-error')
        message = REASON_MESSAGES.get(keyword, f'The printer reports "{keyword}"')
        if message not in errors:
            errors.append(message)
    return errors


def _attribute(tag: int, name: str, value: bytes) -> bytes:
    name_bytes = name.encode()
    return struct.pack('>BH', tag, len(name_bytes)) + name_bytes + struct.pack('>H', len(value)) + value


def encode_request(uri: str, attributes=STATUS_ATTRIBUTES) -> bytes:
    """A Get-Printer-Attributes request for the given attributes."""
    body = struct.pack('>BBHI', 2, 0, GET_PRINTER_ATTRIBUTES, next(_request_ids) & 0x7FFFFFFF)
    body += bytes([TAG_OPERATION])
    body += _attribute(TAG_CHARSET, 'attributes-charset', b'utf-8')
    body += _attribute(TAG_LANGUAGE, 'attributes-natural-language', b'en')
    body += _attribute(TAG_URI, 'printer-uri', uri.encode())
    for i, name in enumerate(attributes):
        # Further values of the same attribute have an empty name
        body += _attribute(TAG_KEYWORD, 'requested-attributes' if i == 0 else '', name.encode())
    return body + bytes([TAG_END])


def _value(tag: int, raw: bytes):
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack('>i', raw)[0]
    if tag == TAG_BOOLEAN and len(raw) == 1:
        return raw != b'\x00'
    if tag == TAG_RANGE and len(raw) == 8:
        return struct.unpack('>ii', raw)
    # Strings of all kinds; octetString, dateTime and resolution are not
    # needed here
    return raw.decode('utf-8', 'replace')


def decode_response(data: bytes) -> dict:
    """The attributes of a response, by name. Attributes with several values
    become lists, collections dictionaries of their members."""
    if len(data) < 8:
        raise IPPError('The answer is too short')
    status = struct.unpack('>H', data[2:4])[0]
    # 0x0000 to 0x00FF are successful, e.g. 0x0001 when some requested
    # attributes are not supported
    if status > 0x00FF:
        raise IPPError(f'The printer answers with status 0x{status:04x}')
    try:
        return _attributes(data)
    except struct.error as e:
        raise IPPError('The answer is cut off') from e
    except IndexError as e:
        raise IPPError('The answer has collection parts outside a collection') from e


def _attributes(data: bytes) -> dict:
    """The attributes of a response, see decode_response()."""
    attributes: dict = {}
    # Collections being decoded: the dictionary and the name of the member
    # whose value comes next
    stack: list = []
    name = ''
    i = 8
    while i < len(data):
        tag = data[i]
        i += 1
        if tag == TAG_END:
            break
        if tag < 0x10:
            # Start of the next group of attributes
            continue
        if i + 2 > len(data):
            raise IPPError('The answer is cut off')
        name_length = struct.unpack('>H', data[i:i + 2])[0]
        new_name = data[i + 2:i + 2 + name_length].decode('utf-8', 'replace')
        i += 2 + name_length
        if i + 2 > len(data):
            raise IPPError('The answer is cut off')
        value_length = struct.unpack('>H', data[i:i + 2])[0]
        raw = data[i + 2:i + 2 + value_length]
        i += 2 + value_length
        if len(raw) != value_length:
            raise IPPError('The answer is cut off')

        if tag in (TAG_MEMBER_NAME, TAG_END_COLLECTION) and not stack:
            raise IPPError('The answer has collection parts outside a collection')
        if tag == TAG_MEMBER_NAME:
            stack[-1][1] = raw.decode('utf-8', 'replace')
            continue
        if tag == TAG_END_COLLECTION:
            collection, _ = stack.pop()
            value = collection
            target_name = name if not stack else stack[-1][1]
            _add(stack[-1][0] if stack else attributes, target_name, value)
            continue
        if not stack and new_name:
            name = new_name
        if tag == TAG_BEGIN_COLLECTION:
            stack.append([{}, ''])
            continue
        value = _value(tag, raw)
        _add(stack[-1][0] if stack else attributes, stack[-1][1] if stack else name, value)
    return attributes


def _add(container: dict, name: str, value):
    """Add a value, a second one turns the attribute into a list."""
    if name not in container:
        container[name] = value
    elif isinstance(container[name], list):
        container[name].append(value)
    else:
        container[name] = [container[name], value]


def _end(connection: http.client.HTTPConnection, ended: threading.Event):
    ended.set()
    if connection.sock is not None:
        try:
            connection.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def get_printer_attributes(host: str, port: int = 631, path: str = '/ipp/print',
                           attributes=STATUS_ATTRIBUTES, timeout: float = 3.0) -> dict:
    """Ask a printer for attributes. Raises OSError if it does not answer,
    e.g. ConnectionRefusedError without IPP, and IPPError for an answer
    that is not one, longer than MAX_ANSWER, or not there within twice the
    timeout after connecting, also when it trickles in."""
    # Here, the drivers import this module
    from .drivers import reach
    host_in_uri = f'[{host}]' if ':' in host else host
    body = encode_request(f'ipp://{host_in_uri}:{port}{path}', attributes)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    # To the host of an added printer only where ADD_PRINTERS allows
    connection._create_connection = reach.create_connection
    try:
        connection.connect()
    except BaseException:
        connection.close()
        raise
    # From the connection on: a name may take its time to resolve, the
    # socket the timer ends must be there
    ended = threading.Event()
    stop = threading.Timer(2 * timeout, _end, (connection, ended))
    stop.start()
    try:
        connection.request('POST', path, body, {'Content-Type': 'application/ipp'})
        response = connection.getresponse()
        length = response.getheader('Content-Length') or ''
        if length.strip().isdigit() and int(length) > MAX_ANSWER:
            raise IPPError('The answer is too long')
        data = b''
        while chunk := response.read1(65536):
            data += chunk
            if len(data) > MAX_ANSWER:
                raise IPPError('The answer is too long')
    except (OSError, http.client.HTTPException):
        if ended.is_set():
            raise IPPError('The printer took too long to answer') from None
        raise
    finally:
        stop.cancel()
        connection.close()
    if ended.is_set():
        raise IPPError('The printer took too long to answer')
    if response.status != 200:
        raise IPPError(f'The printer answers with HTTP status {response.status}')
    return decode_response(data)
