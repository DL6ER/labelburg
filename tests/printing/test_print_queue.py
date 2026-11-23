import os
import subprocess
import sys
import textwrap
import threading

import pytest
from PIL import Image

from app.labeldesigner import print_queue

USB = 'file:///dev/usb/lp0'
WAIT = 10
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Added jobs are not waited for, conftest.py empties the queue around each test
pytestmark = pytest.mark.usefixtures('queued')


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
    assert told['printing'] == {'id': printing.id, 'title': 'First', 'labels': 1, 'from': '', 'from_detail': '',
                                'state': 'printing', 'message': '', 'preview': False}
    assert told['waiting'] == [{'id': waiting.id, 'title': 'Second', 'labels': 1, 'from': 'Homebox', 'from_detail': '',
                                'state': 'waiting', 'message': '', 'preview': True}]
    assert print_queue.line('tcp://192.0.2.5') == {'printing': None, 'waiting': [], 'held': None}
    release.set()


def test_lines_tells_every_line_with_something_in_it():
    run, started, release = blocked()
    printing = add(USB, run)
    assert started.wait(WAIT)
    waiting = add(USB, recorder([], 2))
    idle = add('tcp://192.0.2.5', recorder([], 3))
    assert idle.done.wait(WAIT)
    # The line of the idle printer is empty now
    assert print_queue.lines() == [{'printer': USB, 'printing': printing.told(), 'waiting': [waiting.told()], 'held': None}]
    release.set()
    assert waiting.done.wait(WAIT)
    assert print_queue.lines() == []


def test_lines_tells_a_hold():
    run, started, release = blocked()
    first = add(USB, run)
    assert started.wait(WAIT)
    failing = add(USB, recorder([], 1, 'Cover open'))
    add(USB, recorder([], 2))
    release.set()
    assert failing.done.wait(WAIT)
    assert [(told['printer'], told['held']) for told in print_queue.lines()] == [(USB, 'Cover open')]


def test_previews_and_outcomes_are_dropped_in_time():
    job = add(USB, recorder([], 1), preview=b'png')
    assert job.done.wait(WAIT)
    assert print_queue.get(job.id).preview == b'png'
    job.ended -= print_queue.PREVIEW_KEPT + 1
    assert print_queue.get(job.id).preview is None
    job.ended -= print_queue.OUTCOME_KEPT
    assert print_queue.get(job.id) is None


def test_a_failing_explain_does_not_stop_the_line():
    def raises():
        raise ValueError('x')

    def explain_raises(error):
        raise RuntimeError('y')
    failing = print_queue.add(USB, raises, title='Label', labels=1, source='api', explain=explain_raises)
    assert failing.done.wait(WAIT)
    assert failing.state == 'failed'
    assert failing.message == print_queue.FAILED
    later = add(USB, recorder([], 2))
    assert later.done.wait(WAIT)
    assert later.state == 'printed'
    assert print_queue.line(USB)['printing'] is None


def test_a_raising_job_fails_even_when_explain_says_nothing():
    def raises():
        raise ValueError('x')
    job = print_queue.add(USB, raises, title='Label', labels=1, source='api', explain=lambda error: '')
    assert job.done.wait(WAIT)
    assert job.state == 'failed'


def test_a_job_that_raises_more_than_an_exception_does_not_stop_the_line():
    def exits():
        raise SystemExit
    failing = add(USB, exits)
    assert failing.done.wait(WAIT)
    assert failing.state == 'failed'
    later = add(USB, recorder([], 2))
    assert later.done.wait(WAIT)
    assert later.state == 'printed'


def test_a_worker_that_cannot_start_leaves_no_job_behind(monkeypatch):
    def refuses(thread):
        raise RuntimeError("can't start new thread")
    with monkeypatch.context() as patched:
        patched.setattr(threading.Thread, 'start', refuses)
        with pytest.raises(RuntimeError):
            add(USB, recorder([], 1))
    assert print_queue.line(USB) == {'printing': None, 'waiting': [], 'held': None}
    later = add(USB, recorder([], 2))
    assert later.done.wait(WAIT)
    assert later.state == 'printed'


def test_jobs_fail_when_the_worker_that_stopped_cannot_be_replaced(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def exits():
        started.set()
        release.wait(WAIT)
        raise SystemExit
    first = add(USB, exits)
    assert started.wait(WAIT)
    behind = add(USB, recorder([], 2))

    def refuses(thread):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, 'start', refuses)
    release.set()
    assert first.done.wait(WAIT)
    assert behind.done.wait(WAIT)
    assert behind.state == 'failed'
    assert behind.message == print_queue.FAILED
    assert print_queue.line(USB)['waiting'] == []


def test_an_ended_job_frees_its_functions():
    job = add(USB, recorder([], 1))
    assert job.done.wait(WAIT)
    assert job.run is None
    assert job.explain is None


def test_a_raising_job_logs_its_traceback(caplog):
    def raises():
        raise ValueError('boom')
    job = add(USB, raises)
    assert job.done.wait(WAIT)
    record = next(record for record in caplog.records if record.exc_info)
    assert record.exc_info[0] is ValueError


def test_the_state_comes_after_the_message():
    # A reader outside the lock must not see a failed job without its message
    seen = []

    class Watching(print_queue.Job):
        def __setattr__(self, name, value):
            if name == 'state' and value == 'failed':
                seen.append(self.message)
            super().__setattr__(name, value)
    job = Watching('id', 'key', 'Label', 1, 'api')
    print_queue._end(job, 'failed', 'The cover is open')
    assert seen == ['The cover is open']


def test_resume_does_nothing_on_a_line_that_is_not_held():
    run, started, release = blocked()
    add(USB, run)
    assert started.wait(WAIT)
    waiting = add(USB, recorder([], 2))
    print_queue.resume(USB)
    assert waiting.state == 'waiting'
    assert print_queue.summary(USB) == {'printing': True, 'waiting': 1, 'held': False}
    release.set()
    assert waiting.done.wait(WAIT)
    print_queue.resume('tcp://192.0.2.5')


@pytest.mark.parametrize('inherit', [False, True])
def test_a_worker_starts_with_an_empty_context_on_every_python(monkeypatch, inherit):
    # Before Python 3.14 a thread takes no context, it starts in an empty one
    # unless a free-threaded build has it inherit that of the request
    monkeypatch.setattr(print_queue, 'THREADS_INHERIT_CONTEXT', inherit)
    contexts, real = [], threading.Thread

    def thread(*args, context=None, **kwargs):
        if context is not None and not inherit:
            raise TypeError("Thread.__init__() got an unexpected keyword argument 'context'")
        contexts.append(context)
        # Started without it, Thread() of Python 3.11-3.13 does not take it
        return real(*args, **kwargs)
    monkeypatch.setattr(threading, 'Thread', thread)
    job = add(USB, recorder([], 1))
    assert job.done.wait(WAIT) and job.state == 'printed'
    assert [None if context is None else len(context) for context in contexts] == [0 if inherit else None]


NET = 'tcp://192.0.2.5'


def test_a_line_is_told_by_the_spelling_it_was_last_added_under():
    run, started, release = blocked()
    add('/dev/usb/lp0', run)
    assert started.wait(WAIT)
    add(USB, recorder([], 2))
    assert [told['printer'] for told in print_queue.lines()] == [USB]
    release.set()


def test_the_line_of_the_demo_printer_is_told_as_its_path():
    # The status lists the demo printer as simulation, never as ?
    run, started, release = blocked()
    add('?', run)
    assert started.wait(WAIT)
    assert [told['printer'] for told in print_queue.lines()] == ['simulation']
    release.set()


def test_the_memory_of_images_counts_each_image_once():
    photo, gray = Image.new('RGB', (100, 50)), Image.new('L', (10, 10))
    assert print_queue.image_bytes([photo, photo, None, gray]) == 100 * 50 * 4 + 10 * 10
    assert print_queue.image_bytes([]) == 0


def test_the_images_of_the_jobs_waiting_have_a_limit(monkeypatch):
    monkeypatch.setattr(print_queue, 'MAX_WAITING_BYTES', 1000)
    run, started, release = blocked()
    # A job whose images are larger than all may hold waits alone
    printing = add(USB, run, size=5000)
    assert started.wait(WAIT)
    other, other_started, other_release = blocked()
    add(NET, other)
    assert other_started.wait(WAIT)
    waiting = add(USB, recorder([], 2), size=600)
    assert waiting.size == 600
    # Counted over the lines of all printers
    assert print_queue.full(NET, 600)
    with pytest.raises(print_queue.Full):
        add(NET, recorder([], 3), size=600)
    # Jobs without images, and those that fit, still wait
    assert not print_queue.full(NET)
    add(NET, recorder([], 4))
    add(NET, recorder([], 5), size=400)
    release.set()
    other_release.set()
    assert printing.done.wait(WAIT)


def test_only_the_newest_outcomes_are_kept(monkeypatch):
    monkeypatch.setattr(print_queue, 'MAX_ENDED', 3)
    jobs = [add(USB, recorder([], number), preview=b'png') for number in range(5)]
    assert all(job.done.wait(WAIT) for job in jobs)
    assert [print_queue.get(job.id) is not None for job in jobs] == [False, False, True, True, True]
    assert len(print_queue._jobs) == 3


def test_pruning_looks_only_at_the_oldest_outcomes(monkeypatch):
    read = []

    class Counted(print_queue.Job):
        def __getattribute__(self, name):
            if name == 'ended':
                read.append(self)
            return super().__getattribute__(name)
    monkeypatch.setattr(print_queue, 'Job', Counted)
    jobs = []
    for number in range(50):
        jobs.append(add(USB, recorder([], number), preview=b'png'))
        assert jobs[-1].done.wait(WAIT)
    read.clear()
    assert print_queue.get(jobs[0].id) is jobs[0]
    # The oldest outcome and the oldest preview, none of them due
    assert len(read) <= 2


def test_at_exit_the_job_that_prints_ends_and_no_other_starts():
    started, exiting = threading.Event(), threading.Event()

    def run():
        started.set()
        exiting.wait(WAIT)
        return ''
    printing = add(USB, run)
    assert started.wait(WAIT)
    order = []
    waiting = add(USB, recorder(order, 2))
    # The job ends while the exit waits for it
    wait = printing.done.wait
    printing.done.wait = lambda timeout=None: exiting.set() or wait(timeout)
    print_queue.stop()
    assert printing.state == 'printed'
    for worker in [t for t in threading.enumerate() if t.name.startswith(print_queue.WORKER_PREFIX)]:
        worker.join(WAIT)
    assert (waiting.state, order) == ('waiting', [])
    # Neither does a job added after it
    later = add(NET, recorder(order, 3))
    assert (later.state, order) == ('waiting', [])


def test_at_exit_the_job_that_prints_is_waited_for_a_while(monkeypatch):
    monkeypatch.setattr(print_queue, 'STOP_WAIT', 0.2)
    run, started, release = blocked()
    printing = add(USB, run)
    assert started.wait(WAIT)
    try:
        print_queue.stop()
        assert printing.state == 'printing'
    finally:
        release.set()


def test_the_process_exits_once_the_job_that_prints_ended(tmp_path):
    printed = tmp_path / 'printed'
    script = textwrap.dedent(f"""
        import threading
        from app.labeldesigner import print_queue
        started = threading.Event()

        def run():
            started.set()
            threading.Event().wait(0.5)
            open({str(printed)!r}, 'w').close()
            return ''
        print_queue.add('simulation', run, title='Label', labels=1, source='api', explain=str)
        started.wait(10)
    """)
    subprocess.run([sys.executable, '-c', script], check=True, timeout=60, cwd=ROOT)
    assert printed.exists()


@pytest.mark.parametrize('source, told, detail', [
    ('designer', '', ''), ('calibration', '', ''), ('feed', '', ''), ('homebox', 'Homebox', ''),
    ('api', 'another program', 'Sent over the API'), ('webhook', 'another program', 'Sent over the webhook'),
    ('mcp', 'an AI assistant', 'Sent over MCP')])
def test_where_a_job_comes_from_in_plain_words(source, told, detail):
    # The technical word is told apart, for a hover text
    job = add(USB, recorder([], 1), source=source)
    assert (job.told()['from'], job.told()['from_detail']) == (told, detail)
