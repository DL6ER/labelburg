"""Names of months and weekdays in the language of dates of the label
settings, see app/labeldesigner/dates.py."""
import json
from datetime import date, datetime

import pytest

from app.labeldesigner import dates
from app.labeldesigner.dates import format_date
from app.labeldesigner.label import SimpleLabel, datetime_codes

SATURDAY = date(2026, 3, 7)
SUNDAY = date(2026, 11, 22)


@pytest.mark.parametrize('when, date_format, language, expected', [
    # German, short names as CLDR has them: in a date "März", on its own "Mär"
    (SATURDAY, '%A, %d. %B %Y', 'de', 'Samstag, 07. März 2026'),
    (SATURDAY, '%a %d. %b', 'de', 'Sa. 07. März'),
    (SATURDAY, '%b %Y', 'de', 'Mär 2026'),
    (SUNDAY, '%a, %d. %b %Y', 'de', 'So., 22. Nov. 2026'),
    # French
    (SATURDAY, '%A %-d %B %Y', 'fr', 'samedi 7 mars 2026'),
    (SUNDAY, '%a %d %b', 'fr', 'dim. 22 nov.'),
    (SUNDAY, '%B %Y', 'fr', 'novembre 2026'),
    # Polish: a month in a date where the day of the month is, else on its own
    (SUNDAY, '%d %B %Y', 'pl', '22 listopada 2026'),
    (SUNDAY, '%-d %B', 'pl', '22 listopada'),
    (SUNDAY, '%e %B', 'pl', '22 listopada'),
    (SUNDAY, '%B %Y', 'pl', 'listopad 2026'),
    (SUNDAY, '%A, %B', 'pl', 'niedziela, listopad'),
    (SUNDAY, '%m/%Y (%B)', 'pl', '11/2026 (listopad)'),
    # %%d is a % and a d, no day
    (SUNDAY, '%%d %B', 'pl', '%d listopad'),
    (SUNDAY, '100%% %B', 'pl', '100% listopad'),
    (SUNDAY, '%%B %B', 'de', '%B November'),
    (SUNDAY, '%%%B', 'de', '%November'),
    # The - of %-d next to a name, also on a name
    (SATURDAY, '%-d.%B', 'de', '7.März'),
    (SATURDAY, '%-d. %-B %-m.', 'de', '7. März 3.'),
    # Finnish weekdays as in a full date, on their own
    (SATURDAY, '%A %-d. %B', 'fi', 'lauantai 7. maaliskuuta'),
    # English is that of strftime(), also for a language it does not know
    (SATURDAY, '%A, %d %B %Y, %a %b', '', 'Saturday, 07 March 2026, Sat Mar'),
    (SATURDAY, '%A %B', 'xx', 'Saturday March'),
    # With the time
    (datetime(2026, 3, 7, 14, 5, 9), '%A %H:%M', 'de', 'Samstag 14:05'),
])
def test_names_of_months_and_weekdays_in_the_language(when, date_format, language, expected):
    assert format_date(when, date_format, language) == expected


@pytest.mark.parametrize('date_format', ['%Y-%m-%d %H:%M:%S', '%d.%m.%Y', 'week %V, day %j, %u', '%x %X %p', '%I %y %%', 'no codes', '%Q %', ''])
def test_other_codes_are_written_by_strftime(date_format):
    when = datetime(2026, 3, 7, 14, 5, 9)
    for language in dates.LANGUAGES:
        assert format_date(when, date_format, language) == when.strftime(date_format)


def test_names_are_not_read_as_codes(monkeypatch):
    names = json.loads(json.dumps(dates.NAMES['de']))
    names['months']['stand-alone']['wide'][2] = '%Y'
    monkeypatch.setitem(dates.NAMES, 'zz', names)
    assert format_date(SATURDAY, '%B %Y', 'zz') == '%Y 2026'


def test_the_languages_of_dates():
    # Each named in itself, all with the names of every month and weekday
    assert dates.LANGUAGES == {
        'cs': 'Čeština', 'da': 'Dansk', 'de': 'Deutsch', 'es': 'Español', 'fr': 'Français', 'it': 'Italiano', 'nl': 'Nederlands',
        'nb': 'Norsk bokmål', 'pl': 'Polski', 'pt': 'Português', 'fi': 'Suomi', 'sv': 'Svenska'}
    for names in dates.NAMES.values():
        for context in ('format', 'stand-alone'):
            assert [len(names['months'][context][width]) for width in ('wide', 'abbreviated')] == [12, 12]
        assert [len(names['days'][width]) for width in ('wide', 'abbreviated')] == [7, 7]
        every = [name for context in names['months'].values() for width in context.values() for name in width]
        every += [name for width in names['days'].values() for name in width]
        assert all(name and '%' not in name for name in every)


def _text(date_language: str, text: str, **kwargs) -> str:
    label = SimpleLabel(text=[{'text': text, 'size': '40'}], timestamp=int(datetime(2026, 3, 7, 14, 5).timestamp()),
                        date_language=date_language, **kwargs)
    label.process_templates()
    return label.text[0]['text']


def test_dates_of_templates_and_blanks_are_written_in_the_language():
    assert _text('de', '{{datetime:%A, %-d. %B}} bis {{datetime+1m:%B %Y}}') == 'Samstag, 7. März bis April 2026'
    assert _text('', '{{datetime:%A, %-d. %B}}') == 'Saturday, 7. March'
    fields = {'Due': {'type': 'date', 'format': '%-d %B %Y'}, 'Made': {'type': 'date'}}
    assert _text('pl', '{{blank:Due}}|{{blank:Made}}', blank_fields=fields, blanks={'Made': '2026-11-22'},
                 date_format='%B %Y') == '7 marca 2026|listopad 2026'


def test_the_codes_of_names_have_examples_in_each_language():
    codes = datetime_codes()
    assert codes['example'] == 'Saturday, 7 March 2026, 14:05:09'
    assert {code: example for code, _, example in codes['codes'] if code in ('%a', '%A', '%b', '%B')} == {
        '%a': 'Sat', '%A': 'Saturday', '%b': 'Mar', '%B': 'March'}
    assert set(codes['languages']) == set(dates.LANGUAGES)
    assert codes['languages']['de'] == {'%a': 'Sa.', '%A': 'Samstag', '%b': 'Mär', '%B': 'März'}
    assert codes['languages']['pl'] == {'%a': 'sob.', '%A': 'sobota', '%b': 'mar', '%B': 'marzec'}
