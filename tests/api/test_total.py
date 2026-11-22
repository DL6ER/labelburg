"""{{total}}, the number of labels of a print job: the values the counter
takes, for "Box 1 of 6", printed and in the preview."""
import io
import json

import pytest
from PIL import Image

from app.labeldesigner.label import SimpleLabel
from tests.helpers import EXAMPLE_FORMDATA, _pdf, _upload, make_client, outcome, printing

API = '/labeldesigner/api'
BOX = 'Box {{counter}} of {{total}}'
B1 = {'printer': 'simulation', 'model': 'Niimbot/B1'}


def _lines(*texts, size='40'):
    return json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': size} for text in texts])


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, empty_repo=True)


@pytest.fixture
def drawn(monkeypatch):
    """The lines of each label drawn, and its code, as (printed, lines):
    printed for those the queue prints, not for previews and the check of
    the first label of a job."""
    labels = []
    original = SimpleLabel.process_templates

    def keep(self):
        original(self)
        labels.append((printing(), [line['text'] for line in self.text] + ([self.code_text] if self.code_text else [])))
    monkeypatch.setattr(SimpleLabel, 'process_templates', keep)
    return labels


def printed(drawn) -> list:
    return [lines for queued, lines in drawn if queued]


def previewed(client, drawn, **values) -> list:
    """The lines of the labels a preview draws."""
    drawn.clear()
    response = client.post(f'{API}/preview', data={**EXAMPLE_FORMDATA, 'text': _lines(BOX), **values})
    assert response.status_code == 200, response.get_json()
    return [lines for _, lines in drawn]


def _print(client, **values) -> int:
    """The number of labels the answer tells."""
    response = client.post(f'{API}/print', data={**EXAMPLE_FORMDATA, 'printer': 'simulation', 'text': _lines(BOX), **values})
    assert outcome(client, response) == ('printed', ''), response.get_json()
    return response.get_json()['labels']


def test_copies(client, drawn):
    # The answer tells as many, e.g. for the list of labels printed
    assert _print(client, print_count='3') == 3
    assert printed(drawn) == [['Box 1 of 3'], ['Box 2 of 3'], ['Box 3 of 3']]


def test_one_label_without_copies(client, drawn):
    data = {key: value for key, value in EXAMPLE_FORMDATA.items() if key != 'print_count'}
    response = client.post(f'{API}/print', data={**data, 'printer': 'simulation', 'text': _lines(BOX)})
    assert outcome(client, response) == ('printed', '')
    assert printed(drawn) == [['Box 1 of 1']]


@pytest.mark.parametrize('template, values', [
    # The number of labels, not the last value of the counter
    ('{{counter:10:5}} of {{total}}', ['10 of 3', '15 of 3', '20 of 3']),
    ('{{counter:A}}/{{total}}', ['A/3', 'B/3', 'C/3']),
    ('{{counter:001}}-{{total}}-{{total}}', ['001-3-3', '002-3-3', '003-3-3']),
])
def test_counter_with_a_start_and_a_step(client, drawn, template, values):
    _print(client, print_count='3', text=_lines(template))
    assert printed(drawn) == [[value] for value in values]


def test_in_the_code(client, drawn):
    _print(client, print_count='2', print_type='qrcode_text', code_text='Box {{counter}}/{{total}}', text=_lines('Box'))
    assert printed(drawn) == [['Box', 'Box 1/2'], ['Box', 'Box 2/2']]


@pytest.mark.parametrize('collate', ['1', '0'])
def test_copies_of_several_files_and_pages(client, drawn, collate):
    # Every page of every file of every copy is a label the counter counts,
    # as sets or grouped
    files = [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
             (io.BytesIO(_upload(Image.new('L', (400, 100), 'black'), 'wide.png').stream.read()), 'wide.png', 'image/png')]
    assert _print(client, print_type='image', image_fit='1', print_count='2', pdf_pages='all', collate=collate, image=files) == 6
    assert printed(drawn) == [[f'Box {n} of 6'] for n in range(1, 7)]


def test_roll_with_several_labels_to_a_page(client, drawn):
    response = client.post(f'{API}/print', data={'label_size': 'niimbot15x8-2x3', 'text': _lines('{{counter}}/{{total}}', size='20'),
                                                 'print_count': '8', **B1})
    # Labels, not pages
    assert outcome(client, response) == ('printed', '') and response.get_json()['labels'] == 8
    assert printed(drawn) == [[f'{n}/8'] for n in range(1, 9)]


def test_preview_of_a_roll_with_several_labels_to_a_page(client, drawn):
    # The first page with the copies on it, each of them of all the labels
    values = {'label_size': 'niimbot15x8-2x3', 'text': _lines('{{counter}}/{{total}}', size='20'), **B1}
    assert previewed(client, drawn, **values, print_count='3') == [['1/3'], ['2/3'], ['3/3']]
    assert previewed(client, drawn, **values, print_count='3', labels_per_copy='2') == [[f'{n}/6'] for n in range(1, 7)]


@pytest.mark.parametrize('values, shown', [
    ({}, 'Box 1 of 1'),
    ({'print_count': '6'}, 'Box 1 of 6'),
    # Of the files and pages the label designer does not send with a preview
    ({'print_count': '3', 'labels_per_copy': '2'}, 'Box 1 of 6'),
    # Counts that are no number count one, as for the page of a roll
    ({'print_count': 'abc', 'labels_per_copy': '2.5'}, 'Box 1 of 1'),
])
def test_preview(client, drawn, values, shown):
    assert previewed(client, drawn, **values) == [[shown]]


def test_preview_of_a_job_printed_before(client, drawn):
    # It tells its number of labels itself, the copies on a page roll stay as asked
    assert previewed(client, drawn, total='6') == [['Box 1 of 6']]
    assert previewed(client, drawn, total='6', print_count='2', labels_per_copy='2') == [['Box 1 of 6']]
    assert previewed(client, drawn, total='abc', print_count='2') == [['Box 1 of 2']]
    values = {'label_size': 'niimbot15x8-2x3', 'text': _lines('{{counter}}/{{total}}', size='20'), **B1}
    response = client.post(f'{API}/preview', data={**values, 'total': '6'})
    assert response.status_code == 200 and json.loads(response.headers['X-Label-Sheet'])['filled'] == 1
    drawn.clear()
    assert client.post(f'{API}/preview', data={**values, 'total': '6', 'print_count': '2'}).status_code == 200
    assert [lines for _, lines in drawn] == [['1/6'], ['2/6']]


@pytest.mark.parametrize('print_type, pages, shown', [
    ('image', 'all', 'Box 1 of 6'),
    ('image', '2-3', 'Box 1 of 4'),
    # A text label prints once for each copy, whatever file comes with it
    ('text', 'all', 'Box 1 of 2'),
])
def test_preview_of_the_pages_of_a_pdf(client, drawn, print_type, pages, shown):
    pdf = (io.BytesIO(_pdf((300, 200), (300, 200), (300, 200))), 'pages.pdf')
    assert previewed(client, drawn, print_type=print_type, pdf_pages=pages, print_count='2', image=pdf) == [[shown]]


@pytest.mark.parametrize('values, shown', [
    ({}, 'Box 1 of 6'),
    # The label designer tells them, it sends the file shown only
    ({'labels_per_copy': '4'}, 'Box 1 of 8'),
    # A text label prints once for each copy
    ({'print_type': 'text'}, 'Box 1 of 2'),
])
def test_preview_of_several_files(client, drawn, values, shown):
    # Without labels_per_copy every file sent and its pages, as printed
    files = [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
             (io.BytesIO(_upload(Image.new('L', (400, 100), 'black'), 'wide.png').stream.read()), 'wide.png', 'image/png')]
    data = {'print_type': 'image', 'image_fit': '1', 'print_count': '2', 'pdf_pages': 'all', 'image': files, **values}
    assert previewed(client, drawn, **data) == [[shown]]


def test_saved_label(client, drawn):
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': BOX}]
    assert client.post(f'{API}/repository/save', json={'name': 'Box.json', 'label_size': '62', 'text': text}).status_code == 200
    response = client.post(f'{API}/repository/print', json={'name': 'Box.json', 'print_count': 2})
    assert outcome(client, response) == ('printed', '') and response.get_json()['labels'] == 2
    assert printed(drawn) == [['Box 1 of 2'], ['Box 2 of 2']]
    # Its preview as it prints with as many copies
    for query, shown in (({}, 'Box 1 of 1'), ({'print_count': '4'}, 'Box 1 of 4')):
        drawn.clear()
        assert client.get(f'{API}/repository/preview', query_string={'name': 'Box.json', **query}).status_code == 200
        assert drawn == [(False, [shown])]


def test_the_counter_alone_is_as_before(client, drawn):
    _print(client, print_count='2', text=_lines('Box {{counter}}', '{{totals}} {{total:3}}'))
    assert printed(drawn) == [['Box 1', '{{totals}} {{total:3}}'], ['Box 2', '{{totals}} {{total:3}}']]
