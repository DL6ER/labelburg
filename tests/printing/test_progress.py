"""How far a printer is with a job and how long it still takes: estimated
from the print speed of the model, corrected by the labels the printer
reports printed and by how long its jobs took before."""

import time
from types import SimpleNamespace

import pytest
from PIL import Image

from app.labeldesigner import drivers
from app.labeldesigner.drivers import backends, devices, progress
from app.labeldesigner.drivers.brother import raster, usb
from app.labeldesigner.drivers.brother.models import MODELS
from app.labeldesigner.label import LabelContent, LabelOrientation, LabelType
from tests.helpers import EXAMPLE_FORMDATA, make_client, outcome
from tests.printing.test_usb_jobs import REPLY, Clock, FakePrinter, page, status

DEVICE = 'file:///dev/usb/lp0'


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(progress, 'time', clock)
    monkeypatch.setattr(progress, '_jobs', {})
    monkeypatch.setattr(progress, '_factors', {})
    return clock


def test_time_left_from_the_print_speed(clock):
    # Two labels of 100 mm at 50 mm/s and 1 s each besides: 3 s each
    progress.start(DEVICE, [100, 100], (50, 1))
    assert progress.state(DEVICE) == {'labels': 2, 'seconds_left': 6}
    clock.now += 2
    assert progress.state(DEVICE)['seconds_left'] == 4


def test_time_left_follows_the_labels_printed(clock):
    progress.start(DEVICE, [100] * 4, (50, 1))
    progress.counting(DEVICE)
    assert progress.state(DEVICE) == {'labels': 4, 'label': 1, 'seconds_left': 12}
    # The first label took twice as long as estimated, so will the others
    clock.now += 6
    progress.printed(DEVICE, 1)
    assert progress.state(DEVICE) == {'labels': 4, 'label': 2, 'seconds_left': 18}
    clock.now += 3
    assert progress.state(DEVICE)['seconds_left'] == 15


def test_time_left_never_below_the_labels_still_to_print(clock):
    progress.start(DEVICE, [100, 100], (50, 1))
    clock.now += 10
    assert progress.state(DEVICE)['seconds_left'] == 3


def test_labels_of_different_lengths(clock):
    # 1 + 1 s and 1 + 4 s
    progress.start(DEVICE, [50, 200], (50, 1))
    progress.counting(DEVICE)
    assert progress.state(DEVICE)['seconds_left'] == 7
    clock.now += 2
    progress.printed(DEVICE, 1)
    assert progress.state(DEVICE)['seconds_left'] == 5


def test_labels_counted_only_where_the_printer_tells(clock):
    progress.start(DEVICE, [100, 100], (50, 1))
    assert 'label' not in progress.state(DEVICE)


def test_no_time_without_a_print_speed(clock):
    progress.start(DEVICE, [100, 100], (0, 0))
    assert progress.state(DEVICE) == {'labels': 2}


def test_stage_of_the_label(clock):
    progress.start(DEVICE, [100, 100], (0, 0))
    progress.counting(DEVICE)
    progress.stage(DEVICE, 'Preheating')
    assert progress.state(DEVICE) == {'labels': 2, 'label': 1, 'stage': 'Preheating'}
    progress.stage(DEVICE, 'Printing', 40)
    assert progress.state(DEVICE) == {'labels': 2, 'label': 1, 'stage': 'Printing', 'percent': 40}


def test_job_waiting_for_the_printer_tells_why(clock):
    # E.g. the cover is open, the printer prints the label once it is closed
    progress.start(DEVICE, [100, 100], (50, 1))
    progress.counting(DEVICE)
    progress.waiting(DEVICE, 'The cover is open')
    assert progress.state(DEVICE) == {'labels': 2, 'label': 1, 'stage': 'The cover is open'}
    clock.now += 300
    progress.printed(DEVICE, 1)
    assert progress.state(DEVICE) == {'labels': 2, 'label': 2}
    progress.finish(DEVICE, True)
    # The wait teaches nothing about the printer
    progress.start(DEVICE, [100, 100], (50, 1))
    assert progress.state(DEVICE)['seconds_left'] == 6


def test_job_ends(clock):
    progress.start(DEVICE, [100], (50, 1))
    assert progress.running(DEVICE)
    progress.finish(DEVICE, True)
    assert progress.state(DEVICE) is None and not progress.running(DEVICE)


def test_any_spelling_of_the_device(clock):
    progress.start(DEVICE, [100], (50, 1))
    assert progress.running('/dev/usb/lp0')
    assert progress.state('/dev/usb/lp0') == {'labels': 1, 'seconds_left': 3}


def test_printer_learns_how_long_its_jobs_take(clock):
    progress.start(DEVICE, [100, 100], (50, 1), mode='fast')
    clock.now += 12
    progress.finish(DEVICE, True)
    # Twice as long as estimated
    progress.start(DEVICE, [100, 100], (50, 1), mode='fast')
    assert progress.state(DEVICE)['seconds_left'] == 12
    progress.finish(DEVICE, True)
    # Each mode on its own, e.g. printing for quality
    progress.start(DEVICE, [100, 100], (50, 1), mode='quality')
    assert progress.state(DEVICE)['seconds_left'] == 6
    progress.finish(DEVICE, True)
    # Each printer on its own
    progress.start('file:///dev/usb/lp1', [100, 100], (50, 1), mode='fast')
    assert progress.state('file:///dev/usb/lp1')['seconds_left'] == 6


def test_printer_learns_from_recent_jobs(clock):
    for seconds in (12, 6):
        progress.start(DEVICE, [100, 100], (50, 1))
        clock.now += seconds
        progress.finish(DEVICE, True)
    # Between the two
    progress.start(DEVICE, [100, 100], (50, 1))
    assert progress.state(DEVICE)['seconds_left'] == 9


def test_failed_job_teaches_nothing(clock):
    progress.start(DEVICE, [100, 100], (50, 1))
    clock.now += 60
    progress.finish(DEVICE, False)
    progress.start(DEVICE, [100, 100], (50, 1))
    assert progress.state(DEVICE)['seconds_left'] == 6


def test_job_that_waited_for_the_printer_teaches_little(clock):
    # E.g. the cover was opened, an hour does not make later estimates useless
    progress.start(DEVICE, [100, 100], (50, 1))
    clock.now += 3600
    progress.finish(DEVICE, True)
    progress.start(DEVICE, [100, 100], (50, 1))
    assert progress.state(DEVICE)['seconds_left'] == 6 * progress.SLOWEST


def test_status_queries_do_not_wait_for_a_running_job(clock):
    assert progress.status_wait(DEVICE) > 0
    progress.start(DEVICE, [100], (50, 1))
    assert progress.status_wait(DEVICE) == 0


@pytest.mark.parametrize('driver', drivers.DRIVERS, ids=lambda driver: type(driver).__name__)
def test_every_model_has_a_print_speed(driver):
    for model in driver.models:
        for fast in (True, False):
            speed, besides = driver.print_speed(model, fast, False)
            assert speed > 0 and besides >= 0, model


def test_quality_and_red_take_longer():
    driver = drivers.for_model('QL-800')
    fast, quality, red = (driver.print_speed('QL-800', *mode) for mode in ((True, False), (False, False), (True, True)))
    assert quality[0] < fast[0] and quality[1] > fast[1]
    assert red[0] < fast[0]


@pytest.fixture
def usb_printer(monkeypatch, clock):
    """A printer on USB that prints each label of a job in 2 s once it has
    the job. Whenever it tells something, the job is noted in seen as
    /api/printer_status tells it."""
    seen = []

    class UsbPrinter(FakePrinter):
        def __init__(self, device_specifier):
            super().__init__(clock, [])

        def write(self, data):
            super().write(data)
            if data.endswith(b'\x1biS'):
                # Asked before the job, it answers as a QL does
                self.messages = [(0, status(REPLY))]
            elif self.written.endswith(b'\x1a'):
                self.messages = page(2) * self.written.count(b'\x1biz')

        def read(self, length=32):
            data = super().read(length)
            if data and (state := progress.state(DEVICE)):
                seen.append(state)
            return data
    monkeypatch.setattr(usb, 'time', clock)
    monkeypatch.setattr(backends, 'KernelPrinter', UsbPrinter)
    return seen


def test_job_on_a_usb_printer_tells_its_labels_and_time(tmp_path, usb_printer):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, print_count='3'))
    assert outcome(client, response) == ('printed', '')
    labels = [state['label'] for state in usb_printer]
    assert labels[0] == 1 and labels[-1] == 3 and labels == sorted(labels)
    assert all(state['labels'] == 3 and state['seconds_left'] >= 0 for state in usb_printer)
    # The first label took 2 s, the two others will, too
    assert {'labels': 3, 'label': 2, 'seconds_left': 4} in usb_printer
    assert progress.state(DEVICE) is None


@pytest.mark.parametrize('model, label_size, dots, high_res', [
    ('PT-P900W', 'pt36', 454, False),
    ('PT-E550W', 'pt24', 128, False),
    ('QL-800', '62', 696, False),
    ('QL-800', '62', 1392, True),
])
def test_label_length_at_the_resolution_of_the_printer(model, label_size, dots, high_res):
    # 100 mm, at 600 dpi twice the lines, which take longer
    dpi = MODELS[model].dpi * (2 if high_res else 1)
    lines = round(100 / 25.4 * dpi)
    label = SimpleNamespace(label_type=LabelType.ENDLESS_LABEL, label_orientation=LabelOrientation.STANDARD,
                            label_content=LabelContent.IMAGE_BW, generate=lambda: Image.new('1', (dots, lines), 1))
    queue = SimpleNamespace(model=model, label_size=label_size, cut='each', half_cut=True, chain=False)
    job = raster.render(queue, [{'label': label, 'high_res': high_res, 'fast': True, 'cut': True}])
    assert drivers.for_model(model).label_lengths(job) == [pytest.approx(200 if high_res else 100, abs=0.1)]


def test_label_length_of_a_zpl_printer():
    # 150 mm along the paper at 8 dots per mm
    label = SimpleNamespace(label_type=LabelType.DIE_CUT_LABEL, label_orientation=LabelOrientation.STANDARD,
                            label_content=LabelContent.IMAGE_BW, generate=lambda: Image.new('1', (800, 1200), 1))
    queue = SimpleNamespace(model='Zebra/ZPL', label_size='zpl100x150', cut='each')
    driver = drivers.for_model('Zebra/ZPL')
    assert driver.label_lengths(driver.render(queue, [{'label': label, 'cut': True}])) == [pytest.approx(150)]


@pytest.mark.parametrize('model, label_size, dots, dpi', [
    ('Epson/ESC-POS', 'escpos80', 576, 203),
    ('Epson/ESC-POS-180', 'escpos80-180', 512, 180),
])
def test_label_length_of_a_receipt_printer_at_its_resolution(model, label_size, dots, dpi):
    # 100 mm along the paper
    label = SimpleNamespace(label_type=LabelType.ENDLESS_LABEL, label_orientation=LabelOrientation.STANDARD,
                            label_content=LabelContent.IMAGE_BW, generate=lambda: Image.new('1', (dots, round(100 / 25.4 * dpi)), 1))
    queue = SimpleNamespace(model=model, label_size=label_size, cut='each')
    driver = drivers.for_model(model)
    assert driver.label_lengths(driver.render(queue, [{'label': label, 'cut': True}])) == [pytest.approx(100, abs=0.1)]


@pytest.mark.parametrize('model, device', [
    ('QL-800', DEVICE),
    ('Epson/TM-T20II', 'file:///dev/usb/lp1'),
    ('PUQU/Q00', 'bt://00:11:22:33:44:55'),
    ('Niimbot/D110_M', 'bt://00:11:22:33:44:66'),
    ('VC-500W', 'vc500w://192.0.2.1'),
    ('Zebra/ZPL', 'tcp://192.0.2.2'),
])
def test_status_of_a_printer_does_not_wait_for_its_job(clock, model, device):
    progress.start(device, [100], (50, 1))
    with devices.device_lock(device):
        started = time.monotonic()
        state = drivers.for_model(model).status(device, model)
        assert time.monotonic() - started < devices.STATUS_LOCK_TIMEOUT / 2
    assert state['status_type'] == devices.BUSY_STATUS_TYPE


def test_printer_status_tells_the_job(tmp_path, clock, monkeypatch):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    monkeypatch.setitem(devices.status_cache, devices.lock_key(DEVICE), {'model': 'QL-800', 'media_width': 62, 'media_length': 0})
    progress.start(DEVICE, [100, 100], (50, 1))
    with devices.device_lock(DEVICE):
        # Without waiting for the printer the job holds
        started = time.monotonic()
        status = client.get('/labeldesigner/api/printer_status').get_json()
        assert time.monotonic() - started < devices.STATUS_LOCK_TIMEOUT / 2
    assert status['printers'][0]['job'] == {'labels': 2, 'seconds_left': 6}
    assert status['job'] == {'labels': 2, 'seconds_left': 6}
    assert status['printers'][0]['status_type'] == devices.BUSY_STATUS_TYPE
    # No job on the simulator
    assert 'job' not in status['printers'][1]


def test_pages_of_several_labels_are_told(clock):
    progress.start(DEVICE, [10, 10], (10, 0), unit='page')
    assert progress.state(DEVICE)['unit'] == 'page'
    progress.start(DEVICE, [10, 10], (10, 0))
    assert 'unit' not in progress.state(DEVICE)
