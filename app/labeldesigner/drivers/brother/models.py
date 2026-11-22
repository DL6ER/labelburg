"""The Brother printers that speak the raster protocol, and what each of them
can do, after the model table of brother_ql by Philipp Klaus."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    """A printer model: the bytes of a raster line across its print head, the
    dots its print is moved further off the right edge, the commands it takes
    (mode setting, cutting, expanded mode, compression), whether it prints
    red, the zero bytes that clear its command buffer, and the codes it gives
    in its status (series and model). Its longest label
    has max_length_dots lines at dpi. It prints speed mm/s, as Brother's
    data sheets give it. quality_flag: whether it takes the flag of print
    quality in its print information. status_notification: whether it sends
    its status while printing only when asked to (ESC i !) and is switched
    back to its default mode after a job (ESC i a FFh). optional_cutter:
    whether its cutter is an optional unit."""
    identifier: str
    speed: float = 0
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
    quality_flag: bool = True
    status_notification: bool = False
    optional_cutter: bool = False


# TD models by series and dpi: model code, series 35h (TD2000 p. 22, TD2300
# and TD4000 5.2.1), and mm/s as the user's guides below give it
TD2000 = 'https://download.brother.com/welcome/docp000750/cv_td2000_eng_raster_101.pdf'
TD2300 = 'https://download.brother.com/welcome/docp100812/cv_td2300d_eng_raster_100.pdf'
TD4000 = 'https://download.brother.com/welcome/docp100429/cv_td4000d_eng_raster_103.pdf'
TD2000_GUIDE = 'https://download.brother.com/welcome/docp000737/cv_td2130n_ukeng_usr_04.pdf'
TD2125_GUIDE = 'https://download.brother.com/welcome/docp100733/td2020a_2125_2135_ukeng_ug_02.pdf'
TD2300_GUIDE = 'https://support.brother.com/g/s/es/htmldoc/labelprint/td2310d/uken/PDF/PDF.pdf'
TD4410_GUIDE = ('https://support.brother.com/g/s/es/htmldoc/labelprint/td4410d/uk/html/'
                'GUID-7B885ADC-5C94-49D6-B300-F3012998934B_76.html')
TD4550_GUIDE = ('https://support.brother.com/g/s/es/htmldoc/labelprint/td4550dnwb/us/manual/html/'
                'GUID-7B885ADC-5C94-49D6-B300-F3012998934B_107.html')
TD4215_GUIDE = 'https://support.brother.com/g/s/es/htmldoc/labelprint/td4215d/usen/PDF/PDF.pdf'
TD_SERIES = {
    'td2': {203: {'TD-2020': (0x33, 152.4), 'TD-2120N': (0x35, 152.4), 'TD-2125N': (0x45, 152.4),
                  'TD-2125NWB': (0x46, 152.4)},
            300: {'TD-2130N': (0x36, 152.4), 'TD-2135N': (0x47, 152.4), 'TD-2135NWB': (0x48, 152.4)}},
    'td23': {203: {'TD-2310D': (0x54, 152.4), 'TD-2320D': (0x56, 152.4), 'TD-2320DSA': (0x5A, 152.4),
                   'TD-2350D': (0x62, 152.4), 'TD-2350DSA': (0x66, 152.4)},
             300: {'TD-2310D-300': (0x55, 152.4), 'TD-2320D-300': (0x57, 152.4), 'TD-2320DSA-300': (0x61, 152.4),
                   'TD-2350D-300': (0x63, 152.4), 'TD-2350DSA-300': (0x67, 152.4)}},
    'td4': {203: {'TD-4210D': (0x43, 127), 'TD-4215D': (0x6A, 127), 'TD-4410D': (0x37, 203.2),
                  'TD-4415D': (0x6B, 203), 'TD-4420DN': (0x38, 203.2), 'TD-4425DN': (0x6D, 203),
                  'TD-4455DNWB': (0x6F, 203)},
            300: {'TD-4520DN': (0x41, 152.4), 'TD-4525DN': (0x6E, 152.4), 'TD-4550DNWB': (0x42, 152.4),
                  'TD-4555DNWB': (0x70, 152.4)}},
}
SHOWN_AS = {model: model.removesuffix('-300') for model in TD_SERIES['td23'][300]}
# Bytes of a raster line, the pins of the print head (TD2000 2.3.5, TD2300
# 4.3.5, TD4000 4.3.5), and the longest print of a continuous roll in lines
# (TD2000 2.3.4: 1000 mm, TD2300 and TD4000 4.3.4: 3000 mm)
TD_ROW_BYTES = {'td2': {203: 56, 300: 84}, 'td23': {203: 59, 300: 87}, 'td4': {203: 104, 300: 160}}
TD_LONGEST = {'td2': {203: 7992, 300: 11811}, 'td23': {203: 23977, 300: 35433}, 'td4': {203: 23977, 300: 35433}}
# Zero bytes that clear the command buffer: 200 (TD2000 2.1), 661 (TD2300,
# NULL), 661 or 350 on the TD-4D models (TD4000, NULL)
TD_INVALIDATE = {'td2': 200, 'td23': 661, 'td4': 661}
TD4_INVALIDATE_350 = ('TD-4410D', 'TD-4420DN', 'TD-4520DN', 'TD-4550DNWB', 'TD-4210D')


def _td_models() -> tuple:
    """The models of TD_SERIES, of the TD-2000 series without cut
    commands, with a quality flag and status messages unasked."""
    return tuple(
        Model(model, speed=speed, max_length_dots=TD_LONGEST[series][dpi], dpi=dpi,
              number_bytes_per_row=TD_ROW_BYTES[series][dpi], cutting=series != 'td2', expanded_mode=series != 'td2',
              quality_flag=series == 'td2', status_notification=series != 'td2', optional_cutter=series != 'td2',
              num_invalidate_bytes=350 if model in TD4_INVALIDATE_350 else TD_INVALIDATE[series],
              series_code=0x35, model_code=code)
        for series, resolutions in TD_SERIES.items() for dpi, models in resolutions.items()
        for model, (code, speed) in models.items())


TD_MODELS = _td_models()
# Product IDs of the TD-2000 series on USB, vendor 04F9h (TD2000, Appendix A)
TD_PRODUCT_IDS = {0x2055: 'TD-2020', 0x2057: 'TD-2120N', 0x2058: 'TD-2130N', 0x20F7: 'TD-2125N', 0x20F8: 'TD-2125NWB',
                  0x20F9: 'TD-2135N', 0x20FA: 'TD-2135NWB'}


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
) + TD_MODELS}

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi, their reference has no 600 dpi.
# The TD printers print at 203 or 300 dpi (TD2000 2.3.1, TD2300 and TD4000
# 4.3.1).
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W',
                          *(model.identifier for model in TD_MODELS))

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


# The resolution Brother adds to the name of a TD-2300D model, e.g. "Brother
# TD-2350D (300 dpi)" (TD2300_GUIDE, LabelCLT; TD2300 4.2.1 and 5.2.1)
NAMED_RESOLUTION = re.compile(r'\s*\((203|300)\s*dpi\)$', re.IGNORECASE)


def named(name: str) -> tuple[str | None, bool]:
    """The model a name like "Brother QL-810W" or "Brother TD-2350D (300
    dpi)" names, None for other devices, and whether it tells which of the
    models of one name it is, see SHOWN_AS."""
    model = (name or '').removeprefix('Brother ').strip()
    resolution = NAMED_RESOLUTION.search(model)
    if resolution:
        model = model[:resolution.start()]
        if resolution[1] == '300' and f'{model}-300' in MODELS:
            model += '-300'
    if model not in MODELS:
        return None, False
    return model, bool(resolution) or model not in SHOWN_AS.values()


def longest_label(model: str) -> int:
    """The longest label the model prints, in whole mm."""
    spec = MODELS[model]
    return round(spec.max_length_dots / spec.dpi * 25.4)


def length_problem(model: str) -> str:
    """Why a label is too long for the model."""
    return f'Labels on the {SHOWN_AS.get(model, model)} can be at most {longest_label(model)} mm long'


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {SHOWN_AS.get(model, model)} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''
