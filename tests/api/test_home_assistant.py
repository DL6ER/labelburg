"""The examples of docs/home-assistant.md: each request they send prints on
the demo printer, as Home Assistant fills in their templates."""

import json
import os
import re
from datetime import date
from urllib.parse import parse_qsl, urlencode, urlsplit

import pytest
import yaml
from jinja2 import StrictUndefined
from jinja2.sandbox import ImmutableSandboxedEnvironment

from tests.helpers import make_client, pixels
from tests.repository.helpers import SAVE_URL

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = os.path.join(ROOT, 'app', 'labeldesigner', 'openapi.json')
SAMPLE = 'Tomato soup'

with open(os.path.join(ROOT, 'docs', 'home-assistant.md'), encoding='utf-8') as _page:
    PAGE = _page.read()
BLOCKS = [yaml.safe_load(block) for block in re.findall(r'```yaml\n(.*?)```', PAGE, re.S)]
COMMANDS = next(block['rest_command'] for block in BLOCKS if 'rest_command' in block)

# Templates as Home Assistant renders them, with its own to_json and urlencode
ENV = ImmutableSandboxedEnvironment(undefined=StrictUndefined)
ENV.filters['to_json'] = lambda value: json.dumps(value, ensure_ascii=False)
ENV.globals['urlencode'] = urlencode


def render(value, variables: dict):
    if isinstance(value, str):
        return ENV.from_string(value).render(variables)
    if isinstance(value, dict):
        return {key: render(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, variables) for item in value]
    return value


def labels() -> dict:
    """The labels the page tells to make, by name, as the label designer
    saves them, a blank named like a date as a date (`guessKind()`)."""
    made = {}
    for name, lines in re.findall(r'^- `([^`]+)` with the lines? (.*)$', PAGE, re.M):
        text = [{'text': re.sub(r'\[([^]]+)\]', r'{{blank:\1}}', line), 'size': '40', 'font': 'DejaVu Sans,Book'}
                for line in re.findall(r'`([^`]+)`', lines)]
        blanks = re.findall(r'\[([^]]+)\]', lines)
        dates = [blank for blank in blanks if re.search(r'(^|\s)on$|\bdate\b', blank, re.I)]
        made[name] = {'name': name, 'title': name, 'text': text, 'fields': {blank: {'type': 'date'} for blank in dates}}
    return made


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    return client


@pytest.fixture
def saved(client):
    for label in labels().values():
        assert client.post(SAVE_URL, json=label).status_code == 200


def send(client, command: dict, data: dict) -> dict:
    """What rest_command sends for the action data, and its answer as
    response_variable holds it."""
    url = urlsplit(render(command['url'], data))
    response = client.open(url.path, base_url=f'{url.scheme}://{url.netloc}', method=command['method'].upper(),
                           data=render(command['payload'], data).encode(), content_type=command['content_type'])
    content = response.get_json() if response.is_json else response.get_data(as_text=True)
    return {'status': response.status_code, 'content': content, 'headers': dict(response.headers)}


def run(client, steps: list, variables: dict, sent: list, said: list):
    """The steps of a script or automation: requests to Labelburg go to
    sent, what Home Assistant says or shows to said."""
    for step in steps:
        if 'if' in step:
            run(client, step['then'] if render(step['if'], variables) == 'True' else step.get('else', []), variables, sent, said)
        elif 'set_conversation_response' in step:
            said.append(render(step['set_conversation_response'], variables))
        elif step['action'].startswith('rest_command.'):
            data = render(step.get('data', {}), variables)
            sent.append((data, send(client, COMMANDS[step['action'].removeprefix('rest_command.')], data)))
            if 'response_variable' in step:
                variables[step['response_variable']] = sent[-1][1]
        else:
            said.append(render(step.get('data', {}), variables))


def examples() -> list:
    """The steps of each example and the values a user gives them: the
    fields of a script and the words of a sentence."""
    found = []
    for block in BLOCKS:
        if isinstance(block, dict) and str(block.get('action', '')).startswith('rest_command.'):
            found.append(pytest.param([block], {}, id=block['action']))
        elif isinstance(block, dict) and ('sequence' in block or 'actions' in block):
            variables = {field: SAMPLE for field in block.get('fields', {})}
            words = [name for trigger in block.get('triggers', []) for name in re.findall(r'\{(\w+)\}', trigger.get('command', ''))]
            if words:
                variables['trigger'] = {'slots': {name: SAMPLE for name in words}}
            found.append(pytest.param(block.get('sequence') or block['actions'], variables, id=block['alias']))
    return found


def test_the_page_has_its_examples():
    # Each kind the page shows: actions, a script, a tag and a sentence
    assert len(examples()) == 5
    assert set(COMMANDS) == {'print_label', 'print_text'}
    assert set(labels()) == {'Frozen on', 'Freezer'}
    # The card opens the script, named after its alias
    script = next(block for block in BLOCKS if 'sequence' in block)
    card = next(block for block in BLOCKS if block.get('type') == 'button')
    assert card['entity'] == 'script.' + re.sub(r'\W+', '_', script['alias'].lower())


@pytest.mark.parametrize('steps, variables', examples())
def test_every_example_prints(client, saved, steps, variables):
    sent, said = [], []
    run(client, steps, dict(variables), sent, said)
    assert sent
    for data, answer in sent:
        assert answer['status'] == 200 and answer['content']['success'] is True, answer
        assert answer['content']['labels'] == data.get('count', 1)
        assert client.get(f"/labeldesigner/api/queue/{answer['content']['job']}").get_json()['state'] == 'printed'
    assert all('Not found' not in str(told) for told in said)


@pytest.mark.parametrize('steps, variables', [example for example in examples() if 'response_variable' in str(example.values[0])])
def test_a_refusal_is_told(client, steps, variables):
    # No label is saved
    sent, said = [], []
    run(client, steps, dict(variables), sent, said)
    assert [answer['status'] for _, answer in sent] == [404]
    assert said and all('Not found' in str(told) for told in said)


def test_a_date_left_out_is_today(client, saved):
    def preview(blanks):
        response = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Freezer', 'blanks': json.dumps(blanks)})
        assert response.status_code == 200
        return pixels(response.data)
    assert preview({'Food': SAMPLE}) == preview({'Food': SAMPLE, 'Date': date.today().isoformat()})
    assert preview({'Food': SAMPLE}) != preview({'Food': SAMPLE, 'Date': '2000-01-01'})


def test_the_printer_sensors_read_the_status(client):
    rest = next(block['rest'] for block in BLOCKS if 'rest' in block)
    for source in rest:
        url = urlsplit(source['resource'])
        answer = client.get(url.path, base_url=f'{url.scheme}://{url.netloc}')
        assert answer.status_code == 200
        for sensor in source['sensor']:
            assert render(sensor['value_template'], {'value_json': answer.get_json()}) != ''


def _properties(schema: dict, spec: dict) -> dict:
    """The properties a schema allows, of its references and allOf too."""
    if '$ref' in schema:
        return _properties(spec['components']['schemas'][schema['$ref'].split('/')[-1]], spec)
    found = dict(schema.get('properties', {}))
    for part in schema.get('allOf', []):
        found.update(_properties(part, spec))
    return found


def test_the_page_names_only_routes_and_fields_of_the_api():
    with open(SPEC, encoding='utf-8') as fh:
        spec = json.load(fh)
    for path in re.findall(r'/labeldesigner/api/[\w/]+', PAGE):
        assert path in spec['paths'], path
    for steps, variables in (example.values for example in examples()):
        sent = []
        for step in steps:
            if str(step.get('action', '')).startswith('rest_command.'):
                sent.append((COMMANDS[step['action'].removeprefix('rest_command.')], render(step.get('data', {}), variables)))
        for command, data in sent:
            operation = spec['paths'][urlsplit(command['url']).path][command['method']]
            body = render(command['payload'], data)
            fields = json.loads(body) if command['content_type'] == 'application/json' else dict(parse_qsl(body))
            assert set(fields) <= set(_properties(operation['requestBody']['content'][command['content_type']]['schema'], spec))
            for line in json.loads(fields.get('text', '[]')):
                assert set(line) <= set(_properties(spec['components']['schemas']['TextLine'], spec))
