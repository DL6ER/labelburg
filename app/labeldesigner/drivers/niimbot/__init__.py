"""Niimbot thermal printers for die-cut labels on Bluetooth and USB
(protocol.py): the D110_M, the B1 and the models niimbluelib prints with the
same commands, see models.py. They have no cutter, the labels are torn off.
Other models, e.g. the D110, are refused by the model ID they tell."""

import logging
import re
import time
from dataclasses import replace

from PIL import Image

from .. import backends, bitmap, devices, progress
from ..base import Driver, network_status
from ..labels import FormFactor, Label
from . import calibration, models, protocol

logger = logging.getLogger(__name__)

MODELS = tuple(model.name for model in models.PRINTED)
DOTS_PER_MM = 8
# mm/s as sellers give it, and the seconds a label takes besides, to set up
# its job and to tell that it printed it
SPEED = 30
BESIDES = 2.0
# The dots across the printhead of each model
HEADS = {model.name: model.dots for model in models.PRINTED}
# A B1 prints 0.25 mm right of the middle of a tightly seated roll and starts
# 0.1 to 0.5 mm before the top edge of a label, by roll: its rows are moved
# left by 0.25 mm and down by 0.25 mm
B1_SHIFT = 2
B1_FEED = 2

# Width x length in mm of the rolls sold for the D11 and D110 printers,
# e.g. T12*40 for 12 x 40 mm. Only 12 x 40 mm was printed on.
SIZES = ((12, 22), (12, 40), (14, 22), (14, 40), (14, 60), (15, 30), (15, 50))
# Those sold for the B1, which it prints on 48 mm of. The other models
# take those of the D11 or the B1 by the width of their printhead.
B1_SIZES = ((40, 20), (40, 30), (40, 80), (50, 30), (50, 50), (50, 70), (50, 80))
# Round labels, by their diameter in mm
B1_ROUND = (50,)
# Rolls of several labels to a page, printed as the whole page: the width and
# length of a label, how many across and along, the gap between lanes in mm
B1_PAGES = ((30, 15, 1, 2, 0), (15, 8, 2, 3, 0), (13, 35, 2, 1, 2))
# Width x length of the rolls by the barcode in their RFID tag, the label
# designer then picks them. Niimbot tells it only online, at
# print.niimbot.com, the label designer stays offline.
ROLLS = {'01222281': (12, 40),  # T12*40-155WHITE
         '10262260': (50, 30),  # T50*30-230WHITE
         '080924001': (50, 80),  # T50*80-95WHITE
         # Several labels to a page, by the size of the page, see B1_PAGES
         '052325253': (30, 24),  # T15*8/2L3R-1650White, 2 across, 3 along
         '052325254': (28, 35),  # F13*35/2L-200 Luminous, 2 across, 2 mm apart
         '052325255': (30, 30),  # TT30*15/2R-460 Transparent, 2 along
         '080924002': (50, 50)}  # T50*50ROUND
# Rolls of round labels, told apart from square ones of the same size
ROUND = {'080924002'}
# On Bluetooth it beeps at each connection: it is asked only when the label
# designer looks for printers and when printing, the other status queries are
# answered from the last state. A B1 keeps its connection over Bluetooth LE
# while used, see transports.KEEP_LE, and is also asked by the label designer
# that follows it, see followed_status(). When each was last asked:
_asked: dict[str, float] = {}
# B1s that told no serial number, not asked again: each question unanswered
# waits for its answer
_no_serial: set[str] = set()
# The models printers on Bluetooth LE and serial ports on USB told by their
# model ID, by device, and when they were asked. One that told none is asked
# again once the label designer looks for printers, on Bluetooth LE also
# STATUS_LOST seconds later.
_told_models: dict[str, tuple[models.Model | None, float]] = {}
# Printers found on Bluetooth LE whose name is no B1's, e.g. by Niimbot's
# service alone without a name: their model is the one they tell, the one
# found while they tell none
_to_ask: set[str] = set()
# The models whose name on Bluetooth LE was seen on a printer, B1-<serial>;
# the others' are guesses
NAMES_SEEN = {'Niimbot/B1'}
# Printers on Bluetooth LE, the others paired
_LE = {model.name for model in models.PRINTED if 'le' in model.connections}
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
    """Whether a printer on bt:// is reached over Bluetooth LE, as niimbluelib
    reaches them; the D110_M over classic Bluetooth."""
    return model in _LE


def _told(c, device: str, again: float | None = None) -> models.Model | None:
    """The model the printer tells by its model ID, asked once per device,
    None for none or one niimbluelib does not list. One that told none is
    asked again once the label designer looks for printers, or again
    seconds later."""
    key = devices.lock_key(device)
    known = _told_models.get(key)
    if known and (known[0] or (known[1] >= devices.looked and (again is None or time.monotonic() - known[1] < again))):
        return known[0]
    told = models.BY_ID.get(protocol.model_id(c))
    _told_models[key] = (told, time.monotonic())
    return told


def _kept(device: str, model: str) -> bool:
    """Whether the connection to a printer on bt:// is kept, see
    transports.KEEP_LE: over Bluetooth LE, unless it saves energy."""
    return device.startswith('bt://') and _over_le(model) and not devices.saves_energy(device)


def _beeps(device: str, model: str) -> bool:
    """Whether each status query beeps, a new connection over Bluetooth."""
    return device.startswith('bt://') and not _kept(device, model)


def _dots(mm: float, dpi: int) -> int:
    return round(mm * DOTS_PER_MM) if dpi == protocol.DPI else round(mm * dpi / 25.4)


def _label(width: int, length: int, model: str, form: FormFactor = FormFactor.DIE_CUT) -> Label:
    dpi = models.BY_NAME[model].dpi
    dots = (_dots(width, dpi), _dots(length, dpi))
    printable = (min(dots[0], HEADS[model]), dots[1])
    identifier = f'niimbotd{width}' if form == FormFactor.ROUND_DIE_CUT else f'niimbot{width}x{length}'
    return Label(identifier, (width, length), form, dots, printable, 0, models=(model,))


def _page(width: int, length: int, across: int, along: int, gap: float, model: str) -> Label:
    """The page of a roll of several labels, named by one of them."""
    page = _label(across * width + (across - 1) * gap, along * length, model)
    return replace(page, identifier=f'niimbot{width}x{length}-{across}x{along}', pieces=(across, along, gap))


def _rolls(model: str) -> list[Label]:
    """The labels of the rolls a model takes: those of the D11 on a
    printhead up to 25 mm wide, else those of the B1, its pages of several
    labels at its 8 dots per mm only."""
    dpi = models.BY_NAME[model].dpi
    if HEADS[model] / dpi * 25.4 <= 25:
        return [_label(width, length, model) for width, length in SIZES]
    return ([_label(width, length, model) for width, length in B1_SIZES]
            + [_label(d, d, model, FormFactor.ROUND_DIE_CUT) for d in B1_ROUND]
            + [_page(*page, model) for page in B1_PAGES if dpi == protocol.DPI])


def _labels() -> tuple[Label, ...]:
    """The labels of every model: one of the same dots at the same dpi is
    shared, the D110_M and the B1 name them, another one is named by its
    model, e.g. niimbot50x30-b1pro."""
    labels: dict[str, Label] = {}
    for model in models.PRINTED:
        for label in _rolls(model.name):
            shared = labels.get(label.identifier)
            if shared is None:
                labels[label.identifier] = label
            elif ((shared.dots_total, shared.dots_printable) == (label.dots_total, label.dots_printable)
                  and models.BY_NAME[shared.models[0]].dpi == model.dpi):
                labels[label.identifier] = replace(shared, models=shared.models + (model.name,))
            else:
                identifier = f"{label.identifier}-{model.short.lower().replace('_', '')}"
                labels[identifier] = replace(label, identifier=identifier)
    return tuple(labels.values())


def _name(label: Label) -> str:
    width, length = label.tape_size
    if label.form_factor == FormFactor.ROUND_DIE_CUT:
        return f'{width} mm round die-cut (Niimbot)'
    across, along, gap = label.pieces
    if across * along > 1:
        return f'{width} x {length} mm, {across * along} labels of {(width - (across - 1) * gap) // across} x {length // along} (Niimbot)'
    return f'{width} x {length} mm die-cut (Niimbot)'


LABELS = _labels()
NAMES = {label.identifier: _name(label) for label in LABELS}


def _bluetooth_name(model: models.Model) -> re.Pattern:
    """The name a model is guessed to advertise on Bluetooth LE, B1-<serial>
    as a B1, the parts of a name like B21_PRO also apart."""
    return re.compile(r'[_ -]?'.join(map(re.escape, model.short.split('_'))) + '-.*', re.IGNORECASE | re.DOTALL)


REFUSALS = (tuple(models.other_model(model) for model in models.PRINTED)
            + tuple(models.cannot_print(model) for model in models.MODELS if model not in models.PRINTED))
# The names of the models where they differ from their ids, e.g. B1 Pro
SHOWN_AS = {model.name: f'Niimbot/{model.shown}' for model in models.PRINTED if model.shown != model.short}
BLUETOOTH_NAMES = {_bluetooth_name(model): model.name for model in models.PRINTED if model.name in _LE}


class _Job(list):
    """The rows of each label, and the dots per mm along the paper."""

    def __init__(self, rows=(), per_mm: float = DOTS_PER_MM):
        super().__init__(rows)
        self.per_mm = per_mm


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


def label_rows(label, label_size: str, offset: tuple[float, float] = (0.0, 0.0),
               model: str | None = None) -> tuple[Image.Image, list[bytes]]:
    """A label as the printer of the model prints it, the first of the label
    size if None: rows across the printhead, black is printed. On a B1 rows
    across all of it, moved by offset, mm the printer's labels are moved
    right and down; on the others as wide as the label in whole bytes, as
    niimbluelib sends them."""
    img = label.generate()
    # The label designer draws labels longer than wide lying, e.g. 12 x 40
    # mm as 320 x 96 dots; turned as by the other drivers, their end leaves
    # the printer first
    size = next(size for size in LABELS if size.identifier == label_size)
    across = size.dots_printable[0]
    if img.width != across:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    if (model or size.models[0]) == 'Niimbot/B1':
        return img, placed(img, protocol.B1_HEAD, B1_SHIFT - round(offset[0] * DOTS_PER_MM),
                           B1_FEED + round(offset[1] * DOTS_PER_MM))
    width = -(-across // 8) * 8
    return img, [(row + bytes(width))[:width] for row in bitmap.dot_rows(img)]


class Niimbot(Driver):
    vendor = 'Niimbot'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    shown_as = SHOWN_AS
    # bt://<MAC>[/<RFCOMM channel>], a B1 also on USB: usblp on Linux, else
    # through PyUSB, e.g. usb://0x3513:0x0002
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),
                       re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x3513:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'))
    errors = (OSError, protocol.NiimbotError)
    refusals = (calibration.KIND_MESSAGE, *REFUSALS)
    # The models on Bluetooth LE by their names, a printer that leaves its
    # name out by Niimbot's service, as a B1 does
    bluetooth_names = BLUETOOTH_NAMES
    bluetooth_services = {protocol.NIIMBOT_SERVICE: 'Niimbot/B1'}
    # Those that are a serial port on USB, which tell their model by its ID
    usb_serial_ids = ((0x3513, None),)

    def model_of(self, device, device_id=None):
        if device_id is None:
            device_id = devices.usb_device_id(device) if isinstance(device, str) else ''
        return self.model_in(device_id) or (None if device_id else self._model_told(device))

    def model_in(self, device_id):
        # MFG:NIIMBOT;CMD:ESC;MDL: B1; on USB, also shared over USB/IP. Other
        # models as niimbluelib names them, e.g. B1 Pro as B1_PRO.
        fields = devices.device_id_fields(device_id)
        name = fields.get('MDL', '').strip().upper().replace('-', '_').replace(' ', '_')
        model = f'Niimbot/{name}'
        return model if fields.get('MFG', '').strip().upper() == 'NIIMBOT' and model in MODELS else None

    def _model_told(self, device) -> str | None:
        """The model a serial port on USB tells by its model ID, None for
        another device or a model Labelburg cannot print on."""
        if not (isinstance(device, str) and device.lower().startswith('usb://0x3513:')):
            return None
        known = _told_models.get(devices.lock_key(device))
        if known and (known[0] or known[1] >= devices.looked):
            return known[0].name if known[0] in models.PRINTED else None
        # Not a printer on its printer interface, e.g. a B1 that usblp holds
        if not backends.is_serial_port(device):
            return None
        # Never in between the packets of a job or a status query
        with devices.device_lock(device, timeout=0) as acquired:
            if not acquired:
                return None
            try:
                with protocol.connect(device) as c:
                    told = _told(c, device)
            except Exception:
                # Also no PyUSB backend, e.g. no libusb
                logger.debug('No model ID of %s', device, exc_info=True)
                return None
        return told.name if told in models.PRINTED else None

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # The same roll of another model, e.g. niimbot50x30-b1pro for niimbot50x30
        made = next((label for label in LABELS if label.identifier == label_size), None)
        if made is None or model in made.models:
            return label_size
        paper = (made.tape_size, made.form_factor, made.pieces)
        return next((label.identifier for label in LABELS
                     if model in label.models and (label.tape_size, label.form_factor, label.pieces) == paper), label_size)

    def dpi(self, high_res):
        return protocol.DPI

    def label_dpi(self, label):
        dpi = models.BY_NAME[label.models[0]].dpi
        return dpi, dpi

    def resolution_problem(self, model, high_res):
        return f'The {self.shown_as.get(model, model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True,
                    calibration=model == 'Niimbot/B1', model_shown=self.shown_as.get(model, model).split('/')[-1])

    def keeps_connection(self, device, model):
        return device.startswith('bt://') and _over_le(model)

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

    def found_on_bluetooth(self, device):
        # Other models advertise the service too: it counts only without a name
        name = device.get('name')
        named = {model for pattern, model in self.bluetooth_names.items() if re.fullmatch(pattern, name or '')}
        if name and not named:
            return None
        found = super().found_on_bluetooth(device)
        # One whose name is a guess is asked for its model, see _status()
        if found and not (len(named) == 1 and named <= NAMES_SEEN):
            _to_ask.add(devices.lock_key(found[0]))
        elif found:
            _to_ask.discard(devices.lock_key(found[0]))
        return found

    def followed(self, device, model):
        # Its kept connection over Bluetooth LE costs nothing either
        return _kept(device, model) or super().followed(device, model)

    def followed_status(self, device, model):
        return self.status(device, model, fresh=True)

    def status(self, device, model, unconfirmed=False, fresh=False):
        return self.noted_status(device, lambda: self._status(device, model, fresh))

    def _status(self, device, model, fresh):
        cached = devices.status_cache.get(devices.lock_key(device))
        known = _told_models.get(devices.lock_key(device))
        if devices.lock_key(device) in _to_ask and known and known[0] and known[0] not in models.PRINTED:
            return self._refused(device, model, known[0])
        # Also asked while it cannot print, so that the error goes once the
        # lid is closed; it beeps every status query only then. On USB it is
        # asked every time, it tells another roll or a lost connection so.
        if (device.startswith('bt://') and not (fresh and _kept(device, model)) and cached and not cached.get('errors')
                and _asked.get(device, -1) >= devices.looked):
            return dict(cached)
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, network_status(device, model))
            with protocol.connect(device, _over_le(model)) as c:
                # Its name was a guess: the model it tells is used
                if devices.lock_key(device) in _to_ask:
                    named = _told(c, device, STATUS_LOST)
                    if named is not None:
                        if named not in models.PRINTED:
                            return self._refused(device, model, named)
                        model = named.name
                told = self._asked(c, device, model)
                if told is None:
                    return dict(devices.status_cache[devices.lock_key(device)])
                serial = self._serial(c, device, model)
        return self._keep(device, model, told['heartbeat'], told['roll'], serial)

    def _refused(self, device, model, told: models.Model) -> dict:
        """The state of a printer of a model Labelburg cannot print on."""
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'errors': [models.cannot_print(told)]})
        state['status_type'] = devices.status_type(state['errors'])
        devices.status_cache[devices.lock_key(device)] = state
        return state

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
        # A D110_M neither parks nor prints a roll without its tag, the
        # models that need one refuse to print without it
        if roll is None and not state['errors'] and getattr(models.BY_NAME.get(model), 'needs_tag', True):
            state['errors'] = ['No Niimbot roll with its RFID tag is in the printer']
        state['status_type'] = devices.status_type(state['errors'])
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
        return [len(rows) / getattr(job, 'per_mm', DOTS_PER_MM) for rows in job]

    def render(self, queue, entries, keep=None):
        dpi = models.BY_NAME[queue.model].dpi if queue.model in models.BY_NAME else protocol.DPI
        job = _Job(per_mm=DOTS_PER_MM if dpi == protocol.DPI else dpi / 25.4)
        for i, entry in enumerate(entries):
            img, rows = label_rows(entry['label'], queue.label_size, queue.offset, queue.model)
            if keep:
                keep(img, f'niimbot_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        """Print each label as a job of its own, the way the script this
        came from prints one, with the label type of the roll."""
        device, model = queue.device_specifier, queue.model
        # A page of several labels is printed as one
        size = next((size for size in LABELS if size.identifier == queue.label_size), None)
        what = 'page' if size and size.pieces[0] * size.pieces[1] > 1 else 'label'
        # It asks the printer itself, and may read the answer owed
        _owed.pop(device, None)
        try:
            with protocol.connect(device, _over_le(model)) as c:
                told = models.BY_ID.get(protocol.model_id(c))
                if told is not None and told.name != model:
                    return models.other_model(told) if told in models.PRINTED else models.cannot_print(told)
                # What the status keeps is updated, e.g. another roll
                beat = protocol.heartbeat(c)
                loaded = protocol.roll(c)
                state = self._keep(device, model, beat, loaded, self._serial(c, device, model))
                if state['errors']:
                    devices.note_answer(device)
                    return state['errors'][0]
                label_type = loaded['type'] if loaded else protocol.WITH_GAPS
                known = models.BY_NAME[model]
                progress.counting(device)
                for i, rows in enumerate(job):
                    protocol.print_label(c, rows, label_type, known.sequence, known.density[2], known.dots, known.exact)
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
                return f'The printer did not tell that it printed the {what}'
            return protocol.reason(e) or f'The printer refused the {what}, the log of the server names the reason'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            # Asked again at the next status query, which then tells why
            devices.note_answer(device, e)
            _asked.pop(device, None)
            return devices.not_sent(e)
        return ''


DRIVER = Niimbot()
