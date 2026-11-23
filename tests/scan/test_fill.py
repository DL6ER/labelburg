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


def test_advice_names_the_label_and_its_blanks():
    assert scan.advice(scan.NO_BLANKS, 'Box', []) == (
        'Open "Box" in the editor, type [code where the code goes, choose Blank "code" and save the label.')
    assert scan.advice(scan.SEVERAL_BLANKS, 'Envelope', ['Name', 'Address']) == (
        'Open "Envelope" in the editor, click the blank "Name", change its name to code and save the label. '
        'Contact codes, e.g., from a business card, fill "Name" and "Address" as they are.')
    assert scan.advice(scan.SEVERAL_BLANKS, 'Shelf', ['Room', 'Shelf', 'Name']) == (
        'Open "Shelf" in the editor, click the blank "Room", change its name to code and save the label. '
        'Contact codes, e.g., from a business card, fill "Name" as it is.')
    assert scan.advice(scan.SEVERAL_BLANKS, 'Shelf', ['Room', 'Shelf']) == (
        'Open "Shelf" in the editor, click the blank "Room", change its name to code and save the label.')
    assert scan.advice(scan.NO_CONTACT_MATCH, 'Shelf', ['Room', 'Shelf', 'Box']) == (
        'Open "Shelf" in the editor, click the blank "Room" and change its name to Name, click the blank "Shelf" and '
        'change its name to Address, and save the label. Contacts fill the blanks Name, Address, Company, Phone and Email.')
    assert scan.advice(scan.NO_CONTACT_MATCH, 'Room', ['Room']) == (
        'Open "Room" in the editor, click the blank "Room" and change its name to Name, and save the label. '
        'Contacts fill the blanks Name, Address, Company, Phone and Email.')
    for problem in (scan.NOTHING_SCANNED, scan.WRONG_LAYOUT, scan.INCOMPLETE, 'Text is too long'):
        assert scan.advice(problem, 'Box', ['code']) is None


def test_advice_for_a_contact_renames_only_blanks_a_contact_does_not_fill():
    assert scan.advice(scan.NO_CONTACT_MATCH, 'Envelope', ['Name', 'Address']) is None
    assert scan.advice(scan.NO_CONTACT_MATCH, 'Envelope', ['Name', 'Room']) == (
        'Open "Envelope" in the editor, click the blank "Room" and change its name to Address, and save the label. '
        'Contacts fill the blanks Name, Address, Company, Phone and Email.')


def test_a_vcard_without_its_line_breaks_is_read():
    # As a scanner types it that leaves out the line breaks within a code
    text = ('BEGIN:VCARDVERSION:3.0N:Muster;ErikaFN:Erika MusterTEL:+49 30 1234567EMAIL:erika@example.com'
            'ADR:;;Hauptstr. 1;Köln;;50667;GermanyEND:VCARD')
    assert scan.fill_blanks(['Name', 'Address', 'Phone', 'Email'], {}, text) == {
        'Name': 'Erika Muster', 'Address': 'Hauptstr. 1\n50667 Köln\nGermany', 'Phone': '+49 30 1234567', 'Email': 'erika@example.com'}
    text = ('BEGIN:VCARDVERSION:2.1N:MUSTER;ERIKATEL;CELL;VOICE:+49 228 1234item1.ADR;TYPE=home:;;HAUPTSTR. 5;BERLIN;;10115;GERMANY'
            'EMAIL;INTERNET:erika@example.orgX-ABC:xEND:VCARD')
    assert scan.fill_blanks(['First name', 'Phone', 'City', 'Country', 'Email'], {}, text) == {
        'First name': 'ERIKA', 'Phone': '+49 228 1234', 'City': 'BERLIN', 'Country': 'GERMANY', 'Email': 'erika@example.org'}


def test_a_vcard_cut_in_parts_is_refused():
    # A pause of the scanner within the card splits it into scans of their own
    head, tail = VCARD[:60], VCARD[60:]
    for text in (head, tail, ' begin:vcard\nFN:Erika Muster'):
        with pytest.raises(ValueError, match=re.escape(scan.INCOMPLETE)):
            scan.fill_blanks(['code'], {}, text)
    assert scan.INCOMPLETE in scan.MESSAGES and scan.INCOMPLETE == 'The code came in incomplete. Scan it again.'
    assert refusals.message(ValueError(scan.INCOMPLETE), 'x') == scan.INCOMPLETE


def test_a_byte_order_mark_before_the_card_is_left_out():
    assert scan.fill_blanks(['Name', 'Room'], {}, '\ufeff' + VCARD) == {'Name': 'Erika Muster'}
    assert scan.fill_blanks(['Name', 'Room'], {}, '\ufeffMECARD:N:Muster,Erika;;') == {'Name': 'Erika Muster'}
    assert scan.fill_blanks(['code', 'Room'], {}, '\ufeffA-17') == {'code': '\ufeffA-17'}


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


def test_charset_that_is_no_text_encoding_falls_back_to_utf8():
    for charset in ('BASE64', 'HEX', 'ROT13', 'IDNA', 'undefined', 'UTF\x008'):
        text = f'BEGIN:VCARD\nN;CHARSET={charset};ENCODING=QUOTED-PRINTABLE:M=C3=BCller;J=C3=BCrgen\nEND:VCARD'
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
