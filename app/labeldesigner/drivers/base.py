"""The interface of a printer driver.

A driver is a package below drivers/ for one protocol, e.g. Brother raster
for the QL and P-touch printers. Its __init__.py has a Driver as DRIVER, and
drivers/__init__.py lists it. The label designer, the API and the printer
detection use printers only through this interface."""

import re
from dataclasses import dataclass

from . import devices

# Printers on USB, also shared over USB/IP, and through usblp
_USB = re.compile(r'(usb|usbip|file)://|/dev/')


def network_status(device: str, model: str, red_support: bool = False) -> dict:
    """Status of a network printer that tells nothing about itself."""
    return {
        'errors': [],
        'path': device,
        'media_category': None,
        'media_length': 0,
        'media_type': None,
        'media_width': None,
        'model': model,
        'model_code': None,
        'phase_type': 'Network Printer',
        'series_code': None,
        'setting': None,
        'status_code': 0,
        'status_type': 'Network Printer',
        'tape_color': '',
        'text_color': '',
        'red_support': red_support,
        'red_roll': None,
    }


@dataclass(frozen=True)
class Told:
    """A model as a device ID names it: whether it names the resolution of
    the printhead, which tells models shown alike apart, see
    Driver.shown_as, and whether it names a command set of the driver, not
    only a model of its table of device IDs, e.g. those LPrint lists. Model
    None for a printer of the driver at a resolution it does not print,
    e.g. 600 dpi, which no other driver takes then either."""
    model: str | None
    resolution: bool = True
    command_set: bool = True


def shown(model: str) -> str:
    """A model as texts name it, PUQU Q00 for PUQU/Q00, by the name its
    driver gives it or shows it with."""
    from . import for_model
    driver = for_model(model)
    return driver.names.get(model) or driver.shown_as.get(model, model).replace('/', ' ')


class Driver:
    """The printers of one protocol: their models and label sizes, what a
    label looks like on them, their status, and printing."""

    # The maker of its printers, which the label designer names before the
    # model
    vendor: str = ''
    # Printer models, as PRINTER_MODEL, PRINTERS and requests name them:
    # <maker>/<model>, Brother ones without the maker
    models: tuple = ()
    # Label sizes (labels.Label), in the order the label designer
    # lists them, and the names it shows for them
    labels: tuple = ()
    label_names: dict = {}
    # Models shown by the name of another one, e.g. one for each resolution
    shown_as: dict = {}
    # Models as texts name them, with their maker, where not as their ids
    # do, e.g. DYMO LabelWriter 450 for DYMO/LabelWriter-450
    names: dict = {}
    # Label sizes the label designer shows only while they are selected
    hidden_labels: frozenset = frozenset()
    # Regular expressions for the addresses of its printers a request may
    # name besides the configured ones
    device_patterns: tuple = ()
    # Exceptions that mean that a printer did not answer
    errors: tuple = (OSError,)
    # The messages it refuses a job with, as they are shown to the user
    refusals: tuple = ()
    # MAC address prefixes of its network printers, for those found in the
    # ARP table that do not announce themselves
    mac_address_prefixes: tuple = ()
    # Whether its status tells the tape or roll in the printer
    reports_tape: bool = True
    # Whether it prints on printers another device shares over USB/IP
    takes_usbip: bool = True
    # Whether each of its models is one for printers of many makers or for
    # several printers, whose name its status tells, see device_name()
    names_devices: bool = False

    def model_of(self, device: str, device_id: str | None = None) -> str | None:
        """The model of a printer known by its address or device ID alone,
        e.g. vc500w://<host>, whatever model is configured. None if the
        address does not tell. device_id: its device ID as
        devices.usb_device_id() tells it, read if None."""
        return None

    def model_in(self, device_id: str) -> str | None:
        """The model of a printer of this driver an IEEE 1284 device ID names,
        e.g. MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;..., None for other devices."""
        return None

    def told(self, device_id: str) -> Told | None:
        """The model a device ID names as model_in() reads it, and what tells
        it, see Told. None for other devices."""
        model = self.model_in(device_id)
        return Told(model) if model else None

    def described(self, description: str) -> str | None:
        """The model of a printer of this driver its SNMP system description
        (sysDescr) names, e.g. TSC TE200, None for other devices."""
        return None

    def fits(self, label, model: str) -> bool:
        """Whether the model prints the label size, one of self.labels."""
        raise NotImplementedError

    def colors(self, model: str) -> str:
        """What the model prints: 'black', 'red' (black and red) or 'color'."""
        return 'black'

    def palette(self, label) -> str:
        """What a label size shows: 'black', 'red' or 'color'."""
        return 'black'

    def dpi(self, high_res: bool) -> int:
        """The resolution labels are made with."""
        raise NotImplementedError

    def label_dpi(self, label) -> tuple[int, int]:
        """Dots per inch of the dots of a label size, printed normally and at
        600 dpi. The label designer converts millimeters with them."""
        return self.dpi(False), self.dpi(True)

    def length_rules(self, label) -> tuple[float, float, float] | None:
        """For an endless label of the size of a fixed length, in mm: the
        paper fed before and after its print, each, and the shortest and the
        longest print. None where labels are as long as their content."""
        return None

    def length_problem(self, model: str, dots: int, high_res: bool) -> str:
        """Why the model refuses a label of dots along the tape, made at 600
        dpi with high_res, as too long, or an empty string."""
        return ''

    def label_for(self, label_size: str, model: str) -> str:
        """The label size the model prints a label made for label_size on:
        the same paper of another model, else label_size."""
        return label_size

    def capabilities(self, model: str) -> dict:
        """What the label designer offers for the model: the qualities it
        shows (fast, quality, high for 600 dpi), whether the model prints at
        600 dpi, whether it has a cutter, else whether its labels are cut
        off by hand with a lever, half cuts between labels and chain prints,
        whether it feeds tape before the first label, its colors, why it does
        not leave labels uncut, if it does not, whether labels leave it top
        first, the label designer then shows the printer below them, the
        blank paper in mm before each label that is cut, which was between
        printhead and cutter, the maker of the model, whether its print
        position is calibrated with a calibration label, whether it is
        told how it finds its labels, see diecut.SENSINGS, whether it
        has two rolls to print on, the one chosen in its settings (side),
        the model it is shown as, see shown_as, empty for none, and, of one
        without a cutter here, whether it cuts as it is set up itself."""
        return {'qualities': ['fast', 'quality'], 'high_res': False, 'cutter': True, 'manual_cutter': False,
                'half_cut': False, 'chain': False, 'feed_waste': False, 'colors': self.colors(model),
                'never_cut': '', 'leaves_turned': False, 'lead_mm': 0, 'vendor': self.vendor, 'calibration': False,
                'sensing': False, 'two_rolls': False, 'shown_as': self.shown_as.get(model, ''), 'cut_on_printer': False}

    def calibration_kind(self, model: str, label_size: str) -> str:
        """The calibration label of the model for the label size: narrow,
        where the printhead reaches past both edges, or wide, empty without
        one."""
        return ''

    def calibration_job(self, queue):
        """The job that prints the calibration label for the label size of
        the queue, moved as its offset says, as send() takes it."""
        raise NotImplementedError

    def calibration_example(self, kind: str) -> bytes:
        """A PNG of a calibration label of the kind printed off-center, which
        shows where to read it. ValueError for an unknown kind."""
        raise NotImplementedError

    def feed_kind(self, model: str, label_size: str) -> str:
        """What feed_job() does on the model with the label size loaded:
        'cut' feeds a little paper and cuts it, 'cut_paper' feeds the paper
        to the cutter and cuts it, 'cut_label' feeds one empty label and cuts
        it, 'label' feeds one empty label, an empty string where the model
        cannot."""
        return ''

    def feed_job(self, queue):
        """The job that feeds the paper of the printer of the queue as
        feed_kind() says, e.g. to cut off the last label of a job not cut
        after it, as send() takes it. None where the model cannot."""
        return None

    def followed(self, device: str, model: str) -> bool:
        """Whether the label designer may ask the printer every few seconds:
        one on USB costs nothing, one on the network or on Bluetooth takes
        the network or the radio."""
        return bool(_USB.match(device or ''))

    def resolution_problem(self, model: str, high_res: bool) -> str:
        """Why the model cannot print at the resolution, or an empty string."""
        return ''

    def cut_problem(self, cut: str, model: str) -> str:
        """Why the model cannot cut labels this way (each, last or none), or
        an empty string."""
        return ''

    def status(self, device: str, model: str, unconfirmed: bool = False) -> dict | None:
        """Status of a printer, with the keys of /api/printer_status. With
        unconfirmed, a device found by its MAC address only, None if it is no
        printer of this driver. Raises one of self.errors if it does not
        answer."""
        raise NotImplementedError

    def device_name(self, device: str) -> str:
        """Maker and model a printer on USB names in its device ID, e.g.
        Xprinter XP-58IIH, which status() tells as device_name where its
        driver names_devices; empty otherwise or if it names none."""
        if not self.names_devices:
            return ''
        # usb://<vendor>:<product>/<serial number>
        serial = device.removeprefix('usb://').partition('/')[2] if device.startswith('usb://') else ''
        return devices.device_name(devices.known_device_id(device), self.vendor, serial)

    def noted_status(self, device: str, query) -> dict:
        """The status query() tells, noted whether the printer answered, see
        devices.note_answer(). One that did not answer is not looked for at
        every status query."""
        error = devices.unanswered_before(device)
        if error:
            # The traceback of the last time only, it grew at every query
            raise error.with_traceback(None)
        try:
            state = query()
        except self.errors as e:
            devices.note_answer(device, e)
            raise
        devices.note_answer(device)
        return state

    def note_settings(self, device: str, settings: dict):
        """Take the settings of a printer as the server read them last, see
        printer_settings.of_printer(), where its status depends on them, e.g.
        the roll chosen for a Twin Turbo. The status queries run apart from
        the requests that read them."""

    def followed_status(self, device: str, model: str) -> dict | None:
        """Status of a printer the label designer follows, see followed():
        asked of the printer itself where status() may tell its last state."""
        return self.status(device, model)

    def announced(self, service: dict) -> tuple[str, str] | None:
        """(address, model) of a printer of this driver that announces itself
        on the network as mdns.discover() found it, None for other devices."""
        return None

    def found_on_bluetooth(self, device: dict) -> tuple[str, str] | None:
        """(address, model) of a printer of this driver on Bluetooth LE as
        bluetooth.discover() found it, None for other devices."""
        return None

    def network_device(self, address: str) -> str:
        """The address of a printer found by its MAC address."""
        raise NotImplementedError

    def print_speed(self, model: str, fast: bool, red: bool) -> tuple[float, float]:
        """About how fast the model prints, printing fast or for quality and
        in red or not: mm/s, and the seconds each label takes besides, e.g.
        to feed and cut it. The jobs of a printer correct it, see
        progress.py. A speed of 0 gives no estimate."""
        return 0, 0

    def label_lengths(self, job) -> list[float]:
        """The length of each label of a job of render() along the paper, in
        mm as the printer prints them, empty if unknown."""
        return []

    def render(self, queue, entries: list, keep=None):
        """The job for the labels of the queue, as send() takes it. entries:
        the labels of the queue with their settings and whether they are cut
        after them. keep(image, name), on the simulator, gets every label as
        it would be printed."""
        raise NotImplementedError

    def send(self, queue, job) -> str:
        """Print the job on the printer of the queue, which holds its device.
        An empty string on success, else a message for the user."""
        raise NotImplementedError
