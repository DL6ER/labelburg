"""DYMO's LabelManager tape printers (protocol.py): the LabelManager PnP, 280,
420P and PC II, on USB, as labelle (https://github.com/labelle-org/labelle,
commit 5e8f1e5d, Apache-2.0) prints on them. D1 tape of 6 to 24 mm, on as
many pins of the head as the tape takes, the label sent as an image, row by
row, then fed past the cutter, which is worked by hand. The printer tells
whether a cassette is in, not which.

The pins each tape takes are those labelle's issues measured on each model
(https://github.com/labelle-org/labelle/issues/51, 62 and 69), on the PnP
those thermal-label's d1-core (https://github.com/thermal-label/d1-core,
commit 16126795, MIT) prints on. The models whose head or tapes no source
measured are left out: the LabelManager PC, the Wireless PnP, the 500TS, the
LabelPoint 350 and the MobileLabeler."""

import logging
import re

from PIL import Image

from ...enums import LabelOrientation
from .. import bitmap, devices, progress, usbip
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('DYMO/LabelManager-PnP', 'DYMO/LabelManager-280', 'DYMO/LabelManager-420P', 'DYMO/LabelManager-PC-II')
PNP, LM280, LM420P, PC2 = MODELS
MODEL_NAMES = {PNP: 'LabelManager PnP', LM280: 'LabelManager 280', LM420P: 'LabelManager 420P', PC2: 'LabelManager PC II'}
# 180 dpi (labelle, constants.py; LPrint, lprint-dymo.c), a head of 64 pins
# on the PnP (labelle, doc/margin-calibration-howto.md) and the 280 (issue
# #69), of 128 on the 420P (issue #62) and the PC II (issue #51; 18.1 mm in
# DYMO's "DYMO Label Software User Guide" 7.7, p. 44)
DPI = 180
HEAD_DOTS = {PNP: 64, LM280: 64, LM420P: 128, PC2: 128}
# mm/s after DYMO's product sheets of the PnP, the 280 and the 420P and the
# user guide of DYMO Label 7.7 for the PC II
SPEED = {PNP: 12, LM280: 15, LM420P: 12, PC2: 10}
# The tape between head and cutter (labelle, dymo_labeler.py). Each label is
# followed by twice that: the cut leaves as much blank after it as the
# next one has before it (margins.py, MarginsRenderEngine).
HEAD_TO_CUTTER_MM = 8.1
FEED = round(2 * HEAD_TO_CUTTER_MM * DPI / 25.4)
MEDIA_TYPE = 'Tape'


def _tape(identifier: str, width: int, models: tuple, first: int, last: int) -> Label:
    """D1 tape of width mm that the models print on the pins first to last
    of their head, counted from the one the first bit of a row goes to as
    labelle counts them. offset_r is the pins before the first."""
    return Label(identifier, (width, 0), FormFactor.ENDLESS, (HEAD_DOTS[models[0]], 0), (last - first + 1, 0), first - 1,
                 models=models)


# 12 mm first, the tape a printer is assumed to have. The PnP and the 280
# take 6 to 12 mm, the 420P also 19 mm (DYMO's product sheets), the PC II up
# to 24 mm (DYMO Label 7.7), its 6 and 19 mm tapes were not measured.
LABELS = (
    # On a head of 64 pins: 64, 48 and 32 dots of 12, 9 and 6 mm tape,
    # centered (thermal-label, data/media.json5 and the dot tab of
    # protocol.ts, printed on a PnP); a PnP prints all 64 on 12 mm tape
    # (labelle, margin-calibration-howto.md, and issue #69)
    _tape('d1-12', 12, (PNP,), 1, 64),
    # The 280 prints fewer: pins 4 to 61 of 12 mm tape, 18 to 47 of 6 mm
    # tape set on the printer (issue #69)
    _tape('d1-12-280', 12, (LM280,), 4, 61),
    # Pins 28 to 105 and 32 to 106 of 12 mm tape, 1 and 3 to 128 of 19 mm in
    # the two readings of issue #62: the pins both print
    _tape('d1-12-420p', 12, (LM420P,), 32, 105),
    # 23 pins below and above 12 mm tape, 27 below and 37 above 9 mm tape,
    # all 128 on 24 mm (issue #51)
    _tape('d1-12-pc2', 12, (PC2,), 24, 105),
    _tape('d1-6', 6, (PNP,), 17, 48),
    _tape('d1-6-280', 6, (LM280,), 18, 47),
    _tape('d1-6-420p', 6, (LM420P,), 44, 86),
    # Not measured on a 280
    _tape('d1-9', 9, (PNP, LM280), 9, 56),
    _tape('d1-9-420p', 9, (LM420P,), 35, 98),
    _tape('d1-9-pc2', 9, (PC2,), 28, 91),
    _tape('d1-19-420p', 19, (LM420P,), 3, 128),
    _tape('d1-24-pc2', 24, (PC2,), 1, 128),
)
BY_ID = {label.identifier: label for label in LABELS}
NAMES = {label.identifier: f'{label.tape_size[0]} mm tape (DYMO D1)' for label in LABELS}

# The models by the model the device ID names, MFG:DYMO, as LPrint lists
# them in lprint-dymo.h, compared in any case and spacing
NAMED = {'labelmanager pnp': PNP, 'labelmanager pc ii': PC2}
# Without one by DYMO's product IDs, each as labelle lists it switched to
# printing and before (constants.py, SUPPORTED_PRODUCTS)
VENDOR_ID = 0x0922
PRODUCT_IDS = {0x1001: PNP, 0x1002: PNP, 0x1005: LM280, 0x1006: LM280, 0x1003: LM420P, 0x1004: LM420P, 0x001c: PC2}


def _plain(text: str) -> str:
    return ' '.join(text.split()).casefold()


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: its rows across the pins of the
    tape, the first one comes out first, packed after the pins the dot tab
    skips, and the dot tab in bytes."""
    size = BY_ID[label_size]
    img = label.generate()
    # Standard labels come out top first, rotated ones with the end of
    # their text, as on QL printers
    if label.label_orientation == LabelOrientation.ROTATED:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    dot_tab, skipped = divmod(size.offset_r, 8)
    canvas = Image.new('1', ((skipped + img.width + 7) // 8 * 8, img.height), 1)
    canvas.paste(img, (skipped, 0))
    rows, _ = bitmap.packed_rows(canvas)
    return img, rows, dot_tab


def _answer_key(device: str) -> str:
    """The key protocol.status() notes a printer by: its lock key and its
    device ID, another printer plugged in at the same address is asked
    afresh."""
    return f'{devices.lock_key(device)} {devices.known_device_id(device)}'.rstrip()


def _problems(c, device: str, fresh: bool = False) -> list[str] | None:
    """What the printer tells is wrong, None if it never answered: it is
    printed on, as LPrint prints without asking. NoAnswer if it answered
    before."""
    key = _answer_key(device)
    try:
        return protocol.errors(protocol.status(c, key, fresh))
    except protocol.NoAnswer:
        if protocol.never_answered(key):
            return None
        raise


class LabelManager(Driver):
    vendor = 'DYMO'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    names = {model: f'DYMO {name}' for model, name in MODEL_NAMES.items()}
    # usblp's device, also a name a udev rule gives it, or the printer
    # through PyUSB
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'))
    refusals = protocol.TEXTS
    reports_tape = False
    takes_usbip = False
    usb_ids = frozenset((VENDOR_ID, product) for product in PRODUCT_IDS)

    def model_of(self, device, device_id=None):
        if not isinstance(device, str) or usbip.is_address(device):
            return None
        device_id = devices.usb_device_id(device) if device_id is None else device_id
        fields = devices.device_id_fields(device_id)
        if device_id and _plain(fields.get('MFG', '')) != 'dymo':
            return None
        if model := self.model_in(device_id):
            return model
        # One its device ID does not name by its product ID, never a LabelWriter
        if _plain(fields.get('MDL', '')).startswith('labelwriter'):
            return None
        ids = devices.usb_ids(device)
        return PRODUCT_IDS.get(ids[1]) if ids and ids[0] == VENDOR_ID else None

    def model_in(self, device_id):
        # E.g. MFG:DYMO;MDL:LabelMANAGER PNP ;, a LabelManager takes its
        # tape commands only, its model tells them as a command set would
        fields = devices.device_id_fields(device_id)
        return NAMED.get(_plain(fields.get('MDL', ''))) if _plain(fields.get('MFG', '')) == 'dymo' else None

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # The tape of the same width, e.g. d1-12-280 for d1-12 on a 280
        paper = BY_ID[label_size].tape_size if label_size in BY_ID else None
        return next((label.identifier for label in LABELS if label.tape_size == paper and model in label.models), label_size)

    def dpi(self, high_res):
        return DPI

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # Cut by hand, the top of a label leaves it first, after the tape
        # left between head and cutter
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, manual_cutter=True,
                    leaves_turned=True, lead_mm=HEAD_TO_CUTTER_MM, model_shown=MODEL_NAMES[model])

    def status(self, device, model, unconfirmed=False):
        state = dict(network_status(device, model), media_type=MEDIA_TYPE, phase_type='Waiting to receive')
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, state)
            try:
                with protocol.connect(device, self.usb_ids) as c:
                    errors = _problems(c, device)
            except protocol.UsbDrive:
                errors = [protocol.USB_DRIVE_TEXT]
        if errors is None:
            return state
        state['errors'] = errors
        state['status_type'] = devices.status_type(errors)
        devices.status_cache[devices.lock_key(device)] = state
        return dict(state)

    def print_speed(self, model, fast, red):
        # The tape fed after each label
        return SPEED[model], 2 * HEAD_TO_CUTTER_MM / SPEED[model]

    def label_lengths(self, job):
        return [len(rows) * 25.4 / DPI for rows, _ in job]

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows, dot_tab = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'labelmanager_{i}')
            job.append((rows, dot_tab))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            with protocol.connect(device, self.usb_ids) as c:
                # A row a job broken off left unfinished takes them, ESC A
                # is read as a command again
                c.write(protocol.SYNC)
                problems = _problems(c, device, fresh=True)
                if problems:
                    return problems[0]
                for piece in protocol.pieces([protocol.label(rows, dot_tab, FEED) for rows, dot_tab in job]):
                    c.write(piece)
                    # One that never answered is not waited for
                    if problems is None:
                        continue
                    if found := protocol.errors(protocol.printed(c)):
                        return found[0]
                if problems is None:
                    # The end of a job (thermal-label, buildPrinterStream())
                    c.write(protocol.STATUS)
        except protocol.UsbDrive:
            return protocol.USB_DRIVE_TEXT
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        return ''


DRIVER = LabelManager()
