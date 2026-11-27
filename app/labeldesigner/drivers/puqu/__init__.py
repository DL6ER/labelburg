"""The PUQU Q00, a thermal printer for die-cut labels on Bluetooth
(protocol.py), also sold as the Reekon P1 Tagger (P1L), configured as a
Q00 as well. It has no cutter, the labels are torn off. The Q1 and AQ20
have its printhead after their manuals (48 mm, 384 dots, 203 dpi), and
puquprintJS names their series as using its protocol."""

import logging
import re

from PIL import Image

from .. import bitmap, devices, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('PUQU/Q00', 'PUQU/Q1', 'PUQU/AQ20')

# Width x length in mm, and how far from the left of the printhead the
# label lies, in mm. Labels narrower than the printhead are held by a slider
# against one side, a 40 mm label on a Reekon P1L came out right 1 mm from it.
SIZES = {(50, 30): 0, (50, 80): 0, (40, 30): 1, (40, 50): 1}
OFFSETS = {f'puqu{w}x{h}': offset for (w, h), offset in SIZES.items()}
DOTS_PER_MM = 8
# No figures of the maker: mm/s and the seconds a label takes besides,
# mostly waiting for the printer to tell that it printed it, a guess the
# jobs correct
SPEED = 20
BESIDES = 3.0


def _label(width: int, length: int, offset: int) -> Label:
    total = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    printable = (min(total[0], protocol.HEAD - offset * DOTS_PER_MM), total[1])
    return Label(f'puqu{width}x{length}', (width, length), FormFactor.DIE_CUT, total, printable, 0,
                 models=MODELS)


LABELS = tuple(_label(width, length, offset) for (width, length), offset in SIZES.items())
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (PUQU)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes]]:
    """A label as the printer prints it: rows across the printhead, the
    first one comes out first, black is printed."""
    img = label.generate()
    # Turned across the printhead: rotated labels, and labels longer than
    # wide, which the label designer draws lying, e.g. 50 x 80 mm as 640 x
    # 384 dots
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    offset = OFFSETS[label_size] * DOTS_PER_MM
    return img, [(bytes(offset) + row + bytes(protocol.HEAD))[:protocol.HEAD] for row in bitmap.dot_rows(img)]


class Puqu(Driver):
    vendor = 'PUQU'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # bt://<MAC>[/<RFCOMM channel>]
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),)
    errors = (OSError, protocol.PuquError)
    reports_tape = False

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off. Their top leaves it first.
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        return self.noted_status(device, lambda: self._status(device, model))

    def _status(self, device, model):
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'phase_type': 'Waiting to receive'})
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, state)
            reply = protocol.get_state(device)
        state['errors'] = protocol.errors(reply)
        state['status_type'] = devices.status_type(state['errors'])
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        return [len(rows) / DOTS_PER_MM for rows in job]

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'puqu_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            error = protocol.print_labels(device, job, self.label_lengths(job))
            devices.note_answer(device)
        except protocol.PuquError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        if error:
            logger.warning('Printing on %s failed: %s', device, error)
        return error


DRIVER = Puqu()
