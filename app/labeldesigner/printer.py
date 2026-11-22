"""Printing and the printers: the print queue, the simulator and the
detection of printers. What depends on the protocol of a printer is in its
driver (drivers/)."""

import concurrent.futures
import contextlib
import datetime
import ipaddress
import logging
import os
import threading
import time
from typing import NamedTuple
from urllib.parse import urlsplit

from flask import Config
from PIL import Image

from . import bluetooth, drivers, mdns, sheets
from .drivers import devices, progress, reach, usbip
from .drivers.backends import NoLibusb
from .drivers.base import network_status
from .label_names import check_label_size, label_of

SIMULATED_LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'simulated_labels')

BUSY = 'Printer is busy with another print job, please try again'
WRONG_MODEL = 'The printer is another model than the label was made for, print it again'
NO_FEED = 'This printer offers no feed and cut'

# Number of simulated labels kept in SIMULATED_LABELS_DIR, older ones are
# deleted so that printing to the simulator cannot fill up the disk
MAX_SIMULATED_LABELS = 100
# The simulator as the label designer lists it
DEMO_NAME = 'Demo printer'

logger = logging.getLogger(__name__)

# Where printers are looked for when PRINTER_PRINTER is '?'. The tests point
# them elsewhere, so that they never find, let alone print on, a real printer.
USB_PRINTER_DEVICES = [f'/dev/usb/lp{i}' for i in range(0, 11)]
# Brother printers on USB, which the Brother driver asks for their model
BROTHER_USB = 'usb://0x04f9:'
ARP_TABLE = '/proc/net/arp'
# Network printers that announce themselves (DNS-SD, as for Bonjour and
# CUPS). Asking takes a second, the answer is kept for a minute.
NETWORK_DISCOVERY = True
DISCOVERY_INTERVAL = 60
# (time.monotonic() of the last discovery, printers found), None before the
# first. The monotonic clock starts at boot, so a time of 0 would count as
# recent in a container started less than a minute ago.
_discovered = None
# Printers on Bluetooth LE, e.g. a Niimbot B1. Searched for in the background
# for BLUETOOTH_SEARCH seconds, first at the first detection, then when the
# label designer looks for printers, at most every BLUETOOTH_INTERVAL
# seconds: the search takes the radio. Kept while the server runs, by address
# with their model, since a printer connected stops advertising.
BLUETOOTH_DISCOVERY = True
BLUETOOTH_SEARCH = 5.0
BLUETOOTH_INTERVAL = 60
_bluetooth: dict[str, str] = {}
# time.monotonic() of the last search started, None before the first; the
# search running
_bluetooth_searched = None
_bluetooth_thread = None
_bluetooth_lock = threading.Lock()


def configured_printers(config) -> list[tuple[str, str, str]]:
    """The printers of PRINTERS as (model, device, name), the name is empty
    for a printer without one."""
    return [(drivers.canonical(entry[0]), entry[1], entry[2] if len(entry) > 2 else '') for entry in config.get('PRINTERS') or []]


def is_simulation(device_specifier) -> bool:
    return isinstance(device_specifier, str) and device_specifier in ['simulation', '?']


def _prune_simulated_labels():
    # The names start with a timestamp, so sorting by name sorts by age
    labels = sorted((entry for entry in os.scandir(SIMULATED_LABELS_DIR) if entry.name.endswith('.png')), key=lambda entry: entry.name)
    for entry in labels[:-MAX_SIMULATED_LABELS]:
        try:
            os.remove(entry.path)
        except OSError:
            logger.debug('Failed to remove simulated label %s', entry.path, exc_info=True)


class _Simulator:
    """Keeps the labels of a job printed on the simulator as PNG files."""

    def __init__(self):
        os.makedirs(SIMULATED_LABELS_DIR, exist_ok=True)
        self._ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S_%f')

    def __call__(self, image, name: str):
        path = os.path.join(SIMULATED_LABELS_DIR, f'{self._ts}_{name}.png')
        image.save(path, format='PNG')
        logger.info('Saved simulated label to %s', path)


class Drawn(NamedTuple):
    """The first label of a queue as PrinterQueue.check() drew it: its image
    as the printer gets it, as the label designer shows it (upright, a page
    of several labels laid out as sheets.page_view() does), and the label
    with its templates filled in."""
    image: Image.Image
    shown: Image.Image
    label: object


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size, resolved=False):
        # A printer known by its address or device ID is that model, a model
        # resolved before is taken as it is: resolving may ask the printer
        self.driver, model = (drivers.for_model(model), model) if resolved else drivers.resolve(device_specifier, model)
        # A clear message instead of "Wrong pixel width" from the raster commands
        check_label_size(label_size, model)
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size
        # Cut after each label, the last one or none (see routes._cutting),
        # on PT printers and the VC-500W half cut between labels not cut, on
        # PT printers chain printing
        self.cut = 'each'
        self.half_cut = True
        self.chain = False
        # The end of the labels that leaves the printer first, top or bottom,
        # None as the driver prints them
        self.leaves_first = None
        # mm its labels are moved right and down, as calibrated for the printer
        self.offset = (0.0, 0.0)
        # How the printer finds the start of each label, one of
        # diecut.SENSINGS, None as it is set up
        self.sensing = None
        self._print_queue = []

    def add_label_to_queue(self, label, high_res: bool = False, fast: bool = False):
        problem = self.driver.resolution_problem(self.model, high_res)
        if problem:
            raise ValueError(problem)
        # Turned once for all drivers where the other end than the driver's
        # leaves first, the preview stays upright
        driver_end = 'top' if self.driver.capabilities(self.model)['leaves_turned'] else 'bottom'
        label.turned = self.leaves_first not in (None, driver_end)
        self._print_queue.append({
            'label': label,
            'high_res': high_res,
            'fast': fast
        })

    def images(self) -> list:
        """The images of the labels queued, e.g. uploaded photos, which the
        job holds until it prints. Copies share theirs."""
        return [entry['label'].image for entry in self._print_queue]

    def _model_problem(self) -> str:
        """Why the job cannot go to a printer shared over USB/IP: it cannot
        be reached, or the device ID of its import names another model than
        the job was made for, e.g. one taken from the configuration before
        it told its own."""
        if not usbip.is_address(self.device_specifier):
            return ''
        try:
            told = drivers.told_model(usbip.connect(self.device_specifier).device_id)
        except (OSError, ValueError) as e:
            logger.warning('Printing on %s failed: %s', self.device_specifier, e)
            return devices.not_sent(e)
        if told and told != self.model:
            logger.warning('Not sent to %s, it is a %s, not a %s', self.device_specifier, told, self.model)
            return WRONG_MODEL
        return ''

    def _grouped(self, entries: list[dict]) -> list[dict]:
        """The entries of the queue as printed: on a roll with several labels
        to a page, the labels of each page as one, see sheets.Sheet."""
        label = label_of(self.label_size)
        per = sheets.per_sheet(label) if label else 1
        if per == 1:
            return entries
        return [dict(entries[i], label=sheets.Sheet(label, [entry['label'] for entry in entries[i:i + per]]))
                for i in range(0, len(entries), per)]

    def label_cuts(self) -> list[bool]:
        """Whether the printer cuts after each label of the queue, as
        self.cut says: after each one, the last one or none."""
        last = len(self._print_queue) - 1
        return [self.cut == 'each' or (self.cut == 'last' and i == last) for i in range(last + 1)]

    def check(self) -> Drawn | None:
        """The first label of the queue, drawn as printing draws it, which
        raises what printing would raise for it. None without labels. It is
        drawn again when it prints."""
        if not self._print_queue:
            return None
        size = label_of(self.label_size)
        page = self._print_queue[:sheets.per_sheet(size) if size else 1]
        made = [entry['label'] for entry in page]
        # A page (sheets.Sheet) takes over the turn of its labels, which keep
        # it for printing
        kept = [(each.turned, getattr(each, 'keep_generated', False)) for each in made]
        made[0].keep_generated = True
        try:
            [first] = self._grouped(page)
            cut = self.cut == 'each' or self.cut == 'last' and len(page) == len(self._print_queue)
            self.driver.render(self, [dict(first, cut=cut)], None)
            return Drawn(made[0].generated, made[0].shown, made[0])
        finally:
            for each, (turned, keep) in zip(made, kept):
                each.turned, each.keep_generated = turned, keep
            made[0].generated = made[0].shown = None

    def process_queue(self) -> str:
        """Print the labels of the queue as one job."""
        if not self._print_queue:
            logger.warning("Print queue is empty.")
            return "Print queue is empty."

        labels = len(self._print_queue)
        self._print_queue = self._grouped(self._print_queue)
        size = label_of(self.label_size)
        unit = 'page' if size and sheets.per_sheet(size) > 1 else 'label'
        entries = [dict(entry, cut=cut) for entry, cut in zip(self._print_queue, self.label_cuts())]
        self._print_queue.clear()

        # Hold the device for the whole job so that neither status queries nor
        # other jobs get in between
        simulation = is_simulation(self.device_specifier)
        lock = contextlib.nullcontext(True) if simulation else devices.device_lock(self.device_specifier)
        with lock as acquired:
            if not acquired:
                logger.warning('Printer %s is busy with another job', self.device_specifier)
                return BUSY
            problem = '' if simulation else self._model_problem()
            if problem:
                return problem
            logger.info('Printing %d labels', labels)
            job = self.driver.render(self, entries, _Simulator() if simulation else None)
            if simulation:
                _prune_simulated_labels()
                return ''
            # Estimated for the mode the printer prints in: fast or for
            # quality, at 600 dpi, in red
            fast = all(entry['fast'] for entry in entries)
            red = 'red' in str(self.label_size)
            mode = (fast, any(entry['high_res'] for entry in entries), red)
            return self._send(job, len(entries), mode, unit)

    def _send(self, job, labels: int, mode: tuple[bool, bool, bool], unit: str = 'label') -> str:
        """Send the job of the locked printer, its progress counted."""
        fast, _, red = mode
        lengths = self.driver.label_lengths(job) or [0.0] * labels
        progress.start(self.device_specifier, lengths, self.driver.print_speed(self.model, fast, red), mode, unit)
        printed = False
        try:
            message = self.driver.send(self, job)
            printed = not message
            return message
        finally:
            progress.finish(self.device_specifier, printed)

    def print_calibration(self) -> str:
        """Print the calibration label of the printer, see
        Driver.calibration_job()."""
        if is_simulation(self.device_specifier):
            return ''
        return self._send_alone(self.driver.calibration_job(self))

    def feed(self) -> str:
        """Feed the paper of the printer, cut where it cuts, see
        Driver.feed_kind()."""
        job = self.driver.feed_job(self)
        if job is None:
            raise ValueError(NO_FEED)
        return '' if is_simulation(self.device_specifier) else self._send_alone(job)

    def _send_alone(self, job) -> str:
        """Send a job of its own, e.g. the calibration label, held and checked
        as process_queue() sends the labels of the queue."""
        with devices.device_lock(self.device_specifier) as acquired:
            if not acquired:
                logger.warning('Printer %s is busy with another job', self.device_specifier)
                return BUSY
            return self._model_problem() or self._send(job, 1, (False, False, False))


def _local_address(addresses: list[str]) -> str | None:
    """The first address of the local network, else one of the local link,
    of a printer without DHCP; never one of a cloud metadata service."""
    def ip(address):
        try:
            return ipaddress.ip_address(address)
        except ValueError:
            return None
    told = [(address, ip(address)) for address in addresses if ip(address)]
    local = [address for address, _ in told if reach.allowed(address, 'local')]
    on_link = [address for address, told_ip in told if told_ip.is_link_local and told_ip not in reach.METADATA]
    return (local + on_link + [None])[0]


def found_devices() -> set[str]:
    """The devices of the printers the search found last."""
    return {state.get('path') for state in _cached_printers}


def _discovered_label_printers() -> list[tuple[str, str]]:
    """Label printers that announce themselves on the network, as (address,
    model), e.g. tcp://<address>[:<port>] or vc500w://<address>. Kept for
    DISCOVERY_INTERVAL."""
    global _discovered
    if not NETWORK_DISCOVERY:
        return []
    if _discovered is not None and time.monotonic() - _discovered[0] < DISCOVERY_INTERVAL:
        return _discovered[1]
    printers = []
    for service in mdns.discover():
        # Anyone on the network may answer, the search is a local one
        address = _local_address(service.get('addresses') or [service['address']])
        if address is None:
            continue
        service = dict(service, address=address)
        found = next((found for driver in drivers.DRIVERS if (found := driver.announced(service))), None)
        if found and all(urlsplit(known).hostname != service['address'] for known, _ in printers):
            printers.append(found)
    _discovered = (time.monotonic(), printers)
    return printers


def _search_bluetooth():
    global _last_scan_ts
    found = {}
    for device in bluetooth.discover(BLUETOOTH_SEARCH):
        printer = next((printer for driver in drivers.DRIVERS if (printer := driver.found_on_bluetooth(device))), None)
        if printer:
            found[printer[0]] = printer[1]
    with _bluetooth_lock:
        _bluetooth.update(found)
        # Listed at the next status query
        _last_scan_ts = 0


def bluetooth_searching() -> bool:
    return _bluetooth_thread is not None and _bluetooth_thread.is_alive()


def _bluetooth_printers() -> list[tuple[str, str]]:
    """The printers found on Bluetooth LE as (address, model), a search
    started where one is due, see BLUETOOTH_INTERVAL."""
    global _bluetooth_searched, _bluetooth_thread
    if not BLUETOOTH_DISCOVERY:
        return []
    with _bluetooth_lock:
        now = time.monotonic()
        due = _bluetooth_searched is None or (devices.looked > _bluetooth_searched and now - _bluetooth_searched >= BLUETOOTH_INTERVAL)
        if due and not bluetooth_searching():
            _bluetooth_searched = now
            _bluetooth_thread = threading.Thread(target=_search_bluetooth, daemon=True)
            _bluetooth_thread.start()
        return list(_bluetooth.items())


def _red_support(model: str) -> bool:
    return drivers.for_model(model).colors(model) == 'red'


_last_scan_ts = 0
_cached_printers = []
# What the search for printers could not do, e.g. look on USB without libusb
_cached_hints = []


# The configured printers are asked at the same time, the answer waits at most
# STATUS_QUERY_WAIT seconds for them. The .local name of a PT-E550W that was off
# took 10 s not to resolve, the label designer showed nothing meanwhile. A
# query still running is not started again; its printer is told as it was
# last, or as being asked. Its answer is told next time: a PUQU Q00 took 4 s
# and more to connect over Bluetooth, it was never told as ready otherwise.
STATUS_QUERY_WAIT = 4.0
ASKING = 'Asking the printer'
_status_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='printer-status')
_status_queries = {}
_status_queries_lock = threading.Lock()
# The last answer of each printer, or why it did not answer, by device
_last_states = {}


def _told(device: str, model: str):
    """The driver and model of a printer, see drivers.resolve(). A printer
    shared over USB/IP that never told its device ID, e.g. as its import
    failed a moment ago, is imported first, so that it gets the commands of
    its own model."""
    if usbip.is_address(device) and not usbip.known_device_id(device):
        usbip.connect(device)
    return drivers.resolve(device, model)


def _query_configured(model: str, device: str) -> dict:
    driver, known = _told(device, model)
    return driver.status(device, known)


def _answer(future, model: str, device: str) -> dict:
    """The state a finished status query got, also when the printer did not answer."""
    try:
        return future.result()
    except Exception as e:
        logger.info('Status query of %s failed: %s', device, e)
        return devices.failed_printer_state(device, e, model)


def _configured_states(printers) -> list[dict]:
    """The status of each configured (model, device), in their order."""
    with _status_queries_lock:
        futures = []
        for model, device in printers:
            if device == 'simulation':
                futures.append(None)
                continue
            future = _status_queries.get(device)
            if future is not None and future.done():
                # It answered after the last status was told
                _last_states[device] = _answer(future, model, device)
                future = None
            if future is None:
                future = _status_pool.submit(_query_configured, model, device)
                _status_queries[device] = future
            futures.append(future)
    concurrent.futures.wait([f for f in futures if f], timeout=STATUS_QUERY_WAIT)
    states = []
    for (model, device), future in zip(printers, futures):
        if future is None:
            states.append(None)
        elif future.done():
            _last_states[device] = _answer(future, model, device)
            states.append(_last_states[device])
        elif device in _last_states:
            states.append(dict(_last_states[device]))
        else:
            asking = devices.failed_printer_state(device, TimeoutError(), model)
            states.append(dict(asking, errors=[], status_type=ASKING))
    return states


def _added_states(added) -> list[dict]:
    """The status of each printer added in the Settings, (model, device,
    name), as of a configured one."""
    states = _configured_states([(model, device) for model, device, _ in added])
    return [dict(state, path=device, model=model, name=name, added=True, red_support=_red_support(model))
            for (model, device, name), state in zip(added, states)]


def status_of_printer(config: Config, path: str, added=()) -> dict | None:
    """The status of one printer the label designer lists, asked again, e.g.
    one on USB it follows closely; None for a printer it does not list.
    added as for get_ptr_status()."""
    if config.get('PRINTERS'):
        listed = {device: (model, name) for model, device, name in configured_printers(config)}
    elif config['PRINTER_PRINTER'] == '?':
        listed = {p['path']: (p.get('model', 'Unknown'), '') for p in _cached_printers}
    else:
        listed = {config['PRINTER_PRINTER']: (drivers.canonical(config['PRINTER_MODEL']), '')}
    listed.update({device: (model, name) for model, device, name in added})
    # The path as listed goes on to the driver and into the answer, not the one asked for
    path = next((device for device in listed if device == path), None)
    if path is None or is_simulation(path):
        return None
    model, name = listed[path]
    try:
        driver, known = drivers.resolve(path, model)
        state = driver.followed_status(path, known)
    except Exception as e:
        logger.info('Status query of %s failed: %s', path, e)
        state = devices.failed_printer_state(path, e, model)
    state.setdefault('path', path)
    if name:
        state['name'] = name
    if any(device == path for _, device, _ in added):
        state.update(model=model, added=True, red_support=_red_support(model))
    state.setdefault('red_support', _red_support(state.get('model', model)))
    return state


def device_state(device: str, model: str) -> dict:
    """The status of one printer by its device and its model as
    drivers.resolve() finds it, from its last answer where it does not
    answer."""
    driver = drivers.for_model(model)
    try:
        if usbip.is_address(device):
            driver, model = _told(device, model)
        state = driver.status(device, model)
    except driver.errors as e:
        logger.info('Status query of %s failed: %s', device, e)
        state = devices.failed_printer_state(device, e, model)
    state.setdefault('path', device)
    state.setdefault('red_support', _red_support(state.get('model', model)))
    return state


def get_ptr_status(config: Config, added=()):
    """The status of the printers: the configured or detected ones, then the
    ones added in the Settings, added as (model, device, name)."""
    # Simple in-memory cache for detected printers
    global _last_scan_ts, _cached_printers, _cached_hints

    device_specifier = config['PRINTER_PRINTER']
    default_model = drivers.canonical(config['PRINTER_MODEL'])

    SIMULATOR_PRINTER = dict(network_status('simulation', default_model, _red_support(default_model)),
                             name=DEMO_NAME, phase_type='Simulator', status_type='Simulator')
    status = dict(network_status(device_specifier, 'Unknown'), phase_type='Unknown', status_type='Unknown')
    try:
        if config.get('PRINTERS'):
            # The configured printers, no detection. Each keeps its configured
            # model, a failed query must not move it to another printer. The
            # simulator is listed only when configured, e.g. QL-700=simulation.
            printers = []
            listed = configured_printers(config)
            for (model, device, name), state in zip(listed, _configured_states([(m, d) for m, d, _ in listed])):
                if device == 'simulation':
                    # Configured several times, it is one demo printer
                    if any(p['path'] == device for p in printers):
                        continue
                    name = name or DEMO_NAME
                state = state or SIMULATOR_PRINTER.copy()
                state.update({'path': device, 'model': model, 'name': name})
                state['red_support'] = _red_support(model)
                printers.append(state)
            printers += _added_states(added)
            return {
                **status,
                **printers[0],
                'printers': printers,
                'selected': printers[0]['path'],
            }
        # If device_specifier is the default '?', try to auto-detect multiple printers
        if device_specifier == '?':
            on_bluetooth = _bluetooth_printers()
            now = time.time()
            # Refresh cache every 10 seconds
            if now - _last_scan_ts > 10:
                logger.debug('Auto-detecting printers: scanning local USB and network')
                found_list = []
                # usblp's devices on Linux, then the printers PyUSB finds
                # that usblp does not hold, e.g. all on macOS,
                # those of a maker without a driver here left out
                specs = [f"file://{dev}" for dev in USB_PRINTER_DEVICES if os.path.exists(dev)]
                hints = []
                try:
                    found_usb, shared = devices.found_usb_printers()
                except NoLibusb as e:
                    # Told once below the printers, which are still listed
                    logger.debug('No printers through PyUSB', exc_info=True)
                    found_usb, shared = [], []
                    hints.append(devices.unanswered_reason(e))
                hints += [devices.shared_over_usbip(bus_id) for bus_id in shared]
                for spec in found_usb:
                    fields = devices.device_id_fields(devices.usb_device_id(spec))
                    driver, model = drivers.resolve(spec, fields.get('MDL', 'Unknown'))
                    # Only where its driver reaches it, a VC-500W only through usblp
                    if not any(pattern.fullmatch(spec) for pattern in driver.device_patterns):
                        continue
                    # A Brother printer telling no device ID or Brother's raster commands tells its model in the status
                    raster = spec.startswith(BROTHER_USB) and (not fields or 'PT-CBP' in fields.get('CMD', '').split(','))
                    if model in drivers.MODELS or raster:
                        specs.append(spec)
                for spec in specs:
                    try:
                        # The configured model need not be the model of this
                        # printer. If it is busy before it ever answered, its
                        # model is unknown.
                        driver, model = drivers.resolve(spec, 'Unknown')
                        printer_state = driver.status(spec, model)
                        printer_state.setdefault('path', spec)
                        found_list.append(printer_state)
                        logger.debug('Found compatible printer at %s -> %s', spec, printer_state.get('model'))
                    except Exception as e:
                        # A device file left without a printer, e.g. in a
                        # container that copied /dev when it started
                        if getattr(e, 'errno', None) in devices.NOT_THERE:
                            logger.debug('No printer at %s: %s', spec, e)
                            continue
                        # Keep the printer in the list with the error, otherwise
                        # the label designer switches to another printer (often
                        # the simulator) without telling why
                        logger.info('Status query of %s failed: %s', spec, e)
                        logger.debug('Status query of %s failed', spec, exc_info=True)
                        found_list.append(devices.failed_printer_state(spec, e))

                # Network printers that announce themselves, and ones in the
                # ARP table with a known MAC address prefix, e.g. where
                # multicast does not reach. The latter are dropped if their
                # IPP model is no label printer.
                candidates = [(spec, model, False, drivers.resolve(spec, model)[0]) for spec, model in _discovered_label_printers() + on_bluetooth]
                try:
                    with open(ARP_TABLE, 'r') as arp_file:
                        for line in arp_file.readlines()[1:]:  # skip header line
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            ip, _, _, mac, _, _ = parts
                            known = any(urlsplit(spec).hostname == ip for spec, _, _, _ in candidates)
                            driver = next((driver for driver in drivers.DRIVERS
                                           if any(mac.startswith(prefix) for prefix in driver.mac_address_prefixes)), None)
                            if not known and driver:
                                candidates.append((driver.network_device(ip), default_model, True, driver))
                except Exception:
                    logger.debug('Failed to read ARP table for network printer detection', exc_info=True)
                for spec, model, unconfirmed, driver in candidates:
                    try:
                        printer = driver.status(spec, model, unconfirmed)
                    except driver.errors as e:
                        logger.info('Status query of %s failed: %s', spec, e)
                        printer = devices.failed_printer_state(spec, e, model)
                    if printer is not None:
                        found_list.append(printer)
                        logger.debug('Found network printer at %s -> %s', spec, printer.get('model'))

                _cached_printers = found_list
                _cached_hints = hints
                # A printer busy with a job is scanned again with the next
                # query instead of being shown as printing for 10 s
                busy = any(p.get('status_type') == devices.BUSY_STATUS_TYPE for p in found_list)
                _last_scan_ts = 0 if busy else now
            # Prepare response: include list of printers and a top-level status for the first one (compatibility)
            # Ensure simulator printer is always present
            sim = SIMULATOR_PRINTER.copy()
            # An added printer the search finds too is listed as added
            extra = _added_states(added)
            taken = {devices.lock_key(p['path']) for p in extra}
            printers = [p for p in _cached_printers if devices.lock_key(p.get('path') or '') not in taken] + extra
            # append simulator if not present
            if not any(p.get('path') == 'simulation' for p in printers):
                printers.append(sim)
            if printers:
                # The default is the first printer that answered, else the
                # first listed: never the simulator while a printer is listed
                first = next((p for p in printers if not p.get('errors') and p.get('path') != 'simulation'), printers[0])
                for key, value in first.items():
                    status[key] = value
                return {
                    'printers': printers,
                    'selected': status.get('path'),
                    'hints': list(_cached_hints),
                    # The label designer asks again once it has ended
                    'searching': bluetooth_searching(),
                    **status
                }
            else:
                status['status_type'] = 'Offline'
                status['errors'].append('No compatible printer detected')
                return {
                    'printers': [sim],
                    'selected': None,
                    'hints': list(_cached_hints),
                    'searching': bluetooth_searching(),
                    **status
                }
        if device_specifier == 'simulation':
            status.update(SIMULATOR_PRINTER)
            status['printers'] = [SIMULATOR_PRINTER.copy(), *_added_states(added)]
            status['selected'] = device_specifier
            return status
        # The label designer offers only the printers of the list
        printer = device_state(device_specifier, drivers.resolve(device_specifier, default_model)[1])
        status.update(printer)
        status['printers'] = [printer, *_added_states(added), SIMULATOR_PRINTER.copy()]
        status['selected'] = device_specifier
        return status
    except Exception as e:
        logger.exception("Printer status error: %s", e)
        status['errors'] = ['The status of the printer could not be read, the log of the server names the reason']
        return status
