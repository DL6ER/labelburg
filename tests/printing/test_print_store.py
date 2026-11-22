"""The print queue and the labels printed, kept on disk: what is written
where, what a start finds and how the history is kept."""
import base64
import fcntl
import io
import json
import os
import stat
import threading
import time

import pytest
from PIL import Image

from app.labeldesigner import adding, paperless, print_queue, print_store, printer, routes
from tests.helpers import EXAMPLE_FORMDATA, make_client, without_status


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(print_store, 'ENABLED', True)
    monkeypatch.setattr(print_store, 'FOLDER', None)
    folder = tmp_path / 'labels'
    folder.mkdir()
    assert print_store.start(str(folder)) == []
    yield folder
    print_store.close()


def record(job_id='a1', images=1, kind='request', queued_at=None, **more):
    return {'v': 1, 'id': job_id, 'queued_at': queued_at or time.time(), 'source': 'api', 'title': 'Jar', 'labels': 1,
            'state': 'waiting', 'kind': kind, 'images': images,
            'printer': {'device': 'simulation', 'model': 'QL-800', 'label_size': '62'}, **more}


def files(folder) -> list[str]:
    return sorted(name for name in os.listdir(folder) if name != print_store.LOCK_FILE)


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


IMAGE = Image.new('L', (20, 10), 128)


def test_nothing_is_kept_unless_the_store_is_on(tmp_path):
    # As in every test that does not open it
    assert print_store.start(str(tmp_path)) == []
    assert not print_store.is_open()
    assert print_store.queued(record(), [IMAGE], b'png') is False
    assert os.listdir(tmp_path) == []


def test_a_job_is_kept_with_its_images_readable_by_the_server_only(store):
    queue = store / print_store.QUEUE
    assert mode(queue) & 0o077 == 0
    assert print_store.queued(record(), [IMAGE], b'preview') is True
    assert files(queue) == ['a1-0.png', 'a1-preview.png', 'a1.json']
    assert all(mode(queue / name) == 0o600 for name in files(queue))
    with Image.open(queue / 'a1-0.png') as kept:
        assert kept.mode == 'L' and kept.tobytes() == IMAGE.tobytes()
    assert json.loads((queue / 'a1.json').read_text())['state'] == 'waiting'
    print_store.started(rec := record())
    assert rec['state'] == 'printing' and rec['started_at'] > 0


def test_an_image_png_does_not_take_is_kept_as_rgb(store):
    assert print_store.queued(record(), [Image.new('CMYK', (4, 4), (0, 255, 0, 0))], None)
    with Image.open(store / print_store.QUEUE / 'a1-0.png') as kept:
        assert kept.mode == 'RGB'


def test_a_full_disk_keeps_the_job_in_memory_and_says_so_once(store, monkeypatch, caplog):
    monkeypatch.setattr(print_store, 'MIN_FREE_BYTES', 1 << 62)
    assert print_store.queued(record('a1'), [IMAGE], b'png') is False
    assert print_store.queued(record('a2'), [IMAGE], b'png') is False
    assert files(store / print_store.QUEUE) == []
    assert sum('kept in memory only' in message for message in caplog.messages) == 1


def test_a_failed_write_leaves_nothing_behind(store, monkeypatch):
    write = print_store._write

    def fails_on_the_record(path, content):
        if path.endswith('.json'):
            raise OSError(28, 'No space left on device')
        write(path, content)
    monkeypatch.setattr(print_store, '_write', fails_on_the_record)
    assert print_store.queued(record(), [IMAGE], b'png') is False
    assert files(store / print_store.QUEUE) == []


def test_a_start_finds_the_jobs_left_oldest_first_and_cleans_up(store):
    queue = store / print_store.QUEUE
    print_store.queued(record('new', queued_at=200.0), [IMAGE], None)
    print_store.queued(record('old', queued_at=100.0, state='printing', images=0), [], b'png')
    (queue / '.0123.tmp').write_bytes(b'half')
    # Images of a job that ended without its record
    (queue / 'gone-0.png').write_bytes(b'png')
    found = print_store.start(str(store))
    assert [(job['id'], job['state']) for job in found] == [('old', 'printing'), ('new', 'waiting')]
    assert files(queue) == ['new-0.png', 'new.json', 'old-preview.png', 'old.json']


@pytest.mark.parametrize('broken', ['not json', 'unknown version', 'image missing', 'no printer'])
def test_a_job_that_cannot_be_read_is_moved_aside_and_the_start_goes_on(store, broken, caplog):
    queue = store / print_store.QUEUE
    print_store.queued(record('good'), [IMAGE], None)
    print_store.queued(record('bad', **({'v': 2} if broken == 'unknown version' else {})), [IMAGE], b'png')
    if broken == 'not json':
        (queue / 'bad.json').write_text('{"v": 1, "id": "ba')
    elif broken == 'image missing':
        os.remove(queue / 'bad-0.png')
    elif broken == 'no printer':
        (queue / 'bad.json').write_text(json.dumps(dict(record('bad'), printer=None)))
    assert [job['id'] for job in print_store.start(str(store))] == ['good']
    assert files(queue) == ['broken', 'good-0.png', 'good.json']
    assert 'bad.json' in os.listdir(queue / 'broken')
    assert any('bad cannot be read' in message for message in caplog.messages)


def test_a_job_already_in_the_history_is_not_printed_again(store):
    print_store.queued(rec := record(), [IMAGE], None)
    queue = store / print_store.QUEUE
    kept = {name: (queue / name).read_bytes() for name in files(queue)}
    print_store.ended(rec, 'printed', '', keep=True)
    # As if the server stopped before the files of the queue went
    for name, content in kept.items():
        (queue / name).write_bytes(content)
    assert print_store.start(str(store)) == []
    assert files(queue) == []


def test_a_second_process_keeps_its_queue_in_memory_but_adds_to_the_history(store):
    print_store.queued(record('left'), [], None)
    other = os.open(store / print_store.QUEUE / print_store.LOCK_FILE, os.O_RDWR)
    try:
        # The lock of a file opened elsewhere refuses as one of another process
        print_store.close()
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert print_store.start(str(store)) == []
        assert print_store.queued(record('mine'), [IMAGE], b'png') is False
        print_store.ended(record('mine'), 'printed', '', keep=True, images=[IMAGE], preview=b'png')
    finally:
        os.close(other)
    assert [entry['id'] for entry in print_store.entries(10)] == ['mine']
    assert print_store.image_path('mine', 0) and print_store.preview_path('mine')
    # The queue of the other is left as it is
    assert files(store / print_store.QUEUE) == ['left.json']


def test_an_ended_job_moves_to_the_history(store):
    print_store.queued(rec := record(images=2), [IMAGE, IMAGE], b'preview')
    print_store.ended(rec, 'failed', 'The cover is open', keep=True)
    assert files(store / print_store.QUEUE) == []
    history = store / print_store.HISTORY
    assert files(history) == ['a1-0.png', 'a1-1.png', 'a1-preview.png', 'history.jsonl']
    assert all(mode(history / name) == 0o600 for name in files(history))
    [entry] = print_store.entries(10)
    assert (entry['id'], entry['state'], entry['message'], entry['title']) == ('a1', 'failed', 'The cover is open', 'Jar')
    assert entry['ended_at'] > 0 and print_store.images_kept(entry)
    assert print_store.entry('a1') == entry and print_store.entry('nope') is None


@pytest.mark.parametrize('kind,keep', [('request', False), ('calibration', True)])
def test_a_job_not_kept_leaves_no_trace(store, kind, keep):
    print_store.queued(rec := record(kind=kind), [IMAGE], b'png')
    print_store.ended(rec, 'printed', '', keep=keep)
    assert files(store / print_store.QUEUE) == []
    assert files(store / print_store.HISTORY) == []


def test_entries_newest_first_a_page_at_a_time(store):
    for number in range(5):
        print_store.ended(record(f'j{number}', images=0), 'printed', '', keep=True)
    assert [entry['id'] for entry in print_store.entries(2)] == ['j4', 'j3']
    assert [entry['id'] for entry in print_store.entries(2, before='j3')] == ['j2', 'j1']
    assert print_store.entries(2, before='unknown') == []


def test_lines_that_cannot_be_read_and_old_entries_are_left_out(store, monkeypatch):
    print_store.ended(record('j1', images=0), 'printed', '', keep=True)
    path = store / print_store.HISTORY / print_store.HISTORY_FILE
    newer = json.dumps(dict(record('j2', images=0), v=2, ended_at=time.time()))
    old = json.dumps(dict(record('j0', images=0), ended_at=time.time() - 400 * 86400))
    with open(path, 'a') as fh:
        fh.write(f'{newer}\n{old}\n{{"v": 1, "id": "torn"')
    assert [entry['id'] for entry in print_store.entries(10)] == ['j1']
    # Appended after a torn line, the next one reads
    print_store.ended(record('j3', images=0), 'printed', '', keep=True)
    assert [entry['id'] for entry in print_store.entries(10)] == ['j3', 'j1']


def test_the_history_is_cut_back_to_the_newest(store, monkeypatch):
    monkeypatch.setattr(print_store, 'HISTORY_KEEP', 4)
    for number in range(6):
        print_store.queued(rec := record(f'j{number}'), [IMAGE], b'png')
        print_store.ended(rec, 'printed', '', keep=True)
    assert [entry['id'] for entry in print_store.entries(10)] == ['j5', 'j4', 'j3', 'j2', 'j1', 'j0']
    print_store.ended(record('j6', images=0), 'printed', '', keep=True)
    assert [entry['id'] for entry in print_store.entries(10)] == ['j6', 'j5', 'j4', 'j3']
    assert not any(name.startswith(('j0', 'j1', 'j2')) for name in files(store / print_store.HISTORY))


def test_a_start_drops_entries_older_than_a_year(store, monkeypatch):
    print_store.queued(rec := record('old'), [IMAGE], b'png')
    print_store.ended(rec, 'printed', '', keep=True)
    print_store.ended(record('new', images=0), 'printed', '', keep=True)
    monkeypatch.setattr(time, 'time', lambda real=time.time: real() + 366 * 86400)
    print_store.ended(record('newest', images=0), 'printed', '', keep=True)
    print_store.start(str(store))
    assert [entry['id'] for entry in print_store.entries(10)] == ['newest']
    assert files(store / print_store.HISTORY) == ['history.jsonl']


def test_the_oldest_images_go_beyond_their_limit(store, monkeypatch):
    photo = Image.effect_noise((60, 60), 50).convert('L')
    size = len(print_store.png(photo))
    monkeypatch.setattr(print_store, 'HISTORY_IMAGE_BYTES', 2 * size)
    for number in range(3):
        print_store.queued(rec := record(f'j{number}'), [photo], b'png')
        print_store.ended(rec, 'printed', '', keep=True)
    kept = {entry['id']: print_store.images_kept(entry) for entry in print_store.entries(10)}
    assert kept == {'j2': True, 'j1': True, 'j0': False}
    # Its preview stays
    assert print_store.preview_path('j0')


def test_clear_removes_every_entry_and_image(store):
    print_store.queued(rec := record(), [IMAGE], b'png')
    print_store.ended(rec, 'printed', '', keep=True)
    print_store.clear()
    assert print_store.entries(10) == []
    assert files(store / print_store.HISTORY) == []
    print_store.ended(record('next', images=0), 'printed', '', keep=True)
    assert [entry['id'] for entry in print_store.entries(10)] == ['next']


# --- The queue of the server on disk -----------------------------------------------------

API = '/labeldesigner/api'
LINES = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Jar {{counter}}', 'size': '40'}])
ASN = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'ASN{{asn:5}}', 'size': '40'}])
LABEL = dict(EXAMPLE_FORMDATA, printer='simulation', text=LINES)


@pytest.fixture
def server(tmp_path, monkeypatch, queued):
    """A server keeping its queue and history in the folder of its labels,
    print requests answered once their job is in the queue."""
    monkeypatch.setattr(print_store, 'ENABLED', True)
    monkeypatch.setattr(print_store, 'FOLDER', None)
    monkeypatch.setattr(print_queue, 'STOP_WAIT', 0.1)
    yield make_client(tmp_path, empty_repo=True)
    print_store.close()


def repo(client) -> str:
    return client.application.config['LABEL_REPOSITORY_DIR']


def restart(tmp_path):
    """The server stops, jobs waiting stay in the queue on disk, and starts again."""
    print_queue.stop()
    print_queue.reset()
    print_store.close()
    return make_client(tmp_path, empty_repo=True)


def holding(monkeypatch) -> tuple:
    """The first job on the demo printer prints until release is set."""
    started, release = threading.Event(), threading.Event()
    prune = printer._prune_simulated_labels

    def held():
        if not started.is_set():
            started.set()
            release.wait(10)
        prune()
    monkeypatch.setattr(printer, '_prune_simulated_labels', held)
    return started, release


def made(monkeypatch) -> list:
    """The archive serial number, counter and timestamp of each label made,
    and whether it was made by the queue."""
    calls, create = [], routes.create_label_from_request

    def recorded(d={}, files={}, counter=0, *args, **kwargs):
        calls.append((kwargs.get('asn'), counter, d.get('timestamp'), threading.current_thread().name.startswith(print_queue.WORKER_PREFIX)))
        return create(d, files, counter, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    return calls


def ended(job_id: str) -> print_queue.Job:
    job = print_queue.get(job_id)
    assert job.done.wait(10)
    return job


def test_a_job_is_on_disk_once_it_is_accepted_and_leaves_with_its_end(server, monkeypatch):
    started, release = holding(monkeypatch)
    first = server.post(f'{API}/print', data=LABEL).get_json()['job']
    assert started.wait(10)
    second = server.post(f'{API}/print', data=dict(LABEL, print_count='2')).get_json()['job']
    queue = os.path.join(repo(server), print_store.QUEUE)
    assert files(queue) == sorted([f'{first}-preview.png', f'{first}.json', f'{second}-preview.png', f'{second}.json'])
    kept = json.loads(open(os.path.join(queue, f'{second}.json')).read())
    assert (kept['state'], kept['kind'], kept['labels'], kept['order'], kept['source']) == ('waiting', 'request', 2, [None, None], 'api')
    assert kept['printer'] == {'device': 'simulation', 'model': 'QL-800', 'label_size': '62', 'cut': 'each', 'half_cut': True,
                               'chain': False, 'leaves_first': None}
    assert json.loads(open(os.path.join(queue, f'{first}.json')).read())['state'] == 'printing'
    release.set()
    assert ended(second).state == 'printed'
    assert files(queue) == []
    assert [entry['id'] for entry in print_store.entries(10)] == [second, first]


def test_waiting_jobs_print_after_a_restart_with_their_ids_in_their_order(server, tmp_path, monkeypatch):
    print_queue.stop()
    jobs = [server.post(f'{API}/print', data=dict(LABEL, title=f'Jar {number}')).get_json()['job'] for number in range(3)]
    calls = made(monkeypatch)
    server = restart(tmp_path)
    assert all(ended(job).state == 'printed' for job in jobs)
    assert [entry['id'] for entry in print_store.entries(10)] == jobs[::-1]
    assert [entry['title'] for entry in print_store.entries(10)] == ['Jar 2', 'Jar 1', 'Jar 0']
    # Made when they print, with the dates of then
    assert calls and all(by_queue and timestamp is None for _, _, timestamp, by_queue in calls)
    assert files(os.path.join(repo(server), print_store.QUEUE)) == []


def test_the_job_that_printed_at_a_restart_may_not_have_printed_and_holds_nothing(server, tmp_path, monkeypatch):
    started, release = holding(monkeypatch)
    first = server.post(f'{API}/print', data=dict(LABEL, title='First')).get_json()['job']
    assert started.wait(10)
    second = server.post(f'{API}/print', data=dict(LABEL, title='Second')).get_json()['job']
    # The server dies while the first one prints
    real = print_store.ended
    monkeypatch.setattr(print_store, 'ended', lambda *args, **kwargs: None)
    print_queue.stop()
    release.set()
    print_queue.reset()
    monkeypatch.setattr(print_store, 'ended', real)
    print_store.close()
    make_client(tmp_path, empty_repo=True)
    assert ended(second).state == 'printed'
    first_kept = print_store.entry(first)
    assert (first_kept['state'], first_kept['message']) == ('unknown', print_store.PRINTING_AT_RESTART)
    assert print_queue.line('simulation')['held'] is None


def test_a_restored_job_keeps_its_archive_serial_number(server, tmp_path, monkeypatch):
    print_queue.stop()
    job = server.post(f'{API}/print', data=dict(LABEL, text=ASN, print_count='3')).get_json()['job']
    next_asn = os.path.join(repo(server), paperless.STATE_FILE)
    assert open(next_asn).read().strip() == '4'
    calls = made(monkeypatch)
    restart(tmp_path)
    assert ended(job).state == 'printed'
    assert [(asn, counter) for asn, counter, _, _ in calls if _] == [(1, 0), (1, 1), (1, 2)]
    assert open(next_asn).read().strip() == '4'


NETWORK_PRINTER = 'tcp://192.168.2.50:9100'


@pytest.mark.parametrize('found', [False, True])
def test_a_restored_job_is_checked_as_printers_are_today(server, tmp_path, monkeypatch, found):
    without_status(monkeypatch)
    print_queue.stop()
    # A network printer named by its address, allowed while ADD_PRINTERS is local
    response = server.post(f'{API}/print', data=dict(LABEL, printer=NETWORK_PRINTER, model='QL-810W'))
    assert response.status_code == 200, response.get_json()
    monkeypatch.setenv('ADD_PRINTERS', 'off')
    searched = []

    def search(config, added=()):
        # The search finds it again, as before the restart
        searched.append(True)
        if found:
            monkeypatch.setattr(printer, '_cached_printers', [{'path': NETWORK_PRINTER, 'model': 'QL-810W'}])
        return {}
    monkeypatch.setattr(routes, 'get_ptr_status', search)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda queue: '')
    restart(tmp_path)
    job = ended(response.get_json()['job'])
    assert searched == [True]
    assert (job.state, job.message) == (('printed', '') if found else ('failed', adding.NAMED_OFF))


def test_an_image_from_the_webhook_and_homebox_print_after_a_restart(server, tmp_path, monkeypatch):
    server.application.config['WEBHOOK_PASSWORD'] = 'secret'
    state = {'errors': [], 'path': 'simulation', 'model': 'QL-800', 'media_width': 62, 'media_length': 0}
    monkeypatch.setattr(routes, 'get_ptr_status', lambda config, added=(): {**state, 'printers': [state], 'selected': 'simulation'})
    image = io.BytesIO()
    Image.new('L', (200, 100), 0).save(image, 'PNG')
    print_queue.stop()
    webhook = server.post(f'{API}/webhook/print', json={'password': 'secret', 'printer': 'simulation',
                                                        'images': [base64.b64encode(image.getvalue()).decode('ascii')]})
    homebox = server.post(f'{API}/homebox', data=image.getvalue(), headers={'Content-Type': 'image/png'})
    calibration = server.post(f'{API}/calibration/print', data={'printer': 'simulation', 'model': 'Niimbot/B1', 'across': '0.5'})
    jobs = [response.get_json()['job'] for response in (webhook, homebox, calibration)]
    kept = json.loads(open(os.path.join(repo(server), print_store.QUEUE, f'{jobs[0]}.json')).read())
    assert (kept['kind'], kept['images'], len(kept['labels'])) == ('image', 1, 1)
    restart(tmp_path)
    assert [ended(job).state for job in jobs] == ['printed'] * 3
    # Calibration labels are not kept
    assert [entry['source'] for entry in print_store.entries(10)] == ['homebox', 'webhook']


def test_a_full_disk_prints_from_memory(server, monkeypatch, caplog):
    monkeypatch.setattr(print_store, 'MIN_FREE_BYTES', 1 << 62)
    job = server.post(f'{API}/print', data=LABEL).get_json()['job']
    assert ended(job).state == 'printed'
    assert any('kept in memory only' in message for message in caplog.messages)


def test_the_folders_never_show_up_with_the_saved_labels(server):
    assert ended(server.post(f'{API}/print', data=LABEL).get_json()['job']).state == 'printed'
    assert sorted(os.listdir(repo(server))) == ['.print-history', '.print-queue']
    assert server.get(f'{API}/repository/list').get_json() == {'files': []}
    for name in ('.print-history', '.print-queue', 'print-history', '.print-history/history.jsonl', '../label_repo/.print-history'):
        assert server.get(f'{API}/repository/load', query_string={'name': name}).status_code == 404, name
        assert server.post(f'{API}/repository/print', json={'name': name}).status_code == 404, name
    assert server.get(f'{API}/homebox/.print-history').status_code == 404
