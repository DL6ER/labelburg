"""Status of network printers over IPP, with answers recorded from a
QL-810W and a fake printer for the network part."""
import os
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import create_app
from app.labeldesigner import ipp, printer
from app.labeldesigner.drivers import devices, reach
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.drivers.brother import usb
from tests.helpers import Answering, outcome, within
from tests.printing.test_reach import names  # noqa: F401

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures', 'ipp')


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), 'rb') as f:
        return f.read()


def attribute(tag: int, name: str, value: bytes) -> bytes:
    return struct.pack('>BH', tag, len(name)) + name.encode() + struct.pack('>H', len(value)) + value


def response(*attributes: bytes, status: int = 0) -> bytes:
    """An IPP answer with the given printer attributes."""
    return struct.pack('>BBHI', 2, 0, status, 1) + b'\x04' + b''.join(attributes) + b'\x03'


def keywords(name: str, *values: str) -> bytes:
    return b''.join(attribute(ipp.TAG_KEYWORD, name if i == 0 else '', value.encode()) for i, value in enumerate(values))


def state(value: int) -> bytes:
    return attribute(ipp.TAG_ENUM, 'printer-state', struct.pack('>i', value))


def media(x: int, y: int | None, media_type: str) -> bytes:
    """media-col-ready with the size in hundredths of a millimeter."""
    def member(name, tag, value):
        return attribute(ipp.TAG_MEMBER_NAME, '', name.encode()) + attribute(tag, '', value)
    size = member('x-dimension', ipp.TAG_INTEGER, struct.pack('>i', x))
    if y is not None:
        size += member('y-dimension', ipp.TAG_INTEGER, struct.pack('>i', y))
    return (attribute(ipp.TAG_BEGIN_COLLECTION, 'media-col-ready', b'')
            + member('media-type', ipp.TAG_KEYWORD, media_type.encode())
            + attribute(ipp.TAG_MEMBER_NAME, '', b'media-size') + attribute(ipp.TAG_BEGIN_COLLECTION, '', b'')
            + size + attribute(ipp.TAG_END_COLLECTION, '', b'')
            + attribute(ipp.TAG_END_COLLECTION, '', b''))


def test_ready_ql_810w_is_decoded():
    attributes = ipp.decode_response(fixture('ql810w-ready-62x29.bin'))
    assert attributes['printer-make-and-model'] == 'Brother QL-810W'
    assert attributes['printer-state'] == ipp.IDLE
    assert attributes['printer-state-reasons'] == 'none'
    loaded = attributes['media-col-ready']
    assert loaded['media-type'] == 'labels'
    assert loaded['media-size'] == {'x-dimension': 2900, 'y-dimension': 6200}
    assert loaded['media-source-properties']['media-source-feed-direction'] == 'long-edge-first'
    # Several values become a list
    assert 'om_brother-label-62x100mm_62x100mm' in attributes['media-supported']
    # The 62 mm wide DK-11209, as the USB status reports it
    assert ipp.loaded_media(loaded) == (62, 29, 'Die-cut labels')
    assert ipp.state_errors(attributes) == []


def test_address_labels_are_fed_short_edge_first():
    # DK-11201: the 29 mm side runs across the print head
    attributes = ipp.decode_response(fixture('ql810w-ready-29x90.bin'))
    loaded = attributes['media-col-ready']
    assert loaded['media-source-properties']['media-source-feed-direction'] == 'short-edge-first'
    assert ipp.loaded_media(loaded) == (29, 90, 'Die-cut labels')


def test_stopped_ql_810w_reports_an_error():
    # After a job for endless labels on a die-cut roll
    attributes = ipp.decode_response(fixture('ql810w-stopped-other-error.bin'))
    assert attributes['printer-state'] == ipp.STOPPED
    assert ipp.state_errors(attributes) == ['See the lights or display of the printer']


def test_open_cover_of_a_ql_810w():
    # The QL-810W gives no reason more specific than "other"
    attributes = ipp.decode_response(fixture('ql810w-cover-open.bin'))
    assert attributes['printer-state'] == ipp.STOPPED
    assert ipp.state_errors(attributes) == ['See the lights or display of the printer']
    # The labels are still named
    assert ipp.loaded_media(attributes['media-col-ready']) == (62, 29, 'Die-cut labels')


def test_endless_roll_in_a_ql_810w_is_not_reported():
    # media-ready is empty and media-col-ready missing, the label designer
    # must not offer a label instead
    attributes = ipp.decode_response(fixture('ql810w-ready-endless.bin'))
    assert attributes['media-ready'] == ''
    assert 'media-col-ready' not in attributes
    assert ipp.loaded_media(attributes.get('media-col-ready')) == (None, 0, None)
    assert ipp.state_errors(attributes) == []


def test_request_asks_for_the_status_attributes():
    request = ipp.encode_request('ipp://192.0.2.1:631/ipp/print')
    assert request[:4] == b'\x02\x00\x00\x0b'
    attributes = ipp.decode_response(request)
    assert attributes['printer-uri'] == 'ipp://192.0.2.1:631/ipp/print'
    assert attributes['requested-attributes'] == list(ipp.STATUS_ATTRIBUTES)


@pytest.mark.parametrize('reasons, expected', [
    (['none'], []),
    (['cover-open-error'], ['The cover is open']),
    (['media-empty-error', 'media-needed'], ['There are no labels in the printer']),
    # Warnings and reports do not stop the printer
    (['media-low-warning', 'other-report'], []),
    (['toner-gone-error'], ['The printer reports "toner-gone"']),
])
def test_reasons_that_stop_the_printer(reasons, expected):
    attributes = ipp.decode_response(response(state(ipp.STOPPED if expected else ipp.IDLE), keywords('printer-state-reasons', *reasons)))
    assert ipp.state_errors(attributes) == expected


def test_stopped_printer_without_reason():
    # A QL-810W at the end of every job, no error
    attributes = ipp.decode_response(response(state(ipp.STOPPED), keywords('printer-state-reasons', 'none')))
    assert ipp.state_errors(attributes) == []


@pytest.mark.parametrize('x, y, media_type, expected', [
    (6200, None, 'continuous', (62, 0, 'Continuous length tape')),
    (2900, 0, 'roll', (29, 0, 'Continuous length tape')),
    (6200, 10000, 'labels', (62, 100, 'Die-cut labels')),
])
def test_loaded_media(x, y, media_type, expected):
    attributes = ipp.decode_response(response(media(x, y, media_type)))
    assert ipp.loaded_media(attributes['media-col-ready']) == expected


def test_error_status_is_refused():
    with pytest.raises(ipp.IPPError, match='0x0400'):
        ipp.decode_response(response(status=0x0400))


@pytest.mark.parametrize('body, message', [
    # The length of the value cut off after the name
    (bytes([ipp.TAG_KEYWORD]) + struct.pack('>H', 4) + b'abcd' + b'\x00', 'cut off'),
    (attribute(ipp.TAG_MEMBER_NAME, '', b'x'), 'outside a collection'),
    (attribute(ipp.TAG_END_COLLECTION, '', b''), 'outside a collection'),
], ids=['value cut off', 'member name', 'end of a collection'])
def test_broken_answer_is_refused(body, message):
    with pytest.raises(ipp.IPPError, match=message):
        ipp.decode_response(struct.pack('>BBHI', 2, 0, 0, 1) + b'\x04' + body)


@pytest.fixture
def fake_printer(monkeypatch):
    """An IPP server on localhost that answers with what the test sets:
    'body', or the answers in 'bodies' one after the other, the last one
    again and again."""
    answer = {'body': b'', 'bodies': [], 'status': 200, 'requests': 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            answer['requests'] += 1
            body = answer['bodies'].pop(0) if len(answer['bodies']) > 1 else (answer['bodies'] or [answer['body']])[0]
            self.send_response(answer['status'])
            self.send_header('Content-Type', 'application/ipp')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(network, 'IPP_PORT', server.server_port)
    monkeypatch.setattr(devices, 'status_cache', {})
    # No SNMP, unless a test brings an agent
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unused:
        unused.bind(('127.0.0.1', 0))
        monkeypatch.setattr(network, 'SNMP_PORT', unused.getsockname()[1])
    monkeypatch.setattr(network, '_snmp_unavailable', {})
    yield answer
    server.shutdown()


def test_network_printer_status(fake_printer):
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    app = create_app()
    app.config['PRINTER_PRINTER'] = 'tcp://127.0.0.1'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert status['printers'][0]['path'] == 'tcp://127.0.0.1'
    for key, value in {'model': 'QL-810W', 'media_width': 62, 'media_length': 29, 'media_type': 'Die-cut labels',
                       'errors': [], 'red_support': True}.items():
        assert status[key] == value
        assert status['printers'][0][key] == value


def test_network_printer_without_ipp(monkeypatch):
    # Printers that only take jobs on port 9100 refuse the connection
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    monkeypatch.setattr(network, 'IPP_PORT', port)
    status = network.query_network_status('tcp://127.0.0.1', 'QL-720NW')
    assert status['model'] == 'QL-720NW' and status['errors'] == [] and status['media_width'] is None


def test_network_printer_that_does_not_answer_keeps_its_last_status(fake_printer, monkeypatch):
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    network.query_network_status('tcp://127.0.0.1', 'QL-800')

    def no_answer(*args, **kwargs):
        raise TimeoutError('timed out')
    monkeypatch.setattr(ipp, 'get_printer_attributes', no_answer)
    app = create_app()
    app.config['PRINTER_PRINTER'] = 'tcp://127.0.0.1'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert status['errors'] == ['The printer does not answer']
    assert (status['model'], status['media_width'], status['media_length']) == ('QL-810W', 62, 29)


READY = response(attribute(0x41, 'printer-make-and-model', b'Brother QL-800'), state(ipp.IDLE), keywords('printer-state-reasons', 'none'))
PRINTING = response(attribute(0x41, 'printer-make-and-model', b'Brother QL-800'), state(ipp.PROCESSING), keywords('printer-state-reasons', 'none'))
# What a QL-810W reported for a label it printed: printing for 3 s, briefly
# stopped without a reason, then idle
STOPPED = response(attribute(0x41, 'printer-make-and-model', b'Brother QL-800'), state(ipp.STOPPED), keywords('printer-state-reasons', 'none'))
COVER_OPEN = response(attribute(0x41, 'printer-make-and-model', b'Brother QL-800'), state(ipp.STOPPED), keywords('printer-state-reasons', 'cover-open-error'))


@pytest.fixture
def network_job(fake_printer, monkeypatch, tmp_path):
    """Print on a network printer at 127.0.0.1: the IPP answers come from
    the fake printer, what is sent to port 9100 is collected. With the
    client that prints."""
    from tests.helpers import EXAMPLE_FORMDATA, make_client
    sent = []
    monkeypatch.setattr(network, 'send', lambda data, device: sent.append(device))
    monkeypatch.setattr(network, 'NETWORK_JOB_POLL', 0.01)
    client = make_client(tmp_path)

    def print_label():
        return client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, printer='tcp://127.0.0.1'))
    return fake_printer, sent, print_label, client


def test_job_waits_until_the_network_printer_finished(network_job):
    answer, sent, print_label, client = network_job
    answer['bodies'] = [READY, PRINTING, PRINTING, STOPPED, READY]
    response = print_label()
    assert outcome(client, response) == ('printed', '')
    assert sent == ['tcp://127.0.0.1']
    # Checked before, then asked until it was idle again
    assert answer['requests'] == 5


@pytest.mark.parametrize('speed, success', [(1, True), (1e6, False)])
def test_network_job_waits_as_long_as_its_labels_take(network_job, monkeypatch, speed, success):
    # Besides the time any job may take, as long as the labels take at the
    # slowest printers
    answer, sent, print_label, client = network_job
    monkeypatch.setattr(network, 'NETWORK_JOB_TIMEOUT', 0)
    monkeypatch.setattr(usb, 'SLOWEST_PRINT_SPEED', speed)
    answer['bodies'] = [READY, PRINTING, PRINTING, PRINTING, READY]
    response = print_label()
    if success:
        assert outcome(client, response) == ('printed', '')
    else:
        assert outcome(client, response) == (
            'failed', 'The printer did not report that it finished the label, check whether it was printed')


def test_network_printer_error_after_the_job_is_reported(network_job):
    answer, sent, print_label, client = network_job
    answer['bodies'] = [READY, PRINTING, fixture('ql810w-stopped-other-error.bin')]
    response = print_label()
    assert (response.status_code, outcome(client, response)) == (200, ('failed', 'The printer reports: See the lights or display of the printer'))


def test_job_is_not_sent_to_a_network_printer_with_an_error(network_job):
    answer, sent, print_label, client = network_job
    answer['bodies'] = [COVER_OPEN]
    response = print_label()
    assert (response.status_code, outcome(client, response)) == (200, ('failed', 'The printer reports: The cover is open'))
    assert sent == []


def test_network_printer_without_ipp_prints_as_before(network_job, monkeypatch):
    answer, sent, print_label, client = network_job
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        monkeypatch.setattr(network, 'IPP_PORT', unused.getsockname()[1])
    assert outcome(client, print_label()) == ('printed', '')
    assert sent == ['tcp://127.0.0.1']


def _announced(address, service, port, model):
    return {'name': f'Brother {model}.{service}', 'service': service, 'address': address, 'port': port,
            'txt': {'ty': f'Brother {model}', 'usb_mdl': model}}


def test_announced_label_printers_are_detected(fake_printer, monkeypatch):
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    monkeypatch.setattr(printer, 'NETWORK_DISCOVERY', True)
    monkeypatch.setattr(printer, '_discovered', None)
    # Right after the start of a container the monotonic clock is small
    monkeypatch.setattr(usb.time, 'monotonic', lambda: 5.0)
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer.mdns, 'discover', lambda: [
        _announced('127.0.0.1', '_pdl-datastream._tcp.local', 9100, 'QL-810W'),
        _announced('127.0.0.1', '_ipp._tcp.local', 631, 'QL-810W'),
        # Office printers are not offered
        _announced('192.0.2.7', '_pdl-datastream._tcp.local', 9100, 'MFC-L3760CDW series'),
    ])
    app = create_app()
    app.config['PRINTER_PRINTER'] = '?'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert [(p['path'], p['model']) for p in status['printers']] == [('tcp://127.0.0.1', 'QL-810W'), ('simulation', 'QL-800')]
    assert status['printers'][0]['media_width'] == 62


def test_arp_candidate_that_is_no_label_printer_is_dropped(fake_printer, monkeypatch, tmp_path):
    # The MAC prefix belongs to a Wi-Fi module, also used in other devices
    arp = tmp_path / 'arp'
    arp.write_text('IP address       HW type     Flags       HW address            Mask     Device\n'
                   '127.0.0.1        0x1         0x2         ac:f2:3c:00:00:01     *        eth0\n')
    monkeypatch.setattr(printer, 'ARP_TABLE', str(arp))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    fake_printer['body'] = response(attribute(0x41, 'printer-make-and-model', b'Brother MFC-L3760CDW series'), state(ipp.IDLE))
    app = create_app()
    app.config['PRINTER_PRINTER'] = '?'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert [p['path'] for p in status['printers']] == ['simulation']
    # A label printer with that prefix is kept
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert [p['path'] for p in status['printers']] == ['tcp://127.0.0.1', 'simulation']


def endless_chunks(pause):
    def send(connection):
        connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\nTransfer-Encoding: chunked\r\n\r\n')
        while True:
            connection.sendall(b'4000\r\n' + bytes(0x4000) + b'\r\n')
            time.sleep(pause)
    return send


def trickling_headers(connection):
    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\nX-Slow: ')
    while True:
        connection.sendall(b'a')
        time.sleep(0.05)


def too_long(connection):
    connection.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/ipp\r\nContent-Length: 1000000000\r\n\r\n')
    while True:
        connection.sendall(bytes(4096))
        time.sleep(0.05)


@pytest.mark.parametrize('send', [endless_chunks(0), endless_chunks(0.05), trickling_headers, too_long])
def test_answers_without_end_are_given_up(send):
    printer = Answering(send)
    try:
        started = time.monotonic()
        with pytest.raises((ipp.IPPError, OSError)):
            within(5, lambda: ipp.get_printer_attributes('127.0.0.1', printer.port, timeout=0.5))
        assert time.monotonic() - started < 2
    finally:
        printer.close()


def collection_end(name: str = '') -> bytes:
    return attribute(ipp.TAG_END_COLLECTION, name, b'')


@pytest.mark.parametrize('body', [
    # A member name outside a collection, the end of one never begun
    response(attribute(ipp.TAG_MEMBER_NAME, '', b'x-dimension')),
    response(collection_end('media-col-ready')),
    # Cut off within a value
    response(attribute(ipp.TAG_KEYWORD, 'printer-state-reasons', b'none'))[:-3],
])
def test_malformed_answers_are_refused(body):
    with pytest.raises(ipp.IPPError):
        ipp.decode_response(body)


@pytest.mark.parametrize('attributes, width', [
    ({'media-col-ready': {'media-size': 'x'}}, None),
    ({'media-col-ready': {'media-size': {'x-dimension': 6200, 'y-dimension': 2900}, 'media-source-properties': 5,
                          'media-type': ['labels']}}, 62),
    ({'media-col-ready': [5]}, None),
    ({'printer-state-reasons': [5, None, {'a': 1}]}, None),
    ({'printer-state-reasons': 5}, None),
    ({'printer-make-and-model': 5, 'printer-state': 'idle'}, None),
])
def test_answers_of_strange_types_are_taken_as_telling_nothing(fake_printer, monkeypatch, attributes, width):
    fake_printer['body'] = b'ok'
    monkeypatch.setattr(ipp, 'decode_response', lambda data: attributes)
    status = network.query_network_status('tcp://127.0.0.1', 'QL-800')
    assert (status['model'], status['errors'], status['media_width']) == ('QL-800', [], width)
    assert network.printer_problem('tcp://127.0.0.1', 'QL-800') == ''


def slowly(names, seconds):
    names['slow.test'] = lambda: time.sleep(seconds) or ['127.0.0.1']


def test_a_printer_whose_name_resolves_slowly_is_asked(fake_printer, names):  # noqa: F811
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    slowly(names, 1.2)
    assert ipp.get_printer_attributes('slow.test', network.IPP_PORT, timeout=0.5)['printer-make-and-model'] == 'Brother QL-810W'


def test_an_answer_that_trickles_after_a_slow_name_is_given_up(names):  # noqa: F811
    printer = Answering(trickling_headers)
    slowly(names, 1.2)
    try:
        started = time.monotonic()
        with pytest.raises((ipp.IPPError, OSError)):
            within(8, lambda: ipp.get_printer_attributes('slow.test', printer.port, timeout=0.5))
        assert time.monotonic() - started < 3
    finally:
        printer.close()


def test_an_added_printer_whose_name_resolves_slowly_is_asked(fake_printer, names, monkeypatch):  # noqa: F811
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    slowly(names, 1.2)
    monkeypatch.setattr(reach, '_watched', {'slow.test': 'local'})
    assert ipp.get_printer_attributes('slow.test', network.IPP_PORT, timeout=0.5)['printer-make-and-model'] == 'Brother QL-810W'
