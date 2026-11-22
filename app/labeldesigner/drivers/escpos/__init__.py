"""Receipt printers on ESC/POS (protocol.py), e.g. an Epson TM-T20II, on
endless 58 or 80 mm paper, which they cut. ESC/POS is the same on any of
them, so it is one model for each resolution of their heads, not one per
printer. The label is sent as an image."""

import logging
import re

from PIL import Image

from .. import bitmap, devices, progress
from ..base import Driver, Told, network_status, shown
from ..labels import FormFactor, Label
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('Epson/ESC-POS', 'Epson/ESC-POS-180')
# Most heads print 203 dpi, Epson's TM-T88, TM-T90, TM-T70, TM-T81 and
# TM-T83(II) 180 dpi, by the model in the device ID; a TM-T81III prints 203
HEADS_180 = re.compile(r'TM-T(88|90|70)|TM-T81$|TM-T83(II)?$')
# Epson's own models, whose head HEADS_180 tells
EPSON_TM = re.compile(r'TM-')
# ESC/POS in the command sets of a device ID, also as other makers spell it
COMMAND_SET = re.compile(r'ESC/?POS|ESC/POS-.*', re.IGNORECASE)
# Epson's impact printers, e.g. the TM-U220, print no raster images
IMPACT = re.compile(r'TM-U\d', re.IGNORECASE)
DPI = {MODELS[0]: 203, MODELS[1]: 180}
# Paper width in mm and the dots across it, the first one assumed, as the
# printer does not tell. 384 on 58 mm paper as most printers print, a
# TM-T20II would print 420.
WIDTHS = {MODELS[0]: {80: 576, 58: 384}, MODELS[1]: {80: 512, 58: 360}}

LABELS = tuple(Label(f'escpos{width}' + ('-180' if model == MODELS[1] else ''), (width, 0), FormFactor.ENDLESS,
                     (dots, 0), (dots, 0), 0, models=(model,))
               for model, widths in WIDTHS.items() for width, dots in widths.items())
# Paper fed to the cutter after each label that is cut, blank before the
# next one: on a TM-T20II a 15 mm label took 27 mm of paper, two of them
# not cut between them 42 mm. Other printers have their head nearer to or
# further from the cutter.
LEAD_MM = 12
# mm/s after Epson's data sheet of the TM-T20II, and the seconds a label
# takes besides, to feed and cut it
SPEED = 200
BESIDES = 0.5
# Below the cutter, inside the printer
NEVER_WHY = 'The paper would stay inside the printer until it is cut'
NAMES = {label.identifier: f'{label.tape_size[0]} mm receipt paper' for label in LABELS}
# Epson's names on classic Bluetooth after the Technical Reference Guides (https://files.support.epson.com/pdf/pos/bulk/):
# model, "_" ("-" too on a TM-P20II or TM-P80II) and the last 6 digits of the serial number, "-L" added on LE
BLUETOOTH_NAMES = {
    **{rf'{model}_[0-9]{{6}}': MODELS[0] for model in ('TM-P20', 'TM-P60II', 'TM-P80', 'TM-m10', 'TM-m30', 'TM-m30II', 'TM-m50')},
    **{rf'{model}[_-][0-9]{{6}}(-L)?': MODELS[0] for model in ('TM-P20II', 'TM-P80II')},
    r'TM-m30III_[0-9]{6}(-L)?': MODELS[0],
}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    paper, the first one comes out first, and the bytes of each row."""
    img = label.generate()
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    rows, width = bitmap.packed_rows(img)
    return img, rows, width


def _answer_key(device: str) -> str:
    """The key protocol.get_errors() notes a printer by: its lock key and its
    device ID, so that another printer at the same address, e.g. a clone
    plugged in at /dev/usb/lp0 where an Epson was, is asked afresh."""
    return f'{devices.lock_key(device)} {devices.known_device_id(device)}'.rstrip()


def _problems(c, key: str) -> list[str] | None:
    """What the printer tells is wrong, None if it never answered DLE EOT,
    e.g. a clone without it: it is told as ready and printed on, as a ZPL
    printer that never answered ~HS. EscposError if it answered before."""
    try:
        return protocol.get_errors(c, key)
    except protocol.EscposError:
        if protocol.never_answered(key):
            return None
        raise


class Escpos(Driver):
    vendor = 'Epson'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # Both models are shown alike
    shown_as = {MODELS[1]: MODELS[0]}
    # usblp's device, also a name a udev rule gives it, e.g. /dev/usb/lp_epson,
    # on Linux only, the printer through PyUSB, e.g. usb://0x04b8:0x0e15, on
    # the network on the port of raw printing, as the other drivers, or on
    # classic Bluetooth, bt://<MAC>[/<RFCOMM channel>]
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
                       re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
                       re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'),
                       re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'))
    errors = (OSError, protocol.EscposError)
    reports_tape = False
    # Its models are those of many makers
    names_devices = True
    bluetooth_names = BLUETOOTH_NAMES
    bluetooth_kind = 'classic'

    def model_of(self, device, device_id=None):
        # By the device ID on USB, e.g. MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;...
        if not isinstance(device, str):
            return None
        return self.model_in(devices.usb_device_id(device) if device_id is None else device_id)

    def model_in(self, device_id):
        told = self.told(device_id)
        return told.model if told else None

    def told(self, device_id):
        fields = devices.device_id_fields(device_id)
        if not any(COMMAND_SET.fullmatch(command.strip()) for command in fields.get('CMD', '').split(',')):
            return None
        model = fields.get('MDL', '')
        if IMPACT.match(model):
            return None
        return Told(MODELS[1] if HEADS_180.match(model) else MODELS[0], resolution=bool(EPSON_TM.match(model)))

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # escpos80 and escpos80-180 are the same paper
        paper = next((label.tape_size for label in LABELS if label.identifier == label_size), None)
        return next((label.identifier for label in LABELS if label.tape_size == paper and model in label.models), label_size)

    def dpi(self, high_res):
        return DPI[MODELS[0]]

    def label_dpi(self, label):
        return DPI[label.models[0]], DPI[label.models[0]]

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def cut_problem(self, cut, model):
        return f'The {shown(model)} printer cuts the paper at least after the last label' if cut == 'none' else ''

    def capabilities(self, model):
        return dict(super().capabilities(model), qualities=['quality'], never_cut=NEVER_WHY,
                    # The top of the label leaves it first
                    leaves_turned=True, lead_mm=LEAD_MM,
                    # The model as shown, without its maker
                    model_shown=self.shown_as.get(model, model).split('/')[-1])

    def feed_kind(self, model, label_size):
        return 'cut_paper'

    def feed_job(self, queue):
        # A label of no rows: only the cut after it, which feeds the paper to the cutter
        return [(True, [], 0, DPI[queue.model])]

    def status(self, device, model, unconfirmed=False):
        if device.startswith('bt://'):
            # Paging it takes the radio: one switched off is not paged at every
            # status query, any other one at most every 30 s
            return self.bluetooth_status(device, lambda: self.noted_status(device, lambda: self._status(device, model)))
        return self._status(device, model)

    def _status(self, device, model):
        state = network_status(device, model)
        state.update({'media_type': 'Receipt paper', 'phase_type': 'Waiting to receive'})
        if name := self.device_name(device):
            state['device_name'] = name
        key = devices.lock_key(device)
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(key, {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            with protocol.connect(device) as c:
                errors = _problems(c, _answer_key(device))
        if errors is None:
            return state
        state['errors'] = errors
        state['status_type'] = 'Error occurred' if errors else 'Reply to status request'
        devices.status_cache[key] = state
        return state

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        return [len(rows) / dpi * 25.4 for _, rows, _, dpi in job]

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'escpos_{i}')
            # The dpi of the paper, for the length of the label
            job.append((entry['cut'], rows, width, DPI[next(label.models[0] for label in LABELS if label.identifier == queue.label_size)]))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        self.ask_bluetooth_again(device)
        try:
            with protocol.connect(device) as c:
                problems = _problems(c, _answer_key(device))
                if problems:
                    return problems[0]
                c.write(protocol.job(job))
        except protocol.EscposError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        # Asked again at the next status query, see noted_status()
        devices.note_answer(device)
        return ''


DRIVER = Escpos()
