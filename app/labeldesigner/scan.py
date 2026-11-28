"""Values for the blanks of a label from a text read by a barcode scanner."""
import quopri
import re

from .label import MAX_TEXT_LENGTH, check_blank_choice

NOTHING_SCANNED = 'Nothing was scanned'
NO_BLANKS = 'The label has no blank for the scanned code.'
SEVERAL_BLANKS = 'The label has several blanks and none named code.'
NO_CONTACT_MATCH = "None of the contact's details fits a blank of the label."
WRONG_LAYOUT = 'The scanner is set to another keyboard layout than this computer'
INCOMPLETE = 'The code came in incomplete, scan it again'
NOT_TEXT = 'The code has to be text'
MESSAGES = (NOTHING_SCANNED, NO_BLANKS, SEVERAL_BLANKS, NO_CONTACT_MATCH, WRONG_LAYOUT, INCOMPLETE, NOT_TEXT)

CONTACT_FIELDS = {
    'name': ('Name',),
    'first_name': ('First name', 'Vorname'),
    'last_name': ('Last name', 'Nachname'),
    'organization': ('Company', 'Organization', 'Firma'),
    'street': ('Street', 'Strasse', 'Straße'),
    'postcode': ('Postcode', 'ZIP', 'PLZ'),
    'city': ('City', 'Ort', 'Stadt'),
    'country': ('Country', 'Land'),
    'phone': ('Phone', 'Telefon'),
    'email': ('Email', 'E-Mail'),
    'address': ('Address', 'Adresse', 'Anschrift'),
}

# A German scanner on a US computer types ":" as ">" and "/" as "&", a US
# scanner on a German computer ":" as "Ö" and "/" as "-"
_WRONG_LAYOUT_PREFIXES = ('https>&&', 'http>&&', 'httpsö--', 'httpö--', 'begin>vcard', 'beginövcard', 'mecard>', 'mecardö')


def blank_key(name: str) -> str:
    return re.sub(r'[_\- ]+', ' ', name.casefold()).strip()


def _contact_blank(name: str) -> bool:
    return any(blank_key(name) == blank_key(blank) for blanks in CONTACT_FIELDS.values() for blank in blanks)


def advice(problem: str, title: str, names: list[str]) -> str | None:
    """What to change so that a scan refused with problem fits, in the label
    title with the blanks names or at the scanner, one short hint; None
    where neither helps."""
    if problem == NO_BLANKS:
        return f'In the editor, add the blank code to "{title}".'
    if problem == SEVERAL_BLANKS:
        # The blank for the code is most likely one a contact would not fill
        target = next((name for name in names if not _contact_blank(name)), names[0])
        return f'In the editor, rename the blank "{target}" of "{title}" to code.'
    if problem == NO_CONTACT_MATCH:
        # Blanks a contact fills already are right, the contact lacks those details
        missing = [new for new in ('Name', 'Address') if not any(blank_key(name) == blank_key(new) for name in names)]
        pairs = list(zip((name for name in names if not _contact_blank(name)), missing))
        if not pairs:
            return None
        renames = ' and '.join(f'"{name}" to {new}' for name, new in pairs)
        return f'In the editor, rename the blank{"s" if len(pairs) > 1 else ""} {renames} in "{title}".'
    if problem == WRONG_LAYOUT:
        return 'Set the scanner to the layout of this computer with a code from its manual.'
    return None


def code_text(code) -> str:
    """The text of a code as a request sends it, a number of JSON as its
    digits, e.g. an EAN; ValueError for anything else but text."""
    if code is None:
        return ''
    if isinstance(code, int) and not isinstance(code, bool):
        return str(code)
    if not isinstance(code, str):
        raise ValueError(NOT_TEXT)
    return code


def wrong_layout(text: str) -> bool:
    return text.lower().startswith(_WRONG_LAYOUT_PREFIXES)


def _lead_off(text: str) -> str:
    """text without the white space and byte order marks before it."""
    return re.sub(r'^[\s\ufeff]+', '', text)


def incomplete(text: str) -> bool:
    """A part of a vCard, e.g. split by a pause of the scanner within it."""
    upper = text.upper()
    return _lead_off(upper).startswith('BEGIN:VCARD') != ('END:VCARD' in upper)


def _split(value: str, sep: str) -> list[str]:
    """value split at sep, except where a backslash escapes it; escapes are kept."""
    parts, current, i = [], '', 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            current += value[i:i + 2]
            i += 2
        elif value[i] == sep:
            parts.append(current)
            current, i = '', i + 1
        else:
            current += value[i]
            i += 1
    parts.append(current)
    return parts


def _unescape(value: str) -> str:
    return re.sub(r'\\(.)', lambda m: '\n' if m.group(1) in 'nN' else m.group(1), value, flags=re.S)


def _parts(value: str, sep: str, count: int = 0, decode=str) -> list[str]:
    parts = [decode(_unescape(part)).strip() for part in _split(value, sep)]
    return parts + [''] * (count - len(parts))


def _quoted_printable(charset: str):
    """Decoder of quoted-printable text in charset (UTF-8 if it is no text encoding)."""
    def decode(part: str) -> str:
        raw = quopri.decodestring(part.encode('utf-8'))
        try:
            return raw.decode(charset, 'replace')
        except (LookupError, ValueError):
            return raw.decode('utf-8', 'replace')
    return decode


# Where a line of a vCard begins whose line breaks a scanner left out: at a
# property in capitals, with its group and parameters, before its colon
_LINE_START = re.compile(
    r'(?:item\d+\.)?(?:BEGIN|VERSION|N|FN|ORG|TITLE|ROLE|TEL|EMAIL|ADR|LABEL|URL|NOTE|BDAY|NICKNAME|PHOTO|LOGO'
    r'|REV|UID|CATEGORIES|GEO|TZ|KIND|IMPP|X-[A-Z0-9-]+|END)'
    r'(?:;(?:[A-Za-z-]+=[^;:]*|HOME|WORK|CELL|VOICE|FAX|PREF|INTERNET|MSG|PAGER|POSTAL|PARCEL|DOM|INTL|CAR|VIDEO))*:')


def _vcard_lines(text: str) -> list[tuple[str, str, object]]:
    """(property, value, decoder of its parts) of each line, unfolded."""
    if not re.search(r'[\r\n]', text.strip()):
        # The match takes the whole name, so no shorter one is found within it
        text = _LINE_START.sub(lambda m: ('\n' if m.start() else '') + m.group(), text)
    lines = re.split(r'\r\n|\n|\r', re.sub(r'\r?\n[ \t]', '', text))
    result, i = [], 0
    while i < len(lines):
        line, i = lines[i], i + 1
        head, _, value = line.partition(':')
        name, *params = head.split(';')
        params = {p.partition('=')[0].upper(): p.partition('=')[2].upper() for p in params}
        decode = str
        if params.get('ENCODING') == 'QUOTED-PRINTABLE':
            while value.endswith('=') and i < len(lines):
                value, i = value[:-1] + lines[i], i + 1
            decode = _quoted_printable(params.get('CHARSET', 'UTF-8'))
        result.append((name.rsplit('.', 1)[-1].upper(), value, decode))
    return result


def _address(parts: list[str]) -> dict[str, str]:
    """Street, postcode, city and country of the parts pobox;extended;street;city;region;postcode;country."""
    return {'street': parts[2], 'city': parts[3], 'postcode': parts[5], 'country': parts[6]}


def read_contact(text: str) -> dict[str, str] | None:
    """The details of a vCard or MECARD in text, None for any other text."""
    text = _lead_off(text)
    head = text[:11].upper()
    raw: dict[str, list[str]] = {}
    if head == 'BEGIN:VCARD':
        for name, value, decode in _vcard_lines(text):
            if name == 'ORG':
                raw.setdefault(name, _parts(value, ';', decode=decode)[:1])
            elif name in ('FN', 'TEL', 'EMAIL'):
                raw.setdefault(name, [decode(_unescape(value)).strip()])
            elif name in ('N', 'ADR'):
                raw.setdefault(name, _parts(value, ';', 7, decode))
    elif head[:7] == 'MECARD:':
        for field in _split(text[7:], ';'):
            name, _, value = field.partition(':')
            name = name.strip().upper()
            if name == 'N':
                raw.setdefault(name, _parts(value, ',', 2))
            elif name == 'ADR':
                raw.setdefault(name, _parts(value, ',', 7) if ',' in value.replace('\\,', '') else ['', '', _unescape(value).strip()] + [''] * 4)
            elif name in ('TEL', 'EMAIL', 'ORG'):
                raw.setdefault(name, [_unescape(value).strip()])
    else:
        return None
    last, first = (raw.get('N') or ['', ''])[:2]
    adr = _address(raw.get('ADR') or [''] * 7)
    contact = {
        'name': (raw.get('FN') or [''])[0] or f'{first} {last}'.strip(),
        'first_name': first,
        'last_name': last,
        'organization': (raw.get('ORG') or [''])[0],
        **{key: adr[key] for key in ('street', 'postcode', 'city', 'country')},
        'phone': (raw.get('TEL') or [''])[0],
        'email': (raw.get('EMAIL') or [''])[0],
        'address': '\n'.join(line for line in (adr['street'], f"{adr['postcode']} {adr['city']}".strip(), adr['country']) if line),
    }
    return {key: value for key, value in contact.items() if value}


def fill_blanks(names: list[str], fields: dict, text: str) -> dict[str, str]:
    """The values of the blanks (by name) for a scanned text; ValueError with one of MESSAGES if none fits."""
    if not text.strip():
        raise ValueError(NOTHING_SCANNED)
    if wrong_layout(text):
        raise ValueError(WRONG_LAYOUT)
    if incomplete(text):
        raise ValueError(INCOMPLETE)
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError('Text is too long')
    if not names:
        raise ValueError(NO_BLANKS)
    text = text.rstrip('\r\n')
    values = {name: text for name in names if blank_key(name) == 'code'}
    others = [name for name in names if name not in values]
    contact = read_contact(text)
    if contact is not None:
        keys = {blank_key(blank): key for key, blanks in CONTACT_FIELDS.items() for blank in blanks}
        for name in others:
            if contact.get(keys.get(blank_key(name), '')):
                values[name] = contact[keys[blank_key(name)]]
        if not values:
            raise ValueError(NO_CONTACT_MATCH)
    elif not values:
        if len(names) > 1:
            raise ValueError(SEVERAL_BLANKS)
        values[names[0]] = text
    for name, value in values.items():
        check_blank_choice(name, fields.get(name) or {}, value)
    return values
