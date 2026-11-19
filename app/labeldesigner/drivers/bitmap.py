"""Labels as the dots of the printers that are sent an image: black and white,
as rows of packed dots or of one byte per dot."""

from PIL import Image

from ..enums import LabelContent

# The bytes of a 1 bit image of PIL inverted: PIL packs white as 1
_INVERTED = bytes(range(255, -1, -1))
# Black of an 8 bit image 1, any other gray 0
_BLACK = bytes([1]) + bytes(255)
# Dots as binary digits, any but 0 printed
_DIGITS = b'0' + b'1' * 255


def black_and_white(img: Image.Image, label) -> Image.Image:
    """The image of a label in black and white, dithered unless the label is
    a black and white image."""
    dither = label.label_content != LabelContent.IMAGE_BW
    return img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)


def packed_rows(img: Image.Image) -> tuple[list[bytes], int]:
    """The rows of a 1 bit image packed MSB first, 1 printed, and the bytes
    of each row."""
    width = (img.width + 7) // 8
    data = img.tobytes().translate(_INVERTED)
    rows = [data[y * width:(y + 1) * width] for y in range(img.height)]
    # The padding of the last byte of each row is white
    if img.width % 8:
        mask = (0xff << (8 - img.width % 8)) & 0xff
        rows = [row[:-1] + bytes([row[-1] & mask]) for row in rows]
    return rows, width


def dot_rows(img: Image.Image) -> list[bytes]:
    """The rows of an image of black and white, a byte for each dot, 1 black."""
    data = img.convert('L').tobytes().translate(_BLACK)
    return [data[y * img.width:(y + 1) * img.width] for y in range(img.height)]


def pack(dots) -> bytes:
    """A row of dots, 1 printed, packed MSB first, the last byte padded."""
    size = (len(dots) + 7) // 8
    if not size:
        return b''
    return int(bytes(dots).translate(_DIGITS).ljust(size * 8, b'0'), 2).to_bytes(size, 'big')
