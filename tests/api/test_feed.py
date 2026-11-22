"""Feed and cut: a job of the print queue that feeds the paper of a printer
and cuts it, or feeds one label, where its driver offers it."""

import pytest

from app.labeldesigner import print_queue, printer, refusals
from app.labeldesigner.label_names import label_size_problem

from tests.helpers import make_client, outcome

API = '/labeldesigner/api'
FEED = f'{API}/feed'
STATUS = f'{API}/printer_status'


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path, empty_repo=True, model='QL-800', printer='simulation')


def _demo(client, **settings) -> dict:
    """The demo printer as the status tells it, with settings kept first, one
    after the other: a tape is checked against the model kept before."""
    for name, value in settings.items():
        assert client.post(f'{API}/settings', data={'printer': 'simulation', name: value}).status_code == 200
    return next(p for p in client.get(STATUS).get_json()['printers'] if p['path'] == 'simulation')


@pytest.mark.parametrize('model, label_size, kind', [
    ('QL-800', '62', 'cut'), ('QL-800', '62x29', 'cut_label'), ('QL-800', 'd24', 'cut_label'), ('PT-E550W', 'pt12', 'cut'),
    ('Epson/ESC-POS', 'escpos80', 'cut_paper'), ('Zebra/ZPL', 'zpl57x32', 'label'),
    ('QL-500', '62', None), ('Niimbot/B1', 'niimbot40x30', None), ('VC-500W', 'vc9', None)])
def test_the_status_tells_what_feeding_does_with_the_tape_in_the_printer(client, model, label_size, kind):
    shown = _demo(client, demo_model=model, label_size=label_size)
    assert (shown['model'], shown['label_size']) == (model, label_size)
    assert shown.get('feed') == kind


def test_it_is_a_job_of_the_queue_with_a_plain_title(client, monkeypatch):
    fed = []
    monkeypatch.setattr(printer.PrinterQueue, 'feed', lambda self: fed.append((self.model, self.label_size)) or '')
    answer = client.post(FEED, data={'printer': 'simulation', 'model': 'QL-800'})
    assert answer.status_code == 200 and answer.get_json()['queued'] is True
    assert outcome(client, answer) == ('printed', '')
    job = client.get(f"{API}/queue/{answer.get_json()['job']}").get_json()
    assert (job['title'], job['labels'], job['from'], job['preview']) == ('Feed and cut', 1, '', None)
    assert print_queue.get(answer.get_json()['job']).source == 'feed'
    # One whole label on a die-cut roll
    answer = client.post(FEED, json={'printer': 'simulation', 'model': 'QL-800', 'label_size': '62x29'})
    assert client.get(f"{API}/queue/{answer.get_json()['job']}").get_json()['title'] == 'Feed one label'
    assert fed == [('QL-800', '62'), ('QL-800', '62x29')]


def test_the_demo_printer_feeds_nothing(client):
    for model in ('QL-800', 'PT-E550W', 'Epson/ESC-POS', 'Zebra/ZPL'):
        _demo(client, demo_model=model)
        assert outcome(client, client.post(FEED, data={'printer': 'simulation'})) == ('printed', '')


@pytest.mark.parametrize('model', ['QL-500', 'Niimbot/B1', 'PUQU/Q00', 'VC-500W'])
def test_a_printer_without_it_is_refused_plainly(client, model):
    _demo(client, demo_model=model)
    answer = client.post(FEED, data={'printer': 'simulation'})
    assert (answer.status_code, answer.get_json()) == (400, {'success': False, 'message': printer.NO_FEED})
    assert print_queue.line('simulation')['waiting'] == []


def test_a_label_size_the_printer_does_not_take_is_refused(client):
    answer = client.post(FEED, data={'printer': 'simulation', 'model': 'QL-800', 'label_size': 'pt12'})
    assert answer.status_code == 400
    assert answer.get_json()['message'] == label_size_problem('pt12', 'QL-800') != ''


def test_a_failing_feed_answers_plainly(client, monkeypatch):
    def broken(self):
        raise RuntimeError('/app/instance/secret.json is gone')
    monkeypatch.setattr(printer.PrinterQueue, 'feed', broken)
    answer = client.post(FEED, data={'printer': 'simulation', 'model': 'QL-800'})
    assert outcome(client, answer) == ('failed', refusals.PRINT_FAILED)


def test_the_refusal_is_one_of_the_fixed_messages():
    assert refusals.message(ValueError(printer.NO_FEED), refusals.PRINT_FAILED) == printer.NO_FEED
