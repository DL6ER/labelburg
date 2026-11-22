"""Lines in capitals: a style of the line, drawn in capitals once its
templates and blanks are filled in. The text, a code that takes it and the
title of a print job keep it as typed."""

import json

import pytest

from app.labeldesigner import label as label_module
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import EXAMPLE_FORMDATA, make_client, pixels


def line(text, **more):
    return dict({'font': 'DejaVu Sans,Book', 'text': text, 'size': '50', 'align': 'center'}, **more)


def preview(client, lines, **settings):
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text_fit='none', text=json.dumps(lines))
    response = client.post('/labeldesigner/api/preview', data=dict(data, **settings))
    assert response.status_code == 200, response.data
    return response


@pytest.mark.parametrize('value', [False, None])
def test_lines_without_capitals_stay_as_they_were(tmp_path, value):
    client = make_client(tmp_path)
    plain = preview(client, [line('Shelf 3'), line('spices', size='30')])
    unset = preview(client, [line('Shelf 3', caps=value), line('spices', size='30', caps=value)])
    assert pixels(unset.data) == pixels(plain.data)


@pytest.mark.parametrize('typed, drawn', [
    ('Shelf 3', 'SHELF 3'),
    # German sharp s becomes two letters
    ('Straße', 'STRASSE'),
    # Icons and symbols are no letters
    ('{{mdi:lightbulb}} Lamps', '{{mdi:lightbulb}} LAMPS'),
])
def test_a_line_in_capitals_is_drawn_in_capitals(tmp_path, typed, drawn):
    client = make_client(tmp_path)
    capitals = preview(client, [line(typed, caps=True), line('spices', size='30')])
    assert pixels(capitals.data) == pixels(preview(client, [line(drawn), line('spices', size='30')]).data)
    # Only the line set so
    assert pixels(capitals.data) != pixels(preview(client, [line(drawn), line('SPICES', size='30')]).data)


def test_capitals_apply_to_the_values_of_blanks(tmp_path):
    client = make_client(tmp_path)
    fields = json.dumps({'Name': {'type': 'text'}})
    filled = preview(client, [line('For {{blank:Name}}', caps=True)], blanks=json.dumps({'Name': 'anna'}), fields=fields)
    assert pixels(filled.data) == pixels(preview(client, [line('FOR ANNA')]).data)
    # A preview shows a blank left empty by its name
    marked = preview(client, [line('For {{blank:Name}}', caps=True)], fields=fields, mark_blanks='1')
    assert pixels(marked.data) == pixels(preview(client, [line('FOR NAME?')]).data)


@pytest.mark.parametrize('barcode_type', ['QR', 'code128'])
def test_a_code_that_takes_the_text_keeps_it_as_typed(tmp_path, monkeypatch, barcode_type):
    codes = []
    two_d, barcode = label_module._code_image, label_module._barcode_image
    monkeypatch.setattr(label_module, '_code_image', lambda kind, data, *args: codes.append(data) or two_d(kind, data, *args))
    monkeypatch.setattr(label_module, '_barcode_image', lambda cls, kind, value, *args: codes.append(value) or barcode(cls, kind, value, *args))
    client = make_client(tmp_path)
    preview(client, [line('Shelf {{blank:Shelf}}', caps=True)], print_type='qrcode_text', barcode_type=barcode_type, code_text='',
            blanks=json.dumps({'Shelf': 'b3'}), fields=json.dumps({'Shelf': {'type': 'text'}}), label_size='62')
    assert codes and set(codes) == {'Shelf b3'}


def test_the_text_of_the_label_stays_as_typed(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', text=json.dumps([line('Shelf 3', caps=True)]))
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert label.text[0]['text'] == 'Shelf 3'
        assert [entry[2]['text'] for entry in label._measure()[0]] == ['SHELF 3']


def test_a_wrapped_line_in_capitals_wraps_as_drawn(tmp_path):
    # Wider in capitals, it wraps into more lines
    client = make_client(tmp_path)
    text = 'Store in a cool and dry place'
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', text_wrap='1', text_fit='none')
    with client.application.test_request_context():
        def drawn(**more):
            label = create_label_from_request(dict(data, text=json.dumps([line(text, size='40', **more)])))
            label.generate()
            return [entry[2]['text'] for entry in label._measure()[0]]
        plain, capitals = drawn(), drawn(caps=True)
    assert ' '.join(capitals) == text.upper() and len(capitals) > len(plain)


def test_a_saved_label_keeps_its_capitals(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    lines = [line('Shelf 3', caps=True)]
    label = dict(EXAMPLE_FORMDATA, name='Caps', label_size='62x29', text_fit='none', text=lines)
    assert client.post('/labeldesigner/api/repository/save', json=label).get_json()['success']
    assert json.loads(client.get('/labeldesigner/api/repository/load?name=Caps.json').get_json()['text'])[0]['caps'] is True
    saved = client.post('/labeldesigner/api/repository/preview', json={'name': 'Caps.json'})
    assert pixels(saved.data) == pixels(preview(client, [line('SHELF 3')]).data)
