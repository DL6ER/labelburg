"""Label printers on ZPL (protocol.py), e.g. a Zebra GK420t or a
Datamax-O'Neil E-4204B with its ZPL emulation, tested on both. Die-cut
labels, which the printer finds by their gaps or by black marks on their
back, or a continuous roll, as set for it, torn off. The label is sent
as an image. The printer is asked for its status with ~HS: its errors and
the length of the labels it measured, which picks their size where labels
of a single width have that length."""

import logging
import os
import re
import time

from PIL import Image

from .. import bitmap, devices, diecut, progress
from ..base import Driver, network_status, shown
from ..diecut import SIZES, loaded, measured
from . import protocol

logger = logging.getLogger(__name__)

# Printers of 203 dpi and of 300 dpi
MODELS = ('Zebra/ZPL', 'Zebra/ZPL-300')
DPI = {MODELS[0]: 203, MODELS[1]: 300}
# A printhead of 104 mm, as on a GK420t, a 105 mm label loses 1 mm
HEAD_MM = 104
# mm/s, 4 in/s, the most a Datamax-O'Neil E-4204B prints and less than the
# 5 in/s of a GK420t, and the seconds a label takes besides, to take its job
# and find the gap to the next label
SPEED = 102
BESIDES = 1.0


# A Datamax-O'Neil E-4204B leaves ~HS unanswered within about 5 s of the one
# before: a printer is asked at most every ASK_EVERY seconds, told as it was
# in between
ASK_EVERY = 10
# How long a ~HS left unanswered counts as outstanding, meanwhile no other
# is sent
ASK_AGAIN = 20
# When each printer may be asked again, by devices.lock_key()
_ask_after: dict[str, float] = {}
NO_ANSWER = 'The printer does not answer, is its printhead open?'


def _asked_lately(key: str) -> bool:
    # The monotonic clock starts at boot, a printer never asked was not asked lately
    return time.monotonic() < _ask_after.get(key, 0)


# The command set ZPL in a device ID
ZPL_DEVICE_ID = re.compile(r'(^|;)\s*(CMD|COMMAND SET):[^;]*\bZPL', re.IGNORECASE)
# The resolution of a model, e.g. MDL:ZTC ZD611-300dpi ZPL, and the models
# of each, 200 as some name 203 dpi. A printer of another one, e.g. 600 dpi,
# is no printer of this driver until configured.
RESOLUTION = re.compile(r'(\d+)\s*dpi', re.IGNORECASE)
OF_RESOLUTION = {200: MODELS[0], 203: MODELS[0], 300: MODELS[1]}
# Printers on ZPL by the maker and model of their device ID, where it names
# no ZPL or no resolution. The device IDs are those LPrint lists in
# lprint-zpl.h (https://github.com/michaelrsweet/lprint, Apache-2.0).
KNOWN_IDS = {
    # Arkscan 2054A, ZPL as CEZD, 203 dpi
    ('4BARCODE', '4B-2054A'): MODELS[0],
    # HP KE103, 300 dpi
    ('HP', 'KE103'): MODELS[1],
    # The GX430 prints 300 dpi, LPrint has it at 203: Zebra's GX series spec sheet,
    # https://www.zebra.com/content/dam/external-dam/knowledge-base/cpws/docs/gseries/gx_specs.pdf
    ('Zebra Technologies', 'ZTC GX430d'): MODELS[1],
    ('Zebra Technologies', 'ZTC GX430t'): MODELS[1],
}


def _plain(text: str) -> str:
    """A field of a device ID compared in any case and spacing."""
    return ' '.join(text.split()).casefold()


_KNOWN = {(_plain(maker), _plain(model)): known for (maker, model), known in KNOWN_IDS.items()}


# The names of the 203 dpi sizes are those from before there were two
LABELS = tuple(label for model, prefix in zip(MODELS, ('zpl', 'zpl300-'))
               for label in diecut.labels(prefix, DPI[model], diecut.dots(HEAD_MM, DPI[model]), (model,), SIZES))
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (ZPL)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the last one comes out first, and the bytes of each row."""
    # The bottom of a ZPL label leaves the printer first, on a Zebra GK420t as
    # on a Datamax-O'Neil E-4204B
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    img = diecut.oriented(label.generate(), across, top_first=False)
    img = bitmap.black_and_white(img, label)
    rows, width = bitmap.packed_rows(img)
    return img, rows, width


class Zpl(Driver):
    vendor = 'Zebra'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # Both models are shown alike
    shown_as = {MODELS[1]: MODELS[0]}
    # Its models are those of many makers
    names_devices = True
    # usblp's device, also a name a udev rule gives it, e.g.
    # /dev/usb/lp_datamax, or the printer on the network on the port of raw
    # printing only, as the other drivers
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
                       re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
                       re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'))
    # Its jobs go to usblp, PyUSB or the network only
    takes_usbip = False

    def model_of(self, device, device_id=None):
        # By the IEEE 1284 device ID on USB, e.g. MFG:Zebra Technologies;
        # CMD:ZPL;MDL:GK420t;... A Datamax-O'Neil tells CMD:DPL, its ZPL
        # emulation is configured.
        if not isinstance(device, str):
            return None
        return self.model_in(devices.usb_device_id(device) if device_id is None else device_id)

    def model_in(self, device_id):
        fields = devices.device_id_fields(device_id)
        known = _KNOWN.get((_plain(fields.get('MFG', '')), _plain(fields.get('MDL', ''))))
        if known or not ZPL_DEVICE_ID.search(device_id):
            return known
        resolution = RESOLUTION.search(fields.get('MDL', ''))
        return OF_RESOLUTION.get(int(resolution[1])) if resolution else MODELS[0]

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # zpl102x152 and zpl300-102x152 are the same labels
        paper = next((label.tape_size for label in LABELS if label.identifier == label_size), None)
        return next((label.identifier for label in LABELS if label.tape_size == paper and model in label.models), label_size)

    def dpi(self, high_res):
        return DPI[MODELS[0]]

    def label_dpi(self, label):
        return DPI[label.models[0]], DPI[label.models[0]]

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True,
                    model_shown=self.shown_as.get(model, model).split('/')[-1], sensing=True)

    def device_name(self, device):
        # Zebra's models start with ZTC, Zebra Technologies Corporation
        return re.sub(r'\bZTC\s+', '', super().device_name(device), count=1, flags=re.IGNORECASE)

    def status(self, device, model, unconfirmed=False):
        path = device.removeprefix('file://')
        if not device.startswith(('tcp://', 'usb://')) and not os.path.exists(path):
            raise FileNotFoundError(2, 'No such device', path)
        state = dict(network_status(device, model), media_type='Die-cut labels')
        if name := self.device_name(device):
            state['device_name'] = name
        key = devices.lock_key(device)
        cached = devices.status_cache.get(key)
        if _asked_lately(key):
            return dict(cached) if cached else state
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            # A query that waited for the printer takes the answer of the one before
            cached = devices.status_cache.get(key)
            if _asked_lately(key):
                return dict(cached) if cached else state
            answer = protocol.parse_host_status(protocol.host_status(device))
            _ask_after[key] = time.monotonic() + (ASK_EVERY if answer is not None else ASK_AGAIN)
        if answer is None:
            # A printer that never answered ~HS is told as before, without
            # errors. A Datamax-O'Neil E-4204B with its printhead open does
            # not answer.
            if cached:
                state.update(cached)
                state.update({'errors': [NO_ANSWER], 'status_type': 'Error occurred'})
                devices.status_cache[devices.lock_key(device)] = state
            return state
        state['errors'] = protocol.errors(answer)
        state['status_type'] = devices.status_type(state['errors'])
        state['phase_type'] = 'Waiting to receive'
        # The label designer then picks the labels. Of a length that labels
        # of several widths have, only the length is told.
        dpi = DPI.get(model, DPI[MODELS[0]])
        size = loaded(answer['length'], dpi)
        if size:
            state['media_width'], state['media_length'] = size
        elif length := measured(answer['length'], dpi):
            state['media_length'] = length
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        # Each label of the job tells its rows in ^LL
        return [diecut.millimeters(int(label.split(b'^LL', 1)[1].split(b'\n', 1)[0]), dpi) for label, dpi in job]

    def render(self, queue, entries, keep=None):
        job = []
        # The resolution of the labels, for their length
        dpi = DPI[next(label.models[0] for label in LABELS if label.identifier == queue.label_size)]
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'zpl_{i}')
            job.append((protocol.label(rows, width, queue.sensing), dpi))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            protocol.write(device, b''.join(label for label, _ in job))
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        return ''


DRIVER = Zpl()
