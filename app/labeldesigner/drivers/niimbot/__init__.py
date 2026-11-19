"""The Niimbot D110_M and B1, thermal printers for die-cut labels on
Bluetooth, the B1 also on USB (protocol.py). They have no cutter, the labels
are torn off. Not the D110, which niimbluelib prints with other commands."""

import logging
import re
import time

from PIL import Image

from .. import bitmap, devices, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from . import calibration, protocol

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
# A B1 prints 0.25 mm right of the middle of a tightly seated roll and starts
# 0.1 to 0.5 mm before the top edge of a label, by roll: its rows are moved
# left by 0.25 mm and down by 0.25 mm
B1_SHIFT = 2
B1_FEED = 2

# Width x length in mm of the rolls sold for the D11 and D110 printers,
# e.g. T12*40 for 12 x 40 mm. Only 12 x 40 mm was printed on.
SIZES = ((12, 22), (12, 40), (14, 22), (14, 40), (14, 60), (15, 30), (15, 50))
# Those sold for the B1, which it prints on 48 mm of
B1_SIZES = ((40, 20), (40, 30), (40, 80), (50, 30), (50, 50), (50, 70), (50, 80))
# Round labels, by their diameter in mm
B1_ROUND = (50,)
# Width x length of the rolls by the barcode in their RFID tag, the label
# designer then picks them. Niimbot tells it only online, at
# print.niimbot.com, the label designer stays offline.
ROLLS = {'01222281': (12, 40),  # T12*40-155WHITE
         '10262260': (50, 30),  # T50*30-230WHITE
         '080924001': (50, 80),  # T50*80-95WHITE
         # Several labels to a page, told but not offered by the label designer
         '052325253': (15, 8),  # T15*8/2L3R-1650White, 2 across, 3 along
         '052325254': (13, 35),  # F13*35/2L-200 Luminous, 2 across
         '052325255': (30, 15),  # TT30*15/2R-460 Transparent, 2 along
         '080924002': (50, 50)}  # T50*50ROUND
# Rolls of round labels, told apart from square ones of the same size
ROUND = {'080924002'}
# On Bluetooth it beeps at each connection: it is asked only when the label
# designer looks for printers and when printing, the other status queries are
# answered from the last state. A B1 keeps its connection over Bluetooth LE
# while used, see protocol.KEEP_LE, and is also asked by the label designer
# that follows it, see followed_status(). When each was last asked:
_asked: dict[str, float] = {}
# B1s that told no serial number, not asked again: each question unanswered
# waits for its answer
_no_serial: set[str] = set()
# One status question per printer is outstanding at most: the polls after one
# went unanswered read whether its answer came, for OWED_WAIT seconds, and
# tell the last state meanwhile. After STATUS_LOST seconds it counts as lost:
# the printer does not answer, and the next poll asks anew. Over classic
# Bluetooth no answer outlives its connection, the B1 keeps its connection
# over Bluetooth LE. By device: the question, when
# it was asked, and whether the printer was told as not answering.
STATUS_LOST = 20
OWED_WAIT = 0.2
_owed: dict[str, tuple[str, float, bool]] = {}


def _over_le(model: str) -> bool:
    """Whether a printer on bt:// is reached over Bluetooth LE: the B1
    speaks only that, the D110_M classic Bluetooth."""
    return model == 'Niimbot/B1'


def _beeps(device: str, model: str) -> bool:
    """Whether each status query beeps, a new connection over classic
    Bluetooth."""
    return device.startswith('bt://') and not _over_le(model)


def _label(width: int, length: int, model: str, form: FormFactor = FormFactor.DIE_CUT) -> Label:
    dots = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    printable = (min(dots[0], HEADS[model]), dots[1])
    identifier = f'niimbotd{width}' if form == FormFactor.ROUND_DIE_CUT else f'niimbot{width}x{length}'
    return Label(identifier, (width, length), form, dots, printable, 0, models=(model,))


LABELS = (tuple(_label(width, length, 'Niimbot/D110_M') for width, length in SIZES)
          + tuple(_label(width, length, 'Niimbot/B1') for width, length in B1_SIZES)
          + tuple(_label(d, d, 'Niimbot/B1', FormFactor.ROUND_DIE_CUT) for d in B1_ROUND))
NAMES = {label.identifier: (f'{label.tape_size[0]} mm round die-cut (Niimbot)' if label.form_factor == FormFactor.ROUND_DIE_CUT
                            else f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (Niimbot)') for label in LABELS}


def centered(dots: bytes, head: int, shift: int = 0) -> bytes:
    """A row across the whole printhead with the dots in its middle, moved
    left by shift; dots beyond the printhead are left out."""
    left = (head - len(dots)) // 2 - shift
    placed = bytes(left) + dots if left >= 0 else dots[-left:]
    return (placed + bytes(head))[:head]


def placed(img: Image.Image, head: int, shift: int, feed: int) -> list[bytes]:
    """Rows across the printhead with the image in its middle, moved left by
    shift and down by feed dots (negative: right, up), as many as the image
    has; dots beyond the printhead or the page are left out."""
    rows = [centered(row, head, shift) for row in bitmap.dot_rows(img)]
    empty = [bytes(head)] * abs(feed)
    return (empty + rows)[:len(rows)] if feed >= 0 else (rows + empty)[abs(feed):]


def label_rows(label, label_size: str, offset: tuple[float, float] = (0.0, 0.0)) -> tuple[Image.Image, list[bytes]]:
    """A label as the printer prints it: rows across the printhead, as wide
    as the label, black is printed. offset: mm the printer's labels are
    moved right and down, a B1 only."""
    img = label.generate()
    # The label designer draws labels longer than wide lying, e.g. 12 x 40
    # mm as 320 x 96 dots; turned as by the other drivers, their end leaves
    # the printer first
    size = next(size for size in LABELS if size.identifier == label_size)
    across = size.dots_printable[0]
    if img.width != across:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    if size.models == ('Niimbot/B1',):
        return img, placed(img, protocol.B1_HEAD, B1_SHIFT - round(offset[0] * DOTS_PER_MM),
                           B1_FEED + round(offset[1] * DOTS_PER_MM))
    return img, [row[:protocol.HEAD] for row in bitmap.dot_rows(img)]


class Niimbot(Driver):
    vendor = 'Niimbot'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # bt://<MAC>[/<RFCOMM channel>], a B1 also on USB: usblp on Linux, else
    # through PyUSB, e.g. usb://0x3513:0x0002
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),
                       re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x3513:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'))
    errors = (OSError, protocol.NiimbotError)
    refusals = (calibration.KIND_MESSAGE,)

    def model_of(self, device):
        # By the device ID on USB: MFG:NIIMBOT;CMD:ESC;MDL: B1;
        fields = devices.device_id_fields(devices.usb_device_id(device)) if isinstance(device, str) else {}
        model = f"Niimbot/{fields.get('MDL', '').strip()}"
        return model if fields.get('MFG', '').strip().upper() == 'NIIMBOT' and model in MODELS else None

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True,
                    calibration=model == 'Niimbot/B1')

    def calibration_kind(self, model, label_size):
        size = next((label for label in LABELS if label.identifier == label_size and model in label.models), None)
        if model != 'Niimbot/B1' or size is None:
            return ''
        if size.form_factor == FormFactor.ROUND_DIE_CUT:
            return 'round'
        return 'narrow' if calibration.is_narrow(size.tape_size[0]) else 'wide'

    def calibration_job(self, queue):
        size = next(label for label in LABELS if label.identifier == queue.label_size)
        width, length = size.tape_size
        if size.form_factor == FormFactor.ROUND_DIE_CUT:
            page = calibration.circle(width)
        else:
            page = calibration.narrow(width, length) if calibration.is_narrow(width) else calibration.wide(width, length)
        # Its title is drawn smooth, printed where at least half black
        page = page.point(lambda v: 0 if v < 128 else 255)
        return [placed(page, protocol.B1_HEAD, B1_SHIFT - round(queue.offset[0] * DOTS_PER_MM),
                       B1_FEED + round(queue.offset[1] * DOTS_PER_MM))]

    def calibration_example(self, kind):
        return calibration.example(kind)

    def followed(self, device, model):
        # Its kept connection over Bluetooth LE costs nothing either
        return (device.startswith('bt://') and _over_le(model)) or super().followed(device, model)

    def followed_status(self, device, model):
        return self.status(device, model, fresh=True)

    def status(self, device, model, unconfirmed=False, fresh=False):
        # One that did not answer is not looked for at every status query
        error = devices.unanswered_before(device)
        if error:
            raise error.with_traceback(None)
        cached = devices.status_cache.get(devices.lock_key(device))
        # Also asked while it cannot print, so that the error goes once the
        # lid is closed; it beeps every status query only then. On USB it is
        # asked every time, it tells another roll or a lost connection so.
        if (device.startswith('bt://') and not (fresh and _over_le(model)) and cached and not cached.get('errors')
                and _asked.get(device, -1) >= devices.looked):
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
            with protocol.connect(device, _over_le(model)) as c:
                told = self._asked(c, device, model)
                if told is None:
                    return dict(devices.status_cache[devices.lock_key(device)])
                serial = self._serial(c, device, model)
        return self._keep(device, model, told['heartbeat'], told['roll'], serial)

    def _asked(self, c, device, model) -> dict | None:
        """The heartbeat and the roll the printer tells, None where a
        question is left unanswered and the last state is told, see
        STATUS_LOST. Raises NoAnswer where it does not answer."""
        now = time.monotonic()
        owed = _owed.pop(device, None)
        silent = owed is not None and (owed[2] or now - owed[1] >= STATUS_LOST)
        if owed is not None and now - owed[1] >= STATUS_LOST:
            owed = None
        steps = ['heartbeat', 'roll']
        if owed is not None:
            steps.remove(owed[0])
            steps.insert(0, owed[0])
        told = {}
        for step in steps:
            waiting = owed is not None and step == owed[0]
            try:
                told[step] = getattr(protocol, step)(c, send=not waiting, wait=OWED_WAIT if waiting else None)
            except protocol.NoAnswer:
                if _beeps(device, model):
                    raise
                _owed[device] = (step, owed[1], silent) if waiting else (step, now, silent)
                if silent or devices.lock_key(device) not in devices.status_cache:
                    raise
                return None
        return told

    def _serial(self, c, device, model):
        """The serial number of a B1, asked once more after it told none."""
        if model != 'Niimbot/B1' or device in _no_serial:
            return None
        serial = protocol.printer_serial(c)
        if serial is None:
            _no_serial.add(device)
        return serial

    def _keep(self, device, model, beat, roll, serial=None):
        """The state of the printer by its heartbeat and roll, kept for the
        status queries until it is asked again."""
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'phase_type': 'Waiting to receive'})
        state['errors'] = protocol.errors(beat)
        # A D110_M neither parks nor prints a roll without its tag
        if roll is None and not state['errors']:
            state['errors'] = ['No Niimbot roll with its RFID tag is in the printer']
        state['status_type'] = 'Error occurred' if state['errors'] else 'Reply to status request'
        # It tells 0 to 4, shown as a percentage, low at 1 of 4 or less
        battery = beat.get('battery')
        state['battery_low'] = battery in (0, 1)
        if isinstance(battery, int):
            state['battery'] = min(battery, 4) * 25
        # The settings of a B1 follow it by its serial number, the label
        # designer tells when another roll is in it
        state['serial'] = serial
        serial_of_roll = roll.get('serial') if roll else None
        state['roll'] = (serial_of_roll if serial_of_roll and serial_of_roll.isascii() and serial_of_roll.isalnum()
                         and len(serial_of_roll) <= 32 else None)
        # The label designer then picks the labels of the roll
        if roll and roll['barcode'] in ROLLS:
            state['media_width'], state['media_length'] = ROLLS[roll['barcode']]
            if roll['barcode'] in ROUND:
                state['media_type'] = 'Round die-cut labels'
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
            img, rows = label_rows(entry['label'], queue.label_size, queue.offset)
            if keep:
                keep(img, f'niimbot_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        """Print each label as a job of its own, the way the script this
        came from prints one, with the label type of the roll."""
        device, model = queue.device_specifier, queue.model
        # It asks the printer itself, and may read the answer owed
        _owed.pop(device, None)
        try:
            with protocol.connect(device, _over_le(model)) as c:
                told = MODEL_IDS.get(protocol.model_id(c), model)
                if told != model:
                    return f'This printer is a {shown(told)}, choose that model for it'
                # What the status keeps is updated, e.g. another roll
                beat = protocol.heartbeat(c)
                loaded = protocol.roll(c)
                state = self._keep(device, model, beat, loaded, self._serial(c, device, model))
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
            return protocol.reason(e) or 'The printer refused the label, the log of the server names the reason'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            # Asked again at the next status query, which then tells why
            devices.note_answer(device, e)
            _asked.pop(device, None)
            return devices.not_sent(e)
        return ''


DRIVER = Niimbot()
