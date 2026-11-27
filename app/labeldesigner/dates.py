"""Names of months and weekdays in the language of dates of the label
settings (date_language), from CLDR: tools/date_names.py writes them to
cldr/date_names.json. strftime() would need a locale of the system, and
setting one changes it for every thread."""
import json
import os
import re

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cldr', 'date_names.json'), encoding='utf-8') as _file:
    NAMES = json.load(_file)
# Each with its name in itself, e.g. Deutsch. English, the names of
# strftime(), is the empty one.
LANGUAGES = {language: names['name'] for language, names in NAMES.items()}
# A code of strftime(), %% too: flags like the - of %-d, a width, the E or O
# modifier and the conversion
CODE = re.compile(r'%([-_0^#+]*)(\d*)([EO]?)(.)', re.DOTALL)


def format_date(when, date_format: str, language: str = '') -> str:
    """when, a date or a date and time, as strftime() writes it in
    date_format, with the names of months and weekdays (%a, %A, %b or %h, %B)
    in language. A month is named as in a date where the format has the day
    of the month, e.g. "22 listopada 2026" in Polish, else on its own,
    "listopad 2026". A name with ^ or # is in capitals, as glibc writes it,
    one with a width or the E or O modifier is left to strftime()."""
    names = NAMES.get(language)
    codes = list(CODE.finditer(date_format))
    if names is None:
        # English, with the names strftime() writes, also where it knows no ^
        named = {conversion: when.strftime('%' + conversion) for conversion in 'aAbB'}
    else:
        months = names['months']['format' if any(code[4] in ('d', 'e') for code in codes) else 'stand-alone']
        named = {'a': names['days']['abbreviated'][when.weekday()], 'A': names['days']['wide'][when.weekday()],
                 'b': months['abbreviated'][when.month - 1], 'B': months['wide'][when.month - 1]}
    named['h'] = named['b']
    # The text between the names is written by strftime(), the names never are
    pieces, start = [], 0
    for code in codes:
        flags, width, modifier, conversion = code.groups()
        if conversion in named and not width and not modifier:
            name = named[conversion]
            pieces += [when.strftime(date_format[start:code.start()]), name.upper() if '^' in flags or '#' in flags else name]
            start = code.end()
    return ''.join(pieces) + when.strftime(date_format[start:])
