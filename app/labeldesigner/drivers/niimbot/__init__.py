"""The Niimbot D110_M and B1, thermal printers for die-cut labels on
Bluetooth (protocol.py). They have no cutter, the labels are torn off. Not
the D110, which niimbluelib prints with other commands."""

import logging
import re
import time

from PIL import Image

from .. import devices, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('Niimbot/D110_M', 'Niimbot/B1')
DOTS_PER_MM = 8
# mm/s as sellers give it, and the seconds a label takes besides, to set up
# its job and to tell that it printed it
SPEED = 30
BESIDES = 2.0
# The dots across the printhead and the print sequence of each model
HEADS = {'Niimbot/D110_M': protocol.HEAD, 'Niimbot/B1': protocol.B1_HEAD}
SEQUENCES = {'Niimbot/D110_M': protocol.V4, 'Niimbot/B1': protocol.B1}
# The models by the model ID they tell, which the print sequence and the
# printhead have to fit
MODEL_IDS = {2320: 'Niimbot/D110_M', 4096: 'Niimbot/B1'}

# Width x length in mm of the rolls sold for the D11 and D110 printers,
# e.g. T12*40 for 12 x 40 mm. Only 12 x 40 mm was printed on.
SIZES = ((12, 22), (12, 40), (14, 22), (14, 40), (14, 60), (15, 30), (15, 50))
# Those sold for the B1, which it prints on 48 mm of
B1_SIZES = ((40, 20), (40, 30), (40, 80), (50, 30), (50, 50), (50, 70), (50, 80))
# Width x length of the rolls by the barcode in their RFID tag, the label
# designer then picks them. Niimbot tells it only online, at
# print.niimbot.com, the label designer stays offline.
ROLLS = {'01222281': (12, 40)}  # T12*40-155WHITE
# It beeps at each connection: it is asked only when the label designer looks
# for printers and when printing, the other status queries are answered from
# the last state. When each was last asked:
_asked: dict[str, float] = {}


def _label(width: int, length: int, model: str) -> Label:
    dots = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    printable = (min(dots[0], HEADS[model]), dots[1])
    return Label(f'niimbot{width}x{length}', (width, length), FormFactor.DIE_CUT, dots, printable, 0, models=(model,))


LABELS = (tuple(_label(width, length, 'Niimbot/D110_M') for width, length in SIZES)
          + tuple(_label(width, length, 'Niimbot/B1') for width, length in B1_SIZES))
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (Niimbot)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[list[int]]]:
    """A label as the printer prints it: rows across the printhead, as wide
    as the label, black is printed."""
    img = label.generate()
    # The label designer draws labels longer than wide lying, e.g. 12 x 40
    # mm as 320 x 96 dots; turned as by the other drivers, their end leaves
    # the printer first
    size = next(size for size in LABELS if size.identifier == label_size)
    across = size.dots_printable[0]
    if img.width != across:
        img = img.rotate(90, expand=True)
    dither = label.label_content != LabelContent.IMAGE_BW
    img = img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    if size.models == ('Niimbot/B1',):
        # Rows across the whole printhead, the label in the middle of it
        left = (protocol.B1_HEAD - img.width) // 2
        rows = [[0] * left + [int(img.getpixel((x, y)) == 0) for x in range(img.width)] + [0] * (protocol.B1_HEAD - left - img.width)
                for y in range(img.height)]
        return img, rows
    rows = [[int(img.getpixel((x, y)) == 0) for x in range(min(img.width, protocol.HEAD))] for y in range(img.height)]
    return img, rows


class Niimbot(Driver):
    vendor = 'Niimbot'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # bt://<MAC>[/<RFCOMM channel>]
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),)
    errors = (OSError, protocol.NiimbotError)

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        # One that did not answer is not looked for at every status query
        error = devices.unanswered_before(device)
        if error:
            raise error.with_traceback(None)
        cached = devices.status_cache.get(devices.lock_key(device))
        # Also asked while it cannot print, so that the error goes once the
        # lid is closed; it beeps every status query only then
        if cached and not cached.get('errors') and _asked.get(device, -1) >= devices.looked:
            return dict(cached)
        try:
            state = self._status(device, model)
        except self.errors as e:
            devices.note_answer(device, e)
            raise
        devices.note_answer(device)
        return state

    def _status(self, device, model):
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                state = network_status(device, model)
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            with protocol.connect(device) as c:
                beat = protocol.heartbeat(c)
                roll = protocol.roll(c)
        return self._keep(device, model, beat, roll)

    def _keep(self, device, model, beat, roll):
        """The state of the printer by its heartbeat and roll, kept for the
        status queries until it is asked again."""
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'phase_type': 'Waiting to receive'})
        state['errors'] = protocol.errors(beat)
        # A D110_M neither parks nor prints a roll without its tag
        if roll is None and not state['errors']:
            state['errors'] = ['No Niimbot roll with its RFID tag is in the printer']
        state['status_type'] = 'Error occurred' if state['errors'] else 'Reply to status request'
        # Told only when it needs charging, at 1 of 4 or less
        state['battery_low'] = beat.get('battery') in (0, 1)
        # The label designer then picks the labels of the roll
        if roll and roll['barcode'] in ROLLS:
            state['media_width'], state['media_length'] = ROLLS[roll['barcode']]
        devices.status_cache[devices.lock_key(device)] = state
        _asked[device] = time.monotonic()
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
                keep(img, f'niimbot_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        """Print each label as a job of its own, the way the script this
        came from prints one, with the label type of the roll."""
        device, model = queue.device_specifier, queue.model
        try:
            with protocol.connect(device) as c:
                told = MODEL_IDS.get(protocol.model_id(c), model)
                if told != model:
                    return f'This printer is a {shown(told)}, choose that model for it'
                # What the status keeps is updated, e.g. another roll
                beat = protocol.heartbeat(c)
                loaded = protocol.roll(c)
                state = self._keep(device, model, beat, loaded)
                if state['errors']:
                    devices.note_answer(device)
                    return state['errors'][0]
                label_type = loaded['type'] if loaded else protocol.WITH_GAPS
                progress.counting(device)
                for i, rows in enumerate(job):
                    protocol.print_label(c, rows, label_type, SEQUENCES[model])
                    progress.printed(device, i + 1)
            devices.note_answer(device)
        except protocol.NiimbotError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            # Asked again at the next status query
            _asked.pop(device, None)
            if isinstance(e, protocol.NoAnswer):
                return 'The printer does not answer'
            if isinstance(e, protocol.NoTag):
                return 'The printer prints only on Niimbot rolls with their RFID tag'
            if isinstance(e, protocol.NotPrinted):
                return 'The printer did not tell that it printed the label'
            return 'The printer refused the label, the log of the server names the reason'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            # Asked again at the next status query, which then tells why
            devices.note_answer(device, e)
            _asked.pop(device, None)
            reason = devices.unanswered_reason(e)
            return (f'Could not send the label to the printer: {reason}' if reason
                    else 'Could not send the label to the printer, the log of the server names the reason')
        return ''


DRIVER = Niimbot()
