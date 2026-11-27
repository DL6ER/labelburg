"""The status of a Brother raster printer, the 32 bytes it answers "ESC i S"
with over USB and SNMP, after the reader of brother_ql by Philipp Klaus and
Brother's raster command references."""

from .models import MODELS

HEADER = b'\x80\x20\x42'
LENGTH = 32

ERRORS_1 = {
    0: 'No media when printing',
    1: 'End of media (die-cut size only)',
    2: 'Tape cutter jam',
    3: 'Weak batteries',
    4: 'Main unit in use (QL-560/650TD/1050)',
    5: 'Printer turned off',
    6: 'High-voltage adapter (not used)',
    7: "Fan doesn't work (QL-1050/1060N)",
}

ERRORS_2 = {
    0: 'Replace media error',
    1: 'Expansion buffer full error',
    2: 'Transmission / Communication error',
    3: 'Communication buffer full error (not used)',
    4: 'Cover opened while printing (Except QL-500)',
    5: 'Cancel key (not used) or Overheating error (PT-E550W/P750W/P710BT)',
    6: 'Media cannot be fed (also when the media end is detected)',
    7: 'System error',
}

COVER_OPEN = 'The cover is open'
NO_LABELS = 'There are no labels in the printer'
SWITCHED_OFF = 'The printer is switched off'
TOO_HOT = 'The printer is too hot, let it cool down'
FAULT = 'The printer has a fault, switch it off and on again'
# The printer keeps the job and prints it once the labels are fed
FEED_ERROR = ('The labels cannot be fed. Feed them into the slot (or insert a new roll) and close the cover, '
              'then the printer prints the waiting label by itself. Do not print it again')
# The errors of ERRORS_1 and ERRORS_2 as the label designer shows them
ERROR_MESSAGES = {
    'No media when printing': NO_LABELS,
    'End of media (die-cut size only)': 'The labels are used up',
    'Tape cutter jam': 'The cutter is jammed',
    'Weak batteries': 'The battery is low',
    'Main unit in use (QL-560/650TD/1050)': 'The printer is in use',
    'Printer turned off': SWITCHED_OFF,
    'High-voltage adapter (not used)': 'The power adapter does not fit the printer',
    "Fan doesn't work (QL-1050/1060N)": 'The fan of the printer does not work',
    'Replace media error': 'The loaded labels do not fit the job',
    'Expansion buffer full error': 'The memory of the printer is full',
    'Transmission / Communication error': 'The label did not reach the printer completely',
    'Communication buffer full error (not used)': 'The memory of the printer is full',
    'Cover opened while printing (Except QL-500)': COVER_OPEN,
    'Cancel key (not used) or Overheating error (PT-E550W/P750W/P710BT)': TOO_HOT,
    'Media cannot be fed (also when the media end is detected)': FEED_ERROR,
    'System error': FAULT,
}
UNKNOWN_ERROR = 'The printer reports an error'
# Errors that say what to do, or go away once fixed, e.g. the cover closed
SAY_WHAT_TO_DO = {COVER_OPEN, SWITCHED_OFF, TOO_HOT, FEED_ERROR, FAULT}
# Errors after which the printer keeps the job and prints it once they are
# fixed, with what to tell when the job is given up
KEEPS_JOB = {'Cover opened while printing (Except QL-500)': 'The label prints once the cover is closed, do not print it again'}


def plain_errors(errors: list) -> list:
    """The errors of a status in plain words, each once."""
    return list(dict.fromkeys(ERROR_MESSAGES.get(error, UNKNOWN_ERROR) for error in errors))


# Fabric, FLe, flexible ID and satin tape after Brother's raster command
# reference of the PT-P900 series
MEDIA_TYPES = {
    0x00: ('No media', 'No media'),
    0x01: ('Laminated tape', 'TZe'),
    0x03: ('Non-laminated type', 'TZe'),
    0x04: ('Fabric tape', 'TZe'),
    0x11: ('Heat-Shrink Tube (HS 2:1)', 'TZe'),
    0x13: ('FLe tape', 'FLe'),
    0x14: ('Flexible ID tape', 'TZe'),
    0x15: ('Satin tape', 'TZe'),
    0x17: ('Heat-Shrink Tube (HS 3:1)', 'TZe'),
    0x0A: ('Continuous length tape', 'DK'),
    0x0B: ('Die-cut labels', 'DK'),
    0x4A: ('Continuous length tape', 'RD'),
    0x4B: ('Die-cut labels', 'RD'),
    0xFF: ('Incompatible tape', 'Incompatible'),
}

# Heat-shrink tube, HS 2:1 and 3:1
TUBES = {MEDIA_TYPES[0x11][0], MEDIA_TYPES[0x17][0]}

TAPE_COLORS = {
    0x01: 'White', 0x02: 'Other', 0x03: 'Clear', 0x04: 'Red', 0x05: 'Blue', 0x06: 'Yellow', 0x07: 'Green',
    0x08: 'Black', 0x09: 'Clear(White text)', 0x20: 'Matte White', 0x21: 'Matte Clear', 0x22: 'Matte Silver',
    0x23: 'Satin Gold', 0x24: 'Satin Silver', 0x30: 'Blue(D)', 0x31: 'Red(D)', 0x40: 'Fluorescent Orange',
    0x41: 'Fluorescent Yellow', 0x50: 'Berry Pink(S)', 0x51: 'Light Gray(S)', 0x52: 'Lime Green(S)',
    0x60: 'Yellow(F)', 0x61: 'Pink(F)', 0x62: 'Blue(F)', 0x70: 'White(Heat-shrink Tube)', 0x90: 'White(Flex. ID)',
    0x91: 'Yellow(Flex. ID)', 0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}

TEXT_COLORS = {
    0x01: 'White', 0x02: 'Other', 0x04: 'Red', 0x05: 'Blue', 0x08: 'Black', 0x0A: 'Gold', 0x62: 'Blue(F)',
    0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}

# TZe tape (byte 24) and its print (byte 25) as shown, after tables (8) and (9) of the status in
# the PT-E550W and PT-P900 raster command references. "Other", cleaning, stencil and incompatible have none.
CLEAR = '#ffffff00'
TAPE_SHOWN = {
    0x01: ('white', '#ffffff'),
    0x03: ('clear', CLEAR),
    0x04: ('red', '#d22630'),
    0x05: ('blue', '#2a6ebb'),
    0x06: ('yellow', '#ffd400'),
    0x07: ('green', '#2e9e48'),
    0x08: ('black', '#000000'),
    0x09: ('clear', CLEAR),  # Clear (white text)
    0x20: ('matte white', '#f6f6f3'),
    0x21: ('matte clear', CLEAR),
    0x22: ('matte silver', '#c2c5c8'),
    0x23: ('satin gold', '#c8a64b'),
    0x24: ('satin silver', '#cfd1d3'),
    0x30: ('blue', '#1d4f9c'),  # Blue (D), e.g. TZe-535
    0x31: ('red', '#b5172b'),  # Red (D), TZe-435
    0x40: ('fluorescent orange', '#ff7a21'),
    0x41: ('fluorescent yellow', '#e9f53b'),
    0x50: ('berry pink', '#c94f86'),  # Berry Pink (S), TZe-MQP35
    0x51: ('light gray', '#a9adb1'),  # Light Gray (S), TZe-MQL35
    0x52: ('lime green', '#7fbf3f'),  # Lime Green (S), TZe-MQG35
    0x60: ('yellow', '#ffe14a'),  # Yellow (F)
    0x61: ('pink', '#f4a7c3'),  # Pink (F)
    0x62: ('blue', '#6aa5dc'),  # Blue (F)
    0x70: ('white', '#ffffff'),  # White (Heat-shrink Tube)
    0x90: ('white', '#ffffff'),  # White (Flex. ID)
    0x91: ('yellow', '#ffd400'),  # Yellow (Flex. ID)
}

INK_SHOWN = {
    0x01: ('white', '#ffffff'),
    0x04: ('red', '#d22630'),
    0x05: ('blue', '#1f4fa8'),
    0x08: ('black', '#000000'),
    0x0A: ('gold', '#d4af37'),
    0x62: ('blue', '#1f4fa8'),  # Blue (F)
}


def shown_colors(tape_code: int, ink_code: int) -> dict:
    """The colors of TZe tape and of its print by their codes, each a plain
    name and the color shown, #ffffff00 for clear tape. Both or neither:
    the label designer shows the print on the tape in them."""
    if tape_code not in TAPE_SHOWN or ink_code not in INK_SHOWN:
        return {}
    return {part: dict(zip(('name', 'hex'), table[code]))
            for part, table, code in (('tape', TAPE_SHOWN, tape_code), ('ink', INK_SHOWN, ink_code))}


STATUS_TYPES = {
    0x00: 'Reply to status request',
    0x01: 'Printing completed',
    0x02: 'Error occurred',
    0x03: 'Exit IF mode',
    0x04: 'Turned off',
    0x05: 'Notification',
    0x06: 'Phase change',
    0xF0: 'Settings report',
}

PHASE_TYPES = {
    0x00: 'Waiting to receive',
    0x01: 'Printing state',
}


# Byte 25 of the status, reserved in Brother's reference: the QL-800 series
# sets bit 7 for a black and red roll and reports 0x01 for a black one. Seen
# on two QL-800 and a QL-810W; other values tell nothing.
RED_ROLL_BYTE = 25


def red_roll(data: bytes, model: str, media_width: int, media_category: str) -> bool | None:
    """Whether the roll in a printer that prints red is a black and red one,
    None where the status does not tell, e.g., for other printers."""
    if model not in MODELS or not MODELS[model].two_color or not media_width or media_category == 'TZe':
        return None
    color = data[RED_ROLL_BYTE]
    if color & 0x80:
        return True
    return False if color == 0x01 else None


class StatusError(ValueError):
    """An answer that is no status the printer could have sent."""


def parse_status(data: bytes) -> dict:
    """The status in an answer of the printer: its model, the loaded media,
    the errors, the status and phase type. Raises StatusError for an answer
    that is no status or has values that are not known."""
    data = bytes(data)
    if len(data) < LENGTH or not data.startswith(HEADER):
        raise StatusError(f'No status: {data.hex()}')
    errors = [message for bit, message in ERRORS_1.items() if data[8] & (1 << bit)]
    errors += [message for bit, message in ERRORS_2.items() if data[9] & (1 << bit)]
    if data[11] not in MEDIA_TYPES:
        raise StatusError(f'Unknown media type {data[11]:02X}')
    if data[18] not in STATUS_TYPES:
        raise StatusError(f'Unknown status type {data[18]:02X}')
    media_type, media_category = MEDIA_TYPES[data[11]]
    tape_color = text_color = ''
    colors = {}
    if media_category == 'TZe':
        # A code the references do not list names no color
        tape_color, text_color = TAPE_COLORS.get(data[24], ''), TEXT_COLORS.get(data[25], '')
        colors = shown_colors(data[24], data[25])
    series_code, model_code = data[3], data[4]
    model = next((model.identifier for model in MODELS.values()
                  if model.series_code == series_code and model.model_code == model_code), 'Unknown')
    return {
        'series_code': series_code,
        'model_code': model_code,
        'model': model,
        'status_type': STATUS_TYPES[data[18]],
        'status_code': data[18],
        # An unknown phase stays a number
        'phase_type': PHASE_TYPES.get(data[19], data[19]),
        'media_type': media_type,
        'media_category': media_category,
        'tape_color': tape_color,
        'text_color': text_color,
        'colors': colors,
        'media_width': data[10],
        'media_length': data[17],
        'setting': data[30] if data[18] == 0xF0 else None,
        'errors': errors,
        'red_roll': red_roll(data, model, data[10], media_category),
    }
