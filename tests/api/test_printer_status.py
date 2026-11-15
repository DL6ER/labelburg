"""The printer status of the API: what each printer reports, and the label
size it prints on."""

from app.labeldesigner import routes
from tests.helpers import make_client


def _printer(path, model, **status):
    return {'errors': [], 'path': path, 'model': model, 'media_width': None, 'media_length': 0, **status}


def test_label_size_of_each_printer(tmp_path, monkeypatch):
    printers = [_printer('file:///dev/usb/lp0', 'QL-800', media_width=29, media_length=90),
                _printer('tcp://192.0.2.1', 'PUQU/Q00'),
                _printer('simulation', 'QL-800')]
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config: {**printers[0], 'printers': printers})
    client = make_client(tmp_path)
    client.application.config['LABEL_DEFAULT_SIZE'] = '62'
    status = client.get('/labeldesigner/api/printer_status').get_json()
    # The tape reported, else assumed: the default or the first one the
    # printer takes
    assert [(p['label_size'], p['label_size_assumed']) for p in status['printers']] == [
        ('29x90', False), ('puqu50x30', True), ('62', True)]
    assert (status['label_size'], status['label_size_assumed']) == ('29x90', False)
