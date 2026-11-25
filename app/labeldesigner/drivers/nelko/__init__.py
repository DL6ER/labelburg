"""The Nelko P21, a small label printer on classic Bluetooth (protocol.py):
die-cut labels 14 mm wide, a printhead of 12 mm, the label sent as an image
in TSPL. Its roll has a tag that tells the label size. The connection to it
is kept, it beeps when one opens and when it closes."""

import logging
import re

from PIL import Image

from .. import bitmap, devices, diecut, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from ..tspl import protocol as tspl
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('Nelko/P21',)
DPI = 203
# 96 dots across, the label is 14 mm wide
HEAD = 96
# A 14 x 40 mm label is printed on 35.5 mm (284 dots), as Nelko's app does:
# the print starts a few mm apart on each label
LABELS = (Label('nelko14x40', (14, 40), FormFactor.DIE_CUT, (diecut.dots(14, DPI), diecut.dots(40, DPI)), (HEAD, 284), 0,
                models=MODELS, gap=5),)
NAMES = {label.identifier: '{} x {} mm die-cut (Nelko)'.format(*label.tape_size) for label in LABELS}
# mm/s, about 1 s per label besides
SPEED = 20
BESIDES = 1.0
NO_ANSWER = 'The printer does not answer'
# Seconds sending a job may take
WRITE_WAIT = 15


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots, the first one
    leaves the printer first, and the bytes of each row."""
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    img = bitmap.black_and_white(diecut.oriented(label.generate(), across, top_first=True), label)
    rows, width = bitmap.packed_rows(img)
    return img, rows, width


class Nelko(Driver):
    vendor = 'Nelko'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),)
    takes_usbip = False
    # It advertises its name on Bluetooth LE only, it prints over classic
    # Bluetooth at the same address
    bluetooth_names = {re.compile(r'P21([ _-].*)?', re.DOTALL): MODELS[0]}
    refusals = (*(text for _, text in tspl.ERRORS), NO_ANSWER)

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def dpi(self, high_res):
        return DPI

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        # Its kept connection is asked at most every 30 s, connecting to one
        # switched off takes the radio
        return self.bluetooth_status(device, lambda: self.noted_status(device, lambda: self._status(device, model)))

    def _status(self, device, model):
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, network_status(device, model))
            byte, size, charge = protocol.using(
                device, lambda link: (protocol.status(link), protocol.roll(link), protocol.battery(link)))
        state = dict(network_status(device, model), media_type=diecut.MEDIA_TYPE)
        state['errors'] = tspl.errors(byte)
        printing = byte & tspl.PRINTING and not state['errors']
        state['status_type'] = devices.BUSY_STATUS_TYPE if printing else devices.status_type(state['errors'])
        state['phase_type'] = 'Printing state' if printing else 'Waiting to receive'
        # The label designer then picks the labels of the roll
        if size:
            state['media_width'], state['media_length'] = size
        if charge is not None:
            state['battery'], state['battery_low'] = charge, charge <= 20
        devices.status_cache[devices.lock_key(device)] = state
        return dict(state)

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        return [length for _, length in job]

    def render(self, queue, entries, keep=None):
        size = next(label for label in LABELS if label.identifier == queue.label_size)
        job = []
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'nelko_{i}')
            job.append((tspl.label(rows, width, size.tape_size, size.gap, 'gap'), diecut.millimeters(len(rows), DPI)))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        self.ask_bluetooth_again(device)

        def printed(link):
            # It prints nothing while it tells an error, e.g. its lid open
            errors = tspl.errors(protocol.status(link))
            if not errors:
                link.settimeout(WRITE_WAIT)
                try:
                    link.sendall(b''.join(label for label, _ in job))
                except OSError as e:
                    # Not sent again, it may have printed a part
                    raise protocol.Dropped(e.errno, e.strerror) from e
            return errors

        try:
            errors = protocol.using(device, printed)
            if errors:
                return errors[0]
        except protocol.NoAnswer as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return NO_ANSWER
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        devices.note_answer(device)
        return ''


DRIVER = Nelko()
