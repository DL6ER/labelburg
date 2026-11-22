"""The Brother printers that speak the raster protocol, and what each of them
can do, after the model table of brother_ql by Philipp Klaus."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    """A printer model: the bytes of a raster line across its print head, the
    dots its print is moved further off the right edge, the commands it takes
    (mode setting, cutting, cutting after a number of labels, expanded mode,
    compression), whether it is told to send its status while printing,
    whether it prints red, the zero bytes that clear its command buffer, and
    the codes it gives in its status (series and model). Its longest label
    has max_length_dots lines at dpi. It prints speed mm/s, as Brother's
    data sheets give it. With bluetooth it is reached on bt:// too.
    mode_command selects raster mode, usb_product (vendor 04F9h) tells the
    model on USB where its status does not, and a model of the PT-D460BT
    family is sent the jobs ptouch-print sends it, see Raster.add_alone()."""
    identifier: str
    speed: int = 0
    max_length_dots: int = 11811
    dpi: int = 300
    number_bytes_per_row: int = 90
    additional_offset_r: int = 0
    mode_setting: bool = True
    cutting: bool = True
    cut_every: bool = True
    expanded_mode: bool = True
    compression_support: bool = True
    status_notification: bool = False
    two_color: bool = False
    num_invalidate_bytes: int = 200
    series_code: int = 0xFFFF
    model_code: int = 0xFFFF
    bluetooth: bool = False
    mode_command: bytes = b'\x1b\x69\x61\x01'
    usb_product: int | None = None
    d460bt_family: bool = False


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
    # PT-P750W and PT-P710BT: 66h is the PT-E550W, 68h the PT-P750W. TZe
    # tape is at most 1000 mm long there and in that of the PT-P900 series.
    # https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
    Model('PT-E550W', speed=30, max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x66),
    Model('PT-P700', speed=30, max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x67),
    Model('PT-P750W', speed=30, max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x68),
    # The same reference names no model code of the PT-P710BT. It takes no
    # ESC i A, and Brother's driver turns its status while printing off.
    Model('PT-P710BT', speed=20, max_length_dots=7086, dpi=180, number_bytes_per_row=16, cut_every=False,
          status_notification=True, num_invalidate_bytes=100, series_code=0x30, bluetooth=True),
    # "q" (71h) on USB as 2083h in the reference of the PT-P900 series (pp. 24, 49)
    Model('PT-P900', speed=60, max_length_dots=14173, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x71),
    Model('PT-P900W', speed=60, max_length_dots=14173, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x69),
    Model('PT-P950NW', speed=60, max_length_dots=14173, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x70),
    # After Brother's raster command reference of the PT-P900 series: model
    # code 78h, it sends its status while printing only when told so
    # https://download.brother.com/welcome/docp100407/cv_ptp900_eng_raster_102.pdf
    Model('PT-P910BT', speed=20, max_length_dots=14173, dpi=360, number_bytes_per_row=70, status_notification=True,
          series_code=0x30, model_code=0x78, bluetooth=True),
    # The PT-D460BT family after ptouch-print's libptouch.c, Brother names no model codes;
    # speeds and longest labels after Brother's User's Guides
    Model('PT-D410', speed=20, max_length_dots=2126, dpi=180, number_bytes_per_row=16, cutting=False, expanded_mode=False,
          num_invalidate_bytes=100, mode_command=b'\x1b\x69\x52\x01', usb_product=0x20DF, d460bt_family=True),
    Model('PT-D460BT', speed=30, max_length_dots=2126, dpi=180, number_bytes_per_row=16, cutting=False, expanded_mode=False,
          num_invalidate_bytes=100, bluetooth=True, usb_product=0x20E0, d460bt_family=True),
    Model('PT-D610BT', speed=30, max_length_dots=2126, dpi=180, number_bytes_per_row=16, cutting=False, expanded_mode=False,
          num_invalidate_bytes=100, bluetooth=True, usb_product=0x20E1, d460bt_family=True),
    Model('PT-E310BT', speed=20, max_length_dots=7079, dpi=180, number_bytes_per_row=16, cutting=False, expanded_mode=False,
          num_invalidate_bytes=100, bluetooth=True, usb_product=0x2201, d460bt_family=True),
    Model('PT-E560BT', speed=30, max_length_dots=7079, dpi=180, number_bytes_per_row=16, cutting=False, expanded_mode=False,
          num_invalidate_bytes=100, bluetooth=True, usb_product=0x2203, d460bt_family=True),
)}

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi, their reference has no 600 dpi, nor
# has the PT-P910BT high-resolution printing, nor the PT-D460BT family.
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W', 'PT-P710BT', 'PT-P910BT',
                          *(model for model, spec in MODELS.items() if spec.d460bt_family))

# Black and red on the QL-800 series, after Brother's data sheets
RED_SPEED = 24
# Seconds a label takes besides its print, to feed and cut it and for the
# status messages: a QL-810W printed a 29 x 90 mm label in 1.8 s printing
# fast, 3.0 s for quality. P-touch printers feed tape before the print.
QL_BESIDES = 1.3
PTOUCH_BESIDES = 2.0
QUALITY_SLOWER = 1.7

# P-touch printers with a half cutter, the PT-P700 and the PT-P710BT have none
HALF_CUTTERS = ('PT-E550W', 'PT-P750W', 'PT-P900', 'PT-P900W', 'PT-P950NW', 'PT-P910BT')
# Automatic cutters not told to cut, ptouch-print sends their family no cut command
UNUSED_CUTTERS = ('PT-D610BT', 'PT-E560BT')
# Not found on Bluetooth by name, Brother's guides name none
UNNAMED_ON_BLUETOOTH = ('PT-E310BT', 'PT-E560BT')

# Brother's raster command references: a print of at least 12.7 mm (150
# dots) on QL printers, 4.4 mm (31 dots at 180 dpi) on TZe tape
MIN_PRINT_LENGTH = 12.7
MIN_PTOUCH_PRINT_LENGTH = 4.4
# Heat-shrink tube takes at most 500 mm, after the references of the P-touch printers
TUBE_LENGTH = 500
TUBE_TOO_LONG = f'Labels on heat-shrink tube can be at most {TUBE_LENGTH} mm long'


def longest_label(model: str) -> int:
    """The longest label the model prints, in whole mm."""
    spec = MODELS[model]
    return round(spec.max_length_dots / spec.dpi * 25.4)


def tube_lines(model: str) -> int:
    """The most lines of a label on heat-shrink tube, see TUBE_LENGTH."""
    return round(TUBE_LENGTH / 25.4 * MODELS[model].dpi)


def length_problem(model: str) -> str:
    """Why a label is too long for the model."""
    return f'Labels on the {model} can be at most {longest_label(model)} mm long'


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''
