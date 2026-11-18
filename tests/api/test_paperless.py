"""Archive serial numbers (ASN) of Paperless-ngx on labels: {{asn}} is the
next free one, counted on for each label and never printed twice."""

import io
import json
import urllib.error

import pytest

from app.labeldesigner import paperless
from app.labeldesigner.printer import PrinterQueue
from tests.helpers import make_client

TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'ASN{{asn:5}}', 'size': '40'}])
LABEL = {'print_type': 'qrcode_text', 'label_size': '62', 'text': TEXT, 'code_text': 'ASN{{asn:5}}', 'barcode_type': 'QR'}


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTER_PRINTER'] = 'simulation'
    return client


@pytest.fixture
def printed(monkeypatch):
    """The text and code of each label of each job sent to the printer."""
    jobs = []

    def process_queue(self):
        labels = [entry['label'] for entry in self._print_queue]
        for label in labels:
            label.generate()
        jobs.append([(label.text[0]['text'], label.code_text) for label in labels])
        self._print_queue.clear()
        return ''
    monkeypatch.setattr(PrinterQueue, 'process_queue', process_queue)
    return jobs


class Paperless:
    """Answers as Paperless-ngx with its next free ASN, notes each request."""

    def __init__(self, monkeypatch, client, next_asn=1):
        self.next_asn, self.requests, self.error = next_asn, [], None
        client.application.config['PAPERLESS_URL'] = 'http://paperless.example:8000/'
        client.application.config['PAPERLESS_TOKEN'] = 'abc123'
        monkeypatch.setattr(paperless, 'urlopen', self.urlopen)
        monkeypatch.setattr(paperless, '_answered', (0.0, '', 0))

    def urlopen(self, request, timeout):
        self.requests.append((request.full_url, request.get_header('Authorization'), timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.next_asn).encode())


def print_label(client, count=1, **values):
    return client.post('/labeldesigner/api/print', data=dict(LABEL, print_count=str(count), **values))


def test_each_label_gets_the_next_asn(client, printed):
    assert print_label(client, 3).get_json() == {'success': True}
    assert printed == [[('ASN00001', 'ASN00001'), ('ASN00002', 'ASN00002'), ('ASN00003', 'ASN00003')]]


def test_labels_printed_again_get_numbers_of_their_own(client, printed):
    # The documents were not scanned in between
    print_label(client, 2)
    print_label(client, 2)
    assert [label[0] for job in printed for label in job] == ['ASN00001', 'ASN00002', 'ASN00003', 'ASN00004']


def test_numbers_as_long_as_they_are(client, printed):
    print_label(client, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'ASN{{asn}}', 'size': '40'}]))
    assert printed[0][0][0] == 'ASN1'


def test_asn_starts_at_the_next_one_of_paperless(client, printed, monkeypatch):
    server = Paperless(monkeypatch, client, next_asn=42)
    print_label(client, 2)
    assert [label[0] for label in printed[0]] == ['ASN00042', 'ASN00043']
    assert server.requests == [('http://paperless.example:8000/api/documents/next_asn/', 'Token abc123', paperless.TIMEOUT)]
    # Paperless does not know them before their documents are scanned
    print_label(client, 1)
    assert printed[1][0][0] == 'ASN00044'
    # Documents numbered in Paperless meanwhile
    server.next_asn = 70
    print_label(client, 1)
    assert printed[2][0][0] == 'ASN00070'


@pytest.mark.parametrize('error, message', [
    (urllib.error.URLError('Name or service not known'), paperless.UNREACHABLE),
    (TimeoutError('timed out'), paperless.UNREACHABLE),
    (urllib.error.HTTPError('http://paperless.example:8000/api/documents/next_asn/', 401, 'Unauthorized', None, None), paperless.REFUSED),
])
def test_without_the_next_asn_of_paperless_nothing_is_printed(client, printed, monkeypatch, error, message):
    server = Paperless(monkeypatch, client)
    server.error = error
    response = print_label(client)
    assert response.status_code == 400 and response.get_json()['message'] == message
    assert printed == []
    # Nothing was taken
    server.error = None
    print_label(client)
    assert printed[0][0][0] == 'ASN00001'


def test_answer_that_is_no_number_is_refused(client, printed, monkeypatch):
    server = Paperless(monkeypatch, client)
    server.next_asn = 'soon'
    assert print_label(client).get_json()['message'] == paperless.UNREACHABLE


def test_preview_shows_the_next_asn_without_taking_it(client, printed, monkeypatch):
    server = Paperless(monkeypatch, client, next_asn=7)
    for _ in range(2):
        response = client.post('/labeldesigner/api/preview', data=LABEL)
        assert response.status_code == 200
    print_label(client)
    assert printed[0][0][0] == 'ASN00007'
    # Previews ask Paperless once in a while, not at every change
    assert len(server.requests) == 2


def test_labels_without_asn_do_not_ask_paperless(client, printed, monkeypatch):
    server = Paperless(monkeypatch, client)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Invoices 2026', 'size': '40'}])
    client.post('/labeldesigner/api/print', data={'print_type': 'text', 'label_size': '62', 'text': text})
    client.post('/labeldesigner/api/preview', data={'print_type': 'text', 'label_size': '62', 'text': text})
    assert server.requests == []


def test_numbers_of_a_job_the_printer_failed_are_not_taken_again(client, monkeypatch):
    # Some of its labels may have come out
    monkeypatch.setattr(PrinterQueue, 'process_queue', lambda self: 'The printer reports: Cover open')
    print_label(client, 3)
    jobs = []
    monkeypatch.setattr(PrinterQueue, 'process_queue', lambda self: jobs.append(
        [entry['label'].generate() and entry['label'].text[0]['text'] for entry in self._print_queue]) or '')
    print_label(client)
    assert jobs == [['ASN00004']]


def test_saved_label_counts_on(client, printed):
    label = dict(LABEL, name='ASN.json')
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    client.post('/labeldesigner/api/repository/print?print_count=2', json={'name': 'ASN.json'})
    print_label(client)
    assert [label[0] for job in printed for label in job] == ['ASN00001', 'ASN00002', 'ASN00003']


def test_demo_label_for_paperless(client, printed):
    client.post('/labeldesigner/api/demos/print?id=office/015-paperless-asn-17x54.json')
    assert printed == [[('ASN00001', 'ASN00001')]]
