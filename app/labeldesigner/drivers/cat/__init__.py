"""Cat printers, small thermal printers on Bluetooth LE sold under many names
(protocol.py), on endless paper 58 mm wide, which is torn off. They tell
their model by their Bluetooth name; each is a model of its own, as they
differ in how they start a print and feed the paper."""

import logging
import re
import time

from PIL import Image

from .. import bitmap, devices, progress
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from . import protocol

logger = logging.getLogger(__name__)

# Those Cat-Printer prints on (printer_lib/models.py:48) that TiMini-Print
# also prints on with these packets over Bluetooth LE
# (timiniprint/data/printer_models.json, profiles of protocol tiny and v5g)
NAMES = ('GB01', 'GB02', 'GB03', 'GT01', 'MX05', 'MX06', 'MX08', 'MX09', 'MX10', 'MX11', 'PD01', 'YT01')
MODELS = tuple(f'Cat/{name}' for name in NAMES)
# models.py:52-58
NEW_START = {'Cat/GB03'}
FEED_ROWS = {'Cat/MX05', 'Cat/MX06', 'Cat/MX08', 'Cat/MX09', 'Cat/MX10'}
# Their Bluetooth names: the GB and GT ones by the start of their name as
# both name them, without the GB02SH, GB03PH, GB03SH, GB03SL and GB03PL,
# which TiMini-Print reaches over classic Bluetooth; the others by their whole
# name, as TiMini-Print names them
BLUETOOTH_NAMES = {re.compile('GB01.*', re.DOTALL): 'Cat/GB01', re.compile('GB02(?!SH).*', re.DOTALL): 'Cat/GB02',
                   re.compile('GB03(?!PH|SH|SL|PL).*', re.DOTALL): 'Cat/GB03', re.compile('GT01.*', re.DOTALL): 'Cat/GT01',
                   **{re.compile(name): f'Cat/{name}' for name in NAMES[4:]}}
# Printers of these names that advertise manufacturer data of 5 or 7 bytes
# ending in 59 speak another protocol (TiMini-Print printer_models.json,
# model v5x and ibleem_v5x)
OTHER_PROTOCOL = set(NAMES[4:])

DOTS_PER_MM = 8
# No figures of the makers: mm/s as fast as rows reach the printer, written
# as Cat-Printer writes them, and the paper fed after each label at that
# speed. The jobs correct it.
SPEED = protocol.PIECE / protocol.PIECE_PAUSE / len(protocol.row(bytes(protocol.HEAD // 8))) / DOTS_PER_MM
BESIDES = protocol.FEED / DOTS_PER_MM / SPEED
# A status query keeps the printer connected, see transports.KEEP_LE, and
# awake: once it answered without errors it is asked again only when the
# label designer looks for printers and when printing. When each was last
# asked:
_asked: dict[str, float] = {}
LABELS = (Label('cat58', (58, 0), FormFactor.ENDLESS, (protocol.HEAD, 0), (protocol.HEAD, 0), 0),)
LABEL_NAMES = {'cat58': '58 mm thermal paper'}


def label_rows(label) -> tuple[Image.Image, list[bytes]]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the first one comes out first."""
    img = label.generate()
    # Turned across the paper, as by the ESC/POS driver
    if img.width != protocol.HEAD:
        img = img.rotate(90, expand=True)
    img = bitmap.black_and_white(img, label)
    rows, _ = bitmap.packed_rows(img)
    width = protocol.HEAD // 8
    return img, [(dots + bytes(width))[:width] for dots in rows]


def _other_protocol(device: dict) -> bool:
    return device.get('name') in OTHER_PROTOCOL and any(
        len(data) in (5, 7) and data[-1:] == b'\x59' for data in device.get('manufacturer', []))


class CatPrinter(Driver):
    vendor = 'Cat printer'
    models = MODELS
    labels = LABELS
    label_names = LABEL_NAMES
    # E.g. Cat printer GB01
    shown_as = {model: f'Cat printer/{model[4:]}' for model in MODELS}
    # bt://<MAC>, over Bluetooth LE without pairing
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}'),)
    errors = (OSError, protocol.CatError)
    reports_tape = False
    bluetooth_names = BLUETOOTH_NAMES

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The paper is torn off, the top of the label leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def found_on_bluetooth(self, device):
        return None if _other_protocol(device) else super().found_on_bluetooth(device)

    def status(self, device, model, unconfirmed=False):
        return self.noted_status(device, lambda: self._status(device, model))

    def _status(self, device, model):
        cached = devices.status_cache.get(devices.lock_key(device))
        if cached and not cached.get('errors') and _asked.get(device, -1) >= devices.looked:
            return dict(cached)
        state = network_status(device, model)
        state.update({'media_type': 'Thermal paper', 'phase_type': 'Waiting to receive'})
        with devices.device_lock(device, timeout=progress.status_wait(device)) as acquired:
            if not acquired:
                return devices.busy_state(device, state)
            with protocol.Connection(device) as c:
                told = c.ask_state()
        state['errors'] = protocol.errors(told)
        state['status_type'] = devices.status_type(state['errors'])
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
            img, rows = label_rows(entry['label'])
            if keep:
                keep(img, f'cat_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        device, model = queue.device_specifier, queue.model
        # Asked again at the next status query, which then tells how it is
        _asked.pop(device, None)
        try:
            with protocol.Connection(device) as c:
                progress.counting(device)
                for i, rows in enumerate(job):
                    error = c.print_label(protocol.label_packets(rows, model in NEW_START, model in FEED_ROWS))
                    if error:
                        logger.warning('Printing on %s failed: %s', device, error)
                        return error
                    progress.printed(device, i + 1)
            devices.note_answer(device)
        except protocol.CatError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            devices.note_answer(device, e)
            return devices.not_sent(e)
        return ''


DRIVER = CatPrinter()
