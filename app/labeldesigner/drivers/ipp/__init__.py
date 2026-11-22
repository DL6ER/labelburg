"""Printers on IPP that no other driver prints on, shown as "Other printer":
label printers set up in CUPS, those LPrint drives, AirPrint label printers.
Die-cut labels of the common sizes (diecut.py) and DYMO's are sent as
images as large as the label, in the first format of documents.FORMATS the printer takes,
made at its resolution, 203 or 300 dpi. The printer cuts or tears them off
as it is set up. Where it tells its loaded media, e.g. in LPrint, that is
the roll. Its status is its printer-state and the reasons for it, a job
ends as its job-state tells."""

import http.client
import logging
import re
import time
from typing import NamedTuple

from PIL import Image

from ... import ipp
from .. import bitmap, devices, diecut, dymo
from ..base import Driver, network_status, shown
from ..labels import Label
from . import documents

logger = logging.getLogger(__name__)

# Of 300 dpi, for printers that do not tell, and of 203 dpi
MODELS = ('Other/IPP', 'Other/IPP-203')
DPI = {MODELS[0]: 300, MODELS[1]: 203}
SHOWN = 'Other printer'
# The common die-cut labels and DYMO's, which LPrint drives a LabelWriter on;
# no printhead limits them
SIZES = diecut.SIZES + tuple(size for size in (*dymo.KINDS, *dymo.WIDE) if size not in diecut.SIZES)
WIDEST = max(width for width, _ in SIZES)
# The names of the 300 dpi sizes are the shorter ones, it is the default
LABELS = tuple(label for model, prefix in zip(MODELS, ('ipp', 'ipp203-'))
               for label in diecut.labels(prefix, DPI[model], diecut.dots(WIDEST, DPI[model]), (model,), SIZES))
# mm a size told in whole mm may be off from one of SIZES it is: 1-1/8 x
# 3-1/2 in are 29 x 89 mm, DYMO's 28 x 89 mm address labels
SIZE_TOLERANCE = 1
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut ({SHOWN})' for label in LABELS}

# What a printer is asked before a job: its state, the formats it takes and
# its loaded media; before it is added also its resolution
ASKED = (*ipp.STATUS_ATTRIBUTES, 'document-format-supported', 'pwg-raster-document-type-supported',
         'pwg-raster-document-resolution-supported')
OFFERED = (*ASKED, 'printer-resolution-default')
# What tells a label printer from others, e.g. an office printer that takes
# the same formats
LABEL_SIGNS = ('printer-kind', 'media-ready', 'media-col-ready')
LABEL_KINDS = {'label', 'roll'}
TIMEOUT = 3.0
# Seconds to connect for a job, which may then take as long as _waited() tells
JOB_TIMEOUT = 10.0
# How long a job may take besides printing its labels at SLOWEST mm/s, and
# how often its state is asked meanwhile
JOB_WAIT = 120
JOB_POLL = 0.5
SLOWEST = 10
# Longer names a printer tells are cut
MAX_NAME = 64
# mm/s, about as fast as a DYMO LabelWriter 450, and the seconds a job takes
# besides, to be taken and made ready. The jobs of a printer correct it.
SPEED = 50
BESIDES = 2.0

NO_FORMAT = 'The printer takes none of the kinds of files Labelburg sends'
LOGIN = 'The printer asks for a login, which Labelburg cannot give'
TLS = 'The printer asks for an encrypted connection, give its address with ipps:// instead of ipp://'
NOT_TAKING = 'The printer takes no jobs at the moment, try again later'
REFUSED = 'The printer refused the label, the log of the server names the reason'
CANCELED = 'The label was canceled on the printer'
ABORTED = 'The printer could not print the label'
HELD = 'The printer holds the label until it is released on the printer'
NOT_FINISHED = 'The printer did not report that it finished the label, check whether it was printed'
NO_ANSWER = 'The printer did not answer after receiving the label, check whether it was printed'
# The job-state-reasons of a job CUPS holds until someone logs in
AUTHENTICATION = 'cups-held-for-authentication'


class Job(NamedTuple):
    """The labels of a job as 1 bit images, each as large as the label size."""
    label: Label
    images: list

    @property
    def dpi(self) -> int:
        return DPI[self.label.models[0]]


def dpi_of(resolution) -> int | None:
    """The dots per inch across of a resolution as ipp.decode_response()
    tells it, None for none."""
    if not isinstance(resolution, tuple) or len(resolution) != 3 or not isinstance(resolution[0], int):
        return None
    across, _, units = resolution
    return {ipp.DOTS_PER_INCH: across, ipp.DOTS_PER_CM: round(across * 2.54)}.get(units)


def offered(attributes: dict) -> str | None:
    """The model a printer with the attributes is added as, the one of the
    resolution nearest to its own, see OFFERED. None where it takes none of
    the formats labels are sent in."""
    dpi = dpi_of(attributes.get('printer-resolution-default')) or DPI[MODELS[0]]
    model = min(MODELS, key=lambda model: abs(DPI[model] - dpi))
    return model if documents.chosen(attributes, DPI[model]) else None


def _labels(media) -> bool:
    """Whether media of media-ready (its name, e.g. oe_4x6-label_4x6in) or of
    media-col-ready are labels or a roll."""
    if isinstance(media, str):
        return 'label' in media or media.startswith('roll_')
    if not isinstance(media, dict):
        return False
    kind, source, name = (media.get(member) for member in ('media-type', 'media-source', 'media-size-name'))
    return (isinstance(kind, str) and (kind.startswith('labels') or kind in ipp.ENDLESS_MEDIA_TYPES)
            or isinstance(source, str) and 'roll' in source or isinstance(name, str) and _labels(name))


def tells_labels(attributes: dict) -> bool:
    """Whether a printer tells that it prints labels: by its printer-kind
    (PWG 5100.13) or by its media, see LABEL_SIGNS."""
    def listed(name):
        value = attributes.get(name)
        return value if isinstance(value, list) else [value]
    return bool(set(listed('printer-kind')) & LABEL_KINDS) or any(map(_labels, listed('media-ready') + listed('media-col-ready')))


def ask(device: str, attributes=ASKED) -> dict:
    """The attributes of the printer at device, see ipp.ask()."""
    printer = ipp.printer_at(device)
    return ipp.get_printer_attributes(printer.host, printer.port, printer.path, attributes, TIMEOUT, printer.tls)


def printer_errors(attributes: dict) -> list[str]:
    """Messages for the reasons why the printer cannot print: those of
    REASON_MESSAGES, and the keyword of any other with the suffix -error.
    CUPS tells reasons of no suffix while it prints, e.g.
    cups-waiting-for-job-completed."""
    reasons = attributes.get('printer-state-reasons') or []
    found = []
    for reason in reasons if isinstance(reasons, list) else [reasons]:
        if not isinstance(reason, str):
            continue
        keyword = reason.removesuffix('-error')
        message = ipp.REASON_MESSAGES.get(keyword, keyword if reason.endswith('-error') else '')
        if message and message not in found:
            found.append(message)
    return found


def _loaded(media) -> tuple:
    """Width, length and type of loaded media, see ipp.loaded_media(), of the
    nearest of SIZES within SIZE_TOLERANCE where there is one."""
    width, length, kind = ipp.loaded_media(media)
    near = [(abs(size[0] - width) + abs(size[1] - length), size) for size in SIZES
            if width and length and abs(size[0] - width) <= SIZE_TOLERANCE and abs(size[1] - length) <= SIZE_TOLERANCE]
    return (*min(near)[1], kind) if near else (width, length, kind)


def told_status(device: str, model: str, attributes: dict) -> dict:
    """Status of the printer of the attributes it told, kept for when it
    does not answer. Its roll is the first of its media that are labels: a
    queue of CUPS tells e.g. A4 for a label printer."""
    state = network_status(device, model)
    name = attributes.get('printer-make-and-model')
    if isinstance(name, str) and name.strip():
        state['device_name'] = ' '.join(name.split())[:MAX_NAME]
    ready = attributes.get('media-col-ready')
    labels = [media for media in (ready if isinstance(ready, list) else [ready]) if _labels(media)]
    state['media_width'], state['media_length'], state['media_type'] = _loaded(labels)
    state['errors'] = printer_errors(attributes)
    printer_state = attributes.get('printer-state')
    devices.set_state(state, printer_state == ipp.PROCESSING, printer_state == ipp.STOPPED)
    devices.status_cache[devices.lock_key(device)] = state
    return dict(state)


def media_size(label: Label, ready) -> tuple[int, int]:
    """The media a label is printed on in hundredths of a millimeter, width
    and length: the loaded media of its size as the printer names it, else
    the size of the label."""
    for media in ready if isinstance(ready, list) else [ready]:
        if _loaded(media)[:2] == label.tape_size:
            size = media['media-size']
            return size['x-dimension'], size['y-dimension']
    return label.tape_size[0] * 100, label.tape_size[1] * 100


def _waited(lengths: list[float]) -> float:
    """How long a job of labels of lengths in mm may take."""
    return JOB_WAIT + sum(lengths) / SLOWEST


def asked_for(error: ipp.IPPError) -> str:
    """LOGIN or TLS where the printer refused a request for want of a login
    or of TLS, else an empty string."""
    if error.status in ipp.LOGIN or error.http_status in ipp.HTTP_LOGIN:
        return LOGIN
    return TLS if error.http_status == ipp.HTTP_TLS else ''


def _refusal(error: ipp.IPPError) -> str:
    """Why a job was not taken. One the printer answered without a reason
    did not reach it in time."""
    if asked := asked_for(error):
        return asked
    if error.status is None and error.http_status is None:
        return devices.not_sent(error)
    return NOT_TAKING if error.status in ipp.NOT_TAKING else REFUSED


def _status(device: str, model: str) -> dict:
    """Status of the printer, also of one that asks for a login or TLS."""
    try:
        attributes = ask(device, ipp.STATUS_ATTRIBUTES)
    except ipp.IPPError as e:
        if not (asked := asked_for(e)):
            raise
        state = dict(network_status(device, model), errors=[asked])
        devices.set_state(state, False, True)
        return state
    return told_status(device, model, attributes)


def _outcome(device: str, printer: ipp.Printer, answer: dict, lengths: list[float]) -> str:
    """How a job the printer took ended: an empty string once it is printed,
    else a message for the user. lengths: of its labels in mm."""
    job_id = answer.get('job-id')
    if not isinstance(job_id, int):
        logger.warning('%s took a job without telling its ID: %s', device, answer)
        return NOT_FINISHED
    deadline = time.monotonic() + _waited(lengths)
    job = answer
    while True:
        state = job.get('job-state')
        logger.debug('Job %s on %s: %s %s', job_id, device, state, job.get('job-state-reasons'))
        if state == ipp.COMPLETED:
            return ''
        if state in (ipp.CANCELED, ipp.ABORTED, ipp.HELD):
            reasons = job.get('job-state-reasons')
            logger.warning('Job %s on %s ended in state %s: %s', job_id, device, state, reasons)
            # CUPS holds a job until someone logs in
            if state == ipp.HELD and AUTHENTICATION in (reasons if isinstance(reasons, list) else [reasons]):
                return LOGIN
            return {ipp.CANCELED: CANCELED, ipp.ABORTED: ABORTED, ipp.HELD: HELD}[state]
        try:
            # Waiting or printing: the printer may tell why it does not go on
            problems = printer_errors(ask(device, ipp.STATUS_ATTRIBUTES))
            if problems:
                return f"The printer reports: {', '.join(problems)}, it prints the label once this is fixed"
            if time.monotonic() > deadline:
                return NOT_FINISHED
            time.sleep(JOB_POLL)
            job = ipp.get_job_attributes(printer, job_id, TIMEOUT)
        except ipp.IPPError as e:
            # Some printers soon forget the jobs they printed
            if e.status == ipp.NOT_FOUND:
                return ''
            logger.warning('No state of job %s on %s: %s', job_id, device, e)
            return asked_for(e) or NO_ANSWER
        except (OSError, http.client.HTTPException) as e:
            logger.warning('No state of job %s on %s: %s', job_id, device, e)
            return NO_ANSWER


class Ipp(Driver):
    # Named "Other printer"
    vendor = 'Other'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # Both are shown alike, by what they are, not by a maker, and never as
    # a model of the demo printer
    shown_as = {model: 'Other/printer' for model in MODELS}
    names = {model: SHOWN for model in MODELS}
    # ipp://<host>[:<port>][/<path>], ipps:// alike, cups://<queue>
    device_patterns = (re.compile(rf'ipps?://[A-Za-z0-9.-]+(:\d{{1,5}})?({ipp.PATH})?'),
                       re.compile(rf'ipps?://\[[0-9A-Fa-f:.]+\](:\d{{1,5}})?({ipp.PATH})?'),
                       re.compile(r'cups://[A-Za-z0-9_+-][A-Za-z0-9_.+-]*'))
    # Also an address at a raw port or none on IPP, which is never asked
    errors = (OSError, ipp.IPPError, http.client.HTTPException, ipp.RawPort, ipp.NotOnIpp)
    takes_usbip = False

    def claims(self, device) -> bool:
        """Whether a printer is on IPP, which no other driver prints on."""
        return ipp.is_address(device)

    def configured(self, model: str) -> str:
        """The model of a printer on IPP configured or requested as model,
        the one of 300 dpi where it is unknown. OtherPrinterOnly for a model
        of another driver."""
        if model in MODELS:
            return model
        if model == 'Unknown':
            return MODELS[0]
        raise devices.OtherPrinterOnly()

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def label_for(self, label_size, model):
        # ipp102x152 and ipp203-102x152 are the same labels, also as zpl102x152
        return diecut.label_for(label_size, LABELS, model)

    def dpi(self, high_res):
        return DPI[MODELS[0]]

    def label_dpi(self, label):
        return DPI[label.models[0]], DPI[label.models[0]]

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # Cut or torn off as the printer is set up, the top of a label leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, cut_on_printer=True,
                    leaves_turned=True, model_shown=self.shown_as[model].split('/')[-1])

    def status(self, device, model, unconfirmed=False):
        return self.noted_status(device, lambda: _status(device, model))

    def print_speed(self, model, fast, red):
        return SPEED, BESIDES

    def label_lengths(self, job):
        return [diecut.millimeters(img.height, job.dpi) for img in job.images]

    def render(self, queue, entries, keep=None):
        label = next(size for size in LABELS if size.identifier == queue.label_size)
        images = []
        for i, entry in enumerate(entries):
            # The first row is the top of the page, which leaves the printer first
            img: Image.Image = diecut.oriented(entry['label'].generate(), label.dots_printable[0], top_first=True)
            img = bitmap.black_and_white(img, entry['label'])
            if keep:
                keep(img, f'ipp_{i}')
            images.append(img)
        return Job(label, images)

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            printer = ipp.printer_at(device)
            attributes = ask(device)
        except (ValueError, *self.errors) as e:
            logger.warning('Printing on %s failed: %s', device, e)
            asked = asked_for(e) if isinstance(e, ipp.IPPError) else ''
            return asked or devices.not_sent(e)
        state = told_status(device, queue.model, attributes)
        if state['errors']:
            # Not sent to a printer that cannot take it, it would wait there
            return 'The printer reports: ' + ', '.join(state['errors'])
        chosen = documents.chosen(attributes, job.dpi)
        if chosen is None:
            logger.warning('%s takes none of %s, only %s', device, documents.FORMATS, attributes.get('document-format-supported'))
            return NO_FORMAT
        media = media_size(job.label, attributes.get('media-col-ready'))
        lengths = self.label_lengths(job)
        made = documents.documents(chosen, job.images, media)
        for i, document in enumerate(made):
            logger.info('Sending %d bytes of %s %s to %s', len(document), chosen.kind, chosen.raster_type, device)
            # A document of each label, or one of them all
            printed = [lengths[i]] if len(made) > 1 else lengths
            try:
                answer = ipp.print_job(printer, chosen.kind, media, document, _waited(printed), JOB_TIMEOUT)
            except ipp.Unanswered as e:
                logger.warning('%s took the label without answering: %s', device, e)
                return NO_ANSWER
            except ipp.IPPError as e:
                logger.warning('Printing on %s failed: %s', device, e)
                return _refusal(e)
            except (OSError, http.client.HTTPException) as e:
                logger.warning('Printing on %s failed: %s', device, e)
                return devices.not_sent(e)
            message = _outcome(device, printer, answer, printed)
            if message:
                return message
        return ''


DRIVER = Ipp()
