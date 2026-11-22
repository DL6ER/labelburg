"""Phomemo's label printers (protocol.py): the M110, M120, M220 and M421 for
die-cut labels, the first three also for continuous paper, the D30, D35 and
Q30 for small die-cut labels, which they print turned, and the M02, M02 Pro
and T02 for paper. On Bluetooth LE or classic Bluetooth, on USB or a serial
port, as the sources in protocol.py reach each. They have no cutter, the
labels are torn off. The label is sent as an image."""

import logging
import re

from PIL import Image

from .. import bitmap, devices, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from . import protocol

logger = logging.getLogger(__name__)

M02, M02PRO, T02 = 'Phomemo/M02', 'Phomemo/M02Pro', 'Phomemo/T02'
M110, M120, M220, M421 = 'Phomemo/M110', 'Phomemo/M120', 'Phomemo/M220', 'Phomemo/M421'
D30, D35, Q30 = 'Phomemo/D30', 'Phomemo/D35', 'Phomemo/Q30'
MODELS = (M02, M02PRO, T02, M110, M120, M220, M421, D30, D35, Q30)
# The job of each model, see protocol.py
KINDS = {M02: 'm02', M02PRO: 'm02', T02: 'm02', M110: 'm110', M120: 'm110', M220: 'm110', M421: 'm110',
         D30: 'd30', D35: 'd30', Q30: 'q30'}
# Rows as wide as the printhead, the label centered ([PY] printers.json:157-173, the M220A [LB] rastertopm220.py:50-54),
# else as wide as the label. [PY] has the roll of the M110S and an M220 on the right (printers.json:182, 233).
HEADS = {M110: 384, M120: 384, M220: 576}
DPI = 203
# The M02 Pro prints 300 dpi ([PT] cups/drv/phomemo-m02pro.drv:38)
DPI_300 = (M02PRO,)
# Labels drawn lying turn left, their right end first ([PT] rastertopd30.py:94, [PD] print_text.py:59),
# on the Q30 right ([TP] Q30/index.js:56-68)
TURNS = {Q30: -90}

# mm/s, the least Phomemo names for a model of each kind: 30 for the M110 and
# the M220, 15 to 20 for the D30, 10 to 15 for the M02 Pro. The jobs correct it.
SPEEDS = {'m110': 30, 'd30': 15, 'q30': 15, 'm02': 10}

# On Bluetooth LE ([PY] printers.json, ble.js; [OD] index.js:248-254; [TP] Q30/ble.js:30-62)
OVER_LE = (M110, M120, M220, D30, D35, Q30)
# On classic Bluetooth ([PT] README.md:20-34 and 193-224, issues 12 and 28, pull request 34 on a D35, the M421 as
# any printer of its backend; [PD] README.md:22-25)
OVER_CLASSIC = (M02, M02PRO, T02, M110, M120, M220, M421, D30, D35)
# Names as advertised ([PT] README.md:24, cups/backend/phomemo.py:36-41, pull request 34; [PY] printers.json:51-68, 102,
# 170, 238, 306; [KN] example/lib/main.dart:59); M110S, M120 and M220 units with a serial number instead tell none
NAMES = {r'Mr\.in_M02|M02': M02, r'(?i:M02 ?PRO)': M02PRO, r'T02': T02, r'M110': M110, r'M120': M120, r'M220': M220,
         r'D30': D30, r'D35': D35, r'Q30S?': Q30}
# By their USB IDs, the first model of the family, for the search ([PT] cups/backend/phomemo.py:81-84, README.md:52)
USB_IDS = {(0x0493, 0xb002): M02, (0x0493, 0x8760): M110}

# Die-cut labels, width x length in mm, as the CUPS drivers list them: the
# M110 and M120 ([PT] cups/drv/phomemo-m110.drv:11-31), the M220 also 70 x 80
# (lines 32 and 89), the M421 (phomemo-m421.drv:11-42, 4 x 6 in as 102 x 152)
M110_SIZES = ((20, 100), (20, 10), (20, 20), (25, 10), (25, 30), (25, 38), (30, 20), (30, 25), (30, 30), (35, 15),
              (40, 20), (40, 30), (40, 40), (40, 60), (40, 80), (45, 60), (50, 20), (50, 30), (50, 50), (50, 70),
              (50, 80))
M421_SIZES = ((40, 15), (40, 20), (40, 30), (40, 40), (40, 60), (40, 70), (40, 80), (45, 15), (45, 20), (45, 60),
              (45, 80), (50, 15), (50, 20), (50, 30), (50, 50), (50, 70), (50, 80), (60, 40), (60, 60), (60, 80),
              (60, 86), (62, 100), (70, 40), (70, 70), (70, 80), (102, 152))
M_SIZES = {M110: M110_SIZES, M120: M110_SIZES, M220: M110_SIZES + ((70, 80),), M421: M421_SIZES}
# 1 mm is left blank on both sides ([PT] cups/drv/phomemo-m110.drv:39 and
# phomemo-m421.drv:53)
MARGIN = 8
# Continuous paper 50 mm wide, on the M110 and the M220 ([PT] issue 13)
ENDLESS_WIDTH = 50
# Those of the D30, D35 and Q30 ([PY] constants.js:172-182 and 193-195; [PT]
# cups/drv/phomemo-d30.drv:11-12), more on the Q30 ([TP] Q30/index.html:79-84)
D_SIZES = ((12, 12), (12, 22), (12, 30), (12, 40), (14, 22), (14, 30), (15, 30), (15, 40))
Q30_SIZES = ((12, 25), (12, 50), (12, 70))
D_ROUND = (14,)
# The paper of the M02, M02 Pro and T02, of which the CUPS drivers print 48 mm
# ([PT] cups/drv/phomemo-m02_t02.drv:11-35, README.md:462-466)
PAPER_WIDTH = 50


def _dots(mm: int, dpi: int = DPI) -> int:
    return mm * protocol.DOTS_PER_MM if dpi == DPI else round(mm * dpi / 25.4)


def _die_cut(width: int, length: int) -> Label:
    models = tuple(model for model, sizes in M_SIZES.items() if (width, length) in sizes)
    total = (_dots(width), _dots(length))
    return Label(f'phomemo{width}x{length}', (width, length), FormFactor.DIE_CUT, total,
                 (total[0] - 2 * MARGIN, total[1]), 0, models=models)


def _small(width: int, length: int, models: tuple, form: FormFactor = FormFactor.DIE_CUT) -> Label:
    total = (_dots(width), _dots(length))
    identifier = f'phomemo-round{width}' if form == FormFactor.ROUND_DIE_CUT else f'phomemo-d{width}x{length}'
    return Label(identifier, (width, length), form, total, total, 0, models=models)


def _paper(identifier: str, models: tuple, dpi: int = DPI) -> Label:
    total, printable = _dots(PAPER_WIDTH, dpi), _dots(PAPER_WIDTH - 2, dpi)
    return Label(identifier, (PAPER_WIDTH, 0), FormFactor.ENDLESS, (total, 0), (printable, 0), 0, models=models)


# The size each source starts with first, the one the label designer assumes ([PT] cups/drv/phomemo-m421.drv:94,
# phomemo-m110.drv:56, phomemo-d30.drv:25; [TP] Q30/index.html:83)
FIRST = ((102, 152), (40, 30), (12, 50), (12, 40))


def _first(sizes) -> list:
    return sorted(sizes, key=lambda size: (FIRST.index(size) if size in FIRST else len(FIRST), size))


LABELS = (tuple(_die_cut(width, length) for width, length in _first(set().union(*M_SIZES.values())))
          + (_paper(f'phomemo{ENDLESS_WIDTH}', (M110, M120, M220)),)
          + tuple(_small(width, length, (D30, D35, Q30) if (width, length) in D_SIZES else (Q30,))
                  for width, length in _first(D_SIZES + Q30_SIZES))
          + tuple(_small(d, d, (D30, D35, Q30), FormFactor.ROUND_DIE_CUT) for d in D_ROUND)
          + (_paper('phomemo-m02', (M02, T02)), _paper('phomemo-m02pro', (M02PRO,), 300)))


def _name(label: Label) -> str:
    width, length = label.tape_size
    if label.form_factor == FormFactor.ROUND_DIE_CUT:
        return f'{width} mm round die-cut (Phomemo)'
    if label.form_factor == FormFactor.ENDLESS:
        kind = 'endless' if label.identifier == f'phomemo{ENDLESS_WIDTH}' else 'paper'
        return f'{width} mm {kind} (Phomemo)'
    return f'{width} x {length} mm die-cut (Phomemo)'


NAMES_OF_LABELS = {label.identifier: _name(label) for label in LABELS}
_BY_ID = {label.identifier: label for label in LABELS}


def label_rows(label, size: Label, model: str) -> tuple[Image.Image, list[bytes]]:
    """A label as the printer prints it: its image in black and white, and
    rows of packed dots, the first one comes out first."""
    img = label.generate()
    across = size.dots_printable[0]
    if img.width != across:
        img = img.rotate(TURNS.get(model, 90), expand=True)
    img = bitmap.black_and_white(img, label)
    width = HEADS.get(model) or (size.dots_total[0] if KINDS[model] == 'm110' else across)
    placed = Image.new('1', (width, img.height), 1)
    placed.paste(img, ((width - img.width) // 2, 0))
    rows, _ = bitmap.packed_rows(placed)
    return img, rows


def label_job(rows: list[bytes], model: str, size: Label) -> bytes:
    """The bytes of one label of the model."""
    kind = KINDS[model]
    if kind == 'm110':
        media = protocol.CONTINUOUS if size.form_factor == FormFactor.ENDLESS else protocol.GAPS
        return protocol.m110_job(rows, media)
    if kind == 'd30':
        return protocol.d30_job(rows)
    if kind == 'q30':
        return protocol.q30_job(rows)
    return protocol.m02_job(rows)


def over_le(device: str, model: str) -> bool:
    """Whether a printer on bt:// is reached over Bluetooth LE: a model that
    has it, unless the address names an RFCOMM channel and the model also
    takes classic Bluetooth."""
    if not device.startswith('bt://') or model not in OVER_LE:
        return False
    return model not in OVER_CLASSIC or '/' not in device[len('bt://'):]


class Phomemo(Driver):
    vendor = 'Phomemo'
    models = MODELS
    labels = LABELS
    label_names = NAMES_OF_LABELS
    shown_as = {M02PRO: 'Phomemo/M02 Pro'}
    # Serial ports, also Jieli's 4c4a:4155, only where configured: a request could write to any device there, e.g. a
    # 3D printer ([PT] issues 37, 41, 44, 52 and 53)
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),
                       re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x(0493:0x(b002|8760)|0483:0x5740)(/[A-Za-z0-9_-]+)?', re.IGNORECASE))
    errors = (OSError, protocol.PhomemoError)
    reports_tape = False
    # The M02, M02 Pro, T02 and D35 that are a serial port only ([PT] issues
    # 41, 44 and 52, pull request 34)
    usb_serial_ids = ((0x4c4a, 0x4155),)
    bluetooth_names = {re.compile(pattern, re.DOTALL): model for pattern, model in NAMES.items()}

    def model_of(self, device, device_id=None):
        return USB_IDS.get(devices.usb_ids(device)) if isinstance(device, str) else None

    def kept_model(self, model, known):
        # The USB IDs tell the family only
        return model if model in self.models else known

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def dpi(self, high_res):
        return DPI

    def label_dpi(self, label):
        dpi = 300 if label.models == DPI_300 else DPI
        return dpi, dpi

    def label_for(self, label_size, model):
        # The same paper of another model
        size = _BY_ID.get(label_size)
        if size is None or model in size.models:
            return label_size
        return next((label.identifier for label in LABELS if model in label.models and label.tape_size == size.tape_size
                     and label.form_factor == size.form_factor), label_size)

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=model not in TURNS,
                    model_shown=self.shown_as.get(model, model).split('/')[-1])

    def followed(self, device, model):
        # A serial port on USB costs nothing either
        return device.startswith('serial://') or super().followed(device, model)

    def status(self, device, model, unconfirmed=False):
        return self.noted_status(device, lambda: self._status(device, model))

    def _status(self, device, model):
        state = network_status(device, model)
        state['phase_type'] = 'Waiting to receive'
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, state)
            with protocol.connect(device, over_le(device, model)) as c:
                told = protocol.state(c)
        state['errors'] = protocol.errors(told)
        state['status_type'] = devices.status_type(state['errors'])
        battery = protocol.battery(told[protocol.BATTERY]) if protocol.BATTERY in told else None
        if battery:
            state['battery'], state['battery_low'] = battery
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def print_speed(self, model, fast, red):
        return SPEEDS[KINDS[model]], 0

    def label_lengths(self, job):
        return [length for _, length in job]

    def render(self, queue, entries, keep=None):
        size, model = _BY_ID[queue.label_size], queue.model
        dpi = self.label_dpi(size)[0]
        made = []
        for i, entry in enumerate(entries):
            img, rows = label_rows(entry['label'], size, model)
            if keep:
                keep(img, f'phomemo_{i}')
            made.append((label_job(rows, model, size), len(rows) * 25.4 / dpi))
        return made

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            with protocol.connect(device, over_le(device, queue.model)) as c:
                problems = protocol.errors(protocol.state(c))
                if problems:
                    devices.note_answer(device)
                    return problems[0]
                progress.counting(device)
                error = protocol.print_labels(c, [data for data, _ in job], lambda n: progress.printed(device, n))
            devices.note_answer(device)
        except protocol.PhomemoError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            devices.note_answer(device, e)
            return devices.not_sent(e)
        if error:
            logger.warning('Printing on %s failed: %s', device, error)
        return error


DRIVER = Phomemo()
