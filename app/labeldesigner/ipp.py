"""Network printers over IPP (RFC 8010, 8011): their status, and jobs.

Network printers do not answer status requests on the raw printing port
9100, but many answer IPP on port 631: whether they are ready, why not
(e.g. "cover-open") and which labels are loaded. This module sends a
Get-Printer-Attributes request for these and decodes the answer, and
Print-Job and Get-Job-Attributes requests for printers that take jobs over
IPP, with the standard library only."""

import functools
import socket
import ssl
import struct
import threading
import http.client
from itertools import count
from typing import NamedTuple
from urllib.parse import urlsplit

# The operations of RFC 8011 and the tags of RFC 8010 that are needed here
PRINT_JOB = 0x0002
GET_JOB_ATTRIBUTES = 0x0009
GET_PRINTER_ATTRIBUTES = 0x000B
TAG_OPERATION = 0x01
TAG_JOB = 0x02
TAG_END = 0x03
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_RESOLUTION = 0x32
TAG_RANGE = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MIME_TYPE = 0x49
TAG_MEMBER_NAME = 0x4A

STATUS_ATTRIBUTES = (
    'printer-make-and-model',
    'printer-state',
    'printer-state-reasons',
    'media-col-ready',
)

# printer-state
IDLE, PROCESSING, STOPPED = 3, 4, 5
# job-state (RFC 8011 5.3.7)
PENDING, HELD, JOB_PROCESSING, JOB_STOPPED, CANCELED, ABORTED, COMPLETED = range(3, 10)
# Status codes of refused requests (RFC 8011 B.1.4, B.1.5): a login is
# asked for, no such job, no jobs taken at the moment
LOGIN = (0x0401, 0x0402, 0x0403)
NOT_FOUND = 0x0406
NOT_TAKING = (0x0506, 0x0507)
# HTTP status codes of refused requests (RFC 9110): a login is asked for,
# or TLS, as by CUPS
HTTP_LOGIN = (401, 403)
HTTP_TLS = 426
# Units of a resolution (RFC 8010 3.9)
DOTS_PER_INCH, DOTS_PER_CM = 3, 4

# The port of IPP, also over TLS (RFC 7472). Printers that name no path
# take jobs at /ipp/print, as for IPP Everywhere, the queues of CUPS at
# /printers/<queue>.
PORT = 631
DEFAULT_PATH = '/ipp/print'
SCHEMES = ('ipp://', 'ipps://', 'cups://')
# Who sends the jobs and their name, as the printer lists them
USER = b'labelburg'
JOB_NAME = b'Labelburg'
JOB_ATTRIBUTES = ('job-state', 'job-state-reasons')

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
    """The printer answered, but not with the attributes. status: the
    status code of an answer that refuses the request, http_status that of
    an HTTP answer that does."""

    def __init__(self, message: str, status: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.status, self.http_status = status, http_status


class Unanswered(IPPError):
    """The printer took a request, e.g. a job it may print, but gave no
    answer to it."""


class Printer(NamedTuple):
    """A printer on IPP: whether it is asked over TLS, its host, port and path."""
    tls: bool
    host: str
    port: int
    path: str

    @property
    def uri(self) -> str:
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f"{'ipps' if self.tls else 'ipp'}://{host}:{self.port}{self.path}"


def is_address(device) -> bool:
    """Whether device is the address of a printer on IPP, see printer_at()."""
    return isinstance(device, str) and device.lower().startswith(SCHEMES)


def printer_at(device: str) -> Printer:
    """The printer at ipp://<host>[:<port>][/<path>], ipps:// alike over
    TLS, or cups://<queue>, a queue of CUPS on this computer. ValueError
    for another address."""
    if not is_address(device):
        raise ValueError('No address of a printer on IPP')
    if device.lower().startswith('cups://'):
        device = f'ipp://localhost:{PORT}/printers/{device[len("cups://"):]}'
    parts = urlsplit(device)
    if not parts.hostname:
        raise ValueError('No address of a printer on IPP')
    return Printer(parts.scheme.lower() == 'ipps', parts.hostname, PORT if parts.port is None else parts.port,
                   parts.path or DEFAULT_PATH)


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


def _integer(value: int) -> bytes:
    return struct.pack('>i', value)


def _keywords(name: str, values) -> bytes:
    # Further values of the same attribute have an empty name
    return b''.join(_attribute(TAG_KEYWORD, name if i == 0 else '', value.encode()) for i, value in enumerate(values))


def _member(name: str, tag: int, value: bytes) -> bytes:
    """A member of a collection (RFC 8010 3.1.6): its name, then its value
    without a name."""
    return _attribute(TAG_MEMBER_NAME, '', name.encode()) + _attribute(tag, '', value)


def _collection(name: str, members: bytes) -> bytes:
    return _attribute(TAG_BEGIN_COLLECTION, name, b'') + members + _attribute(TAG_END_COLLECTION, '', b'')


def _operation(operation: int, uri: str) -> bytes:
    """The start of a request (RFC 8010 3.1.1) with the operation attributes
    every request starts with (RFC 8011 4.1.4, 4.1.5)."""
    return (struct.pack('>BBHI', 2, 0, operation, next(_request_ids) & 0x7FFFFFFF) + bytes([TAG_OPERATION])
            + _attribute(TAG_CHARSET, 'attributes-charset', b'utf-8')
            + _attribute(TAG_LANGUAGE, 'attributes-natural-language', b'en')
            + _attribute(TAG_URI, 'printer-uri', uri.encode()))


def encode_request(uri: str, attributes=STATUS_ATTRIBUTES) -> bytes:
    """A Get-Printer-Attributes request for the given attributes."""
    return _operation(GET_PRINTER_ATTRIBUTES, uri) + _keywords('requested-attributes', attributes) + bytes([TAG_END])


def encode_print_job(uri: str, document_format: str, media_size: tuple[int, int]) -> bytes:
    """A Print-Job request (RFC 8011 4.2.1), its document follows it: the
    media of media_size, width and length in hundredths of a millimeter,
    without margins (media-col, PWG 5100.7), and the document printed as
    large as it is (print-scaling, PWG 5100.16)."""
    size = _member('x-dimension', TAG_INTEGER, _integer(media_size[0])) + _member('y-dimension', TAG_INTEGER, _integer(media_size[1]))
    media = (_attribute(TAG_MEMBER_NAME, '', b'media-size') + _collection('', size)
             + b''.join(_member(f'media-{side}-margin', TAG_INTEGER, _integer(0)) for side in ('bottom', 'left', 'right', 'top')))
    return (_operation(PRINT_JOB, uri)
            + _attribute(TAG_NAME, 'requesting-user-name', USER)
            + _attribute(TAG_NAME, 'job-name', JOB_NAME)
            + _attribute(TAG_MIME_TYPE, 'document-format', document_format.encode())
            + bytes([TAG_JOB])
            + _collection('media-col', media)
            + _attribute(TAG_KEYWORD, 'print-scaling', b'none')
            + bytes([TAG_END]))


def encode_get_job_attributes(uri: str, job_id: int) -> bytes:
    """A Get-Job-Attributes request (RFC 8011 4.3.4) for the state of a job."""
    return (_operation(GET_JOB_ATTRIBUTES, uri)
            + _attribute(TAG_INTEGER, 'job-id', _integer(job_id))
            + _attribute(TAG_NAME, 'requesting-user-name', USER)
            + _keywords('requested-attributes', JOB_ATTRIBUTES)
            + bytes([TAG_END]))


def _value(tag: int, raw: bytes):
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack('>i', raw)[0]
    if tag == TAG_BOOLEAN and len(raw) == 1:
        return raw != b'\x00'
    if tag == TAG_RANGE and len(raw) == 8:
        return struct.unpack('>ii', raw)
    # Across, along and their unit, see DOTS_PER_INCH
    if tag == TAG_RESOLUTION and len(raw) == 9:
        return struct.unpack('>iib', raw)
    # Strings of all kinds; octetString and dateTime are not needed here
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
        raise IPPError(f'The printer answers with status 0x{status:04x}', status)
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


@functools.cache
def _tls() -> ssl.SSLContext:
    """TLS without checking the certificate: printers and CUPS make their
    own, which no authority signs."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def get_printer_attributes(host: str, port: int = PORT, path: str = DEFAULT_PATH,
                           attributes=STATUS_ATTRIBUTES, timeout: float = 3.0, tls: bool = False) -> dict:
    """Ask a printer for attributes, see ask()."""
    printer = Printer(tls, host, port, path)
    return ask(printer, encode_request(printer.uri, attributes), timeout)


def print_job(printer: Printer, document_format: str, media_size: tuple[int, int], document: bytes,
              wait: float, timeout: float = 10.0) -> dict:
    """Print a document, see encode_print_job() and ask(): the printer may
    take wait seconds, it may read the document while it prints. The answer
    has job-id and job-state."""
    return ask(printer, encode_print_job(printer.uri, document_format, media_size) + document, timeout, wait)


def get_job_attributes(printer: Printer, job_id: int, timeout: float = 3.0) -> dict:
    """The state of a job, see encode_get_job_attributes() and ask()."""
    return ask(printer, encode_get_job_attributes(printer.uri, job_id), timeout)


def ask(printer: Printer, body: bytes, timeout: float, wait: float | None = None) -> dict:
    """Send a request to a printer and decode its answer. Raises OSError if
    it does not answer, e.g. ConnectionRefusedError without IPP, and
    IPPError for an answer that is not one, refuses the request, is longer
    than MAX_ANSWER, or not there within twice the timeout after
    connecting, also when it trickles in. With wait, the request may take
    that long once connected, and Unanswered is raised for anything but an
    answer once the request was sent."""
    # Imported here: the drivers import this module
    from .drivers import reach
    if printer.tls:
        connection = http.client.HTTPSConnection(printer.host, printer.port, timeout=timeout, context=_tls())
    else:
        connection = http.client.HTTPConnection(printer.host, printer.port, timeout=timeout)
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
    if wait is not None:
        connection.sock.settimeout(wait)
    stop = threading.Timer(2 * timeout if wait is None else wait, _end, (connection, ended))
    stop.start()
    sent = False
    try:
        connection.request('POST', printer.path, body, {'Content-Type': 'application/ipp'})
        sent = True
        response = connection.getresponse()
        length = response.getheader('Content-Length') or ''
        if length.strip().isdigit() and int(length) > MAX_ANSWER:
            raise IPPError('The answer is too long')
        data = b''
        while chunk := response.read1(65536):
            data += chunk
            if len(data) > MAX_ANSWER:
                raise IPPError('The answer is too long')
    except (OSError, http.client.HTTPException, IPPError) as e:
        if sent and wait is not None:
            raise Unanswered(f'No answer to the request: {e!r}') from e
        if ended.is_set():
            raise IPPError('The printer took too long to answer') from None
        raise
    finally:
        stop.cancel()
        connection.close()
    if ended.is_set():
        raise (Unanswered if wait is not None else IPPError)('The printer took too long to answer')
    if response.status != 200:
        raise IPPError(f'The printer answers with HTTP status {response.status}', http_status=response.status)
    try:
        return decode_response(data)
    except IPPError as e:
        if wait is not None and e.status is None:
            raise Unanswered(f'No answer to the request: {e}') from e
        raise
