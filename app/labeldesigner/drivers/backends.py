"""The connections to printers on USB, through the printer interface of
the kernel (/dev/usb/lp<N>, Linux only), PyUSB
(usb://<vendor>:<product>[/<serial>], also on macOS, on Windows untested) or
USB/IP (usbip://<host>), after the backends of brother_ql by Philipp Klaus,
and on classic Bluetooth (bt://<MAC>, see transports.RfcommPrinter)."""

import ctypes.util
import errno
import logging
import os
import re
import select
import struct
import sys
import time

from . import usbip

logger = logging.getLogger(__name__)

# The devices Linux shares over USB/IP, by their bus IDs
USBIP_HOST = '/sys/bus/usb/drivers/usbip-host'
# Reads of what came before a status request at most, one per late answer
# over USB/IP: a printer that keeps sending must not hold the query and lock
DISCARD_READS = 100


class NoLibusb(OSError):
    """PyUSB finds no libusb to reach usb:// printers through."""


def discard(read):
    """Drop what the printer sent before a status request, read() returning
    nothing when there is nothing left."""
    for _ in range(DISCARD_READS):
        if not read():
            return


class Printer:
    """A connection to a printer: write, read up to length bytes, b'' when
    there is nothing to read, and dispose."""

    def write(self, data: bytes):
        logger.debug('Writing %d bytes.', len(data))
        self._write(data)

    def read(self, length: int = 32) -> bytes:
        data = self._read(length)
        if data:
            logger.debug('Read %d bytes.', len(data))
        return data

    def dispose(self):
        # Once, also when let go after it: a file opened since may have the
        # number of the closed one
        if getattr(self, '_disposed', False):
            return
        self._disposed = True
        try:
            self._dispose()
        except Exception:
            pass

    def __del__(self):
        self.dispose()


class KernelPrinter(Printer):
    """A printer through the printer interface of the kernel (usblp)."""

    # A read waits this long for an answer before it tries once more
    read_timeout = 0.01

    def __init__(self, device_specifier: str):
        self.dev = os.open(device_specifier.removeprefix('file://'), os.O_RDWR)

    def _write(self, data: bytes):
        os.write(self.dev, data)

    def _read(self, length: int) -> bytes:
        start = time.time()
        while time.time() - start < self.read_timeout:
            ready, _, _ = select.select([self.dev], [], [], 0)
            if self.dev in ready:
                data = os.read(self.dev, length)
                if data:
                    return data
            time.sleep(0.001)
        return os.read(self.dev, length)

    def _dispose(self):
        os.close(self.dev)


class UsbPrinter(Printer):
    """A printer through PyUSB, as usb://<vendor>:<product>[/<serial>]: the
    serial number tells printers of the same model apart."""

    # In ms
    read_timeout = 10.
    write_timeout = 15000.
    answer_timeout = 100.
    # The interface it prints through
    interface_class, interface_name = 7, 'printer'

    def __init__(self, device_specifier: str):
        import usb.core
        import usb.util
        self._usb = usb
        self.dev = _find(usb, device_specifier, self.interface_class)
        # The interfaces it took from drivers of the kernel, given back at dispose
        self.detached = []
        for number in self._held():
            try:
                if self.dev.is_kernel_driver_active(number):
                    self.dev.detach_kernel_driver(number)
                    self.detached.append(number)
            except NotImplementedError:
                pass
        self.dev.set_configuration()
        interface = usb.util.find_descriptor(self.dev.get_active_configuration(), bInterfaceClass=self.interface_class)
        if interface is None:
            raise ValueError(f'The device has no {self.interface_name} interface')
        direction = usb.util.endpoint_direction
        self.read_dev = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        self.write_dev = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        if self.read_dev is None or self.write_dev is None:
            raise ValueError(f'The {self.interface_name} interface has no endpoints to read and write')

    def _held(self) -> list[int]:
        """The interfaces it takes from the drivers of the kernel."""
        return [0]

    def _write(self, data: bytes):
        self.write_dev.write(data, int(self.write_timeout))

    def _read(self, length: int) -> bytes:
        data = self._read_once(length)
        if data:
            return data
        time.sleep(self.read_timeout / 1000.)
        return self._read_once(length)

    def _read_once(self, length: int) -> bytes:
        # A printer with nothing to tell may let the read time out instead
        # of answering it empty
        try:
            return bytes(self.read_dev.read(length, int(self.answer_timeout)))
        except self._usb.core.USBTimeoutError:
            return b''

    def _dispose(self):
        self._usb.util.dispose_resources(self.dev)
        del self.write_dev, self.read_dev
        for number in self.detached:
            try:
                self.dev.attach_kernel_driver(number)
            except self._usb.core.USBError:
                # Taken back already with another one, e.g. by cdc_acm
                logger.debug('Interface %d was not given back to the kernel', number, exc_info=True)
        del self.dev


class UsbSerialPrinter(UsbPrinter):
    """A printer that is a serial port on USB (CDC ACM) through PyUSB, as
    usb://<vendor>:<product>[/<serial>]: its data interface, set to 115200
    baud 8N1 with DTR and RTS on as transports.SerialLink sets the port. For
    the models a driver lists in usb_serial_ids only."""

    interface_class, interface_name = 0x0A, 'serial'
    # Of the communications interface: SET_LINE_CODING, SET_CONTROL_LINE_STATE
    LINE_CODING = struct.pack('<IBBB', 115200, 0, 0, 8)
    DTR_RTS = 0x03

    def __init__(self, device_specifier: str):
        super().__init__(device_specifier)
        usb = self._usb
        control = usb.util.find_descriptor(self.dev.get_active_configuration(), bInterfaceClass=0x02)
        if control is None:
            return
        try:
            self.dev.ctrl_transfer(0x21, 0x20, 0, control.bInterfaceNumber, self.LINE_CODING, 1000)
            self.dev.ctrl_transfer(0x21, 0x22, self.DTR_RTS, control.bInterfaceNumber, None, 1000)
        except usb.core.USBError:
            # Taken as it is by a port that does not know these requests
            logger.debug('The serial port of %s was not set up', device_specifier, exc_info=True)

    def _held(self) -> list[int]:
        # cdc_acm holds the communications and the data interface
        try:
            config = self.dev.get_active_configuration()
        except self._usb.core.USBError:
            # Not configured, no driver holds it
            return []
        found = (self._usb.util.find_descriptor(config, bInterfaceClass=kind) for kind in (0x02, self.interface_class))
        return sorted({interface.bInterfaceNumber for interface in found if interface is not None})


class UsbipPrinter(Printer):
    """A printer another device shares over USB/IP, as
    usbip://<host>[:<port>][/<bus ID>]. The connection stays open after
    dispose, see usbip.connect()."""

    # In seconds
    read_timeout = 0.01
    write_timeout = 15.

    def __init__(self, device_specifier: str):
        self.dev = usbip.connect(device_specifier)

    def _write(self, data: bytes):
        self.dev.write(data, self.write_timeout)

    def _read(self, length: int) -> bytes:
        return self.dev.read(length, self.read_timeout)

    def _dispose(self):
        usbip.release(self.dev)


def _has_interface(usb, device, interface_class: int) -> bool:
    if device.bDeviceClass == interface_class:
        return True
    return any(usb.util.find_descriptor(config, bInterfaceClass=interface_class) is not None for config in device)


def _usb_printers(usb, vendor: int | None = None, interface_class: int = 7):
    """The devices with a printer interface (class 7), or one of
    interface_class, of a maker or all."""
    maker = {} if vendor is None else {'idVendor': vendor}
    try:
        return usb.core.find(find_all=True, custom_match=lambda device: _has_interface(usb, device, interface_class),
                             backend=_libusb(), **maker)
    except usb.core.NoBackendError as e:
        raise NoLibusb(str(e)) from e


def _ids(device_specifier: str) -> tuple[int, int]:
    """Vendor and product ID of usb://<vendor>:<product>[/<serial>]."""
    vendor, _, product = device_specifier.removeprefix('usb://').partition('/')[0].partition(':')
    return int(vendor, 16), int(product, 16)


def _find(usb, device_specifier: str, interface_class: int = 7):
    """The device of usb://<vendor>:<product>[/<serial>] with an interface
    of the class."""
    serial = device_specifier.removeprefix('usb://').partition('/')[2]
    vendor, product = _ids(device_specifier)
    for printer in _usb_printers(usb, vendor, interface_class):
        if printer.idProduct == product and (not serial or _same_serial(_serial_number(usb, printer), serial)):
            return printer
    raise FileNotFoundError(errno.ENODEV, 'Device not found', device_specifier)


def _device_id(usb, device) -> str:
    """The IEEE 1284 device ID of a printer, e.g. MFG:EPSON;CMD:ESC/POS;
    MDL:TM-T20II;..., asked with GET_DEVICE_ID of the printer class, for
    which PyUSB claims the interface. Empty when it does not tell."""
    try:
        interface = usb.util.find_descriptor(device.get_active_configuration(), bInterfaceClass=7)
        answer = bytes(device.ctrl_transfer(0xA1, 0, 0, interface.bInterfaceNumber << 8 | interface.bAlternateSetting, 1024, 1000))
    except (AttributeError, NotImplementedError, ValueError, usb.core.USBError):
        return ''
    # Its length first, big-endian, the two bytes included
    return answer[2:int.from_bytes(answer[:2], 'big')].decode('ascii', 'replace')


def device_id(device_specifier: str) -> str:
    """The IEEE 1284 device ID of the printer usb://..., empty when it is not
    there or does not tell. Asking claims its interface, so a printer that
    usblp holds on Linux tells it only as /dev/usb/lp<N>."""
    try:
        import usb.core
        import usb.util
        return _device_id(usb, _find(usb, device_specifier))
    except Exception:
        # Also no PyUSB backend, e.g. no libusb
        logger.debug('No device ID of %s', device_specifier, exc_info=True)
        return ''


def _shared_bus_ids() -> set[str]:
    """The bus IDs of the USB devices Linux shares over USB/IP, e.g. 3-2."""
    try:
        return {name for name in os.listdir(USBIP_HOST) if re.fullmatch(r'\d+-[\d.]+', name)}
    except OSError:
        return set()


def _bus_id(device) -> str:
    """The bus ID of a device as Linux names it, e.g. 3-2 or 1-4.1."""
    try:
        return f"{device.bus}-{'.'.join(str(port) for port in device.port_numbers)}"
    except (AttributeError, TypeError, NotImplementedError):
        return ''


def found_printers() -> tuple[list[tuple[str, str]], list[str]]:
    """usb://<vendor>:<product>[/<serial>] and the IEEE 1284 device ID of the
    printers PyUSB finds that no driver of the kernel holds: on Linux usblp
    holds them as /dev/usb/lp<N>, on macOS none does. Serial ports a driver
    prints on are taken from the kernel's driver, see Driver.usb_serial_ids.
    Then the bus IDs of those Linux shares over USB/IP, which are left to
    the computers using them that way."""
    try:
        import usb.core
        import usb.util
        printers = list(_usb_printers(usb))
        ports = [device for device in _usb_printers(usb, interface_class=0x0A)
                 if _serial_listed((device.idVendor, device.idProduct), _has_interface(usb, device, 7))]
    except NoLibusb:
        # The printer list tells it
        raise
    except Exception:
        logger.debug('No printers through PyUSB', exc_info=True)
        return [], []
    found, shared = [], []
    usbip_host = _shared_bus_ids()
    port_ids = {(device.idVendor, device.idProduct) for device in ports}
    listed = [(printer, False) for printer in printers if (printer.idVendor, printer.idProduct) not in port_ids]
    for printer, is_port in listed + [(device, True) for device in ports]:
        # usbip-host holds the device, not its interfaces
        if usbip_host and (bus_id := _bus_id(printer)) in usbip_host:
            shared.append(bus_id)
            continue
        try:
            if not is_port and printer.is_kernel_driver_active(0):
                continue
        except NotImplementedError:
            pass
        except usb.core.USBError:
            # PyUSB may not open it, e.g. without permission, so it cannot print on it either
            continue
        try:
            serial = _serial_number(usb, printer)
        except NotImplementedError:
            # On Windows a printer without the WinUSB driver
            continue
        spec = f'usb://0x{printer.idVendor:04x}:0x{printer.idProduct:04x}'
        if serial and re.fullmatch(r'[A-Za-z0-9_-]+', serial):
            spec += f'/{serial}'
        found.append((spec, '' if is_port else _device_id(usb, printer)))
    return found, shared


def _libusb():
    """PyUSB's libusb backend, None without libusb. On Alpine Linux, ctypes
    does not find libusb by its name, so on Linux its file name is the
    fallback; elsewhere PyUSB looks in more places by itself."""
    import usb.backend.libusb1
    if sys.platform != 'linux':
        return usb.backend.libusb1.get_backend()
    return usb.backend.libusb1.get_backend(
        find_library=lambda name: ctypes.util.find_library(name) or 'libusb-1.0.so.0')


def _same_serial(told: str | None, given: str) -> bool:
    """Whether a printer that tells its serial number is the one given. A
    QL-700 tells 000L9Z378866, its sticker says L9Z378866: leading zeros do
    not count."""
    return told is not None and told.lstrip('0') == given.lstrip('0')


def _serial_number(usb, device) -> str | None:
    """The serial number of a device, which USB tells as the index of a
    string, or None when it cannot be read."""
    try:
        return usb.util.get_string(device, device.iSerialNumber)
    except (ValueError, usb.core.USBError):
        return None


def _serial_listed(ids: tuple[int, int], printer_interface: bool) -> bool:
    """Whether a driver reaches the device of (vendor, product) through its
    serial interface, see Driver.usb_serial_ids: one it lists, or one of a
    vendor it lists without a product that has no printer interface."""
    from . import DRIVERS
    return any(ids in driver.usb_serial_ids or (not printer_interface and (ids[0], None) in driver.usb_serial_ids)
               for driver in DRIVERS)


def open_printer(device_specifier: str) -> Printer:
    """A connection to the printer on USB, usb://... through PyUSB, its
    serial interface for the models of Driver.usb_serial_ids, usbip://...
    shared by another device, bt://... on classic Bluetooth, any other
    device through the printer interface of the kernel."""
    if device_specifier.startswith(('usb://', '0x')):
        try:
            ids = _ids(device_specifier)
        except ValueError:
            ids = None
        if ids and _serial_listed(ids, printer_interface=True):
            return UsbSerialPrinter(device_specifier)
        try:
            return UsbPrinter(device_specifier)
        except FileNotFoundError:
            if ids and _serial_listed(ids, printer_interface=False):
                return UsbSerialPrinter(device_specifier)
            raise
    if usbip.is_address(device_specifier):
        return UsbipPrinter(device_specifier)
    if device_specifier.startswith('bt://'):
        from .transports import RfcommPrinter
        return RfcommPrinter(device_specifier)
    return KernelPrinter(device_specifier)
