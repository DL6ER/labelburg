"""Brother QL and P-touch printers, which speak Brother's raster protocol
(commands.py, raster.py), on USB (usb.py, ../backends.py) and on the network
(network.py). Based on brother_ql by Philipp Klaus and its fork
brother_ql-inventree by Matthias Mair, both GPLv3."""

import errno
import logging
import re
import time

from .. import devices, usbip
from ..base import Driver
from ..labels import Color, FormFactor
from . import labels as tables, names, network, raster, usb
from .models import (HALF_CUTTERS, MIN_PRINT_LENGTH, MIN_PTOUCH_PRINT_LENGTH, MODELS, PTOUCH_BESIDES, QL_BESIDES, QUALITY_SLOWER,
                     RED_SPEED, TUBE_TOO_LONG, UNNAMED_ON_BLUETOOTH, UNUSED_CUTTERS, length_problem, longest_label,
                     resolution_problem, tube_lines)

logger = logging.getLogger(__name__)


# Brother's USB vendor ID, the models known by their product ID on USB
USB_VENDOR = 0x04F9
USB_PRODUCTS = {spec.usb_product: model for model, spec in MODELS.items() if spec.usb_product}

# A printer on Bluetooth is asked at most every BLUETOOTH_STATUS_EVERY s, or
# when looking for printers or printing; status queries between get its last
# answer. When each was asked, by devices.lock_key(), and the error it gave.
BLUETOOTH_STATUS_EVERY = 30
_bluetooth_asked: dict[str, tuple[float, Exception | None]] = {}


def _without_bluetooth(device: str, model: str):
    """Refuse a printer on bt:// of a model without Bluetooth, without
    contacting it, as a device file that is not there."""
    if device.startswith('bt://') and not getattr(MODELS.get(model), 'bluetooth', False):
        raise FileNotFoundError(errno.ENOENT, 'No such file or directory', device)


def _on_bluetooth(device: str, model: str) -> dict:
    """The status of a printer on Bluetooth, see BLUETOOTH_STATUS_EVERY."""
    key = devices.lock_key(device)
    asked, error = _bluetooth_asked.get(key, (None, None))
    if asked is not None and asked >= devices.looked and time.monotonic() - asked < BLUETOOTH_STATUS_EVERY:
        if error is not None:
            raise error.with_traceback(None)
        if key in devices.status_cache:
            return dict(devices.status_cache[key])
    try:
        state = usb.query_status(device, model)
    except OSError as e:
        _bluetooth_asked[key] = (time.monotonic(), e)
        raise
    # One a job holds was not asked
    if state.get('status_type') != devices.BUSY_STATUS_TYPE:
        _bluetooth_asked[key] = (time.monotonic(), None)
    return state


def _named(state: dict, model: str) -> dict:
    """The status with the model asked for where it names none and Brother
    documents no model code of that model, e.g., the PT-P710BT."""
    if state.get('model') == 'Unknown' and model in MODELS and MODELS[model].model_code > 0xFF:
        state['model'] = model
    return state


class BrotherRaster(Driver):
    vendor = 'Brother'
    models = tuple(MODELS)
    labels = tables.LABELS
    label_names = names.LABEL_NAMES
    hidden_labels = tables.HIDDEN_LABELS
    device_patterns = (
        re.compile(r'(file://)?/dev/usb/lp\d+'),
        re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
        re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
        # An IPv6 address in brackets
        re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'),
    )
    mac_address_prefixes = network.MAC_ADDRESS_PREFIXES
    # On classic Bluetooth by the model and digits, see UNNAMED_ON_BLUETOOTH
    bluetooth_names = {re.escape(model) + r'\d*': model for model, spec in MODELS.items()
                       if spec.bluetooth and model not in UNNAMED_ON_BLUETOOTH}
    bluetooth_kind = 'classic'
    refusals = (*(length_problem(model) for model in MODELS), TUBE_TOO_LONG)

    def model_of(self, device, device_id=None):
        # On USB the product ID of a model known by it
        ids = devices.usb_ids(device) if isinstance(device, str) else None
        if ids and ids[0] == USB_VENDOR and ids[1] in USB_PRODUCTS:
            return USB_PRODUCTS[ids[1]]
        # Over USB/IP the device ID read at the import names the model, on USB
        # that of a model whose status names it by no documented model code
        told = self.model_in(devices.usb_device_id(device) if device_id is None else device_id)
        if usbip.is_address(device) or (told and MODELS[told].model_code > 0xFF):
            return told
        return None

    def model_in(self, device_id):
        model = devices.device_id_fields(device_id).get('MDL')
        return model if model in MODELS else None

    def fits(self, label, model):
        return tables.label_fits(label, model)

    def colors(self, model):
        return 'red' if model in MODELS and MODELS[model].two_color else 'black'

    def palette(self, label):
        return 'red' if label.color == Color.BLACK_RED_WHITE else 'black'

    def dpi(self, high_res):
        return 600 if high_res else 300

    def label_dpi(self, label):
        # The dpi of the printers taking the label: 180 for the small P-touch
        # print head, 360 for the PT-P900 series, 300 for QL printers
        dpi = next((spec.dpi for model, spec in MODELS.items() if tables.label_fits(label, model)), 300)
        return dpi, dpi * 2

    def length_rules(self, label):
        if label.form_factor not in (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS):
            return None
        shortest = MIN_PTOUCH_PRINT_LENGTH if label.identifier in tables.PTOUCH_LABELS else MIN_PRINT_LENGTH
        # The longest of the printers taking the tape, each refuses more than it prints
        longest = max(longest_label(model) for model in MODELS if tables.label_fits(label, model))
        # The printer feeds the margin before and after the print
        return label.feed_margin / self.label_dpi(label)[0] * 25.4, shortest, longest

    def length_problem(self, model, dots, high_res, device=''):
        # As raster.py counts the lines, at 600 dpi a label has twice as many
        lines = dots // (2 if high_res else 1)
        if lines > MODELS[model].max_length_dots:
            return length_problem(model)
        if lines > tube_lines(model) and raster.on_tube(device):
            return TUBE_TOO_LONG
        return ''

    def capabilities(self, model):
        ptouch = model.startswith('PT-')
        # The QL-500 takes no cut commands, its labels are cut with a lever
        cutting = MODELS[model].cutting
        unused = model in UNUSED_CUTTERS
        # The PT-D460BT family is told no print quality
        qualities = ['quality'] if MODELS[model].d460bt_family else ['fast', 'quality', 'high']
        return dict(super().capabilities(model), qualities=qualities,
                    high_res=not resolution_problem(model, True), cutter=cutting, manual_cutter=not cutting,
                    cutter_unused=unused, half_cut=model in HALF_CUTTERS, chain=ptouch and cutting, feed_waste=ptouch,
                    # The top of a label leaves a QL or P-touch printer first
                    leaves_turned=True)

    def resolution_problem(self, model, high_res):
        return resolution_problem(model, high_res)

    def status(self, device, model, unconfirmed=False):
        if device.startswith('tcp://'):
            return network.query_network_status(device, model, label_printers_only=unconfirmed)
        if device.startswith('bt://'):
            _without_bluetooth(device, model)
            # One switched off is not paged at every status query, it takes the radio
            return _named(self.noted_status(device, lambda: _on_bluetooth(device, model)), model)
        return _named(usb.query_status(device, model), model)

    def announced(self, service):
        return network.announced(service)

    def network_device(self, address):
        return f'tcp://{address}'

    def print_speed(self, model, fast, red):
        spec = MODELS[model]
        slower = 1 if fast else QUALITY_SLOWER
        speed = RED_SPEED if red and spec.two_color else spec.speed
        return speed / slower, (PTOUCH_BESIDES if model.startswith('PT-') else QL_BESIDES) * slower

    def label_lengths(self, job):
        return job.page_lengths

    def render(self, queue, entries, keep=None):
        return raster.render(queue, entries, keep)

    def send(self, queue, job) -> str:
        """Send the raster data, the status messages of a USB printer and the
        IPP status of a network printer tell whether it was printed."""
        device = queue.device_specifier
        try:
            _without_bluetooth(device, queue.model)
            # On Bluetooth asked again at the next status query
            _bluetooth_asked.pop(devices.lock_key(device), None)
            network_printer = device.startswith('tcp://')
            if network_printer:
                # Not sent to a printer that cannot take it, e.g. with an
                # open cover, the job would be lost
                problem = network.printer_problem(device, queue.model)
                if problem:
                    logger.warning('Not printing on %s: %s', device, problem)
                    return problem
            logger.info("Sending %d bytes to printer at %s", len(job.data), device)
            if network_printer:
                network.send(job.data, device)
                logger.info('Sent %d bytes to printer %s', len(job.data), device)
                # Port 9100 gives no answer, IPP tells how the job ended
                message = network.job_result(device, queue.model, job.page_lengths)
                if message:
                    logger.warning('Printing failed: %s', message)
                    return message
                return ""
            info = usb.send_job(job.data, device, job.page_lengths)
            # Asked again at the next status query, see noted_status()
            devices.note_answer(device)
            logger.info('Sent %d bytes to printer %s', len(job.data), device)
            logger.info('Printer response: %s', str(info))
            if info.get('did_print') and info.get('ready_for_next_job'):
                logger.info('Label printed successfully and printer is ready for next job')
                return ""
            message = usb.print_failure(info)
            logger.warning('Printing failed: %s', message)
            return message
        except Exception as e:
            logger.exception("Exception during sending to printer: %s", e)
            return devices.not_sent(e)


DRIVER = BrotherRaster()
