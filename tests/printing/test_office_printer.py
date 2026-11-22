"""Office printers, "Office printer": sheets of labels printed as pages on a
fake IPP printer, where a print starts on a sheet and where it goes on,
lines to cut plain paper along, the offset of a printer and its test sheet,
and how a printer typed by its address becomes one."""
import io
import json
import re
import struct

import pytest
from PIL import Image

from app.labeldesigner import adding, ipp, printer, printer_settings, refusals, sheets
from app.labeldesigner.drivers import ipp as other
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.drivers.ipp import calibration, documents
from app.labeldesigner.label_names import SIZES
from tests.helpers import make_client, outcome
from tests.printing.test_adding import connections, fake_printer, identity  # noqa: F401
from tests.printing.test_ipp import attribute, keywords
from tests.printing.test_other_printer import FakePrinter, pages, printer_attributes, values

API = '/labeldesigner/api'
BLACK = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{counter}}', 'size': '40', 'inverted': True}])
OFFICE = 'Other/Office'


def integers(name: str, *numbers: int) -> bytes:
    return values(ipp.TAG_INTEGER, name, [struct.pack('>i', n) for n in numbers])


def office_attributes(resolutions=(300, 600), media=('iso_a4_210x297mm', 'na_letter_8.5x11in'), margins=(0, 423),
                      columns=('media-size', 'media-top-margin', 'media-bottom-margin', 'media-left-margin', 'media-right-margin'),
                      types=('sgray_8',), formats=('application/pdf', 'image/pwg-raster', 'image/urf')) -> bytes:
    """What an office laser printer answers: A4 and US Letter paper, its
    margins, media-col, 300 and 600 dpi."""
    told = printer_attributes(formats=formats, types=types, resolutions=resolutions, default=resolutions[-1], loaded=None,
                              name=b'HP LaserJet Pro M404dn', kinds=('document',), ready=media)[:-1]
    told += keywords('media-supported', *media) if media else b''
    told += keywords('media-col-supported', *columns) if columns else b''
    for side in ('top', 'bottom', 'left', 'right'):
        told += integers(f'media-{side}-margin-supported', *margins) if margins else b''
    return told + keywords('sides-supported', 'one-sided', 'two-sided-long-edge') + b'\x03'


@pytest.fixture
def laser(monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes())
    yield fake
    fake.close()


def client_of(tmp_path, fake: FakePrinter):
    return make_client(tmp_path, empty_repo=True, model=OFFICE, printer=fake.address)


def print_sheet(client, label_size='sheet-l7160', **data):
    response = client.post(f'{API}/print', data={'label_size': label_size, 'text': BLACK, **data})
    assert response.status_code == 200, response.get_json()
    return response


def printed(client, fake, label_size='sheet-l7160', **data) -> tuple[dict, list]:
    """The Print-Job of a print and its pages, header and rows."""
    assert outcome(client, print_sheet(client, label_size, **data)) == ('printed', '')
    [(job, document)] = fake.jobs()
    return job, pages(document)


def header(page) -> dict:
    head = page[0]
    number = lambda at: struct.unpack('>I', head[at:at + 4])[0]  # noqa: E731
    return {'dpi': number(276), 'points': (number(352), number(356)), 'dots': (number(372), number(376)),
            'pages': number(452), 'name': head[1732:1796].rstrip(b'\0').decode()}


def image(page) -> Image.Image:
    head, rows = page
    width = struct.unpack('>I', head[372:376])[0]
    return Image.frombytes('L', (width, len(rows)), b''.join(rows))


def inked(page: Image.Image, label: str = 'sheet-l7160') -> list[bool]:
    """Which labels of the page are printed on, in their order."""
    size = sheets.piece(SIZES[label]).dots_printable
    return [page.crop((x + 10, y + 10, x + size[0] - 10, y + size[1] - 10)).getextrema()[0] < 128
            for x, y in sheets.spots(SIZES[label], False)]


# --- The page ---------------------------------------------------------------------


def test_a4_sheet_is_a_page_of_a4(tmp_path, laser):
    job, [page] = printed(client_of(tmp_path, laser), laser, print_count=2)
    assert header(page) == {'dpi': 300, 'points': (595, 842), 'dots': (2480, 3508), 'pages': 1, 'name': 'iso_a4_210x297mm'}
    assert job['media-col'] == {'media-size': {'x-dimension': 21000, 'y-dimension': 29700}, 'media-bottom-margin': 0,
                                'media-left-margin': 0, 'media-right-margin': 0, 'media-top-margin': 0}
    assert (job['sides'], job['print-scaling'], job['document-format']) == ('one-sided', 'none', 'image/pwg-raster')
    assert 'media' not in job and 'media-type' not in str(job)
    assert inked(image(page)) == [True, True] + [False] * 19


def test_us_letter_sheet_is_a_page_of_us_letter(tmp_path, laser):
    job, [page] = printed(client_of(tmp_path, laser), laser, 'sheet-5160')
    assert header(page) == {'dpi': 300, 'points': (612, 792), 'dots': (2550, 3300), 'pages': 1, 'name': 'na_letter_8.5x11in'}
    assert job['media-col']['media-size'] == {'x-dimension': 21590, 'y-dimension': 27940}


def test_sheets_are_pages_of_one_document(tmp_path, laser):
    _, found = printed(client_of(tmp_path, laser), laser, print_count=50)
    assert [header(page)['pages'] for page in found] == [3, 3, 3]
    assert [sum(inked(image(page))) for page in found] == [21, 21, 8]


def test_labels_lie_where_the_catalog_puts_them(tmp_path, laser):
    client = client_of(tmp_path, laser)
    _, [page] = printed(client, laser, 'sheet-l7163')
    label = Image.open(io.BytesIO(client.post(f'{API}/preview', data={'label_size': 'sheet-l7163', 'text': BLACK, 'high_res': '0'}).data))
    # The label of 99.06 x 38.1 mm, 4.67 mm from the left and 15.15 from the top, within a dot
    x, y = 4.67 * 300 / 25.4, 15.15 * 300 / 25.4
    found = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
             if image(page).crop((round(x) + dx, round(y) + dy, round(x) + dx + label.width, round(y) + dy + label.height)).tobytes()
             == label.convert('L').tobytes()]
    assert label.size == (1170, 450) and found


def test_a_page_is_moved_by_the_offset_of_the_printer(tmp_path, laser):
    client = client_of(tmp_path, laser)
    first = image(printed(client, laser, 'sheet-l7163')[1][0]).point(lambda v: 255 if v < 128 else 0).getbbox()
    laser.requests.clear()
    assert client.post(f'{API}/calibration', data={'across': '1', 'along': '-0.5'}).status_code == 200
    moved = image(printed(client, laser, 'sheet-l7163')[1][0]).point(lambda v: 255 if v < 128 else 0).getbbox()
    # 1 mm is 12 dots at 300 dpi
    assert (moved[0] - first[0], moved[1] - first[1]) == (12, -6)


@pytest.mark.parametrize('margins, told', [((0, 423), 0), ((423, 635), 423), ((), None)])
def test_margins_as_the_printer_takes_them(tmp_path, monkeypatch, margins, told):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(margins=margins))
    try:
        job, _ = printed(client_of(tmp_path, fake), fake)
    finally:
        fake.close()
    assert job['media-col'].get('media-top-margin') == told
    assert ('media-left-margin' in job['media-col']) == (told is not None)


def test_page_by_its_name_without_media_col(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(columns=()))
    try:
        job, _ = printed(client_of(tmp_path, fake), fake)
    finally:
        fake.close()
    assert job['media'] == 'iso_a4_210x297mm' and 'media-col' not in job


def test_printer_of_600_dpi_only_gets_the_page_dot_by_dot(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(resolutions=(600,)))
    try:
        _, [page] = printed(client_of(tmp_path, fake), fake, 'sheet-l7163')
    finally:
        fake.close()
    assert header(page)['dpi'] == 600 and header(page)['dots'] == (4960, 7016) and header(page)['points'] == (595, 842)
    shown = image(page)
    half = shown.resize((2480, 3508), Image.Resampling.NEAREST)
    assert half.resize((4960, 7016), Image.Resampling.NEAREST).tobytes() == shown.tobytes()


def test_printer_of_pdf_only_gets_a_pdf_of_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(formats=('application/pdf',)))
    try:
        assert outcome(client_of(tmp_path, fake), print_sheet(client_of(tmp_path, fake))) == ('printed', '')
        [(job, document)] = fake.jobs()
    finally:
        fake.close()
    assert job['document-format'] == 'application/pdf' and document.startswith(b'%PDF')
    # A4 at 72 points to the inch
    assert re.search(rb'/MediaBox \[ ?0 0 595\.2 841\.92 ?\]', document)


def test_a4_is_not_sent_to_a_printer_of_letter_only(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(media=('na_letter_8.5x11in',)))
    try:
        client = client_of(tmp_path, fake)
        assert outcome(client, print_sheet(client)) == ('failed', 'The printer takes no A4 paper')
        assert not fake.jobs()
    finally:
        fake.close()
    assert refusals.message(ValueError('The printer takes no US Letter paper'), '') == 'The printer takes no US Letter paper'


def test_one_side_of_the_paper_even_where_both_are_the_default(tmp_path, laser):
    job, _ = printed(client_of(tmp_path, laser), laser)
    assert job['sides'] == 'one-sided'


def test_a_page_takes_its_length_at_the_speed_of_an_office_printer():
    assert other.DRIVER.print_speed(OFFICE, False, False) == (other.OFFICE_SPEED, other.OFFICE_BESIDES)
    job = other.Job(SIZES['sheet-l7160'], [Image.new('L', (2480, 3508), 255)] * 2)
    assert other.DRIVER.label_lengths(job) == [pytest.approx(297, abs=0.1)] * 2


# --- Where a print starts on a sheet ------------------------------------------------


def test_a_print_starts_at_the_label_asked_for(tmp_path, laser):
    _, found = printed(client_of(tmp_path, laser), laser, print_count=5, sheet_start=20)
    assert [inked(image(page)) for page in found] == [[False] * 19 + [True, True], [True] * 3 + [False] * 18]


def test_counters_go_on_from_sheet_to_sheet(tmp_path, laser):
    _, found = printed(client_of(tmp_path, laser), laser, print_count=4, sheet_start=20)
    shown = [image(page) for page in found]
    cells = [page.crop((x, y, x + 750, y + 450)).tobytes() for page, spots in zip(shown, ([19, 20], [0, 1]))
             for x, y in [sheets.spots(SIZES['sheet-l7160'], False)[i] for i in spots]]
    assert len(set(cells)) == 4


@pytest.mark.parametrize('start', ['0', '22', 'abc', '2.5'])
def test_start_off_the_sheet_is_refused(tmp_path, laser, start):
    response = client_of(tmp_path, laser).post(f'{API}/print', data={'label_size': 'sheet-l7160', 'text': BLACK, 'sheet_start': start})
    assert (response.status_code, response.get_json()['message']) == (400, 'sheet_start must be 1 to 21 or next')


def test_start_messages_are_listed():
    for per in {sheets.per_sheet(size) for size in other.SHEETS}:
        message = f'sheet_start must be 1 to {per} or next'
        assert refusals.message(ValueError(message), '') == message


def test_where_a_sheet_goes_on_is_kept_for_the_printer_and_sheet(tmp_path, laser):
    client = client_of(tmp_path, laser)
    printed(client, laser, print_count=5, sheet_start=20)
    settings = client.get(f'{API}/settings').get_json()
    assert settings['continue_at'] == {'sheet-l7160': 4}
    laser.requests.clear()
    _, [page] = printed(client, laser, print_count=2, sheet_start='next')
    assert inked(image(page)) == [False] * 3 + [True, True] + [False] * 16
    assert client.get(f'{API}/settings').get_json()['continue_at'] == {'sheet-l7160': 6}
    # Without a start a print takes a new sheet, one that ends a sheet leaves none partly used
    laser.requests.clear()
    printed(client, laser, print_count=21)
    assert 'continue_at' not in client.get(f'{API}/settings').get_json()


def test_next_without_a_sheet_partly_used_starts_a_new_one(tmp_path, laser):
    _, [page] = printed(client_of(tmp_path, laser), laser, print_count=1, sheet_start='next')
    assert inked(image(page))[0]


def test_a_print_that_fails_keeps_where_the_sheet_goes_on(tmp_path, laser):
    client = client_of(tmp_path, laser)
    printed(client, laser, print_count=3)
    laser.refusal = 0x0400
    assert outcome(client, print_sheet(client, print_count=5, sheet_start='next'))[0] == 'failed'
    assert client.get(f'{API}/settings').get_json()['continue_at'] == {'sheet-l7160': 4}


def test_one_whole_sheet_is_more_than_print_max_count(tmp_path, laser):
    client = client_of(tmp_path, laser)
    client.application.config['PRINT_MAX_COUNT'] = 100
    _, [page] = printed(client, laser, 'sheet-l4731rev', print_count=189, text=json.dumps([]))
    assert header(page)['pages'] == 1
    too_many = client.post(f'{API}/print', data={'label_size': 'sheet-l4731rev', 'text': BLACK, 'print_count': 190})
    assert too_many.get_json()['message'] == 'print_count must not exceed 189'


# --- Plain paper ------------------------------------------------------------------


def test_plain_paper_gets_lines_to_cut_along(tmp_path, laser):
    client = client_of(tmp_path, laser)
    assert client.post(f'{API}/settings', data={'cut_lines': '1'}).status_code == 200
    _, [page] = printed(client, laser, 'sheet-3474', print_count=1, text=json.dumps([]))
    shown = image(page)
    # Around the second label, 70 mm from the left, nothing printed on it
    x = round(70 * 300 / 25.4)
    assert shown.getpixel((x, 200)) == sheets.CUT_GRAY and shown.getpixel((x + 5, 200)) == 255
    laser.requests.clear()
    _, [page] = printed(client, laser, 'sheet-3474', print_count=1, text=json.dumps([]), cut_lines=0)
    assert image(page).getextrema() == (255, 255)


def test_cut_lines_of_black_dots_only_are_dotted(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(types=('black_1',)))
    try:
        _, [page] = printed(client_of(tmp_path, fake), fake, 'sheet-3474', text=json.dumps([]), cut_lines=1)
    finally:
        fake.close()
    head, rows = page
    x = round(70 * 300 / 25.4)
    column = [rows[y][x // 8] >> (7 - x % 8) & 1 for y in range(100, 300)]
    assert 0 < sum(column) < len(column)


def test_simulator_keeps_each_page(tmp_path, monkeypatch):
    shots = []
    monkeypatch.setattr(printer._Simulator, '__call__', lambda self, image, name: shots.append((name, image)))
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'demo_model': OFFICE}).status_code == 200
    assert client.post(f'{API}/settings', data={'printer': 'simulation', 'label_size': 'sheet-l7160'}).status_code == 200
    response = client.post(f'{API}/print', data={'printer': 'simulation', 'model': OFFICE, 'label_size': 'sheet-l7160',
                                                 'text': BLACK, 'print_count': 22, 'cut_lines': 1})
    assert outcome(client, response) == ('printed', '')
    assert [(name, shot.size, shot.mode) for name, shot in shots] == [('sheet_0', (2480, 3508), 'L'), ('sheet_1', (2480, 3508), 'L')]
    assert shots[1][1].histogram()[sheets.CUT_GRAY]


# --- Calibration ------------------------------------------------------------------


def test_office_printer_is_calibrated_with_a_test_sheet(tmp_path, laser):
    client = client_of(tmp_path, laser)
    assert client.get(f'{API}/calibration', query_string={'label_size': 'sheet-l7160'}).get_json() == {
        'across': 0.0, 'along': 0.0, 'roll': None, 'kind': 'sheet'}
    # Read where the cuts cross the scales: the labels are moved by that
    answer = client.post(f'{API}/calibration', data={'read_across': '-1', 'read_along': '0.5'}).get_json()
    assert answer['calibration'] == {'across': -1.0, 'along': 0.5, 'roll': None}
    answer = client.post(f'{API}/calibration', data={'read_across': '-0.5', 'read_along': '0'}).get_json()
    assert answer['calibration']['across'] == -1.5


@pytest.mark.parametrize('reading', ['3.5', '0.1', 'x'])
def test_readings_off_the_scale_are_refused(tmp_path, laser, reading):
    answer = client_of(tmp_path, laser).post(f'{API}/calibration', data={'read_across': reading})
    assert (answer.status_code, answer.get_json()['message']) == (400, printer_settings.CALIBRATION_MESSAGE)


def test_test_sheet_has_its_zero_ticks_on_the_cuts():
    label = SIZES['sheet-l7160']
    page = calibration.test_sheet(label)
    dots = 300 / 25.4
    across, row, column, down = calibration.cuts(label)
    assert (across, down) == (7.23 + 63.5, 15.15 + 38.1)
    # The thick zero tick of each scale, on the cut
    x, y = round(across * dots), round(row * dots)
    assert page.crop((x - 1, y - 20, x + 2, y)).getextrema()[1] == 0
    x, y = round(column * dots), round(down * dots)
    assert page.crop((x, y - 1, x + 20, y + 2)).getextrema()[0] == 0


def test_a_sheet_of_one_column_is_read_at_its_edges():
    assert calibration.cuts(SIZES['sheet-l7168'])[0] == 5.2 and calibration.cuts(SIZES['sheet-l7168'])[3] == 4.97 + 143.53


def test_test_sheet_is_printed_on_the_sheet_moved_as_set(tmp_path, laser):
    client = client_of(tmp_path, laser)
    response = client.post(f'{API}/calibration/print', data={'label_size': 'sheet-l7160', 'across': '1'})
    assert outcome(client, response) == ('printed', '')
    [(job, document)] = laser.jobs()
    [page] = pages(document)
    shown = image(page)
    expected = calibration.test_sheet(SIZES['sheet-l7160'])
    assert shown.point(lambda v: 255 if v < 128 else 0).getbbox()[0] == expected.point(lambda v: 255 if v < 128 else 0).getbbox()[0] + 12


def test_a_whole_page_is_calibrated_on_a_sheet_of_labels_of_its_size():
    queue = printer.PrinterQueue('Other/Office', 'simulation', 'sheet-a4', resolved=True)
    [page] = other.DRIVER.calibration_job(queue).images
    assert page.tobytes() == calibration.test_sheet(SIZES['sheet-l7160']).tobytes()


def test_example_of_the_test_sheet(tmp_path, laser):
    client = client_of(tmp_path, laser)
    response = client.get(f'{API}/calibration/example.png', query_string={'kind': 'sheet'})
    assert response.status_code == 200 and Image.open(io.BytesIO(response.data)).size[0] > 300
    refused = client.get(f'{API}/calibration/example.png', query_string={'kind': 'narrow'})
    assert refused.get_json()['message'] == calibration.KIND_MESSAGE


# --- Adding -----------------------------------------------------------------------


def test_office_printer_by_its_host_is_an_office_printer(fake_printer):  # noqa: F811
    fake_printer['body'] = office_attributes()[:-1] + attribute(0x41, 'printer-device-id', b'MFG:HP;MDL:LaserJet;') + b'\x03'
    found = adding.identify('tcp://127.0.0.1', 'local')
    assert (found['model'], found['address']) == (OFFICE, f'ipp://127.0.0.1:{network.IPP_PORT}/ipp/print')
    assert found['state']['paper_margin'] == 0


def test_office_printer_given_on_ipp_is_an_office_printer(fake_printer):  # noqa: F811
    fake_printer['body'] = office_attributes()
    assert adding.identify(f'ipp://127.0.0.1:{network.IPP_PORT}/ipp/print', 'local')['model'] == OFFICE


def test_printer_of_labels_stays_another_printer(fake_printer):  # noqa: F811
    # Labels told besides A4, e.g. a queue of CUPS for a label printer
    fake_printer['body'] = printer_attributes(kinds=('label',), ready=('iso_a4_210x297mm',))
    assert adding.identify('tcp://127.0.0.1', 'local')['model'] == 'Other/IPP-203'


def test_paper_the_printer_leaves_blank_is_told_where_labels_lie_in_it(tmp_path, monkeypatch):
    monkeypatch.setattr(other, 'JOB_POLL', 0.01)
    fake = FakePrinter(office_attributes(margins=(508,)))
    try:
        client = client_of(tmp_path, fake)
        for size, edge in (('sheet-3474', 5), ('sheet-l7160', None)):
            assert client.post(f'{API}/settings', data={'label_size': size}).status_code == 200
            [state] = [state for state in client.get(f'{API}/printer_status').get_json()['printers'] if state['path'] == fake.address]
            assert (state['paper_margin'], state.get('blank_edge')) == (5.08, edge)
    finally:
        fake.close()


def test_margins_of_the_printer_in_mm():
    assert other.margin({'media-top-margin-supported': [423, 0], 'media-left-margin-supported': 300}) == 3
    assert other.margin({}) is None
