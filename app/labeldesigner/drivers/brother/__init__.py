"""Brother QL, P-touch and TD printers, which speak Brother's raster protocol
(commands.py, raster.py), on USB (usb.py, ../backends.py) and on the network
(network.py). Based on brother_ql by Philipp Klaus and its fork
brother_ql-inventree by Matthias Mair, both GPLv3."""

import logging
import re

from .. import devices, usbip
from ..base import Driver, Told
from ..labels import Color, FormFactor
from . import labels as tables, labels_td, names, network, raster, usb
from .models import (HALF_CUTTERS, MIN_PRINT_LENGTH, MIN_PTOUCH_PRINT_LENGTH, MODELS, PTOUCH_BESIDES, QL_BESIDES, QUALITY_SLOWER,
                     RED_SPEED, SHOWN_AS, TD_PRODUCT_IDS, length_problem, longest_label, named, resolution_problem)

VENDOR_ID = 0x04F9

logger = logging.getLogger(__name__)


class BrotherRaster(Driver):
    vendor = 'Brother'
    models = tuple(MODELS)
    labels = tables.LABELS
    label_names = {**names.LABEL_NAMES, **labels_td.NAMES}
    hidden_labels = tables.HIDDEN_LABELS
    # The 300 dpi TD-2300D models have the names of the 203 dpi ones
    shown_as = SHOWN_AS
    device_patterns = (
        re.compile(r'(file://)?/dev/usb/lp\d+'),
        re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
        re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
        # An IPv6 address in brackets
        re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'),
    )
    mac_address_prefixes = network.MAC_ADDRESS_PREFIXES
    refusals = tuple(length_problem(model) for model in MODELS)

    def model_of(self, device, device_id=None):
        if not isinstance(device, str):
            return None
        # Over USB/IP the device ID read at the import names the model
        if usbip.is_address(device):
            return self.model_in(devices.usb_device_id(device) if device_id is None else device_id)
        # On USB a TD printer of the TD-2000 series by its product ID
        ids = devices.usb_ids(device)
        return TD_PRODUCT_IDS.get(ids[1]) if ids and ids[0] == VENDOR_ID else None

    def model_in(self, device_id):
        return named(devices.device_id_fields(device_id).get('MDL', ''))[0]

    def told(self, device_id):
        # The name of a TD-2300D model may be that of either resolution
        model, resolution = named(devices.device_id_fields(device_id).get('MDL', ''))
        return Told(model, resolution=resolution) if model else None

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
        shortest = labels_td.SHORTEST.get(label.identifier, shortest)
        # The longest of the printers taking the tape, each refuses more than it prints
        longest = max(longest_label(model) for model in MODELS if tables.label_fits(label, model))
        # The printer feeds the margin before and after the print
        return label.feed_margin / self.label_dpi(label)[0] * 25.4, shortest, longest

    def length_problem(self, model, dots, high_res):
        # As raster.py counts the lines, at 600 dpi a label has twice as many
        if dots // (2 if high_res else 1) > MODELS[model].max_length_dots:
            return length_problem(model)
        return ''

    def label_for(self, label_size, model):
        # A TD printer prints a label of the same paper on its own roll of it
        return labels_td.label_for(label_size, model) if model in labels_td.MODELS else label_size

    def capabilities(self, model):
        ptouch = model.startswith('PT-')
        td = model in labels_td.MODELS
        # The QL-500 takes no cut commands, its labels are cut with a lever,
        # those of the TD-2000 series are torn off
        cutting = MODELS[model].cutting
        qualities = ['fast', 'quality'] if MODELS[model].quality_flag else ['quality']
        return dict(super().capabilities(model), qualities=qualities if td else ['fast', 'quality', 'high'],
                    high_res=not resolution_problem(model, True), cutter=cutting, manual_cutter=not cutting and not td,
                    half_cut=model in HALF_CUTTERS, chain=ptouch, feed_waste=ptouch,
                    # The top of a label leaves a QL, P-touch or TD printer first
                    leaves_turned=True, **({'model_shown': SHOWN_AS[model]} if model in SHOWN_AS else {}))

    def resolution_problem(self, model, high_res):
        return resolution_problem(model, high_res)

    def status(self, device, model, unconfirmed=False):
        if device.startswith('tcp://'):
            return network.query_network_status(device, model, label_printers_only=unconfirmed)
        return usb.query_status(device, model)

    def announced(self, service):
        return network.announced(service)

    def network_device(self, address):
        return f'tcp://{address}'

    def print_speed(self, model, fast, red):
        spec = MODELS[model]
        # A printer without the flag of print quality prints alike either way
        slower = 1 if fast or not spec.quality_flag else QUALITY_SLOWER
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
