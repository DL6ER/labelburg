"""The sheets of labels of office printers: each as its maker gives it, laid
out on its page, A4 first, and how labels of other sizes go on them."""
import re
from fractions import Fraction

import pytest

from app.labeldesigner import sheets, tape
from app.labeldesigner.drivers import ipp as other
from app.labeldesigner.drivers.ipp import catalog
from app.labeldesigner.label_names import SIZES, label_name, label_size_problem

# As the makers' spec sheets give them, typed apart from the catalog: the
# page, the label as the maker writes it, the labels on a sheet, and the
# margins left and top where the maker gives them. Avery gives inches for
# US Letter, height first.
MAKERS = {
    'L7160': ('A4', '63.5 x 38.1 mm', 21), 'L7159': ('A4', '63.5 x 33.86 mm', 24), 'L7161': ('A4', '63.5 x 46.56 mm', 18),
    'L7162': ('A4', '99.06 x 33.87 mm', 16), 'L7163': ('A4', '99.06 x 38.1 mm', 14), 'L7165': ('A4', '99.06 x 67.73 mm', 8),
    'L7166': ('A4', '99.06 x 93.13 mm', 6), 'L7167': ('A4', '199.6 x 289.05 mm', 1), 'L7168': ('A4', '199.6 x 143.53 mm', 2),
    'L7169': ('A4', '139 x 99.1 mm', 4), 'L7173': ('A4', '99.06 x 57 mm', 10), 'L7651': ('A4', '38.1 x 21.17 mm', 65),
    'L7656': ('A4', '46 x 11.1 mm', 84), 'L4731REV': ('A4', '25.4 x 10 mm', 189), 'L4732': ('A4', '35.6 x 16.9 mm', 80),
    'L4736': ('A4', '45.7 x 21.2 mm', 48), 'L4737': ('A4', '63.5 x 29.6 mm', 27),
    '3474': ('A4', '70 x 37 mm', 24), '3475': ('A4', '70 x 36 mm', 24), '3490': ('A4', '70 x 36 mm', 24),
    '3481': ('A4', '70 x 41 mm', 21), '3483': ('A4', '105 x 148 mm', 4), '3425': ('A4', '105 x 57 mm', 10),
    '4780': ('A4', '48.5 x 25.4 mm', 40),
    '4226': ('A4', '48.26 x 16.93 mm', 64, '8.48 mm', '13.06 mm'), '4360': ('A4', '70 x 36 mm', 24, None, '4.5 mm'),
    '10001': ('A4', '25.4 x 10 mm', 189, '8.48 mm', '13.5 mm'),
    '5160': ('Letter', '1" x 2-5/8"', 30, '3/16"', '1/2"'), '5161': ('Letter', '1" x 4"', 20, '0.167"', '0.5"'),
    '5162': ('Letter', '1-1/3" x 4"', 14, '0.156"', '0.833"'), '5163': ('Letter', '2" x 4"', 10),
    '5164': ('Letter', '3-1/3" x 4"', 6, '0.156"', '0.5"'), '5167': ('Letter', '1/2" x 1-3/4"', 80, '0.3"', '0.5"'),
    '5195': ('Letter', '2/3" x 1-3/4"', 60, '0.3"', '0.5"'), '5168': ('Letter', '3-1/2" x 5"', 4), '5165': ('Letter', '8-1/2" x 11"', 1),
}


def mm(text: str, unit: str = '') -> float:
    """A length as a maker writes it, e.g. 63.5 mm, 2-5/8" or 0.167", in
    unit where it does not say."""
    text = text.strip()
    if text.endswith('mm') or unit == 'mm':
        return float(text.removesuffix('mm'))
    text = text.rstrip('"')
    whole, _, part = text.rpartition('-') if '/' in text and '-' in text else ('0', '', text)
    return float((Fraction(whole) + Fraction(part)) * Fraction('25.4'))


def layout_of(product: str) -> catalog.Layout:
    return next(layout for layout in catalog.LAYOUTS if product in layout.products)


@pytest.mark.parametrize('product', sorted(MAKERS))
def test_sheet_is_as_its_maker_gives_it(product):
    layout, (page, label, count, *margins) = layout_of(product), MAKERS[product]
    told = [mm(side, 'mm' if label.endswith('mm') else '') for side in label.split(' x ')]
    if page == 'Letter':
        told.reverse()
    assert layout.page == page
    assert sorted(abs(a - b) for a, b in zip(sorted(layout.label), sorted(told)))[-1] <= 0.05
    assert layout.columns * layout.rows == count
    for given, kept in zip(margins, layout.left_top):
        if given is not None:
            assert abs(mm(given) - kept) <= 0.05


def test_every_product_has_its_makers_numbers():
    assert {product for layout in catalog.LAYOUTS if layout.maker for product in layout.products} - set(MAKERS) == {
        'J8160', 'J8159', 'J8161', 'J8162', 'J8163', 'J8165', 'J8166', 'J8167', 'J8168', 'J8169', 'J8173', 'J8651', 'L4731',
        '8160', '5260', '5960', '18160', '6460'}


@pytest.mark.parametrize('layout', catalog.LAYOUTS, ids=catalog.identifier)
def test_labels_lie_on_the_page_apart(layout):
    page = catalog.PAGES[layout.page]
    (left, top), (across, down) = layout.left_top, layout.step
    assert left >= 0 and top >= 0
    assert across >= layout.label[0] - 0.01 and down >= layout.label[1] - 0.01
    assert left + (layout.columns - 1) * across + layout.label[0] <= page[0] + 0.05
    assert top + (layout.rows - 1) * down + layout.label[1] <= page[1] + 0.05


@pytest.mark.parametrize('layout', [layout for layout in catalog.LAYOUTS if layout.maker == 'Avery' and layout.page == 'A4'],
                         ids=catalog.identifier)
def test_avery_a4_labels_are_centered_across(layout):
    right = 210 - layout.left_top[0] - (layout.columns - 1) * layout.step[0] - layout.label[0]
    assert abs(right - layout.left_top[0]) <= 0.05


def test_ids_and_products_are_unique():
    ids = [catalog.identifier(layout) for layout in catalog.LAYOUTS]
    products = [product for layout in catalog.LAYOUTS for product in layout.products]
    assert len(ids) == len(set(ids)) and len(products) == len(set(products))
    assert all(re.fullmatch(r'sheet-[a-z0-9]+', i) for i in ids)


def test_a4_comes_first():
    pages = [layout.page for layout in catalog.LAYOUTS]
    assert pages == sorted(pages, key=['A4', 'Letter'].index)
    assert catalog.identifier(catalog.LAYOUTS[0]) == 'sheet-a4' and catalog.LAYOUTS[1].products[0] == 'L7160'


def test_sheets_are_label_sizes_of_the_office_printer():
    a4, letter = SIZES['sheet-l7160'], SIZES['sheet-5160']
    assert (a4.tape_size, a4.dots_total, a4.dots_printable, a4.models) == ((210, 297), (2480, 3508), (2480, 3508), ('Other/Office',))
    assert (letter.tape_size, letter.dots_total) == ((215.9, 279.4), (2550, 3300))
    assert sheets.is_paper(a4) and sheets.per_sheet(a4) == 21 and sheets.per_sheet(SIZES['sheet-a4']) == 1
    assert tape.takes('Other/Office') == [catalog.identifier(layout) for layout in catalog.LAYOUTS]


def test_sheets_are_named_by_their_products():
    assert label_name(SIZES['sheet-l7160']) == 'Avery L7160 / J8160 - 21 labels, 63.5 x 38.1 mm'
    assert label_name(SIZES['sheet-l4731rev']) == 'Avery L4731REV / L4731 - 189 labels, 25.4 x 10 mm'
    assert label_name(SIZES['sheet-a4']) == 'Whole A4 page'
    assert catalog.short(catalog.of('sheet-4226')) == 'Herma 4226' and catalog.short(catalog.of('sheet-a4')) == 'A4 page'


@pytest.mark.parametrize('text, size', [('L7160', 'sheet-l7160'), ('j8160', 'sheet-l7160'), ('Avery L 7160', 'sheet-l7160'),
                                        ('5160', 'sheet-5160'), ('Avery 8160', 'sheet-5160'), ('Herma 10001', 'sheet-10001'),
                                        ('Avery Zweckform 3490', 'sheet-3475'), ('L4731', 'sheet-l4731rev'), ('A4', 'sheet-a4'),
                                        ('L9999', None), ('Avery', None), ('', None)])
def test_sheets_by_the_number_on_their_pack(text, size):
    assert catalog.named(text) == size


@pytest.mark.parametrize('layout', catalog.LAYOUTS, ids=catalog.identifier)
def test_each_label_lies_within_a_dot_of_its_place(layout):
    label = SIZES[catalog.identifier(layout)]
    dots = 300 / 25.4
    places = [(layout.left_top[0] + col * layout.step[0], layout.left_top[1] + row * layout.step[1])
              for row in range(layout.rows) for col in range(layout.columns)]
    assert all(abs(x - left * dots) <= 1 and abs(y - top * dots) <= 1 for (x, y), (left, top) in zip(sheets.spots(label, False), places))


@pytest.mark.parametrize('model', [None, 'Unknown', 'QL-700', 'Other/IPP'])
def test_other_printers_take_no_sheets(model):
    assert not [size for size in tape.takes(model) if sheets.is_paper(SIZES[size])]


def test_sheets_print_on_office_printers_only():
    assert label_size_problem('sheet-l7160', 'Other/Office') == ''
    assert label_size_problem('sheet-l7160', 'QL-700') == 'The label size Avery L7160 / J8160 - 21 labels, 63.5 x 38.1 mm cannot be printed on a QL-700'


def test_label_of_a_sheets_size_goes_on_it_as_it_is():
    # The same 70 x 36 mm labels of another maker, not the labels of another size
    herma = tape.Tape('sheet-4360', assumed=False, manual=True, model='Other/Office')
    assert tape.fits('sheet-3475', herma) and not tape.fits('sheet-3481', herma)
    assert tape.on_tape({'label_size': 'sheet-3475', 'orientation': 'standard'}, herma)['label_size'] == 'sheet-4360'


def test_labels_of_other_printers_go_on_a_sheet_of_their_size():
    office = other.DRIVER
    # 102 x 51 mm die-cut labels are 4 x 2 in ones
    assert office.label_for('ipp102x51', 'Other/Office') == 'sheet-5163'
    assert office.label_for('sheet-l7160', 'Other/Office') == 'sheet-l7160'
    # None of that size, and an endless label, stay as they are
    assert office.label_for('ipp102x152', 'Other/Office') == 'ipp102x152'
    assert office.label_for('62', 'Other/Office') == '62'
    # A printer of labels keeps its own
    assert office.label_for('zpl102x51', 'Other/IPP') == 'ipp102x51'


def test_label_sizes_of_a_sheet_compare_one_label_each():
    assert sheets.same_size(SIZES['sheet-5163'], SIZES['ipp102x51'])
    assert not sheets.same_size(SIZES['sheet-l7160'], SIZES['sheet-l7159'])


def test_label_goes_on_the_sheet_assumed_where_none_is_chosen():
    unknown = tape.in_printer({}, 'Other/Office', '', '62')
    assert (unknown.id, unknown.known) == ('sheet-a4', False)
    assert tape.on_tape({'label_size': '62', 'orientation': 'standard'}, unknown)['label_size'] == 'sheet-a4'
    # A sheet of the printer stays, a label of the size of one goes on it
    assert not tape.moves('sheet-l7160', unknown)
    assert tape.on_tape({'label_size': 'ipp102x51', 'orientation': 'standard'}, unknown)['label_size'] == 'sheet-5163'
    # Label printers that do not tell their tape keep labels as they are
    assert not tape.moves('62', tape.in_printer({}, 'Zebra/ZPL', '', '62'))
