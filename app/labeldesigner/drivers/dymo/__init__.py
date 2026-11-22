"""DYMO's LabelWriter 450 series (protocol.py): the 450, 450 Turbo, 450 Twin
Turbo, the label side of the 450 Duo, the 4XL and the SE450, on USB. Die-cut
DYMO labels, which the printer finds by the holes between them, torn off.
The label is sent as an image, row by row. The printer tells no labels, only
whether it has any and whether they are jammed.

The values are those of DYMO's technical references of the 450 series and
the SE450 and of its product sheets of the 450 Turbo and the 4XL, LPrint's
DYMO driver (https://github.com/michaelrsweet/lprint, lprint-dymo.c and
lprint-dymo.h of commit 54f1c462, Apache-2.0) and
https://thermal-label.github.io/labelwriter (MIT) where named."""

import logging
import re

from PIL import Image

from .. import bitmap, devices, diecut, progress, usbip
from ..base import Driver, network_status, shown
from . import protocol

logger = logging.getLogger(__name__)

# The 450 is also the 450 Turbo and the label side of the 450 Duo
MODELS = ('DYMO/LabelWriter-450', 'DYMO/LabelWriter-450-Twin-Turbo', 'DYMO/LabelWriter-4XL', 'DYMO/LabelWriter-SE450')
LW450, TWIN_TURBO, XL, SE450 = MODELS
# The models as texts and the label designer name them
MODEL_NAMES = {LW450: 'LabelWriter 450', TWIN_TURBO: 'LabelWriter 450 Twin Turbo', XL: 'LabelWriter 4XL',
               SE450: 'LabelWriter SE450'}
# 300 dpi with 672 dots across, of 57 mm (the reference, p. 7); the 4XL 1248
# dots, 4.16 in (4XL product sheet; thermal-label, LW_4XL); the SE450 203 dpi
# with 448 dots (SE450 reference, SYN)
DPI = {LW450: 300, TWIN_TURBO: 300, XL: 300, SE450: 203}
HEAD_DOTS = {LW450: 672, TWIN_TURBO: 672, XL: 1248, SE450: 448}

# DYMO's labels of the product sheets, width x length in mm, as DYMO sells
# them: 30252 address labels of 1 1/8 x 3 1/2 in, 30321 of 1.4 x 3 1/2 in,
# 30330 return address 3/4 x 2 in, 30323 shipping 2 1/8 x 4 in, 30256
# shipping 2 5/16 x 4 in, 30857 name badges 2 1/4 x 4 in, 30336
# multi-purpose 1 x 2 1/8 in and 30327 file folder labels 9/16 x 3 7/16 in.
# The same sizes are in LPrint's list.
KINDS = {(28, 89): 'address', (36, 89): 'large address', (19, 51): 'return address', (54, 101): 'shipping',
         (59, 102): 'shipping', (57, 102): 'name badge', (25, 54): 'multi-purpose', (14, 87): 'file folder'}
# 1744907 shipping labels of 4 x 6 in, of the 4XL only
WIDE = {(102, 152): 'shipping'}

# Labels as wide as the head of the 450 on every model of 300 dpi, the 4XL
# prints them alike; DYMO's labels start at the first dot of the head, as
# LPrint and CUPS send them
LABELS = (diecut.labels('dymo', DPI[LW450], HEAD_DOTS[LW450], (LW450, TWIN_TURBO, XL), tuple(KINDS))
          + diecut.labels('dymo', DPI[XL], HEAD_DOTS[XL], (XL,), tuple(WIDE))
          + diecut.labels('dymo203-', DPI[SE450], HEAD_DOTS[SE450], (SE450,), tuple(KINDS)))
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm {({**KINDS, **WIDE})[label.tape_size]} (DYMO)'
         for label in LABELS}

# Labels per minute of 4-line address labels, 89 mm long: 51 on the 450, 71
# on the 450 Turbo and Twin Turbo (450 Turbo product sheet), 53 on the 4XL
# (its product sheet), 48 on the SE450 (SE450 reference, specifications)
PER_MINUTE = {LW450: 51, TWIN_TURBO: 71, XL: 53, SE450: 48}
ADDRESS_MM = 89

# The models by the model the device ID names, MFG:DYMO, as in LPrint's
# lprint-dymo.h, compared in any case and spacing
NAMED = {'labelwriter 450': LW450, 'labelwriter 450 turbo': LW450, 'labelwriter 450 duo label': LW450,
         'labelwriter 450 twin turbo': TWIN_TURBO, 'labelwriter 4xl': XL, 'labelwriter se450': SE450}
# Of a printer that tells no device ID by the USB product ID of DYMO (0922):
# the 450, Turbo and Twin Turbo (the reference, p. 14), the SE450 (SE450
# reference, USB settings), 0020 and 0400 also in usb.ids, and the 4XL
# (thermal-label, hardware). The tape side of the Duo has its product ID,
# 0023, as well: the Duo is known by its device ID only.
VENDOR_ID = 0x0922
PRODUCT_IDS = {0x0020: LW450, 0x0021: LW450, 0x0022: TWIN_TURBO, 0x001f: XL, 0x0400: SE450}


def _plain(text: str) -> str:
    return ' '.join(text.split()).casefold()


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the first one comes out first, and the bytes of each row."""
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    img = diecut.oriented(label.generate(), across, top_first=True)
    img = bitmap.black_and_white(img, label)
    rows, width = bitmap.packed_rows(img)
    return img, rows, width


def _answer_key(device: str) -> str:
    """The key protocol.status() notes a printer by: its lock key and its
    device ID, another printer plugged in at the same address is asked
    afresh."""
    return f'{devices.lock_key(device)} {devices.known_device_id(device)}'.rstrip()


def _problems(c, device: str, model: str, roll: str | None = None) -> list[str] | None:
    """What the printer tells is wrong, of the roll given of a Twin Turbo,
    None if it never answered: it is printed on as a ZPL printer that never
    answered. NoAnswer if it answered before."""
    key = _answer_key(device)
    try:
        return protocol.errors(protocol.status(c, key, HEAD_DOTS[model] // 8, roll), jam=model != SE450)
    except protocol.NoAnswer:
        if protocol.never_answered(key):
            return None
        raise


class Dymo(Driver):
    vendor = 'DYMO'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # The SE450 is its 203 dpi model: its labels are those of the 450, the
    # lists of models and of labels show those of the 450 for it
    shown_as = {SE450: LW450}
    names = {model: f'DYMO {name}' for model, name in MODEL_NAMES.items()}
    # usblp's device, also a name a udev rule gives it, or the printer
    # through PyUSB
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'))
    reports_tape = False
    takes_usbip = False
    # Its status names the printer, e.g. a 450 Turbo
    names_devices = True

    def model_of(self, device, device_id=None):
        if not isinstance(device, str) or usbip.is_address(device):
            return None
        device_id = devices.usb_device_id(device) if device_id is None else device_id
        if device_id:
            return self.model_in(device_id)
        ids = devices.usb_ids(device)
        return PRODUCT_IDS.get(ids[1]) if ids and ids[0] == VENDOR_ID else None

    def model_in(self, device_id):
        # E.g. MFG:DYMO;MDL:LabelWriter 450 Turbo;, not a 550 nor the tape
        # side of a Duo. A LabelWriter takes its raster commands only, its
        # model tells them as a command set would, see Told.
        fields = devices.device_id_fields(device_id)
        return NAMED.get(_plain(fields.get('MDL', ''))) if _plain(fields.get('MFG', '')) == 'dymo' else None

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # dymo28x89 and dymo203-28x89 are the same labels, as those of other
        # drivers of the same size, e.g. zpl102x152 and dymo102x152
        return diecut.label_for(label_size, LABELS, model)

    def dpi(self, high_res):
        return DPI[LW450]

    def label_dpi(self, label):
        return DPI[label.models[0]], DPI[label.models[0]]

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # Torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True,
                    model_shown=MODEL_NAMES[model], two_rolls=model == TWIN_TURBO)

    def status(self, device, model, unconfirmed=False):
        state = dict(network_status(device, model), media_type=diecut.MEDIA_TYPE, phase_type='Waiting to receive')
        if name := self.device_name(device):
            state['device_name'] = name
        key = devices.lock_key(device)
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(key, {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            with protocol.connect(device) as c:
                errors = _problems(c, device, model)
        if errors is None:
            return state
        state['errors'] = errors
        state['status_type'] = devices.status_type(errors)
        devices.status_cache[key] = state
        return dict(state)

    def print_speed(self, model, fast, red):
        return PER_MINUTE.get(model, PER_MINUTE[LW450]) * ADDRESS_MM / 60, 0

    def label_lengths(self, job):
        return [diecut.millimeters(len(rows), dpi) for rows, _, dpi in job]

    def render(self, queue, entries, keep=None):
        job = []
        # The resolution of the labels, for their length
        dpi = DPI[next(label.models[0] for label in LABELS if label.identifier == queue.label_size)]
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'dymo_{i}')
            job.append((rows, width, dpi))
        return job

    def send(self, queue, job) -> str:
        device, model = queue.device_specifier, queue.model
        # A Twin Turbo prints on the left roll unless the right one is chosen,
        # as CUPS and LPrint send it, and is asked for the status of that one
        roll = (queue.side or 'left') if model == TWIN_TURBO else None
        try:
            with protocol.connect(device) as c:
                problems = _problems(c, device, model, roll)
                if problems:
                    return problems[0]
                # The SE450 takes no short form feed (SE450 reference, raster mode)
                c.write(protocol.job([(rows, width) for rows, width, _ in job], HEAD_DOTS[model] // 8,
                                     short_feed=model != SE450, roll=roll))
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        return ''


DRIVER = Dymo()
