"""The MCP tools on sheets of labels of an office printer: a sheet named by
the number on its pack, and where its labels start."""
import os

import pytest

from app.labeldesigner import mcp_tools
from tests.api.test_mcp import call, tool


@pytest.fixture
def client(tmp_path):
    from tests.helpers import make_client
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('Other/Office', 'simulation', 'Office')]
    return client


def pages(tmp_path) -> int:
    folder = tmp_path / 'simulated_labels'
    return len(os.listdir(folder)) if folder.exists() else 0


@pytest.mark.parametrize('model, label_size, size', [('Other/Office', 'L7160', 'sheet-l7160'), ('Other/Office', 'Avery 5160', 'sheet-5160'),
                                                     ('Other/Office', 'Herma 10001', 'sheet-10001'), (None, 'J8160', 'sheet-l7160')])
def test_a_sheet_by_the_number_on_its_pack(model, label_size, size):
    assert mcp_tools.label_size_for(model, label_size) == size


def test_print_text_starts_at_the_label_asked_for(client, tmp_path):
    result = tool(client, 'print_text', printer='Office', label_size='L7160', count=3, lines=['Anna'], sheet_start='20')
    assert not result.get('isError'), result
    # Labels 20 and 21 of the first sheet, 1 of the second
    assert pages(tmp_path) == 2
    tools = {t['name']: t for t in call(client, 'tools/list')[1]['result']['tools']}
    assert all('sheet_start' in tools[name]['inputSchema']['properties'] for name in ('print_label', 'print_text'))


def test_jobs_of_one_call_go_on_one_after_the_other():
    assert mcp_tools._starts('sheet-l7160', '20', 3) == [{'sheet_start': '20'}, {'sheet_start': 'next'}, {'sheet_start': 'next'}]
    assert mcp_tools._starts('sheet-l7160', None, 2) == [{}, {'sheet_start': 'next'}]
    assert mcp_tools._starts('62', '20', 2) == [{}, {}]


def test_calls_in_a_row_go_on_on_the_sheet(client, tmp_path):
    # Labels 20, 21 and 1 of the next sheet, then 2 and 3
    for start, count in (('20', 3), ('next', 2)):
        result = tool(client, 'print_text', printer='Office', label_size='L7160', count=count, lines=['Anna'], sheet_start=start)
        assert not result.get('isError'), result
    assert pages(tmp_path) == 3
    assert client.get('/labeldesigner/api/settings?printer=simulation').get_json()['continue_at'] == {'sheet-l7160': 4}


def test_fillings_of_one_call_go_on_on_the_sheet(client, tmp_path):
    saved = client.post('/labeldesigner/api/repository/save', json={'name': 'Badge', 'label_size': 'sheet-l7160',
                                                                    'text': [{'text': '{{blank:Name}}', 'size': '40'}]})
    assert saved.status_code == 200, saved.get_json()
    result = tool(client, 'print_label', name='Badge.json', printer='Office', count=2, sheet_start='20',
                  fillings=[{'Name': 'Anna'}, {'Name': 'Ben'}])
    assert not result.get('isError'), result
    assert client.get('/labeldesigner/api/settings?printer=simulation').get_json()['continue_at'] == {'sheet-l7160': 3}
