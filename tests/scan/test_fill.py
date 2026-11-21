import re

import pytest

from app.labeldesigner import refusals, scan

VCARD = 'BEGIN:VCARD\r\nVERSION:3.0\r\nN:Muster;Erika;;;\r\nFN:Erika Muster\r\nORG:Beispiel GmbH\r\nADR;TYPE=work:;;Hauptstr. 5;Bonn;;53111;Deutschland\r\nTEL;TYPE=cell:+49 228 1234\r\nEMAIL:erika@example.org\r\nEND:VCARD\r\n'


def test_code_blank_gets_the_text_as_it_is():
    assert scan.fill_blanks(['Code', 'Room'], {}, 'A-17 / 2') == {'Code': 'A-17 / 2'}


def test_blank_names_match_loosely():
    assert scan.blank_key('  first_name ') == scan.blank_key('First-Name') == 'first name'
    assert scan.blank_key('Straße') == scan.blank_key('STRASSE')


def test_only_blank_gets_plain_text_with_its_lines():
    assert scan.fill_blanks(['Address'], {}, 'Erika Muster\nHauptstr. 5\n53111 Bonn\n') == {'Address': 'Erika Muster\nHauptstr. 5\n53111 Bonn'}


def test_vcard_fills_name_and_address():
    assert scan.fill_blanks(['Name', 'Anschrift', 'Room'], {}, VCARD) == {'Name': 'Erika Muster', 'Anschrift': 'Hauptstr. 5\n53111 Bonn\nDeutschland'}


def test_mecard_fills_first_and_last_name():
    text = 'MECARD:N:Muster,Erika;ADR:,,Hauptstr. 5,Bonn,,53111,;TEL:+492281234;EMAIL:erika@example.org;;'
    assert scan.fill_blanks(['Vorname', 'Nachname', 'PLZ', 'Ort', 'E-Mail'], {}, text) == {
        'Vorname': 'Erika', 'Nachname': 'Muster', 'PLZ': '53111', 'Ort': 'Bonn', 'E-Mail': 'erika@example.org'}


def test_vcard_as_written_in_the_wild():
    # vCard 2.1 quoted-printable umlauts with a soft line break, LF only, escaped comma
    text = ('BEGIN:VCARD\nVERSION:2.1\nN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen\n'
            'ORG:Meier\\, Schmidt & Co\n'
            'ADR;HOME;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:;;Lange Stra=C3=9Fe 1;K=C3=B6=\nln;;50667;\nEND:VCARD')
    filled = scan.fill_blanks(['First name', 'Last name', 'Firma', 'Street', 'City'], {}, text)
    assert filled == {'First name': 'Jürgen', 'Last name': 'Müller', 'Firma': 'Meier, Schmidt & Co',
                      'Street': 'Lange Straße 1', 'City': 'Köln'}


def test_vcard_folded_lines_and_escapes():
    # vCard 3.0/4.0: a line continued by CRLF and one space, \n inside a value
    text = ('BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Erika\r\n  Muster\r\nADR;TYPE=work:;Hinterhaus\\nAufgang B;Hauptstr\r\n . 5;Bonn;;53111;\r\n'
            'EMAIL;TYPE=INTERNET:erika@example.org\r\nEND:VCARD\r\n')
    assert scan.fill_blanks(['Name', 'Street', 'Email'], {}, text) == {
        'Name': 'Erika Muster', 'Street': 'Hauptstr. 5', 'Email': 'erika@example.org'}


def test_refusals():
    for names, text, message in (
            (['Code'], '  \n', scan.NOTHING_SCANNED), ([], 'x', scan.NO_BLANKS),
            (['Room', 'Shelf'], 'x', scan.SEVERAL_BLANKS), (['Room', 'Shelf'], VCARD, scan.NO_CONTACT_MATCH),
            (['Code'], 'x' * 10_001, 'Text is too long')):
        with pytest.raises(ValueError, match=re.escape(message)):
            scan.fill_blanks(names, {}, text)


def test_choice_blank_refuses_a_value_it_does_not_offer():
    with pytest.raises(ValueError, match='is none of the choices of the blank Room') as error:
        scan.fill_blanks(['Room'], {'Room': {'type': 'choice', 'choices': ['Garage', 'Attic']}}, 'Kitchen')
    assert refusals.message(error.value, 'x') == 'A value is none of the choices of its blank'


def test_a_vcard_only_label_with_code_blank_takes_the_card_as_text():
    assert scan.fill_blanks(['code'], {}, VCARD) == {'code': VCARD.rstrip('\r\n')}


def test_code_blank_with_unmatched_blank_takes_the_card_as_text():
    assert scan.fill_blanks(['code', 'Room'], {}, VCARD) == {'code': VCARD.rstrip('\r\n')}


def test_unknown_charset_falls_back_to_utf8():
    text = 'BEGIN:VCARD\nN;CHARSET=FOO;ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen\nEND:VCARD'
    assert scan.fill_blanks(['First name', 'Last name'], {}, text) == {'First name': 'Jürgen', 'Last name': 'Müller'}


def test_blank_key_collapses_before_it_strips():
    assert scan.blank_key('_code_') == scan.blank_key('-code') == 'code'
    assert scan.fill_blanks(['_name_'], {}, 'BEGIN:VCARD\nFN:Erika\nEND:VCARD') == {'_name_': 'Erika'}


def test_vcard_lines_split_only_at_line_breaks():
    text = 'BEGIN:VCARD\nFN:Erika\x85Muster\u2028Jr\x0b\nEND:VCARD'
    assert scan.fill_blanks(['Name'], {}, text) == {'Name': 'Erika\x85Muster\u2028Jr'}


def test_quoted_printable_semicolon_stays_in_its_part():
    text = 'BEGIN:VCARD\nADR;ENCODING=QUOTED-PRINTABLE:;;Weg =3B 5 =5Cn;Bonn;;53111;\nEND:VCARD'
    assert scan.fill_blanks(['Street', 'City'], {}, text) == {'Street': 'Weg ; 5 \\n', 'City': 'Bonn'}


def test_grouped_and_lowercase_properties():
    text = 'BEGIN:VCARD\nitem1.ADR;TYPE=home:;;Hauptstr. 5;Bonn;;53111;\nemail;type=home:erika@example.org\nEND:VCARD'
    assert scan.fill_blanks(['Street', 'Email'], {}, text) == {'Street': 'Hauptstr. 5', 'Email': 'erika@example.org'}


def test_url_is_no_wrong_layout():
    assert not scan.wrong_layout('https://example.org/a?b=1')


def test_wrong_layout_prefixes():
    for text in ('http>&&a', 'HTTPÖ--a', 'BEGIN>VCARD', 'beginÖvcard', 'MECARD>N', 'mecardÖN'):
        assert scan.wrong_layout(text)
    assert not scan.wrong_layout('A https>&&')
