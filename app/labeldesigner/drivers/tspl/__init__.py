"""Label printers on TSPL (protocol.py), the language of TSC printers and of
most cheap 4 x 6 in shipping label printers. Die-cut labels, which the
printer finds by their gaps or by black marks on their back, or a continuous
roll, as set for it, torn off. The label is sent as an image. The printer
tells its errors at any time, not the labels it has."""

import logging
import os
import re
import time

from PIL import Image

from .. import bitmap, devices, diecut, progress
from ..base import Driver, Told, network_status, shown
from ..diecut import SIZES
from . import protocol

logger = logging.getLogger(__name__)

# Printers of 203 dpi and of 300 dpi
MODELS = ('TSC/TSPL', 'TSC/TSPL-300')
DPI = {MODELS[0]: 203, MODELS[1]: 300}
# A printhead of 104 mm as for ZPL printers, a 105 mm label loses 1 mm
HEAD_MM = 104
# mm/s, 4 in/s as for ZPL printers, and the seconds a label takes besides, to
# take its job and find the gap to the next label
SPEED = 102
BESIDES = 1.0

# How long a status request left unanswered counts as outstanding, meanwhile
# no other is sent
ASK_AGAIN = 20
# When each printer that left one unanswered may be asked again, by
# devices.lock_key()
_ask_after: dict[str, float] = {}
# The model each printer answered ~!T with, by _answer_key(), empty for one
# that gave none while it was ready
_names: dict[str, str] = {}
NO_ANSWER = 'The printer does not answer'

# TSPL in the command sets of a device ID, e.g. TSPL2 or TSPL-EZ
COMMAND_SET = re.compile(r'TSPL[\w-]*', re.IGNORECASE)
# The resolution of a model, as for ZPL. A printer of another one, e.g. 600
# dpi, is no printer of this driver until configured.
RESOLUTION = re.compile(r'(\d+)\s*dpi', re.IGNORECASE)
OF_RESOLUTION = {200: MODELS[0], 203: MODELS[0], 300: MODELS[1]}
# Printers on TSPL by the maker, model and command sets of their device ID,
# which names no TSPL, as LPrint lists them in lprint-tspl.h
# (https://github.com/michaelrsweet/lprint, 54f1c462, Apache-2.0)
KNOWN_IDS = {
    # Rollo X1038
    ('Printer', 'Thermal Printer', 'XPP,XL'): MODELS[0],
    ('Marklife', 'X4', 'XPP,XL'): MODELS[0],
    # Phomemo PM-241-BT
    ('', 'PM-241-BT', 'XPP,XL'): MODELS[0],
    ('VEVOR', 'Y428BT', ''): MODELS[1],
}
# TSC in a device ID or the SNMP system description of a network printer
MAKER = re.compile(r'\bTSC\b')
# The resolution in the leading digit of the number of a TSC model, e.g.
# TE200 or TTP-345; Alpha-3R and TDM-30 name their width so, and a 6 is no
# resolution either, the TDP-643 Plus prints 203 dpi
# (https://www.barcodesinc.com/media/pdf/TSC/tdp643.pdf)
TSC_MODEL = re.compile(r'\b[A-Za-z]+-?([23])\d{2}')
OF_DIGIT = {'2': MODELS[0], '3': MODELS[1]}
# TSC's models of 600 dpi, as their data sheets name them: TX600
# (https://fs.tscprinters.com/en/dl/4/2172), TX610
# (https://fs.tscprinters.com/en/dl/3/7446), TTP-644M Pro
# (https://www.jarltech.com/ger_new/new/pdf/fr/tsc_ttp2410mpro.pdf), MH641
# (https://fs.tscprinters.com/en/dl/3/3607), MX640P
# (https://fs.tscprinters.com/fr/dl/3/2039) and MX641P
# (https://fs.tscprinters.com/en/dl/3/2841)
TSC_600_DPI = re.compile(r'\b(TX6[01]0|TTP-644|MH641|MX64[01])', re.IGNORECASE)


def _plain(text: str) -> str:
    """A field of a device ID compared in any case and spacing."""
    return ' '.join(text.split()).casefold()


def _id_key(maker: str, model: str, commands: str) -> tuple[str, str, str]:
    return _plain(maker), _plain(model), ','.join(_plain(command) for command in commands.split(','))


_KNOWN = {_id_key(*fields): known for fields, known in KNOWN_IDS.items()}


def _of_resolution(text: str, tsc: bool) -> Told:
    """The model of the resolution text names, also by the model of TSC it
    names where tsc, 203 dpi where it names none. Of another resolution,
    e.g. 600 dpi, none, see Told."""
    if resolution := RESOLUTION.search(text):
        return Told(OF_RESOLUTION.get(int(resolution[1])))
    if tsc and TSC_600_DPI.search(text):
        return Told(None)
    if tsc and (number := TSC_MODEL.search(text)):
        return Told(OF_DIGIT[number[1]])
    return Told(MODELS[0], resolution=False)


def _answer_key(device: str) -> str:
    """The key a printer is asked for its model by: its lock key and its
    device ID, so that another printer at the same address is asked afresh."""
    return f'{devices.lock_key(device)} {devices.known_device_id(device)}'.rstrip()


def _asked_lately(key: str) -> bool:
    # The monotonic clock starts at boot, a printer never asked was not asked lately
    return time.monotonic() < _ask_after.get(key, 0)


LABELS = tuple(label for model, prefix in zip(MODELS, ('tspl', 'tspl300-'))
               for label in diecut.labels(prefix, DPI[model], diecut.dots(HEAD_MM, DPI[model]), (model,), SIZES))
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (TSPL)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the first one comes out first, and the bytes of each row."""
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    img = diecut.oriented(label.generate(), across, top_first=True)
    img = bitmap.black_and_white(img, label)
    rows, width = bitmap.packed_rows(img)
    return img, rows, width


class Tspl(Driver):
    vendor = 'TSC'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # Both models are shown alike
    shown_as = {MODELS[1]: MODELS[0]}
    reports_tape = False
    # Its models are those of many makers
    names_devices = True
    # usblp's device, also a name a udev rule gives it, the printer through
    # PyUSB, or on the network on the port of raw printing, as the other drivers
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
                       re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
                       re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'))
    # Its jobs go to usblp, PyUSB or the network only
    takes_usbip = False

    def model_of(self, device, device_id=None):
        # By the IEEE 1284 device ID on USB, e.g. MFG:TSC;CMD:TSPL2;MDL:TE200;...
        if not isinstance(device, str):
            return None
        return self.model_in(devices.usb_device_id(device) if device_id is None else device_id)

    def model_in(self, device_id):
        told = self.told(device_id)
        return told.model if told else None

    def told(self, device_id):
        fields = devices.device_id_fields(device_id)
        named = any(COMMAND_SET.fullmatch(command.strip()) for command in fields.get('CMD', '').split(','))
        known = _KNOWN.get(_id_key(fields.get('MFG', ''), fields.get('MDL', ''), fields.get('CMD', '')))
        if known:
            return Told(known, command_set=named)
        if not named:
            return None
        return _of_resolution(fields.get('MDL', ''), bool(MAKER.search(fields.get('MFG', ''))))

    def described(self, description):
        return _of_resolution(description, tsc=True).model if MAKER.search(description) else None

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # tspl102x152, tspl300-102x152 and zpl102x152 are the same labels
        return diecut.label_for(label_size, LABELS, model)

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

    def status(self, device, model, unconfirmed=False):
        path = device.removeprefix('file://')
        if not device.startswith(('tcp://', 'usb://')) and not os.path.exists(path):
            raise FileNotFoundError(2, 'No such device', path)
        state = dict(network_status(device, model), media_type=diecut.MEDIA_TYPE)
        named = self.device_name(device) or _names.get(_answer_key(device))
        if named:
            state['device_name'] = named
        key = devices.lock_key(device)
        cached = devices.status_cache.get(key)
        if _asked_lately(key):
            return dict(cached) if cached else state
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(key, {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            # A query that waited for the printer takes the answer of the one before
            cached = devices.status_cache.get(key)
            if _asked_lately(key):
                return dict(cached) if cached else state
            answer, told = protocol.ask(device, name=named is None)
            if answer is None:
                _ask_after[key] = time.monotonic() + ASK_AGAIN
            else:
                _ask_after.pop(key, None)
        if told is not None:
            _names[_answer_key(device)] = name = devices.device_name(f"MDL:{told.replace(';', ' ')};")
            if name:
                state['device_name'] = name
        if answer is None:
            # A printer that never answered is told as ready and printed on,
            # as a ZPL printer that never answered ~HS
            if cached:
                state.update(cached)
                state.update({'errors': [NO_ANSWER], 'status_type': 'Error occurred'})
                devices.status_cache[key] = state
            return dict(state)
        state['errors'] = protocol.errors(answer)
        printing = answer & protocol.PRINTING and not state['errors']
        state['status_type'] = devices.BUSY_STATUS_TYPE if printing else devices.status_type(state['errors'])
        state['phase_type'] = 'Printing state' if printing else 'Waiting to receive'
        # A copy, the one kept stays as the printer told it
        devices.status_cache[key] = state
        return dict(state)

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        return [length for _, length in job]

    def render(self, queue, entries, keep=None):
        size = next(label for label in LABELS if label.identifier == queue.label_size)
        dpi = DPI[size.models[0]]
        job = []
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'tspl_{i}')
            job.append((protocol.label(rows, width, size.tape_size, size.gap, queue.sensing), diecut.millimeters(len(rows), dpi)))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            protocol.write(device, b''.join(label for label, _ in job))
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return devices.not_sent(e)
        return ''


DRIVER = Tspl()
