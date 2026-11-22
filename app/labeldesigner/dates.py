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
# A code of strftime(), %% too, with a flag like the - of %-d
CODE = re.compile(r'%[-_0^#]?(.)', re.DOTALL)


def format_date(when, date_format: str, language: str = '') -> str:
    """when, a date or a date and time, as strftime() writes it in
    date_format, with the names of months and weekdays (%a, %A, %b, %B) in
    language. A month is named as in a date where the format has the day of
    the month, e.g. "22 listopada 2026" in Polish, else on its own,
    "listopad 2026"."""
    names = NAMES.get(language)
    if names is None:
        return when.strftime(date_format)
    codes = list(CODE.finditer(date_format))
    months = names['months']['format' if any(code[1] in ('d', 'e') for code in codes) else 'stand-alone']
    named = {'a': names['days']['abbreviated'][when.weekday()], 'A': names['days']['wide'][when.weekday()],
             'b': months['abbreviated'][when.month - 1], 'B': months['wide'][when.month - 1]}
    # The text between the names is written by strftime(), the names never are
    pieces, start = [], 0
    for code in codes:
        if code[1] in named:
            pieces += [when.strftime(date_format[start:code.start()]), named[code[1]]]
            start = code.end()
    return ''.join(pieces) + when.strftime(date_format[start:])
