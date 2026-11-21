import threading
import time

import pytest

from app.labeldesigner import print_queue

USB = 'file:///dev/usb/lp0'
WAIT = 10


@pytest.fixture(autouse=True)
def clean_queue():
    print_queue.reset()
    yield
    print_queue.reset()


def explain(error):
    return f'Explained: {error}'


def add(device, run, title='Label', source='designer', **kwargs):
    return print_queue.add(device, run, title=title, labels=1, source=source, explain=explain, **kwargs)


def recorder(order, number, result=''):
    def run():
        order.append(number)
        return result
    return run


def blocked():
    """A run function that starts, then waits until released."""
    started, release = threading.Event(), threading.Event()

    def run():
        started.set()
        release.wait(WAIT)
        return ''
    return run, started, release


def test_jobs_print_in_the_order_they_came():
    order = []
    jobs = [add(USB, recorder(order, number)) for number in (1, 2, 3)]
    for job in jobs:
        assert job.done.wait(WAIT)
    assert order == [1, 2, 3]
    assert [job.state for job in jobs] == ['printed'] * 3


def test_lines_of_two_printers_do_not_wait_for_each_other():
    run, started, release = blocked()
    first = add(USB, run)
    assert started.wait(WAIT)
    other = add('tcp://192.0.2.5', recorder([], 1))
    assert other.done.wait(WAIT)
    assert other.state == 'printed'
    assert first.state == 'printing'
    release.set()
    assert first.done.wait(WAIT)


@pytest.mark.parametrize('one, other', [(USB, '/dev/usb/lp0'), ('?', 'simulation')])
def test_every_spelling_of_a_device_is_one_line(one, other):
    assert print_queue.key_of(one) == print_queue.key_of(other)
    run, started, release = blocked()
    add(one, run)
    assert started.wait(WAIT)
    add(other, recorder([], 1))
    assert print_queue.summary(one) == {'printing': True, 'waiting': 1, 'held': False}
    assert print_queue.summary(other) == {'printing': True, 'waiting': 1, 'held': False}
    release.set()


def test_a_full_line_refuses_the_next_job():
    run, started, release = blocked()
    add(USB, run)
    assert started.wait(WAIT)
    for _ in range(print_queue.MAX_WAITING):
        add(USB, recorder([], 1))
    with pytest.raises(print_queue.Full):
        add(USB, recorder([], 1))
    release.set()


def test_a_waiting_job_is_removed():
    run, started, release = blocked()
    add(USB, run)
    assert started.wait(WAIT)
    order = []
    waiting = add(USB, recorder(order, 2))
    assert print_queue.remove(waiting.id) == ''
    assert waiting.state == 'removed'
    assert waiting.message == print_queue.REMOVED
    assert waiting.done.is_set()
    release.set()
    print_queue.reset()
    assert order == []


def test_a_printing_or_ended_job_is_not_removed():
    run, started, release = blocked()
    job = add(USB, run)
    assert started.wait(WAIT)
    assert print_queue.remove(job.id) == print_queue.PRINTING_ALREADY
    release.set()
    assert job.done.wait(WAIT)
    assert print_queue.remove(job.id) == print_queue.NOT_WAITING
    assert print_queue.remove('unknown') is None


def test_a_failure_holds_the_jobs_behind_it_until_resume():
    run, started, release = blocked()
    first = add(USB, run)
    assert started.wait(WAIT)
    failing = add(USB, recorder([], 2, 'The cover is open'))
    order = []
    behind = add(USB, recorder(order, 3))
    release.set()
    assert failing.done.wait(WAIT)
    assert failing.state == 'failed'
    assert failing.message == 'The cover is open'
    assert first.state == 'printed'
    assert behind.state == 'waiting'
    assert print_queue.line(USB)['held'] == 'The cover is open'
    assert print_queue.summary(USB)['held'] is True
    print_queue.resume(USB)
    assert behind.done.wait(WAIT)
    assert behind.state == 'printed'
    assert print_queue.line(USB)['held'] is None


def test_a_failure_with_nothing_behind_it_holds_nothing():
    failing = add(USB, recorder([], 1, 'The cover is open'))
    assert failing.done.wait(WAIT)
    assert print_queue.line(USB)['held'] is None
    later = add(USB, recorder([], 2))
    assert later.done.wait(WAIT)
    assert later.state == 'printed'


def test_a_held_line_whose_jobs_are_all_removed_is_not_held():
    run, started, release = blocked()
    add(USB, run)
    assert started.wait(WAIT)
    failing = add(USB, recorder([], 2, 'The cover is open'))
    behind = add(USB, recorder([], 3))
    release.set()
    assert failing.done.wait(WAIT)
    assert print_queue.line(USB)['held'] == 'The cover is open'
    assert print_queue.remove(behind.id) == ''
    assert print_queue.line(USB)['held'] is None
    later = add(USB, recorder([], 4))
    assert later.done.wait(WAIT)
    assert later.state == 'printed'


def test_a_job_that_raises_fails_alone():
    def raises():
        raise ValueError('x')
    run, started, release = blocked()
    add(USB, run)
    assert started.wait(WAIT)
    failing = add(USB, raises)
    behind = add(USB, recorder([], 3))
    release.set()
    assert failing.done.wait(WAIT)
    assert failing.state == 'failed'
    assert failing.message == 'Explained: x'
    assert behind.done.wait(WAIT)
    assert behind.state == 'printed'
    assert print_queue.line(USB)['held'] is None


def test_line_tells_the_printing_job_and_the_waiting_ones():
    run, started, release = blocked()
    printing = add(USB, run, title='First')
    assert started.wait(WAIT)
    waiting = add(USB, recorder([], 2), title='Second', source='homebox', preview=b'png')
    told = print_queue.line(USB)
    assert told['held'] is None
    assert told['printing'] == {'id': printing.id, 'title': 'First', 'labels': 1, 'from': '', 'state': 'printing',
                                'message': '', 'preview': False}
    assert told['waiting'] == [{'id': waiting.id, 'title': 'Second', 'labels': 1, 'from': 'Homebox', 'state': 'waiting',
                                'message': '', 'preview': True}]
    assert print_queue.line('tcp://192.0.2.5') == {'printing': None, 'waiting': [], 'held': None}
    release.set()


def test_previews_and_outcomes_are_dropped_in_time(monkeypatch):
    monkeypatch.setattr(print_queue, 'PREVIEW_KEPT', 0.05)
    monkeypatch.setattr(print_queue, 'OUTCOME_KEPT', 0.1)
    job = add(USB, recorder([], 1), preview=b'png')
    assert job.done.wait(WAIT)
    assert print_queue.get(job.id).preview == b'png'
    time.sleep(0.06)
    assert print_queue.get(job.id).preview is None
    time.sleep(0.06)
    assert print_queue.get(job.id) is None
