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
    found = {'settings': {'continue_at': {'sheet-l7160': 20}}}
    assert mcp_tools._starts(found, 'sheet-l7160', 'next', [2, 2, 21]) == [{'sheet_start': '20'}, {'sheet_start': '1'}, {'sheet_start': '3'}]
    assert mcp_tools._starts({}, 'sheet-l7160', 'next', [1]) == [{'sheet_start': '1'}]
    assert mcp_tools._starts(found, 'sheet-l7160', None, [1, 1]) == [{}, {}]
    # Refused by the API as it is given
    assert mcp_tools._starts(found, 'sheet-l7160', 'x', [1]) == [{'sheet_start': 'x'}]
