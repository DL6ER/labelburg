# Labels in your language

Labelburg writes the names of months and weekdays on your labels in your language, e.g., "März" instead of "March". The label designer itself is in English.

## Months and weekdays

Choose your language in the Settings, under Labels, at "Language of dates". It is kept on the server, so it applies to every label, in every browser, and to the labels other programs print through Labelburg, e.g., Grocy or Homebox. Labels you saved before follow it too. Without a choice, months and weekdays are in English.

Dates on labels are numbers unless you ask for names, e.g., `[today]` prints 07.03.2026. To write the month or weekday as a word, click the date in the text of the label and change its Format, e.g., to `%A, %-d %B %Y`. "Codes" next to it lists every part, with examples in your language. The format also sets the order, dots and words in between, e.g., `%A, %-d. %B %Y` for "Samstag, 7. März 2026".

How `%A, %-d %B %Y` and `%B %Y` print Saturday, 7 March 2026:

| Language | With the day | Month and year |
|---|---|---|
| English | Saturday, 7 March 2026 | March 2026 |
| Čeština (Czech) | sobota, 7 března 2026 | březen 2026 |
| Dansk (Danish) | lørdag, 7 marts 2026 | marts 2026 |
| Deutsch (German) | Samstag, 7 März 2026 | März 2026 |
| Español (Spanish) | sábado, 7 marzo 2026 | marzo 2026 |
| Français (French) | samedi, 7 mars 2026 | mars 2026 |
| Italiano (Italian) | sabato, 7 marzo 2026 | marzo 2026 |
| Nederlands (Dutch) | zaterdag, 7 maart 2026 | maart 2026 |
| Norsk bokmål (Norwegian) | lørdag, 7 mars 2026 | mars 2026 |
| Polski (Polish) | sobota, 7 marca 2026 | marzec 2026 |
| Português (Portuguese) | sábado, 7 março 2026 | março 2026 |
| Suomi (Finnish) | lauantai, 7 maaliskuuta 2026 | maaliskuu 2026 |
| Svenska (Swedish) | lördag, 7 mars 2026 | mars 2026 |

In some languages a month reads differently with a day than without one, e.g., in Polish "7 marca 2026" but "marzec 2026", likewise in Czech and Finnish. Labelburg picks the right one by itself.

## Ready-made labels

The ready-made labels come in English and German. In Templates, the menu next to "Made for" switches them between English and Deutsch. At first it follows the language of your browser, German for a German one and English for all others, and then this browser remembers your choice.

## Technical details

For those who use the API or work on Labelburg.

- The setting is `date_language` of `/labeldesigner/api/settings/labels`, with the other label settings: `cs`, `da`, `de`, `es`, `fi`, `fr`, `it`, `nb`, `nl`, `pl`, `pt` or `sv`, empty for English. All labels with text take it, those of the API, the MCP tools, Homebox and saved labels printed by name as well, e.g., `curl -X POST http://localhost:8013/labeldesigner/api/settings/labels -d date_language=de`.
- It names months and weekdays in `{{datetime:<format>}}` and in date blanks: `%a`, `%A`, `%b` (also `%h`) and `%B`, in capitals with `^` or `#`, e.g., `%^B` for `MÄRZ`. A month takes its form in a date where the format has the day of the month (e.g., `%d`, `%-d` or `%e`), else its form on its own. Weekdays take the form of the language's own full date, which in Finnish is the one on its own.
- A name with a width (`%10B`) or the `E` or `O` modifier, and `%c`, `%x` and `%p`, stay as `strftime()` writes them, in English. Other codes, e.g., `%d` or `%Y`, are the same in every language.
- The names come from the [Unicode CLDR](https://cldr.unicode.org/): `tools/date_names.py` writes them to `app/labeldesigner/cldr/date_names.json` with the version of [Babel](https://babel.pocoo.org/) pinned in `requirements-dates.txt`. The image needs neither Babel nor the locales of the system.
- To add a language, add its code to `LANGUAGES` in `tools/date_names.py`, then run `pip install -r requirements-dates.txt` and `python3 tools/date_names.py`. Add the code to the `date_language` enum in `app/labeldesigner/openapi.json`, the row to the table above and the language to the tests that list them.
