"""The printer drivers, see base.py, and which one drives a printer."""

from . import devices
from .base import Driver, Told
from .brother import DRIVER as BROTHER
from .vc500w import DRIVER as VC500W
from .puqu import DRIVER as PUQU
from .niimbot import DRIVER as NIIMBOT
from .escpos import DRIVER as ESCPOS
from .tspl import DRIVER as TSPL
from .zpl import DRIVER as ZPL

# In the order the label designer lists their models and label sizes, and
# asks them which printer a device ID names: one naming TSPL and ZPL is TSPL
DRIVERS: tuple[Driver, ...] = (BROTHER, VC500W, PUQU, NIIMBOT, ESCPOS, TSPL, ZPL)

MODELS = tuple(model for driver in DRIVERS for model in driver.models)
LABELS = tuple(label for driver in DRIVERS for label in driver.labels)
DEVICE_PATTERNS = tuple(pattern for driver in DRIVERS for pattern in driver.device_patterns)

_BY_MODEL = {model: driver for driver in DRIVERS for model in driver.models}
_BY_LABEL = {label.identifier: driver for driver in DRIVERS for label in driver.labels}
# Models other than Brother ones are named with their maker, e.g. PUQU/Q00,
# Brother ones also are, and without it as before. Q00 and TM-T20II alone
# were their names before, they are taken for a while.
_ALIASES = {f'{driver.vendor}/{model}': model for driver in (BROTHER, VC500W) for model in driver.models}
_ALIASES['Q00'] = 'PUQU/Q00'
# Names before ESC/POS was one model for every printer
_ALIASES['TM-T20II'] = _ALIASES['Epson/TM-T20II'] = 'Epson/ESC-POS'


def canonical(model: str) -> str:
    """A model as the drivers name it, from a configuration or a request:
    Brother/QL-700 is QL-700."""
    return _ALIASES.get(model, model) if isinstance(model, str) else model


def for_model(model: str) -> Driver:
    """The driver of a model. Unknown models are left to the Brother driver."""
    return _BY_MODEL.get(model, BROTHER)


def for_label(label_size: str) -> Driver | None:
    """The driver whose printers print a label size, None if none does."""
    return _BY_LABEL.get(label_size)


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    return for_model(model).resolution_problem(model, high_res)


def _unprinted(device_id: str) -> bool:
    """Whether a device ID names a printer of a driver at a resolution it
    does not print, e.g. a TSC TX600 that also names ZPL, see Told."""
    return any(told is not None and told.model is None for told in (driver.told(device_id) for driver in DRIVERS))


def told_model(device_id: str) -> str | None:
    """The model an IEEE 1284 device ID names, None if it is no printer of
    a driver."""
    if _unprinted(device_id):
        return None
    return next((model for driver in DRIVERS if (model := driver.model_in(device_id))), None)


def described_model(description: str) -> str | None:
    """The model an SNMP system description names, None if it is no printer
    of a driver."""
    return next((model for driver in DRIVERS if (model := driver.described(description))), None)


def _claim(driver: Driver, device: str, device_id: str) -> Told | None:
    """The model the address or device ID of a printer names for the driver,
    see Driver.told()."""
    known = driver.model_of(device, device_id)
    if not known:
        return None
    told = driver.told(device_id)
    return told if told and told.model == known else Told(known)


def resolve(device: str, model: str, assumed: bool = False) -> tuple[Driver, str]:
    """The driver and the model of a printer: the one its address or device
    ID tells, else the configured or requested model. The driver of that
    model keeps a printer it also takes, e.g. ZPL one that also names TSPL,
    and the model where it is the same printer of another resolution, shown
    alike, and the device ID names none, e.g. TSC/TSPL-300 for TSC/TSPL.
    Another driver takes it from that model only by its command set, not by
    a device ID of its table alone. An assumed model, e.g. PRINTER_MODEL
    left at its default, is none of these, only what is left. A printer of a
    resolution its driver does not print is the model given."""
    # Read once for all drivers, it may ask the printer
    device_id = devices.usb_device_id(device) if isinstance(device, str) else ''
    if _unprinted(device_id):
        return for_model(model), model
    named = canonical(model)
    chosen = None if assumed else _BY_MODEL.get(named)
    if chosen and (told := _claim(chosen, device, device_id)):
        alike = chosen.shown_as.get(named, named) == chosen.shown_as.get(told.model, told.model)
        return chosen, named if alike and not told.resolution else told.model
    for driver in DRIVERS:
        told = _claim(driver, device, device_id)
        if told and (told.command_set or chosen is None):
            return driver, told.model
    return for_model(model), model
