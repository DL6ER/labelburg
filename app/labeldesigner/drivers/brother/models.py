"""The Brother printers that speak the raster protocol, and what each of them
can do, after the model table of brother_ql by Philipp Klaus."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    """A printer model: the bytes of a raster line across its print head, the
    dots its print is moved further off the right edge, the commands it takes
    (mode setting, cutting, expanded mode, compression), whether it prints
    red, the zero bytes that clear its command buffer, and the codes it gives
    in its status (series and model). Its longest label
    has max_length_dots lines at dpi. It prints speed mm/s, as Brother's
    data sheets give it. With bluetooth it is reached on bt:// too."""
    identifier: str
    speed: int = 0
    max_length_dots: int = 11811
    dpi: int = 300
    number_bytes_per_row: int = 90
    additional_offset_r: int = 0
    mode_setting: bool = True
    cutting: bool = True
    expanded_mode: bool = True
    compression_support: bool = True
    two_color: bool = False
    num_invalidate_bytes: int = 200
    series_code: int = 0xFFFF
    model_code: int = 0xFFFF
    bluetooth: bool = False


MODELS = {model.identifier: model for model in (
    Model('QL-500', speed=90, mode_setting=False, cutting=False, expanded_mode=False, compression_support=False,
          series_code=0x30, model_code=0x4F),
    Model('QL-550', speed=90, mode_setting=False, compression_support=False, series_code=0x30, model_code=0x4F),
    Model('QL-560', speed=90, mode_setting=False, compression_support=False, series_code=0x34, model_code=0x31),
    Model('QL-570', speed=110, mode_setting=False, compression_support=False, series_code=0x34, model_code=0x32),
    Model('QL-580N', speed=110, series_code=0x34, model_code=0x33),
    Model('QL-600', speed=71, series_code=0x34, model_code=0x47),
    Model('QL-650TD', speed=90, series_code=0x30, model_code=0x51),
    Model('QL-700', speed=150, mode_setting=False, compression_support=False, series_code=0x34, model_code=0x35),
    Model('QL-710W', speed=150, series_code=0x34, model_code=0x36),
    Model('QL-720NW', speed=150, series_code=0x34, model_code=0x37),
    Model('QL-800', speed=148, compression_support=False, two_color=True, num_invalidate_bytes=400,
          series_code=0x34, model_code=0x38),
    Model('QL-810W', speed=176, two_color=True, num_invalidate_bytes=400, series_code=0x34, model_code=0x39),
    Model('QL-820NWB', speed=176, two_color=True, num_invalidate_bytes=400, series_code=0x34, model_code=0x41),
    Model('QL-1050', speed=110, max_length_dots=35433, number_bytes_per_row=162, additional_offset_r=44, series_code=0x30, model_code=0x50),
    Model('QL-1060N', speed=110, max_length_dots=35433, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x34),
    Model('QL-1100', speed=110, max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x43),
    Model('QL-1110NWB', speed=110, max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x44),
    Model('QL-1115NWB', speed=110, max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x45),
    # Model codes after Brother's raster command reference of the PT-E550W,
    # PT-P750W and PT-P710BT: 66h is the PT-E550W, 68h the PT-P750W
    # https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
    Model('PT-E550W', speed=30, max_length_dots=14172, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x66),
    Model('PT-P700', speed=30, max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x67),
    Model('PT-P750W', speed=30, max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x68),
    # "o" is 6Fh, not the 69h of the PT-P900 reference; Brother's index links it as 6F30
    # https://support.brother.com/g/s/es/dev/en/command/reference/index.html
    Model('PT-P900W', speed=60, max_length_dots=28346, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x6F),
    Model('PT-P950NW', speed=60, max_length_dots=28346, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x70),
)}

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi, their reference has no 600 dpi.
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W')

# Black and red on the QL-800 series, after Brother's data sheets
RED_SPEED = 24
# Seconds a label takes besides its print, to feed and cut it and for the
# status messages: a QL-810W printed a 29 x 90 mm label in 1.8 s printing
# fast, 3.0 s for quality. P-touch printers feed tape before the print.
QL_BESIDES = 1.3
PTOUCH_BESIDES = 2.0
QUALITY_SLOWER = 1.7

# P-touch printers with a half cutter, the PT-P700 has none
HALF_CUTTERS = ('PT-E550W', 'PT-P750W', 'PT-P900W', 'PT-P950NW')

# Brother's raster command references: a print of at least 12.7 mm (150
# dots) on QL printers, 4.4 mm (31 dots at 180 dpi) on TZe tape
MIN_PRINT_LENGTH = 12.7
MIN_PTOUCH_PRINT_LENGTH = 4.4


def longest_label(model: str) -> int:
    """The longest label the model prints, in whole mm."""
    spec = MODELS[model]
    return round(spec.max_length_dots / spec.dpi * 25.4)


def length_problem(model: str) -> str:
    """Why a label is too long for the model."""
    return f'Labels on the {model} can be at most {longest_label(model)} mm long'


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''
