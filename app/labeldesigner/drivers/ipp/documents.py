"""The documents a printer on IPP gets the labels of a job in: PWG raster
(PWG 5102.4) of gray or black dots, PNG or PDF, the first of them it takes.
Each label is a page as large as the label, each sheet of labels a page."""

import io
import struct
from typing import NamedTuple

from PIL import Image

from ... import ipp
from .. import bitmap, diecut

PWG_RASTER, PNG, PDF = 'image/pwg-raster', 'image/png', 'application/pdf'
# In the order they are chosen
FORMATS = (PWG_RASTER, PNG, PDF)
# The dots of PWG raster (Table 12): 8 bit gray, which every printer of IPP
# Everywhere takes, before black ones of 1 bit. LPrint 1.3 hands black dots
# of 1 bit as they are to its drivers, which take gray ones.
SGRAY_8, BLACK_1 = 'sgray_8', 'black_1'
RASTER_TYPES = (SGRAY_8, BLACK_1)
# The start of the document (4.2) and the size of the header of each page (4.3)
SYNC = b'RaS2'
HEADER_SIZE = 1796
# ColorSpace (Table 3): sGray, where 0 is black, and Black, where 1 is a
# printed dot (4.3.2.10)
SGRAY, BLACK = 18, 3
# The pages of office printers by their size in hundredths of a millimeter,
# named as PWG 5101.1 names them
PAGES = {(21000, 29700): 'iso_a4_210x297mm', (21590, 27940): 'na_letter_8.5x11in'}


class Format(NamedTuple):
    """What a printer is sent: the document format, the resolution, of PWG
    raster also the kind of its dots, see RASTER_TYPES."""
    kind: str
    dpi: int
    raster_type: str = ''


def _list(value) -> list:
    return value if isinstance(value, list) else [value]


def _same_dots(told: int, dpi: int) -> bool:
    """Whether a printer of told dpi has the dots of one of dpi, see
    diecut.EIGHT_PER_MM."""
    return told == dpi or {told, dpi} <= set(diecut.EIGHT_PER_MM)


def chosen(attributes: dict, dpi: int, multiples: bool = False) -> Format | None:
    """The format a printer with the attributes gets labels made at dpi in:
    PWG raster where it takes it at that resolution, with multiples also at
    a multiple of it, e.g. 600 dpi for pages made at 300, else PNG, else
    PDF, which it scales to its own. None where it takes none of them."""
    formats = _list(attributes.get('document-format-supported'))
    types = _list(attributes.get('pwg-raster-document-type-supported'))
    raster_type = next((kind for kind in RASTER_TYPES if kind in types), None)
    if PWG_RASTER in formats and raster_type:
        square = [resolution[0] for resolution in _list(attributes.get('pwg-raster-document-resolution-supported'))
                  if isinstance(resolution, tuple) and len(resolution) == 3 and resolution[0] == resolution[1]
                  and resolution[2] == ipp.DOTS_PER_INCH and isinstance(resolution[0], int) and resolution[0] > 0]
        for across in square:
            if _same_dots(across, dpi):
                return Format(PWG_RASTER, across, raster_type)
        if multiples and (more := sorted(across for across in square if across > dpi and across % dpi == 0)):
            return Format(PWG_RASTER, more[0], raster_type)
    return next((Format(kind, dpi) for kind in (PNG, PDF) if kind in formats), None)


def size_name(media_size: tuple[int, int]) -> str:
    """The name of a size in hundredths of a millimeter as PWG 5101.1 makes
    one, e.g. custom_101.6x152.4mm_101.6x152.4mm, of a page of PAGES its
    own, e.g. iso_a4_210x297mm."""
    if tuple(media_size) in PAGES:
        return PAGES[tuple(media_size)]
    size = 'x'.join(f'{hundredths / 100:g}' for hundredths in media_size) + 'mm'
    return f'custom_{size}_{size}'


def page_header(width: int, height: int, dpi: int, media_size: tuple[int, int], pages: int,
                raster_type: str = SGRAY_8) -> bytes:
    """The header of a page (4.3) of dots of raster_type, width dots across
    and height along at dpi, on media of media_size, width and length in
    hundredths of a millimeter, of a document of pages. The fields not set
    are 0, the defaults of the printer."""
    across, along = media_size
    if (across > along) != (width > height):
        across, along = along, across
    bits, space = (8, SGRAY) if raster_type == SGRAY_8 else (1, BLACK)
    header = bytearray(HEADER_SIZE)
    header[0:9] = b'PwgRaster'
    struct.pack_into('>II', header, 276, dpi, dpi)
    # In points, as CUPS counts them, A4 595 x 842
    struct.pack_into('>II', header, 352, round(across * 72 / 2540), round(along * 72 / 2540))
    struct.pack_into('>II', header, 372, width, height)
    # BitsPerColor, BitsPerPixel, BytesPerLine, ColorOrder chunky, ColorSpace
    struct.pack_into('>IIIII', header, 384, bits, bits, (width * bits + 7) // 8, 0, space)
    struct.pack_into('>I', header, 420, 1)
    # TotalPageCount, CrossFeedTransform and FeedTransform as on a front side
    struct.pack_into('>Iii', header, 452, pages, 1, 1)
    struct.pack_into('>IIII', header, 464, 0, 0, width, height)
    name = size_name(media_size).encode()
    header[1732:1732 + len(name)] = name
    return bytes(header)


def _packed(row: bytes) -> bytes:
    """A row of bytes run-length encoded (4.4): a byte repeated 1 to 128 times
    as count - 1 and the byte, 2 to 128 others as 257 - count and them."""
    out = bytearray()
    i = 0
    while i < len(row):
        same = 1
        while i + same < len(row) and same < 128 and row[i + same] == row[i]:
            same += 1
        if same > 1:
            out += bytes((same - 1, row[i]))
            i += same
            continue
        # Up to the next byte that starts a repetition
        end = i + 1
        while end < len(row) and end - i < 128 and not (end + 1 < len(row) and row[end] == row[end + 1]):
            end += 1
        out += bytes((0, row[i])) if end - i == 1 else bytes((257 - (end - i),)) + row[i:end]
        i = end
    return bytes(out)


def page_bitmap(rows: list[bytes]) -> bytes:
    """The rows of a page compressed (4.4): each row after the count of 1 to
    256 equal rows it stands for, less one."""
    out = bytearray()
    y = 0
    while y < len(rows):
        same = 1
        while y + same < len(rows) and same < 256 and rows[y + same] == rows[y]:
            same += 1
        out.append(same - 1)
        out += _packed(rows[y])
        y += same
    return bytes(out)


def _rows(img: Image.Image, raster_type: str) -> list[bytes]:
    """The rows of an image of 1 bit or gray as dots of raster_type: of 8
    bit gray a byte each, 0 black and 255 white, of black ones 8 to a byte,
    see bitmap.packed_rows(), gray dithered."""
    if raster_type == BLACK_1:
        return bitmap.packed_rows(img.convert('1'))[0]
    data = img.convert('L').tobytes()
    return [data[y * img.width:(y + 1) * img.width] for y in range(img.height)]


def pwg_raster(images: list[Image.Image], dpi: int, media_size: tuple[int, int], raster_type: str = SGRAY_8) -> bytes:
    """A PWG raster document of images of 1 bit or gray, a page each."""
    pages = [SYNC]
    for img in images:
        pages += [page_header(img.width, img.height, dpi, media_size, len(images), raster_type),
                  page_bitmap(_rows(img, raster_type))]
    return b''.join(pages)


def png(img: Image.Image, dpi: int) -> bytes:
    """A PNG of an image in 8 bit gray, which LPrint 1.3 takes where it
    prints one of 1 bit all black, its resolution told, the size of the
    label when it is printed as large as it is."""
    buffer = io.BytesIO()
    img.convert('L').save(buffer, 'PNG', dpi=(dpi, dpi))
    return buffer.getvalue()


def pdf(images: list[Image.Image], dpi: int) -> bytes:
    """A PDF of the images, a page each, as large as the label."""
    buffer = io.BytesIO()
    images[0].save(buffer, 'PDF', resolution=dpi, save_all=True, append_images=images[1:])
    return buffer.getvalue()


def documents(chosen_format: Format, images: list[Image.Image], media_size: tuple[int, int], made_at: int = 0) -> list[bytes]:
    """The documents of the images in the format, see chosen(): one with all
    pages, of PNG one for each image. Images made_at another resolution
    than the raster's are scaled to it dot by dot."""
    kind, dpi, raster_type = chosen_format
    if kind == PWG_RASTER:
        if made_at and dpi != made_at and dpi % made_at == 0:
            images = [img.resize((img.width * dpi // made_at, img.height * dpi // made_at), Image.Resampling.NEAREST)
                      for img in images]
        return [pwg_raster(images, dpi, media_size, raster_type)]
    if kind == PDF:
        return [pdf(images, dpi)]
    return [png(img, dpi) for img in images]
