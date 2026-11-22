"""TSPL of TSC label printers, as in TSC's TSPL/TSPL2 programming manual
(version 3.0, https://fs.tscprinters.com/system/files/31-0000001-00_tspl_tspl2_programming_3_0.pdf),
pages as printed there: the label as a bitmap (BITMAP, pp. 67-69) after its
size (SIZE, p. 1) and how the printer finds it (GAP, pp. 3-5, BLINE, p. 9),
in the order of the manual's examples (pp. 31, 69), gaps and black marks of
3 mm as LPrint's lprint-tspl.c sends them (https://github.com/michaelrsweet/lprint,
54f1c462, Apache-2.0); the status the printer tells at any time (<ESC>!?,
p. 122) and its model (~!T, p. 142)."""

import os
import select
import time

from .. import backends, devices, reach

# Port of raw printing on the network, and how long connecting may take
PORT = 9100
CONNECT_WAIT = 10
# How long the printer may take to answer
ANSWER_WAIT = 2
# A label goes to a printer on PyUSB in pieces: PyUSB gives up on a single
# write after 15 s
WRITE_CHUNK = 16384
# A command line ends with CR LF (p. X)
END = '\r\n'
STATUS = b'\x1b!?'
# Answered only while the printer is ready, as all ~ commands (p. X)
MODEL_NAME = b'~!T'
# The bits of the status byte (p. 122), the table lists their combinations
ERRORS = ((0x01, 'The printhead is open'),
          (0x02, 'The labels are stuck or the printer cannot find where each one starts'),
          (0x04, 'No labels are in the printer'),
          (0x08, 'The ribbon has run out'),
          (0x10, 'The printer is paused'),
          (0x80, 'The printer reports an error, switch it off and on'))
PRINTING = 0x20
# A 0 bit of a bitmap is printed (p. 68)
_WHITE_AS_ONE = bytes(range(255, -1, -1))


def label(rows: list[bytes], width: int, size: tuple, gap: float, sensing: str | None = None) -> bytes:
    """One label of rows of packed dots, width bytes each, MSB left, 1
    printed, its first row the first to leave the printer (DIRECTION 0, pp.
    XI and 17). size is the label's (width, length) in mm, gap the mm between
    labels, also the height of a black mark. sensing tells the printer how it
    finds the labels, see diecut.SENSINGS, None sends nothing: the printer
    finds them as last told or as set up on it."""
    finding = {'gap': f'GAP {gap:g} mm,0 mm{END}', 'mark': f'BLINE {gap:g} mm,0 mm{END}', 'endless': f'GAP 0,0{END}'}
    # SIZE starts a line of its own, also after requests a printer left
    # unanswered, which end with no CR LF (p. X)
    head = (f'{END}SIZE {size[0]:g} mm,{size[1]:g} mm{END}{finding.get(sensing, "")}DIRECTION 0,0{END}'
            f'REFERENCE 0,0{END}CLS{END}BITMAP 0,0,{width},{len(rows)},0,')
    return head.encode() + b''.join(rows).translate(_WHITE_AS_ONE) + f'{END}PRINT 1,1{END}'.encode()


def write(device: str, data: bytes):
    """Send to tcp://<host>[:<port>], to usb://<vendor>:<product>[/<serial>]
    through PyUSB or to a device file, e.g. usblp's."""
    if device.startswith('usb://'):
        printer = backends.UsbPrinter(device)
        try:
            for start in range(0, len(data), WRITE_CHUNK):
                printer.write(data[start:start + WRITE_CHUNK])
        finally:
            printer.dispose()
    elif device.startswith('tcp://'):
        with reach.create_connection(devices.tcp_address(device, PORT), timeout=CONNECT_WAIT) as s:
            s.sendall(data)
    else:
        with open(device.removeprefix('file://'), 'wb') as f:
            f.write(data)


class _Link:
    """The printer asked over usblp's device file: write, read what came
    within left seconds, None once nothing will, and close."""

    def __init__(self, device: str):
        self.fd = os.open(device.removeprefix('file://'), os.O_RDWR)

    def discard(self):
        backends.discard(lambda: select.select([self.fd], [], [], 0)[0] and os.read(self.fd, 256))

    def write(self, data: bytes):
        os.write(self.fd, data)

    def read(self, left: float) -> bytes | None:
        # usblp also tells empty USB packets
        return os.read(self.fd, 256) if select.select([self.fd], [], [], left)[0] else None

    def close(self):
        os.close(self.fd)


class _UsbLink(_Link):
    def __init__(self, device: str):
        self.printer = backends.UsbPrinter(device)

    def discard(self):
        backends.discard(lambda: self.printer.read(256))

    def write(self, data: bytes):
        self.printer.write(data)

    def read(self, left: float) -> bytes:
        return self.printer.read(256)

    def close(self):
        self.printer.dispose()


class _TcpLink(_Link):
    def __init__(self, device: str):
        self.socket = reach.create_connection(devices.tcp_address(device, PORT), timeout=CONNECT_WAIT)

    def discard(self):
        # A late answer went to the connection it was asked on
        pass

    def write(self, data: bytes):
        self.socket.sendall(data)

    def read(self, left: float) -> bytes | None:
        self.socket.settimeout(left)
        return self.socket.recv(256) or None

    def close(self):
        self.socket.close()


def _link(device: str) -> _Link:
    if device.startswith('tcp://'):
        return _TcpLink(device)
    if device.startswith('usb://'):
        return _UsbLink(device)
    return _Link(device)


def _answer(link: _Link, done) -> bytes:
    """What the printer answered within ANSWER_WAIT, until done(answer)."""
    answer = b''
    deadline = time.monotonic() + ANSWER_WAIT
    while not done(answer) and (left := deadline - time.monotonic()) > 0:
        try:
            part = link.read(left)
        except OSError:
            break
        if part is None:
            break
        answer += part
    return answer


def ask(device: str, name: bool = False) -> tuple[int | None, str | None]:
    """The status byte the printer answers <ESC>!? with, None without an
    answer, and with name its model as it answers ~!T where it is ready:
    empty without an answer, None where not asked."""
    link = _link(device)
    try:
        # An answer from before is not taken for this one
        link.discard()
        link.write(STATUS)
        answer = _answer(link, bool)
        if not answer:
            return None, None
        status = answer[-1]
        if not name or status:
            return status, None
        link.write(MODEL_NAME)
        told = _answer(link, lambda answer: b'\r' in answer or b'\n' in answer)
        return status, told.replace(b'\r', b'\n').split(b'\n')[0].decode('ascii', 'replace').strip()
    finally:
        link.close()


def errors(status: int) -> list[str]:
    """What the status byte of <ESC>!? tells is wrong, in plain words."""
    return [text for bit, text in ERRORS if status & bit]
