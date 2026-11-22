"""The print queue and the labels printed, kept on disk: what is written
where, what a start finds and how the history is kept."""
import fcntl
import json
import os
import stat
import time

import pytest
from PIL import Image

from app.labeldesigner import print_store


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
