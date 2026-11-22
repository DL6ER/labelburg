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

from app import create_app
from app.labeldesigner import adding, paperless, print_queue, print_store, printer, routes
from config import Config
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


def test_entries_older_than_a_year_go_when_the_next_is_added(store, monkeypatch):
    print_store.queued(rec := record('old'), [IMAGE], b'png')
    print_store.ended(rec, 'printed', '', keep=True)
    monkeypatch.setattr(time, 'time', lambda real=time.time: real() + 366 * 86400)
    print_store.ended(record('new', images=0), 'printed', '', keep=True)
    assert [entry['id'] for entry, _ in print_store._read()] == ['new']
    assert files(store / print_store.HISTORY) == ['history.jsonl']


def test_jobs_that_could_not_be_read_go_after_a_week(store):
    print_store.queued(record('bad', v=2), [IMAGE], None)
    print_store.start(str(store))
    broken = store / print_store.QUEUE / print_store.BROKEN
    assert sorted(os.listdir(broken)) == ['bad-0.png', 'bad.json']
    week = time.time() - 8 * 86400
    os.utime(broken / 'bad.json', (week, week))
    print_store.start(str(store))
    assert os.listdir(broken) == ['bad-0.png']


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
    print_store.queued(record('bad', v=2), [IMAGE], None)
    print_store.start(str(store))
    assert os.listdir(store / print_store.QUEUE / print_store.BROKEN)
    print_store.clear()
    assert print_store.entries(10) == []
    assert files(store / print_store.HISTORY) == []
    # Also the jobs that could not be read, they hold contents of labels too
    assert os.listdir(store / print_store.QUEUE / print_store.BROKEN) == []
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
                                                        'images': [base64.b64encode(image.getvalue()).decode('ascii')] * 2})
    homebox = server.post(f'{API}/homebox', data=image.getvalue(), headers={'Content-Type': 'image/png'})
    calibration = server.post(f'{API}/calibration/print', data={'printer': 'simulation', 'model': 'Niimbot/B1', 'across': '0.5'})
    jobs = [response.get_json()['job'] for response in (webhook, homebox, calibration)]
    kept = json.loads(open(os.path.join(repo(server), print_store.QUEUE, f'{jobs[0]}.json')).read())
    assert (kept['kind'], kept['images'], kept['labels'], len(kept['shapes'])) == ('image', 2, 2, 2)
    server = restart(tmp_path)
    assert server.get(f'{API}/queue/{jobs[0]}').get_json()['labels'] == 2
    assert [ended(job).state for job in jobs] == ['printed'] * 3
    # Calibration labels are not kept
    assert [(entry['source'], entry['labels']) for entry in print_store.entries(10)] == [('homebox', 1), ('webhook', 2)]


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


def test_a_file_of_the_history_is_named_by_an_id_only(store):
    print_store.queued(rec := record(), [IMAGE], b'png')
    print_store.ended(rec, 'printed', '', keep=True)
    (store / 'secret-preview.png').write_bytes(b'png')
    assert print_store.preview_path('a1') and print_store.image_path('a1', 0)
    for job_id in ('../secret', '..', '', 'a1/../a1'):
        assert print_store.preview_path(job_id) is None and print_store.image_path(job_id, 0) is None


def test_files_a_crash_left_half_written_go(store):
    (store / print_store.HISTORY / '.0123.tmp').write_bytes(b'half')
    print_store.start(str(store))
    assert files(store / print_store.HISTORY) == []


@pytest.mark.parametrize('enabled', [False, True])
def test_a_folder_of_labels_that_cannot_be_made_does_not_stop_the_server(tmp_path, monkeypatch, caplog, enabled):
    monkeypatch.setattr(print_store, 'ENABLED', enabled)
    monkeypatch.setattr(print_store, 'FOLDER', None)
    (tmp_path / 'file').write_text('not a folder')
    config = type('Unwritable', (Config,), {'LABEL_REPOSITORY_DIR': str(tmp_path / 'file' / 'labels')})
    app = create_app(config)
    routes.restore_print_queue(app)
    client = app.test_client()
    assert client.get('/labeldesigner/api/openapi.json').status_code == 200
    assert not print_store.is_open()
    assert any('not kept' in message for message in caplog.messages) == enabled


def test_a_job_the_queue_does_not_take_leaves_nothing_on_disk(server, monkeypatch):
    def refuses(*args, **kwargs):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(print_queue, 'add', refuses)
    with pytest.raises(RuntimeError):
        server.post(f'{API}/print', data=LABEL)
    assert files(os.path.join(repo(server), print_store.QUEUE)) == []


def test_a_job_whose_start_was_not_kept_never_prints_twice(server, tmp_path, monkeypatch):
    write = print_store._write

    def fails_to_keep_the_start(path, content):
        if path.endswith('.json') and b'"printing"' in content:
            raise OSError(28, 'No space left on device')
        write(path, content)
    monkeypatch.setattr(print_store, '_write', fails_to_keep_the_start)
    started, release = holding(monkeypatch)
    job = server.post(f'{API}/print', data=LABEL).get_json()['job']
    assert started.wait(10)
    # Kept as waiting it would print again after a crash
    assert f'{job}.json' not in files(os.path.join(repo(server), print_store.QUEUE))
    # The server dies while it prints
    ended = print_store.ended
    monkeypatch.setattr(print_store, 'ended', lambda *args, **kwargs: None)
    print_queue.stop()
    release.set()
    print_queue.reset()
    monkeypatch.setattr(print_store, 'ended', ended)
    monkeypatch.setattr(print_store, '_write', write)
    calls = made(monkeypatch)
    restart(tmp_path)
    assert print_queue.lines() == [] and calls == []


def test_a_job_ends_on_disk_also_where_the_setting_cannot_be_read(server, monkeypatch):
    started, release = holding(monkeypatch)
    server.post(f'{API}/print', data=LABEL)
    assert started.wait(10)
    waiting = server.post(f'{API}/print', data=LABEL).get_json()['job']

    def unreadable():
        raise RuntimeError('settings unreadable')
    monkeypatch.setattr(routes, '_keeping', unreadable)
    assert server.post(f'{API}/queue/{waiting}/remove').status_code == 200
    assert f'{waiting}.json' not in files(os.path.join(repo(server), print_store.QUEUE))
    release.set()


def test_a_label_printed_again_as_it_was_keeps_its_date_after_a_restart(server, tmp_path, monkeypatch):
    first = server.post(f'{API}/print', data=dict(LABEL, text=json.dumps([{'text': '{{datetime:%H:%M}}', 'size': '40'}]))).get_json()['job']
    assert ended(first).state == 'printed'
    print_queue.stop()
    again = server.post(f'{API}/history/{first}/print', data={'printer': 'simulation'}).get_json()['job']
    calls = made(monkeypatch)
    restart(tmp_path)
    assert ended(again).state == 'printed'
    assert {timestamp for _, _, timestamp, by_queue in calls if by_queue} == {int(print_store.entry(first)['started_at'])}


def photo(width, height) -> tuple:
    out = io.BytesIO()
    Image.effect_noise((width, height), 40).convert('RGB').save(out, 'PNG')
    return io.BytesIO(out.getvalue()), 'photo.png'


@pytest.mark.parametrize('values, kept', [
    ({}, None), ({'image_fit': '0'}, (3000, 1500)), ({'image_crop': '1'}, (3000, 1500)), ({'image_rotation': '10'}, (3000, 1500))])
def test_a_photo_fitted_to_the_label_is_kept_no_larger_than_the_label_shows_it(server, monkeypatch, values, kept):
    drawn, create = [], routes.create_label_from_request

    def recorded(d={}, files={}, counter=0, image=None, *args, **kwargs):
        drawn.append(image.size)
        return create(d, files, counter, image, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    print_queue.stop()
    upload = dict(LABEL, print_type='image', image=photo(3000, 1500), **values)
    job = server.post(f'{API}/print', data=upload, content_type='multipart/form-data').get_json()['job']
    with Image.open(os.path.join(repo(server), print_store.QUEUE, f'{job}-0.png')) as image:
        size = image.size
    # Its shorter side as long as the longest side of the label at 600 dpi;
    # cropped or turned it is kept as it is, the label shows a part of it
    longest = max(routes._canvas('62', True, routes.LabelOrientation.STANDARD))
    assert size == (kept or (2 * longest, longest))
    # Printed now as it came
    assert set(drawn) == {(3000, 1500)}


def logo() -> tuple:
    """A picture with a white border around lines, which crop takes off."""
    from PIL import ImageDraw
    image = Image.new('RGB', (4000, 3000), 'white')
    draw = ImageDraw.Draw(image)
    for x in range(1700, 2300, 6):
        draw.line([(x, 1275), (x, 1725)], fill='black', width=2)
    out = io.BytesIO()
    image.save(out, 'PNG')
    return io.BytesIO(out.getvalue()), 'logo.png'


@pytest.mark.parametrize('picture, crop', [(logo, '0'), (logo, '1'), (lambda: photo(2004, 1500), '0')], ids=['logo', 'cropped', 'photo'])
def test_a_photo_printed_again_prints_as_it_did(server, picture, crop):
    def shown(path, **kwargs) -> tuple[str, Image.Image]:
        answer = server.post(f'{API}/{path}?return_format=png', **kwargs).get_json()
        return answer['job'], Image.open(io.BytesIO(base64.b64decode(answer['image']))).convert('L')
    job, first = shown('print', data=dict(LABEL, print_type='image', image_crop=crop, image=picture()), content_type='multipart/form-data')
    assert ended(job).state == 'printed'
    _, again = shown(f'history/{job}/print', data={'printer': 'simulation'})
    # Of the same size, also where the smaller copy kept has another
    # aspect ratio by a part of a pixel: 2004 x 1500 kept as 1860 x 1392
    assert again.size == first.size
    differences = [abs(a - b) for a, b in zip(first.tobytes(), again.tobytes())]
    if crop == '1':
        # Kept as it came
        assert max(differences) == 0
    else:
        # Kept smaller, printed again at the size it had
        assert sum(differences) / len(differences) < 2


def test_the_images_are_written_while_the_label_is_drawn(server, monkeypatch):
    order, stage, check = [], print_store.stage, printer.PrinterQueue.check
    monkeypatch.setattr(print_store, 'stage', lambda *args: (order.append('stage'), stage(*args))[1])
    monkeypatch.setattr(printer.PrinterQueue, 'check', lambda queue: (order.append('check'), check(queue))[1])
    upload = dict(LABEL, print_type='image', image=photo(400, 200))
    assert server.post(f'{API}/print', data=upload, content_type='multipart/form-data').status_code == 200
    assert order == ['stage', 'check']


def test_images_written_for_a_job_that_is_refused_go(server):
    upload = dict(LABEL, print_type='image_qrcode_text', barcode_type='EAN13', code_text='123', image=photo(400, 200))
    response = server.post(f'{API}/print', data=upload, content_type='multipart/form-data')
    assert response.status_code == 400
    assert files(os.path.join(repo(server), print_store.QUEUE)) == []


def test_a_job_refused_before_its_images_are_read_is_told(server):
    upload = dict(LABEL, print_type='image', pdf_pages='9', image=(io.BytesIO(routes_pdf()), 'two.pdf'))
    response = server.post(f'{API}/print', data=upload, content_type='multipart/form-data')
    assert (response.status_code, response.get_json()['message']) == (400, 'The PDF has no such page')


def routes_pdf() -> bytes:
    out = io.BytesIO()
    Image.new('L', (100, 50), 0).save(out, 'PDF')
    return out.getvalue()


def test_a_process_without_the_queue_takes_it_once_the_other_one_left(store, monkeypatch):
    monkeypatch.setattr(print_store, 'LOCK_RETRY', 0.05)
    print_store.queued(record('left', images=0), [], None)
    other = os.open(store / print_store.QUEUE / print_store.LOCK_FILE, os.O_RDWR)
    taken = threading.Event()
    found = []

    def restore(records):
        found.extend(rec['id'] for rec in records)
        taken.set()
    try:
        print_store.close()
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert print_store.start(str(store), restore) == []
        time.sleep(0.2)
        assert not taken.is_set() and print_store.queued(record('mine'), [], None) is False
    finally:
        # E.g. the worker of gunicorn that is replaced exits
        os.close(other)
    assert taken.wait(5)
    assert found == ['left'] and print_store.queued(record('new'), [], None) is True


def test_the_history_is_cut_back_while_another_process_adds_to_it(store, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(print_store, 'HISTORY_KEEP', 10)
    script = f"""
import time
from app.labeldesigner import print_store
print_store.ENABLED, print_store.HISTORY_KEEP = True, 10
print_store.start({str(store)!r})
for number in range(150):
    print_store.ended({{'v': 1, 'id': f'other{{number}}', 'queued_at': time.time(), 'kind': 'request', 'images': 0,
                       'printer': {{'device': 'simulation', 'model': 'QL-800', 'label_size': '62'}}}}, 'printed', '', keep=True)
"""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    other = subprocess.Popen([sys.executable, '-c', script], cwd=root)
    for number in range(150):
        print_store.ended(record(f'mine{number}', images=0), 'printed', '', keep=True)
    assert other.wait(60) == 0
    print_store.ended(record('last', images=0), 'printed', '', keep=True)
    with open(store / print_store.HISTORY / print_store.HISTORY_FILE) as fh:
        lines = fh.read().splitlines()
    ids = [json.loads(line)['id'] for line in lines]
    # Every line whole, none twice, cut back to the newest
    assert len(ids) == len(set(ids)) and 1 <= len(ids) <= 15 and ids[-1] == 'last'


@pytest.mark.parametrize('mode, size, own', [('colored', (300, 200), False), ('grayscale', (300, 200), False), ('grayscale', (3000, 2000), True)])
def test_the_images_written_are_loaded_and_not_copied_where_they_are_kept_as_they_came(server, monkeypatch, mode, size, own):
    staged, drawn, stage, create = [], [], print_store.stage, routes.create_label_from_request

    def staging(job_id, images):
        # Loaded before they are written: two threads must not read one file at once
        staged.extend((image, getattr(image, 'fp', None) is None) for image in images)
        return stage(job_id, images)
    monkeypatch.setattr(print_store, 'stage', staging)

    def recorded(d={}, files={}, counter=0, image=None, *args, **kwargs):
        drawn.append(image)
        return create(d, files, counter, image, *args, **kwargs)
    monkeypatch.setattr(routes, 'create_label_from_request', recorded)
    jpeg = io.BytesIO()
    Image.effect_noise(size, 60).convert('RGB').save(jpeg, 'JPEG')
    upload = dict(LABEL, print_type='image', image_mode=mode, image=(io.BytesIO(jpeg.getvalue()), 'photo.jpg'))
    assert server.post(f'{API}/print', data=upload, content_type='multipart/form-data').status_code == 200
    [(kept, loaded)] = staged
    assert loaded and drawn
    # Copied only where the picture is kept smaller, else it would take twice the memory
    assert (kept is drawn[0]) is not own


def test_a_job_whose_images_fail_in_any_way_prints_from_memory(server, monkeypatch, caplog):
    def fails(job_id, images):
        raise AssertionError('image file is truncated')
    monkeypatch.setattr(print_store, '_write_images', fails)
    upload = dict(LABEL, print_type='image', image=photo(300, 200))
    response = server.post(f'{API}/print', data=upload, content_type='multipart/form-data')
    assert response.status_code == 200 and ended(response.get_json()['job']).state == 'printed'
    assert files(os.path.join(repo(server), print_store.QUEUE)) == []
    assert any('kept in memory only' in message for message in caplog.messages)


def test_a_job_queued_while_the_queue_is_taken_over_is_not_restored(store, monkeypatch):
    monkeypatch.setattr(print_store, 'LOCK_RETRY', 0.05)
    other = os.open(store / print_store.QUEUE / print_store.LOCK_FILE, os.O_RDWR)
    found, taken, kept = [], threading.Event(), []
    print_store.close()
    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert print_store.start(str(store), lambda records: (found.extend(rec['id'] for rec in records), taken.set())) == []
    history_ids = print_store._history_ids

    def request_meanwhile():
        # A request of this process while the jobs left are listed
        kept.append(print_store.queued(record('mine', images=0), [], None))
        return history_ids()
    monkeypatch.setattr(print_store, '_history_ids', request_meanwhile)
    os.close(other)
    assert taken.wait(5)
    # Kept in memory, it prints once
    assert found == [] and kept == [False]


def test_a_picture_printed_again_with_copies_prints_them_after_a_restart(server, tmp_path, monkeypatch):
    server.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    Image.new('L', (200, 100), 0).save(image, 'PNG')
    job = server.post(f'{API}/webhook/print', json={'password': 'secret', 'printer': 'simulation',
                                                    'images': [base64.b64encode(image.getvalue()).decode('ascii')]}).get_json()['job']
    assert ended(job).state == 'printed'
    print_queue.stop()
    again = server.post(f'{API}/history/{job}/print', data={'printer': 'simulation', 'print_count': '2'}).get_json()['job']
    sent, process = [], printer.PrinterQueue.process_queue
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda queue: (sent.append(len(queue.images())), process(queue))[1])
    restart(tmp_path)
    assert ended(again).state == 'printed' and sent == [2]


def test_a_feed_waiting_at_a_restart_feeds_after_it_and_is_not_listed(server, tmp_path, monkeypatch):
    print_queue.stop()
    job = server.post(f'{API}/feed', data={'printer': 'simulation', 'model': 'QL-800'}).get_json()['job']
    fed = []
    monkeypatch.setattr(printer.PrinterQueue, 'feed', lambda queue: (fed.append(queue.label_size), '')[1])
    restart(tmp_path)
    assert ended(job).state == 'printed' and fed == ['62']
    assert print_store.entries(10) == []


def test_one_of_its_labels_printed_again_counts_as_it_did_after_a_restart(server, tmp_path, monkeypatch):
    text = json.dumps([{'text': 'Box {{counter}} of {{total}}', 'size': '40'}])
    first = server.post(f'{API}/print', data=dict(LABEL, text=text, print_count='3')).get_json()['job']
    assert ended(first).state == 'printed'
    print_queue.stop()
    again = server.post(f'{API}/history/{first}/print', data={'printer': 'simulation', 'print_count': '1'}).get_json()['job']
    totals, create = [], routes.create_label_from_request
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: (totals.append(kwargs.get('total')), create(*args, **kwargs))[1])
    restart(tmp_path)
    assert ended(again).state == 'printed' and set(totals) == {3}
