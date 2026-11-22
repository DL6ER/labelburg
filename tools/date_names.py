"""Write the names of months and weekdays in the languages of dates on
labels, app/labeldesigner/cldr/date_names.json, from the CLDR data of Babel:

    pip install -r requirements-dates.txt
    python3 tools/date_names.py

The label designer needs neither Babel nor a locale of the system. The same
Babel version gives the same file: the dates job of .github/workflows/ci.yml
writes it again and fails when the committed one differs, e.g. on a pull
request that raises Babel: then write it again here.
"""
import json
import os
import re
import unicodedata

from babel import Locale
from babel.dates import get_date_format, get_day_names, get_month_names

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'labeldesigner', 'cldr', 'date_names.json')
# English is not among them, its names are those of strftime()
LANGUAGES = ('de', 'fr', 'es', 'it', 'nl', 'pt', 'pl', 'cs', 'da', 'sv', 'nb', 'fi')
WIDTHS = ('wide', 'abbreviated')


def names(language: str) -> dict:
    """The name of the language in itself, the names of its months in a date
    (format) and on their own (stand-alone), and of its weekdays, Monday
    first as date.weekday() counts."""
    locale = Locale.parse(language)
    name = locale.get_display_name(locale)
    # Weekdays as the language's own full date names them: in Finnish on
    # their own (lauantai), not as in "on Saturday" (lauantaina)
    days = 'stand-alone' if 'cccc' in get_date_format('full', locale).pattern else 'format'
    return {
        # It starts an entry of a menu, e.g. Français
        'name': name[:1].upper() + name[1:],
        'months': {context: {width: [get_month_names(width, context, locale)[month] for month in range(1, 13)] for width in WIDTHS}
                   for context in ('format', 'stand-alone')},
        'days': {width: [get_day_names(width, days, locale)[day] for day in range(7)] for width in WIDTHS},
    }


def sort_key(name: str) -> str:
    """name as it is sorted in a menu, Čeština among the C."""
    return ''.join(c for c in unicodedata.normalize('NFD', name) if not unicodedata.combining(c)).casefold()


def main():
    data = {language: names(language) for language in LANGUAGES}
    data = dict(sorted(data.items(), key=lambda item: sort_key(item[1]['name'])))
    # A list of names on one line
    text = re.sub(r'\[\s+([^\[\]]*?)\s+\]', lambda m: '[' + re.sub(r',\s+', ', ', m.group(1)) + ']',
                  json.dumps(data, ensure_ascii=False, indent=2))
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text + '\n')


if __name__ == '__main__':
    main()
