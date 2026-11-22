"""The PT-D460BT family of ptouch-print: the PT-D410, PT-D460BT, PT-D610BT,
PT-E310BT and PT-E560BT, of which Brother publishes no raster command
reference. Their jobs follow ptouch-print by Dominic Radermacher (GPLv3) at
b7c55272, src/libptouch.c and src/ptouch-print.c:
https://github.com/probonopd/ptouch-print
their tapes, longest labels and Bluetooth names Brother's User's Guides and
Quick Setup Guides."""
import pytest
from PIL import Image

from app.labeldesigner import drivers, printer, refusals
from app.labeldesigner.drivers import brother, devices
from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.raster import convert
from tests.helpers import EXAMPLE_FORMDATA, make_client, outcome
from tests.printing.test_ptouch_bluetooth import DEVICE, MAC, PRINT, STATUS, UNNAMED, UsbBrother, brother_on_bluetooth, rows  # noqa: F401
from tests.printing.test_transports import PRINTER_CLASS

FAMILY = ('PT-D410', 'PT-D460BT', 'PT-D610BT', 'PT-E310BT', 'PT-E560BT')
# ESC i a 01 (FLAG_P700_INIT), on the PT-D410 ESC i R 01 (libptouch.c 78-93, 290-304)
MODES = {'PT-D410': b'\x1b\x69\x52\x01', 'PT-D460BT': b'\x1b\x69\x61\x01', 'PT-D610BT': b'\x1b\x69\x61\x01',
         'PT-E310BT': b'\x1b\x69\x61\x01', 'PT-E560BT': b'\x1b\x69\x61\x01'}
# ptdevs[] (libptouch.c 78-93)
PRODUCTS = {'PT-D410': 0x20DF, 'PT-D460BT': 0x20E0, 'PT-D610BT': 0x20E1, 'PT-E310BT': 0x2201, 'PT-E560BT': 0x2203}


@pytest.mark.parametrize('model', FAMILY)
def test_job_is_what_ptouch_print_sends(model):
    # Two labels on 18 mm tape, the first pixel black in the first line,
    # asked to cut each, half cut and chain print, which they are not told
    raster = Raster(model)
    page = rows([b'\x80' + bytes(13), bytes(14)], 112)
    convert(raster, [page, page], 'pt18', rotate=0, compress=True, cut_every=1, half_cut=True, chain=True)
    # Pixel 8 of 128, centered as ptouch-print places 112 pixels: bit 0 of the
    # last byte but one (ptouch-print.c 141-150, 170)
    black = bytes(14) + b'\x01\x00'
    page_bytes = (MODES[model]
                  # Print information without valid flags, 18 mm, 2 lines, n9 = 2 (libptouch.c 248-275)
                  + b'\x1b\x69\x7a\x00\x00\x12\x00' + b'\x02\x00\x00\x00' + b'\x02\x00'
                  # Margins of 0Eh, P-touch software's (libptouch.c 234), directly followed by M 00 (227-239)
                  + b'\x1b\x69\x64\x0e\x00' + b'\x4d\x00'
                  # 16 bytes a line without compression (libptouch.c 441-454)
                  + b'\x47\x10\x00' + black + b'\x47\x10\x00' + bytes(16)
                  # Print with feeding after each copy (libptouch.c 321-334, ptouch-print.c 821-827)
                  + b'\x1a')
    # Invalidate with 100 bytes, initialize, status request (libptouch.c 207-215, 352; ptouch-print.c 728-731)
    assert raster.data == bytes(100) + b'\x1b\x40' + b'\x1b\x69\x53' + page_bytes * 2


@pytest.mark.parametrize('model', FAMILY)
def test_family_is_told_no_cut_chain_quality_or_status_notification(model):
    raster = Raster(model)
    convert(raster, [Image.new('1', (70, 3), 1)] * 3, 'pt12', rotate=0, compress=True, cut_every=3, chain=False)
    for command in (b'\x1b\x69\x4d', b'\x1b\x69\x41', b'\x1b\x69\x4b', b'\x1b\x69\x21', b'\x4d\x02'):
        assert command not in raster.data
    # Each label printed with feeding, the next one in raster mode again
    assert raster.data.count(b'\x1a' + MODES[model]) == 2 and raster.data.endswith(b'\x1a')
    assert drivers.BROTHER.capabilities(model)['qualities'] == ['quality']


@pytest.mark.parametrize('model, tapes', [
    ('PT-D410', ['pt6', 'pt9', 'pt12', 'pt18']), ('PT-D460BT', ['pt6', 'pt9', 'pt12', 'pt18']),
    ('PT-E310BT', ['pt6', 'pt9', 'pt12', 'pt18']), ('PT-D610BT', ['pt6', 'pt9', 'pt12', 'pt18', 'pt24']),
    ('PT-E560BT', ['pt6', 'pt9', 'pt12', 'pt18', 'pt24'])])
def test_tapes_after_the_users_guides(model, tapes):
    # Specifications, Media: up to 18 mm on the PT-D410, PT-D460BT and
    # PT-E310BT, up to 24 mm on the PT-D610BT and PT-E560BT
    assert [label.identifier for label in drivers.BROTHER.labels if drivers.BROTHER.fits(label, model)] == tapes


@pytest.mark.parametrize('model, longest, lines', [('PT-D410', 300, 2126), ('PT-D460BT', 300, 2126), ('PT-D610BT', 300, 2126),
                                                   ('PT-E310BT', 999, 7079), ('PT-E560BT', 999, 7079)])
def test_longest_labels_after_the_users_guides(model, longest, lines):
    # "Labels can be between 30 mm and 300 mm in length" on the PT-D models,
    # a label length of up to 999 mm on the PT-E models
    problem = f'Labels on the {model} can be at most {longest} mm long'
    assert drivers.BROTHER.length_problem(model, lines, False) == ''
    assert drivers.BROTHER.length_problem(model, lines + 1, False) == problem and problem in refusals.MESSAGES
    problem = f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    assert drivers.resolution_problem(model, True) == problem and problem in refusals.MESSAGES


@pytest.mark.parametrize('model', FAMILY)
def test_known_by_their_usb_product_id(tmp_path, monkeypatch, model):
    product = PRODUCTS[model]
    assert drivers.resolve(f'usb://0x04f9:0x{product:04x}/A1', 'QL-800') == (drivers.BROTHER, model)
    assert drivers.resolve(f'usb://0x04F9:0x{product:04X}', 'Unknown') == (drivers.BROTHER, model)
    # Through usblp by the IDs the kernel tells
    monkeypatch.setattr(devices, 'USB_IDS', str(tmp_path / '{name}-{field}'))
    (tmp_path / 'lp1-idVendor').write_text('04f9\n')
    (tmp_path / 'lp1-idProduct').write_text(f'{product:04x}\n')
    assert drivers.resolve('/dev/usb/lp1', 'QL-800') == (drivers.BROTHER, model)
    assert drivers.resolve('file:///dev/usb/lp1', 'QL-800') == (drivers.BROTHER, model)
    # Not a device of another maker, nor one usblp does not tell
    assert drivers.resolve(f'usb://0x1234:0x{product:04x}', 'QL-800') == (drivers.BROTHER, 'QL-800')
    assert drivers.resolve('/dev/usb/lp2', 'QL-800') == (drivers.BROTHER, 'QL-800')


def test_other_brother_printers_are_still_known_by_their_status():
    assert drivers.resolve('usb://0x04f9:0x2042/A1', 'QL-800') == (drivers.BROTHER, 'QL-800')
    assert devices.usb_ids('tcp://192.0.2.1') is None and devices.usb_ids('usb://0x04f9') is None


@pytest.mark.parametrize('model', FAMILY)
def test_status_names_the_model_given(monkeypatch, model):
    # Brother documents no model code of them, their status keeps the model
    monkeypatch.setattr(brother.usb, 'get_printer', lambda device: UsbBrother(UNNAMED))
    state = drivers.BROTHER.status('/dev/usb/lp0', model)
    assert (state['model'], state['media_width'], state['errors']) == (model, 12, [])


def test_found_on_usb_by_their_product_id(tmp_path, monkeypatch):
    found = 'usb://0x04f9:0x20e1/A1'
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(devices, 'found_usb_printers', lambda: ([found], []))
    monkeypatch.setattr(devices, 'usb_device_id', lambda device, connect=True: '')
    monkeypatch.setattr(brother.usb, 'get_printer', lambda device: UsbBrother(UNNAMED))
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    first = client.get(STATUS).get_json()['printers'][0]
    assert (first['path'], first['model'], first['errors'], first['label_size']) == (found, 'PT-D610BT', [], 'pt12')


@pytest.mark.parametrize('name, model', [('PT-D460BT1234', 'PT-D460BT'), ('PT-D610BT0042', 'PT-D610BT'),
                                         ('PT-E310BT1234', None), ('PT-E560BT', None), ('PT-D410', None)])
def test_known_by_their_names_on_classic_bluetooth(name, model):
    # PT-D460BT**** and PT-D610BT**** in their Quick Setup Guides, Brother
    # names none of the PT-E models, the PT-D410 has no Bluetooth
    device = {'address': MAC, 'name': name, 'le': False, 'services': [], 'classic': True, 'device_class': PRINTER_CLASS}
    assert drivers.BROTHER.found_on_bluetooth(device) == ((DEVICE, model) if model else None)


def test_pt_d410_is_not_reached_on_bluetooth():
    with pytest.raises(FileNotFoundError):
        drivers.BROTHER.status(DEVICE, 'PT-D410')


@pytest.mark.parametrize('model', ['PT-D460BT', 'PT-E560BT'])
def test_printing_on_bluetooth(tmp_path, brother_on_bluetooth, model):  # noqa: F811
    fake = brother_on_bluetooth(UNNAMED, 12)
    client = make_client(tmp_path, model=model, printer=DEVICE)
    state = client.get(STATUS).get_json()['printers'][0]
    assert (state['model'], state['errors'], state['label_size']) == (model, [], 'pt12')
    response = client.post(PRINT, data=dict(EXAMPLE_FORMDATA, label_size='pt12'))
    assert outcome(client, response) == ('printed', '')
    [job] = fake.jobs
    assert job.startswith(bytes(100) + b'\x1b\x40\x1b\x69\x53\x1b\x69\x61\x01\x1b\x69\x7a\x00\x00\x0c\x00')
    assert job.endswith(b'\x1a') and b'\x1b\x69\x4d' not in job
    assert set(fake.addresses) == {(MAC, 1)}
