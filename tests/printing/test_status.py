"""The status of a printer that answers late or only after a reset."""

import functools

import pytest


class FakeSlowPrinter:
    """Printer that returns empty reads before answering a status request,
    like the QL-1100."""

    def __init__(self, empty_reads: int, response: bytes):
        self.empty_reads = empty_reads
        self.response = response
        self.written = b''

    def write(self, data: bytes):
        self.written += data

    def read(self, length: int) -> bytes:
        if b'\x1b\x69\x53' not in self.written:
            return b''
        if self.empty_reads > 0:
            self.empty_reads -= 1
            return b''
        chunk, self.response = self.response[:length], self.response[length:]
        return chunk


class FakeResetPrinter(FakeSlowPrinter):
    """Printer that answers only after "ESC @", like the QL-560 after its
    first status request."""

    def __init__(self, response: bytes):
        super().__init__(empty_reads=0, response=response)

    def read(self, length: int) -> bytes:
        if b'\x1b\x40\x1b\x69\x53' not in self.written:
            return b''
        return super().read(length)


class FakeHoldingPrinter:
    """Printer that holds an error, like the QL-1100: it answers no status
    request until the error is cleared, then every one it was asked."""

    def __init__(self, width: int):
        self.width, self.held, self.unread = width, 0, b''
        self.holding = True
        # With gaps each reply is a round trip away: the read before it is empty
        self.gaps, self.gap = False, False
        # With cleared_at, the error is cleared while that request waits
        self.cleared_at = None

    def write(self, data: bytes):
        if data.endswith(b'\x1b\x69\x53'):
            if self.holding:
                self.held += 1
                if self.held == self.cleared_at:
                    self.clear()
            else:
                self.unread += make_status_packet(self.width)

    def clear(self):
        self.holding = False
        self.unread += make_status_packet(self.width) * self.held

    def read(self, length: int) -> bytes:
        self.gap = self.gaps and not self.gap
        if self.gap:
            return b''
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def dispose(self):
        pass


def make_status_packet(width: int = 62, kind: int = 0x00) -> bytes:
    packet = bytearray(32)
    packet[0:3] = b'\x80\x20\x42'  # header
    packet[3] = 0x34  # series code
    packet[4] = 0x38  # model code
    packet[10] = width  # media width
    packet[11] = 0x0A  # continuous length tape
    packet[18] = kind  # status type, 00h a reply to a status request
    return bytes(packet)


class TestPrinterStatus:
    def test_get_status_waits_for_delayed_response(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeSlowPrinter(empty_reads=5, response=make_status_packet())
        status = get_status(printer)
        assert printer.written == b'\x1b\x69\x53'
        assert status['media_width'] == 62
        assert status['errors'] == []

    def test_get_status_resets_a_printer_that_does_not_answer(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeResetPrinter(make_status_packet())
        status = get_status(printer, timeout=0.1)
        assert printer.written == b'\x1b\x69\x53' + b'\x00' * 200 + b'\x1b\x40\x1b\x69\x53'
        assert status['media_width'] == 62

    def test_get_status_times_out_without_response(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeSlowPrinter(empty_reads=10**6, response=b'')
        with pytest.raises(TimeoutError):
            get_status(printer, timeout=0.1)

    def test_get_status_reads_the_reply_to_its_own_request(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeHoldingPrinter(width=62)
        with pytest.raises(TimeoutError):
            get_status(printer, timeout=0.1)
        assert printer.held == 2
        # Cleared at the printer, it answers the request and its retry, then
        # the roll is swapped
        printer.clear()
        printer.width = 29
        assert get_status(printer, timeout=0.1)['media_width'] == 29

    def test_get_status_drops_the_replies_of_many_polls(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeHoldingPrinter(width=62)
        # Held for 15 polls of the label designer, a request and its retry each
        printer.held = 30
        printer.clear()
        printer.width = 29
        assert get_status(printer, timeout=0.1)['media_width'] == 29

    def test_get_status_takes_only_a_reply_as_the_answer(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        # A notification the printer sent unasked comes first
        printer = FakeSlowPrinter(empty_reads=0, response=make_status_packet(62, kind=0x05) + make_status_packet(29))
        status = get_status(printer)
        assert (status['status_type'], status['media_width']) == ('Reply to status request', 29)

    def test_get_status_of_a_printer_that_keeps_sending(self):
        from app.labeldesigner.drivers.brother.usb import get_status
        printer = FakeSlowPrinter(empty_reads=0, response=b'')
        reads = []

        def read(length):
            # Reading on and on fails the test instead of hanging it
            assert len(reads) < 1000
            reads.append(length)
            return make_status_packet()[:length]
        printer.read = read
        assert get_status(printer)['media_width'] == 62


@pytest.fixture
def held(monkeypatch):
    """A FakeHoldingPrinter at /dev/usb/lp0, each reply a round trip away."""
    from app.labeldesigner.drivers.brother import usb
    printer = FakeHoldingPrinter(width=62)
    printer.gaps = True
    monkeypatch.setattr(usb, 'get_printer', lambda device: printer)
    monkeypatch.setattr(usb, 'get_status', functools.partial(usb.get_status, timeout=0.1))
    return printer


@pytest.fixture
def lost_at_once(monkeypatch):
    """Each query asks again, as one does once the request before counts as
    lost."""
    from app.labeldesigner.drivers.brother import usb
    monkeypatch.setattr(usb, 'ASK_AGAIN', 0)


@pytest.mark.parametrize('cleared_at', [5, 6], ids=['request', 'retry'])
def test_error_cleared_while_a_poll_waits(held, lost_at_once, cleared_at):
    # Held through two requests and their retries, cleared while the third
    # request or its retry waits: the reply that comes first is the oldest
    from app.labeldesigner.drivers.brother import usb
    for _ in range(2):
        with pytest.raises(TimeoutError):
            usb.query_status('file:///dev/usb/lp0')
    held.cleared_at = cleared_at
    usb.query_status('file:///dev/usb/lp0')
    for width in (29, 12, 38, 17):
        held.width = width
        assert usb.query_status('file:///dev/usb/lp0')['media_width'] == width


def test_messages_sent_unasked_are_no_replies(held, lost_at_once):
    # A phase change before the replies owed does not count as one of them
    from app.labeldesigner.drivers.brother import usb
    with pytest.raises(TimeoutError):
        usb.query_status('file:///dev/usb/lp0')
    held.unread = make_status_packet(62, kind=0x06)
    held.clear()
    held.width = 29
    assert usb.query_status('file:///dev/usb/lp0')['media_width'] == 29


def test_requests_of_many_polls_are_counted(held, monkeypatch):
    # E.g. a QL-1100 holding an error for an hour of polls
    from app.labeldesigner.drivers.brother import usb
    monkeypatch.setitem(usb._unanswered, '/dev/usb/lp0', 150)
    with pytest.raises(TimeoutError):
        usb.query_status('file:///dev/usb/lp0')
    assert usb._unanswered['/dev/usb/lp0'] == 152


@pytest.mark.parametrize('told', ['before', 'while the request waits'])
def test_error_told_unasked_is_held(held, told):
    # A QL-1100 tells e.g. "Replace media error" by itself, then answers no
    # status request until the error is cleared
    from app.labeldesigner.drivers.brother import usb
    error = bytearray(make_status_packet(62, kind=0x02))
    error[9] = 0x01
    if told == 'before':
        # Read before the request
        held.gaps, held.unread = False, bytes(error)
    else:
        write, sent = held.write, []

        def writing(data):
            write(data)
            if held.held and not sent:
                sent.append(error)
                held.unread = bytes(error)
        held.write = writing
    assert usb.query_status('file:///dev/usb/lp0')['errors'] == ['Replace media error, clear it at the printer']


@pytest.mark.parametrize('cleared', ['before', 'while the request waits'])
def test_a_query_reads_owed_replies_for_a_while_only(held, monkeypatch, cleared):
    # After an error held for hours, so as not to keep the printer locked;
    # the latest reply read is the answer, the next queries read the rest
    from app.labeldesigner.drivers.brother import usb
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(usb, 'time', clock)
    monkeypatch.setattr(usb, 'OWED_WAIT', 0.2)
    monkeypatch.setitem(usb._unanswered, '/dev/usb/lp0', 40)
    held.held = 40
    if cleared == 'before':
        held.clear()
    else:
        held.cleared_at = 41
    start = clock.now
    assert usb.query_status('file:///dev/usb/lp0')['media_width'] == 62
    assert clock.now - start < 0.2 + usb.REPLY_WAIT + 0.1
    assert 0 < usb._unanswered['/dev/usb/lp0'] <= 40
    held.width = 29
    for _ in range(10):
        state = usb.query_status('file:///dev/usb/lp0')
        if not usb._unanswered['/dev/usb/lp0']:
            break
    assert state['media_width'] == 29 and held.unread == b''


def test_wait_for_a_reply_follows_the_monotonic_clock(monkeypatch):
    # The wall clock set back meanwhile, e.g. on a Pi without a battery-backed
    # clock, does not stretch it
    from app.labeldesigner.drivers.brother import usb

    class Clock:
        now, wall = 1000.0, 1000.0

        def monotonic(self):
            return self.now

        def time(self):
            self.wall, wall = self.now - 3600, self.wall
            return wall

        def sleep(self, seconds):
            self.now += seconds

    class Silent:
        reads = 0

        def read(self, length):
            # Reading on and on fails the test instead of hanging it
            assert self.reads < 1000
            self.reads += 1
            return b''
    clock = Clock()
    monkeypatch.setattr(usb, 'time', clock)
    assert usb._read_reply(Silent(), 0.5) == b''
    assert clock.now - 1000 == pytest.approx(0.5, abs=0.05)


def test_status_query_waits_for_the_replies_it_is_owed(held, lost_at_once):
    from app.labeldesigner.drivers.brother import usb
    with pytest.raises(TimeoutError):
        usb.query_status('file:///dev/usb/lp0')
    held.clear()
    held.width = 29
    assert usb.query_status('file:///dev/usb/lp0')['media_width'] == 29


DEVICE, KEY = 'file:///dev/usb/lp0', '/dev/usb/lp0'
HELD = ['Replace media error, clear it at the printer']


@pytest.fixture
def polled(monkeypatch):
    """A FakeHoldingPrinter at /dev/usb/lp0 on a fake clock, which told
    "Replace media error" before, with what was written to it and when."""
    from app.labeldesigner.drivers import devices
    from app.labeldesigner.drivers.brother import usb
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(usb, 'time', clock)
    printer = FakeHoldingPrinter(width=62)
    printer.clock, printer.writes = clock, []
    write = printer.write

    def writing(data):
        printer.writes.append((clock.now, data))
        write(data)
    printer.write = writing
    printer.asked = lambda: [at for at, data in printer.writes if data.endswith(b'\x1b\x69\x53')]
    monkeypatch.setattr(usb, 'get_printer', lambda device: printer)
    error = bytearray(make_status_packet(62, kind=0x02))
    error[9] = 0x01
    devices.status_cache[KEY] = usb.parse_status(bytes(error))
    return printer


def test_printer_holding_an_error_polled_for_a_minute(polled):
    # As the label designer asks it every 2 s: a request and its retry at
    # most every 20 s, the polls between write nothing and tell the error
    # at once
    from app.labeldesigner.drivers.brother import usb
    clock = polled.clock
    end = clock.now + 60
    while clock.now < end:
        start, written = clock.now, len(polled.writes)
        assert usb.query_status(DEVICE)['errors'] == HELD
        assert len(polled.writes) > written or clock.now == start
        clock.sleep(2)
    asked = polled.asked()
    assert len(asked) == 6
    assert all(len([at for at in asked if first <= at < first + 20]) <= 2 for first in asked)


def test_error_cleared_the_late_reply_then_a_fresh_one(polled):
    # Held over requests that counted as lost, each reply a round trip away:
    # once cleared, the poll after takes what it answered then without asking
    # again, the next poll asks, after the roll was swapped
    from app.labeldesigner.drivers.brother import usb
    clock = polled.clock
    while len(polled.asked()) < 6:
        usb.query_status(DEVICE)
        clock.sleep(2)
    polled.gaps = True
    polled.clear()
    polled.width = 29
    written = len(polled.writes)
    state = usb.query_status(DEVICE)
    if state['errors']:
        # The read before the reply came empty
        state = usb.query_status(DEVICE)
    assert (state['errors'], state['media_width'], len(polled.writes)) == ([], 62, written)
    assert usb.query_status(DEVICE)['media_width'] == 29
    assert polled.unread == b''


def test_printer_switched_off_and_on_meanwhile_is_asked_within_20_s(polled):
    # Between two polls: it holds no request any more and answers the next
    from app.labeldesigner.drivers.brother import usb
    clock = polled.clock
    usb.query_status(DEVICE)
    polled.holding, polled.width = False, 29
    for _ in range(15):
        clock.sleep(2)
        state = usb.query_status(DEVICE)
    # Its request and the retry, then the next one
    asked = polled.asked()
    assert asked[2] - asked[0] <= 20 + 2
    assert (state['errors'], state['media_width']) == ([], 29)


class FakeQL560:
    """Answers a status request only right after invalidate and "ESC @", as
    the QL-560 does after its first one since power-on."""

    def __init__(self, clock):
        self.clock, self.writes, self.unread, self.reset = clock, [], b'', False

    def write(self, data):
        self.writes.append((self.clock.now, data))
        if data.endswith(b'\x1b\x40'):
            self.reset = True
        elif data.endswith(b'\x1b\x69\x53'):
            if self.reset:
                self.unread += make_status_packet(29)
            self.reset = False

    def read(self, length):
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def dispose(self):
        pass


def test_printer_answering_only_after_esc_at_polled_for_a_minute(monkeypatch):
    # The first query is answered after the retry, the request before it
    # stays outstanding: the retry at most every 20 s, the polls between
    # write nothing and tell its answer at once
    from app.labeldesigner.drivers.brother import usb
    from tests.printing.test_usb_jobs import Clock
    clock = Clock()
    monkeypatch.setattr(usb, 'time', clock)
    printer = FakeQL560(clock)
    monkeypatch.setattr(usb, 'get_printer', lambda device: printer)
    end = clock.now + 60
    while clock.now < end:
        start, written = clock.now, len(printer.writes)
        state = usb.query_status(DEVICE)
        assert (state['errors'], state['media_width']) == ([], 29)
        assert len(printer.writes) > written or clock.now == start
        clock.sleep(2)
    assert len([at for at, data in printer.writes if data.endswith(b'\x1b\x40')]) == 3


@pytest.mark.parametrize('code, model', [(0x66, 'PT-E550W'), (0x67, 'PT-P700'), (0x68, 'PT-P750W'), (0x6F, 'PT-P900W'),
                                         (0x70, 'PT-P950NW'), (0x69, 'Unknown')])
def test_ptouch_models_are_told_apart(code, model):
    # brother_ql gave the PT-E550W the code of the PT-P750W, a PT-E550W was
    # an unknown printer and a PT-P750W was taken for a PT-E550W. The
    # PT-P900W is "o" (6Fh), not the 69h its reference prints next to it.
    from app.labeldesigner.drivers.brother.usb import get_status
    packet = bytearray(make_status_packet())
    packet[3], packet[4] = 0x30, code
    packet[10], packet[11], packet[24], packet[25] = 12, 0x01, 0x01, 0x08  # 12 mm laminated, white, black text
    assert get_status(FakeSlowPrinter(empty_reads=0, response=bytes(packet)))['model'] == model


# Read from a QL-800 by the author of the fork mfwarren/brother_ql_web, who
# swapped the rolls, and the same from another QL-800 with a DK-22205 and a
# DK-22251: byte 25 is 0x01 for black rolls, 0x81 for black and red
QL800_62_BLACK = bytes.fromhex('802042343830000000003e0a0000150000000000000000000001000000000000')
QL800_62_RED = bytes.fromhex('802042343830000000003e0a0000230000000000000000000081000000000000')
QL800_29x90 = bytes.fromhex('802042343830000000001d0b00000100005a0000000000000001000000000000')


def _with(frame: bytes, index: int, value: int) -> bytes:
    return frame[:index] + bytes([value]) + frame[index + 1:]


@pytest.mark.parametrize('frame, red_roll', [
    (QL800_62_RED, True),
    (QL800_62_BLACK, False),
    (QL800_29x90, False),
    # Other values tell nothing
    (_with(QL800_62_BLACK, 25, 0x00), None),
    # A QL-700 prints no red, whatever it reports
    (_with(QL800_62_RED, 4, 0x35), None),
    # Nor is there a roll without labels
    (_with(_with(QL800_62_RED, 10, 0), 11, 0), None),
    # Red text on white 12 mm TZe tape (bytes 24 and 25) is no black and
    # red roll
    (bytes.fromhex('802042' '3068' + '00' * 5 + '0c01' + '00' * 12 + '0104' + '00' * 6), None),
])
def test_color_of_the_roll(frame, red_roll):
    from app.labeldesigner.drivers.brother.usb import get_status
    assert get_status(FakeSlowPrinter(empty_reads=0, response=frame))['red_roll'] is red_roll
