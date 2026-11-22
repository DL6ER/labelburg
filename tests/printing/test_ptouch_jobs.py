"""The commands of P-touch jobs as Brother's raster command references give
them, the PT-E550W/P750W/P710BT reference and that of the PT-P900 series:
https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
https://download.brother.com/welcome/docp100407/cv_ptp900_eng_raster_102.pdf"""
import pytest
from PIL import Image

from app.labeldesigner import refusals
from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.raster import convert

TUBE_TOO_LONG = 'Labels on heat-shrink tube can be at most 500 mm long'


def expanded_modes(data: bytes) -> list[int]:
    """The parameters of each ESC i K of a job."""
    return [data[at + 3] for at in range(len(data)) if data.startswith(b'\x1b\x69\x4b', at)]


@pytest.mark.parametrize('half_cut, flags', [(False, 0x48), (True, 0x4c)])
def test_pt_p900w_prints_at_high_resolution_in_bit_6(half_cut, flags):
    # High-resolution printing is bit 6 of ESC i K (p. 36), no chain printing
    # bit 3 and half cut bit 2; twice the lines along the tape
    raster = Raster('PT-P900W')
    convert(raster, [Image.new('1', (2 * 454, 4), 1)], 'pt36', rotate=0, dpi_600=True, half_cut=half_cut, compress=True)
    assert expanded_modes(raster.data) == [flags]
    assert b'\x1b\x69\x7a\xcc\x00\x24\x00\x04\x00\x00\x00\x00\x00' in raster.data


def test_pt_p900w_prints_normally_without_bit_6():
    raster = Raster('PT-P900W')
    convert(raster, [Image.new('1', (454, 4), 1)], 'pt36', rotate=0, half_cut=False)
    assert expanded_modes(raster.data) == [0x08]


def test_pt_p700_is_not_told_to_half_cut():
    # It has no half cutter: no chain printing alone, where it was told 0Ch
    raster = Raster('PT-P700')
    convert(raster, [Image.new('1', (70, 2), 1)], 'pt12', rotate=0, half_cut=True)
    assert expanded_modes(raster.data) == [0x08]


@pytest.mark.parametrize('model, label_size, width, longest', [
    # 500 mm of heat-shrink tube, 3543 lines at 180 dpi (p. 18), 7087 at 360 dpi (p. 18)
    ('PT-P710BT', 'pt12', 70, 3543), ('PT-E550W', 'pt12', 70, 3543), ('PT-P900W', 'pt36', 454, 7087)])
def test_labels_on_heat_shrink_tube_are_at_most_500_mm_long(model, label_size, width, longest):
    convert(Raster(model), [Image.new('1', (width, longest), 1)], label_size, rotate=0, tube=True)
    with pytest.raises(ValueError, match=f'^{TUBE_TOO_LONG}$'):
        convert(Raster(model), [Image.new('1', (width, longest + 1), 1)], label_size, rotate=0, tube=True)
    # On TZe tape up to 1000 mm
    convert(Raster(model), [Image.new('1', (width, longest + 1), 1)], label_size, rotate=0)
    assert TUBE_TOO_LONG in refusals.MESSAGES
