"""Values for the blanks of a label from a text read by a barcode scanner."""
import quopri
import re

from .label import MAX_TEXT_LENGTH, check_blank_choice

NOTHING_SCANNED = 'Nothing was scanned'
NO_BLANKS = 'The label has no blank for the scanned code. Add one in the editor, e.g., a blank named "code".'
SEVERAL_BLANKS = 'The label has several blanks, so it is not clear which one the code fills. Name the blank for the code "code".'
NO_CONTACT_MATCH = 'The code holds a contact, but none of its details fits a blank of the label. Name the blanks e.g. "Name" and "Address".'
WRONG_LAYOUT = ('The scanner types with another keyboard layout than this computer. '
                'Set the scanner to the layout of the computer with the setup code in its manual.')
MESSAGES = (NOTHING_SCANNED, NO_BLANKS, SEVERAL_BLANKS, NO_CONTACT_MATCH, WRONG_LAYOUT)

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
    return re.sub(r'[_\- ]+', ' ', name.casefold().strip())


def wrong_layout(text: str) -> bool:
    return text.lower().startswith(_WRONG_LAYOUT_PREFIXES)


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


def _parts(value: str, sep: str, count: int = 0) -> list[str]:
    parts = [_unescape(part).strip() for part in _split(value, sep)]
    return parts + [''] * (count - len(parts))


def _vcard_lines(text: str) -> list[tuple[str, dict[str, str], str]]:
    """(property, parameters, value) of each line, unfolded and decoded."""
    lines = re.sub(r'\r?\n[ \t]', '', text).splitlines()
    result, i = [], 0
    while i < len(lines):
        line, i = lines[i], i + 1
        head, _, value = line.partition(':')
        name, *params = head.split(';')
        params = {p.partition('=')[0].upper(): p.partition('=')[2].upper() for p in params}
        if params.get('ENCODING') == 'QUOTED-PRINTABLE':
            while value.endswith('=') and i < len(lines):
                value, i = value[:-1] + lines[i], i + 1
            value = quopri.decodestring(value.encode('utf-8')).decode(params.get('CHARSET', 'UTF-8'), 'replace')
        result.append((name.rsplit('.', 1)[-1].upper(), params, value))
    return result


def _address(parts: list[str]) -> dict[str, str]:
    """Street, postcode, city and country of the parts pobox;extended;street;city;region;postcode;country."""
    return {'street': parts[2], 'city': parts[3], 'postcode': parts[5], 'country': parts[6]}


def read_contact(text: str) -> dict[str, str] | None:
    """The details of a vCard or MECARD in text, None for any other text."""
    head = text.lstrip()[:11].upper()
    raw: dict[str, list[str]] = {}
    if head == 'BEGIN:VCARD':
        for name, _, value in _vcard_lines(text):
            if name == 'ORG':
                raw.setdefault(name, _parts(value, ';')[:1])
            elif name in ('FN', 'TEL', 'EMAIL'):
                raw.setdefault(name, [_unescape(value).strip()])
            elif name in ('N', 'ADR'):
                raw.setdefault(name, _parts(value, ';', 7))
    elif head[:7] == 'MECARD:':
        for field in _split(text.lstrip()[7:], ';'):
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
        if others and not any(name in values for name in others):
            raise ValueError(NO_CONTACT_MATCH)
    elif not values:
        if len(names) > 1:
            raise ValueError(SEVERAL_BLANKS)
        values[names[0]] = text
    for name, value in values.items():
        check_blank_choice(name, fields.get(name) or {}, value)
    return values
