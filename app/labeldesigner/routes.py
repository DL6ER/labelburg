import io
import os
import re
import hmac
import math
import uuid
import time
import base64
import secrets
import threading
import contextlib
import datetime
import functools
import dataclasses
from collections import OrderedDict
from io import BytesIO
from typing import BinaryIO
from urllib.parse import urlparse
from . import adding, bp, codes, demos, icons, ipp, paperless, print_queue, printer_settings, refusals, sheets, tape
from .refusals import IMAGE_MODE_REFUSED, UNKNOWN_MODEL
from app import get_fonts
from app.fonts import FontNotFound, sample as font_sample
from config import Default
from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.exceptions import HTTPException
from . import drivers
from .drivers import DEVICE_PATTERNS, devices, diecut, progress, reach, resolution_problem, usbip
from .drivers.devices import flock_within, look_again
from .printer import BUSY, Drawn, PrinterQueue, configured_printers, device_state, found_devices, found_model, get_ptr_status, is_simulation, status_of_printer
from .drivers.labels import FormFactor
from .cable_flag import CABLE_FLAG_REFUSED, cable_diameter, wrap_dots
from .label import BLANK_TEMPLATE, MAX_TEXT_LENGTH, SimpleLabel, LabelContent, LabelOrientation, LabelType, TextTooLong, check_blank_choice, check_blank_choices, check_image_position, check_image_size, check_parts_direction, check_parts_order, check_vertical_align, datetime_codes, parse_color, shift_date
from .label_names import LABELS, MODELS, check_cut, label_dpi, label_length_problem, label_name, label_of, label_palette, label_size_problem, length_limits
from .printer_settings import CUTS
from flask import current_app, g, has_request_context, json, jsonify, redirect, render_template, request, make_response, url_for
from werkzeug.security import safe_join
from werkzeug.utils import secure_filename
from app.utils import (
    convert_image_to_bw, convert_image_to_grayscale, convert_image_to_red_and_black, fill_first_line_fields,
    pdffile_to_image, imgfile_to_image, image_to_png_bytes, pdf_page_count, selected_pdf_pages, PageSelectionError,
    looks_faint, stronger_contrast
)

# The label size of a request or a saved label that names none, the same for
# the label and the printer
DEFAULT_LABEL_SIZE = '62'

# Image types the repository stores next to the label JSON files
REPO_IMAGE_EXTENSIONS = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/gif': '.gif',
    'application/pdf': '.pdf'
}
REPO_IMAGE_MIME_TYPES = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.pdf': 'application/pdf'
}


@bp.errorhandler(ValueError)
def handle_value_error(e):
    _log_refused_request('Request', e)
    # A fixed message, the details are in the log
    return jsonify({"error": refusals.message(e, refusals.INVALID_INPUT)}), 400


@bp.before_request
def reject_cross_origin_requests():
    """Refuse state-changing requests that another website sends through the
    browser of a visitor (cross-site request forgery).

    Browsers add an Origin header to cross-origin form posts and fetches. Such
    requests are only accepted if they carry the X-Requested-With header (set
    by the label designer), which a foreign page cannot add without a CORS
    preflight that this server never grants. Clients without an Origin header
    (curl, scripts, webhooks) are not affected."""
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    # Android sends what is shared with the installed app from its own share
    # menu, possibly with "Origin: null". It is only kept for the page.
    if request.endpoint == 'labeldesigner.share':
        return None
    origin = request.headers.get('Origin')
    if not origin or request.headers.get('X-Requested-With'):
        return None
    if urlparse(origin).netloc == request.host:
        return None
    current_app.logger.warning(f"Rejected cross-origin {request.method} {request.path} from {origin}")
    return _fail('Cross-origin request rejected', 403)


def open_image(file: FileStorage | BinaryIO, filename: str, dpi: int) -> Image.Image:
    """Open an uploaded or stored image, of a PDF the first page."""
    # Without a page selection the page count is not needed
    if _is_pdf(file, filename):
        return pdffile_to_image(file, dpi, 1)
    return open_images(file, filename, dpi, first_only=True)[0][0]


def _is_pdf(file: FileStorage | BinaryIO, filename: str) -> bool:
    """PDFs are recognized by extension or content."""
    stream = file.stream if isinstance(file, FileStorage) else file
    stream.seek(0)
    return os.path.splitext(filename or '')[1].lower() == '.pdf' or stream.read(5) == b'%PDF-'


def open_images(file: FileStorage | BinaryIO, filename: str, dpi: int, pdf_pages: str = '',
                first_only: bool = False, copies: int = 1, page_count: int | None = None) -> tuple[list[Image.Image], int | None]:
    """Open an uploaded or stored image. PDFs are rendered with the given
    resolution, one image for each page pdf_pages selects (only the first of
    them with first_only). The second value is the page count of a PDF,
    None for other files; page_count where it was read before. Files without
    extension are left to Pillow to identify."""
    ext = os.path.splitext(filename or '')[1].lower()
    if page_count is not None or _is_pdf(file, filename):
        if page_count is None:
            page_count = pdf_page_count(file)
        pages = selected_pdf_pages(pdf_pages, page_count)
        if first_only:
            pages = pages[:1]
        elif len(pages) * copies > current_app.config['PRINT_MAX_COUNT']:
            # Checked before any page is rendered
            raise PageSelectionError(f"A print job must not have more than {current_app.config['PRINT_MAX_COUNT']} labels, "
                                     f"{len(pages)} pages and {copies} copies would be {len(pages) * copies}")
        return [pdffile_to_image(file, dpi, page) for page in pages], page_count
    if ext and ext not in {ex for ex, f in Image.registered_extensions().items() if f in Image.OPEN}:
        raise ValueError("Unsupported file type")
    return [imgfile_to_image(file)], None


# What is shared with the installed app from the share menu of a phone
# (share_target in the manifest), kept until the label designer fetches it.
# The page fetches it right away, the limits only matter if it never does.
SHARE_MAX_AGE = 600
SHARE_MAX_ITEMS = 5
_shared: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_shared_lock = threading.Lock()
_URL = re.compile(r'https?://\S+')


@bp.route('/share', methods=['POST'])
def share():
    """Receive a file, link or text from the share menu and open the label
    designer with it: a file becomes the image, a link a QR code, a text the
    label text."""
    item = {}
    file = request.files.get('file')
    if file and file.filename:
        data = file.read()
        if data:
            item = {'name': file.filename, 'type': file.mimetype or 'application/octet-stream',
                    'data': base64.b64encode(data).decode('ascii')}
    if not item:
        # Android puts a shared link into the text, often after a title
        text = request.form.get('text', '').strip()
        url = request.form.get('url', '').strip() or (text if _URL.fullmatch(text) else '')
        if url:
            item = {'url': url}
        elif text:
            item = {'text': text[:MAX_TEXT_LENGTH]}
    if not item:
        return redirect(url_for('.index'), code=303)
    token = secrets.token_urlsafe(16)
    now = time.monotonic()
    with _shared_lock:
        for key in [key for key, (stored, _) in _shared.items() if now - stored > SHARE_MAX_AGE]:
            del _shared[key]
        _shared[token] = (now, item)
        while len(_shared) > SHARE_MAX_ITEMS:
            _shared.popitem(last=False)
    return redirect(url_for('.index', shared=token), code=303)


@bp.route('/share', methods=['GET'])
def shared():
    """Hand what was shared to the label designer, once."""
    token = request.args.get('token', '')
    with _shared_lock:
        stored = _shared.pop(token, None)
    if stored is None or time.monotonic() - stored[0] > SHARE_MAX_AGE:
        return make_response(jsonify({'message': 'The shared content is no longer available, share it again.'}), 404)
    return jsonify(stored[1])


def _asset_version() -> int:
    """Changes when a script or a style sheet of the label designer changes,
    so browsers load the new files and cache them otherwise."""
    static = os.path.join(current_app.root_path, 'static', 'designer')
    return int(max(os.path.getmtime(os.path.join(static, name)) for name in os.listdir(static) if name.endswith(('.js', '.css'))))


@bp.route('/')
def index():
    """The label designer: the editor, the templates, the printed labels and
    the settings. It uses the API below."""
    return render_template('designer.html', config=_designer_config(), version=_asset_version())


def _designer_config() -> dict:
    """What the pages of the label designer are given: label sizes, fonts,
    the defaults of new labels and what each printer model offers."""
    fonts = get_fonts()
    # Of a roll with several labels to a page, the page is shown and one of
    # its labels (piece) designed
    label_sizes = [
        {'id': label.identifier, 'name': label_name(label), 'form': label.form_factor.name.lower(),
         'tape': list(label.tape_size), 'dots': list(label.dots_printable), 'red': label.color == 1,
         'pieces': list(label.pieces), 'piece': list(sheets.piece(label).tape_size),
         'palette': label_palette(label), 'models': tape.TAKEN_BY[label.identifier],
         'dpi': list(drivers.for_label(label.identifier).label_dpi(label)),
         # The shortest and longest endless label as cut, None without a length to set
         'length': list(limits[:2]) if (limits := length_limits(label)) else None}
        for label in LABELS
    ]
    return {
        'base': url_for('.index').rstrip('/'),
        # Instances below one host, e.g. served below different paths
        # behind a reverse proxy, keep what they store in the browser
        # apart. The one at the root keeps the names it always had.
        'storage_prefix': f'{request.script_root}/' if request.script_root else '',
        # The font menu: families, the weight, width and slant of each style
        'font_families': fonts.families(),
        'default_font': ','.join(fonts.get_default_font()),
        'label_sizes': label_sizes,
        'default_label_size': current_app.config['LABEL_DEFAULT_SIZE'],
        # The sizes taken without a known printer
        'takes': tape.takes(None),
        # New labels start with them
        'defaults': {
            'orientation': current_app.config['LABEL_DEFAULT_ORIENTATION'],
            'font_size': current_app.config['LABEL_DEFAULT_FONT_SIZE'],
            'line_spacing': current_app.config['LABEL_DEFAULT_LINE_SPACING'],
            'qrcode_size': current_app.config['LABEL_DEFAULT_QR_SIZE'],
            'margin_top': current_app.config['LABEL_DEFAULT_MARGIN_TOP'],
            'margin_bottom': current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'],
            'margin_left': current_app.config['LABEL_DEFAULT_MARGIN_LEFT'],
            'margin_right': current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'],
            'image_mode': current_app.config['IMAGE_DEFAULT_MODE'],
            'image_bw_threshold': current_app.config['IMAGE_DEFAULT_BW_THRESHOLD'],
        },
        # What the label designer offers for each printer model
        'models': {model: drivers.for_model(model).capabilities(model) for model in MODELS},
        # The kinds of codes, as /api/barcodes lists them
        'codes': _code_kinds(),
        'datetime_codes': datetime_codes(),
        # Kept on the server for every browser, see /api/settings/labels
        'label_settings': _label_settings(),
        'date_formats': list(printer_settings.DATE_FORMATS),
        # How a printer told so finds its labels, by the words of its roll
        'rolls': [[sensing, roll] for sensing, roll in diecut.ROLLS.items()],
    }


# --- Label repository utilities and API -------------------------------------------------
def _get_repo_dir():
    repo = current_app.config.get('LABEL_REPOSITORY_DIR')
    if not repo:
        # default to a folder inside the app root
        repo = os.path.join(current_app.root_path, 'labels')
    os.makedirs(repo, exist_ok=True)
    return repo


def _remove_repo_images(repo: str, base: str, keep: str | None = None):
    """Remove the image files stored for the label named base, except keep."""
    for ext in set(REPO_IMAGE_EXTENSIONS.values()):
        if base + '_image' + ext == keep:
            continue
        path = os.path.join(repo, base + '_image' + ext)
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                current_app.logger.exception(f'Failed to remove associated image {path}')


def _repo_path(repo: str, name: str) -> str:
    """Path of a file in the label repository. The callers sanitize the
    names, this makes sure that no path leaves the repository anyway."""
    path = safe_join(repo, name)
    if path is None:
        raise ValueError(f'Invalid file name: {name}')
    return path


def _stage_file(directory: str, content: bytes) -> str:
    """Write content to a new hidden file with a unique name in directory, so
    that concurrent saves do not write into the same temporary file."""
    tmp_path = os.path.join(directory, f'.{uuid.uuid4().hex}.tmp')
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, 'wb') as fh:
            fh.write(content)
    except BaseException:
        os.remove(tmp_path)
        raise
    return tmp_path


try:
    import fcntl
except ImportError:
    # Windows: one process
    fcntl = None


# Labels are put in place one after the other, so that a new one is not
# saved under a name that another request has just taken
_SAVING = threading.Lock()
# Seconds to wait for another request putting labels in place, e.g. one held
# up on a slow network share
SAVE_LOCK_TIMEOUT = 10


# The status of a print the printer did not do, e.g. it reports an error,
# does not answer or is busy with another job: the same request may succeed
# later. A request that is refused, e.g. for an unknown label size, gets 400.
PRINTER_FAILED = 503


class LabelsBusy(Exception):
    """Labels were put in place by another request for longer than
    SAVE_LOCK_TIMEOUT."""


def _fail(message: str, status: int):
    """The answer to a request that was refused or failed."""
    return make_response(jsonify({'success': False, 'message': message}), status)


def _busy():
    return _fail('Another label is being saved, try again', 503)


# Told once where the folder of the labels cannot be locked
_told_unlockable = False


def _lock_folder(folder: str, deadline: float, shared: bool = False) -> int | None:
    """The folder of the labels opened and locked, shared with other readers
    if shared, waiting until deadline, else LabelsBusy. None where it cannot
    be locked, e.g. on some network shares."""
    global _told_unlockable
    if fcntl is None:
        return None
    fd = None
    try:
        fd = os.open(folder, os.O_RDONLY)
        locked = flock_within(fd, max(deadline - time.monotonic(), 0), shared)
    except OSError as e:
        if fd is not None:
            os.close(fd)
        if not _told_unlockable:
            _told_unlockable = True
            current_app.logger.warning(f'Cannot lock the folder of the labels, saving waits for saves of this process only: {e}')
        return None
    if not locked:
        os.close(fd)
        raise LabelsBusy()
    return fd


@contextlib.contextmanager
def _saving(folder: str):
    """One label put in place at a time: in threads by a lock, in processes
    of the server, e.g. workers of gunicorn, by locking the folder of the
    labels, where it can be locked. Raises LabelsBusy where others take
    longer than SAVE_LOCK_TIMEOUT in all."""
    deadline = time.monotonic() + SAVE_LOCK_TIMEOUT
    if not _SAVING.acquire(timeout=SAVE_LOCK_TIMEOUT):
        raise LabelsBusy()
    try:
        fd = _lock_folder(folder, deadline)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)
    finally:
        _SAVING.release()


@contextlib.contextmanager
def _reading(folder: str):
    """A label and its image read as they were saved together: not while
    one is put in place, alongside other readers. Raises LabelsBusy where a
    save takes longer than SAVE_LOCK_TIMEOUT."""
    fd = _lock_folder(folder, time.monotonic() + SAVE_LOCK_TIMEOUT, shared=True)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


def _write_atomically(path: str, content: bytes):
    """Write a file so that it either has the old or the new content, also
    when writing fails part way through (e.g. disk full). The caller holds
    _saving()."""
    tmp_path = _stage_file(os.path.dirname(path), content)
    try:
        os.replace(tmp_path, path)
    except BaseException:
        os.remove(tmp_path)
        raise


def _write_new(path: str, content: bytes):
    """Write a file that does not exist yet, FileExistsError if it does, also
    when another request writes it at the same time. The written content is
    given the name in one step, so the file is never seen empty or half
    written: as a hard link, which also another process cannot take at the
    same time, else, e.g. on some network shares, after looking that there
    is none while no other save of this server puts one in place: the
    caller holds _saving()."""
    tmp_path = _stage_file(os.path.dirname(path), content)
    try:
        try:
            os.link(tmp_path, path)
        except FileExistsError:
            raise
        except OSError:
            if os.path.lexists(path):
                raise FileExistsError(path) from None
            os.replace(tmp_path, path)
            return
    except BaseException:
        os.remove(tmp_path)
        raise
    # The label is saved, a hidden copy left over does not change that
    try:
        os.remove(tmp_path)
    except OSError as e:
        current_app.logger.warning(f'Staged file {tmp_path} not removed: {e}')


class _Written(str):
    """A number of a JSON body as it is written, e.g. 2.50."""


def _as_numbers(value):
    """value of a JSON body read with _Written, its numbers as numbers."""
    if isinstance(value, _Written):
        return json.loads(value)
    if isinstance(value, dict):
        return {key: _as_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_as_numbers(item) for item in value]
    return value


def _defaults_written(fields):
    """The settings of blanks of a JSON body, their defaults and choices as
    they are written, all other numbers as numbers."""
    if not isinstance(fields, dict):
        return _as_numbers(fields)
    return {name: {key: value if key in ('default', 'choices') else _as_numbers(value) for key, value in field.items()} if isinstance(field, dict) else _as_numbers(field)
            for name, field in fields.items()}


def _json_body() -> dict:
    """The JSON body of the request as object, else empty. Read once. The
    values of blanks, their defaults and choices keep their numbers as they
    are written, e.g. 2.50, all other numbers are numbers."""
    body = request.environ.get('labeldesigner.json_body')
    if body is None:
        data = None
        # Files are uploaded as a form, read once by it, never as JSON
        if request.mimetype != 'multipart/form-data':
            try:
                data = json.loads(request.get_data(cache=True), parse_int=_Written, parse_float=_Written)
                if isinstance(data, dict):
                    data = {key: value if key == 'blanks' else _defaults_written(value) if key == 'fields' else _as_numbers(value)
                            for key, value in data.items()}
                request.environ['labeldesigner.json_object'] = isinstance(data, dict)
            except (ValueError, RecursionError):
                # Also a number of more digits than Python takes, or nested
                # deeper than it reads
                data = None
        body = request.environ['labeldesigner.json_body'] = data if isinstance(data, dict) else {}
    return body


@bp.route('/api/repository/list', methods=['GET'])
def repo_list():
    repo = _get_repo_dir()
    files = []
    for name in sorted(os.listdir(repo)):
        if name.startswith('.') or not name.lower().endswith('.json'):
            continue
        path = os.path.join(repo, name)
        # Read label metadata, a single broken file must not break the list
        try:
            stat = os.stat(path)
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError) as e:
            current_app.logger.warning(f'Skipping unreadable repository file {name}: {e}')
            continue
        if not isinstance(data, dict):
            current_app.logger.warning(f'Skipping repository file {name}: not a label')
            continue
        entry = {'name': name, 'mtime': int(stat.st_mtime), 'size': stat.st_size}
        # Tags written by hand may be anything, those that are no list of
        # texts are left out
        try:
            entry['tags'] = _tags(data.get('tags', []))
        except ValueError:
            entry['tags'] = []
        # The name the label was saved under, the file name lacks e.g. umlauts
        if isinstance(data.get('title'), str) and data['title'].strip():
            entry['title'] = data['title'].strip()
        # Parse label size (support both snake_case and legacy camelCase)
        label_size = stored_label_size(data.get('label_size') or data.get('labelSize'))
        entry['label_size_id'] = label_size
        entry['label_size'] = _label_size_name(label_size)
        files.append(entry)
    return {'files': files}


# Tags of a saved label: words or a few words each, a handful per label,
# as refusals.TAGS_LIMIT says
MAX_TAGS = 20
MAX_TAG_LENGTH = 40


def _tags(value) -> list[str]:
    """The tags of a saved label, from a list or from text with commas
    between them: trimmed, without empty ones, each once whatever its case,
    in the order given. Their case is compared as the label designer does
    (lower(), toLowerCase())."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(tag, str) for tag in value):
        raise ValueError(refusals.TAGS_MESSAGE)
    # The label designer cannot show a tag with a comma
    value = (part for text in value for part in text.split(','))
    tags, seen = [], set()
    for tag in (' '.join(tag.split()) for tag in value):
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            tags.append(tag)
            if len(tags) > MAX_TAGS:
                break
    if len(tags) > MAX_TAGS or any(len(tag) > MAX_TAG_LENGTH for tag in tags):
        raise ValueError(refusals.TAGS_LIMIT)
    return tags


def _stored_tags(path: str) -> dict:
    """{'tags': [...]} of the label saved at path, empty for none."""
    try:
        with open(path, encoding='utf-8') as fh:
            stored = json.load(fh)
        tags = _tags(stored.get('tags')) if isinstance(stored, dict) else []
    except (OSError, ValueError):
        tags = []
    return {'tags': tags} if tags else {}


def stored_label_size(value):
    """A label size as the labels name it. JSON clients may send a whole
    number, e.g. 62 for "62", labels saved like that before are read the same."""
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return value


def _label_size_name(label_size: str | None) -> str | None:
    """The readable name of a label size, e.g. "62 mm endless (DK-22205 / DK-2205)"."""
    if not label_size:
        return None
    label = label_of(label_size)
    return label_name(label) if label is not None else str(label_size)


@bp.route('/api/repository/save', methods=['POST'])
def repo_save():
    # Expect JSON payload, values of blanks and their defaults as they are
    # written, e.g. 2.50
    data = dict(_json_body())
    if not request.environ.get('labeldesigner.json_object'):
        return _fail('No JSON payload provided', 400)
    # The name as for printing, previewing and removing: of the address,
    # else of the body
    name = _requested_name()
    if not name:
        return _fail('No name provided', 400)
    # Checked as when the label is printed, which would refuse them. Blanks
    # and their settings sent as JSON text are saved as objects.
    try:
        for key, parse in (('blanks', _blank_values), ('fields', _blank_fields)):
            if key in data:
                value = parse(data[key])
                if value:
                    data[key] = value
                else:
                    del data[key]
        # A value saved for a choice blank is one of its choices
        for blank, value in data.get('blanks', {}).items():
            check_blank_choice(blank, data.get('fields', {}).get(blank, {}), value)
        # A label saved again without tags keeps those it had
        tags_given = 'tags' in data
        if tags_given:
            data['tags'] = _tags(data['tags'])
            if not data['tags']:
                del data['tags']
        _image_text_gap(data.get('image_text_gap'))
        _text_fit(data)
        cut = _cutting(data.get)[0]
        _flag(data, 'text_wrap')
        _flag(data, 'mirror')
        check_image_position(data.get('image_position'))
        check_parts_order(data.get('parts_order'))
        check_parts_direction(data.get('parts_direction'))
        check_vertical_align(data.get('vertical_align'))
        check_image_mode(data.get('image_mode'))
        if 'label_size' in data:
            data['label_size'] = stored_label_size(data['label_size'])
            if label_of(data['label_size']) is None:
                raise ValueError('Unknown label_size')
            for model in tape.TAKEN_BY[data['label_size']]:
                check_cut(cut, model)
        # Without one it prints on the loaded tape, unknown here, or on
        # LABEL_DEFAULT_SIZE; checked as on 62 where that names no label size
        default = current_app.config['LABEL_DEFAULT_SIZE']
        size = tape.SIZES.get(data.get('label_size', default)) or tape.SIZES[DEFAULT_LABEL_SIZE]
        _printed_length(_label_length(data.get('label_length')), size, _cable_flag(data, size.form_factor))
        check_code_correction(check_code_type(data.get('barcode_type')), data.get('qrcode_correction'))
    except ValueError as e:
        return _refused('Saving the label', e, refusals.INVALID_INPUT)
    filename = secure_filename(name)
    if not filename:
        return _fail('Invalid name', 400)
    if not filename.lower().endswith('.json'):
        filename = filename + '.json'
    # The name kept with it is the one it is saved under
    data['name'] = filename
    repo = _get_repo_dir()
    # A label saved under the name is replaced, unless replace is false
    # Read like the other flags, e.g. true, false, 1 or 0, in the address
    # before the body
    data.pop('replace', None)
    # Set for each printer and given with each print, never saved
    data.pop('leaves_first', None)
    try:
        replace = _whole_or(_requested('replace'), 1)
    except (TypeError, ValueError):
        replace = None
    if replace not in (0, 1):
        return _fail('replace must be true or false', 400)
    replace = replace == 1

    # Accept JSON image payloads that include raw base64 image data in
    # fields `image_data` (base64 string), `image_mime` and optional
    # `image_name` so clients can submit images using pure JSON payloads.
    # Decode before touching the stored files, so a broken payload does not
    # destroy the existing image.
    image_bytes = None
    img_b64 = data.get('image_data')
    if isinstance(img_b64, str) and len(img_b64) > 0:
        try:
            image_bytes = base64.b64decode(img_b64)
        except ValueError:
            return _fail('Invalid image data', 400)

    try:
        # Extract text properties. The label designer sends them as JSON string
        # in fontSettingsPerLine, API clients may send text as list or string.
        text = data.get('fontSettingsPerLine', data.get('text', []))
        if isinstance(text, str):
            text = json.loads(text)
        data['text'] = text

        # The image file is always named after the label, a name chosen by
        # the client could overwrite or refer to images of other labels
        base = os.path.splitext(filename)[0]
        own_images = {base + '_image' + ext for ext in REPO_IMAGE_EXTENSIONS.values()}
        image_name = None
        if image_bytes is not None:
            ext = REPO_IMAGE_EXTENSIONS.get(data.get('image_mime', 'image/png'), '.png')
            image_name = base + '_image' + ext
            data['image'] = image_name
        elif not isinstance(data.get('image'), str) or data['image'] not in own_images:
            data.pop('image', None)

        # Remove raw image data from JSON before saving, redundant information
        # about zeroth line font settings and marks of blanks, which are for
        # previews only
        for key in ('image_data', 'font_size', 'font_inverted', 'font', 'font_align', 'font_checkbox', 'font_color',
                    'line_spacing', 'fontSettingsPerLine', 'mark_blanks'):
            data.pop(key, None)

        # Finally, save the JSON file. The new image is written beforehand but
        # only put in place once the label was saved, and images stored with
        # another file type before are removed after that.
        staged_image = _stage_file(repo, image_bytes) if image_bytes is not None else None
        try:
            # The label, its image and removing images of before, while no
            # other save or removal of a label is under way
            with _saving(repo):
                if replace and not tags_given:
                    data.update(_stored_tags(_repo_path(repo, filename)))
                write = _write_atomically if replace else _write_new
                write(_repo_path(repo, filename), json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8'))
                if staged_image is not None:
                    os.replace(staged_image, _repo_path(repo, image_name))
                    staged_image = None
                # Images of before the label no longer shows go, e.g. one
                # removed in the label designer
                _remove_repo_images(repo, base, keep=data.get('image'))
        finally:
            if staged_image is not None:
                os.remove(staged_image)
    except FileExistsError:
        return _fail('A label is saved under this name already', 409)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to save file', 500)
    return {'success': True, 'name': filename}


@bp.route('/api/repository/tags', methods=['POST'])
def repo_tags():
    """Change the tags of a saved label, the rest stays as saved."""
    filename = secure_filename(_requested_name() or '')
    if not filename:
        return _fail('No name specified', 400)
    try:
        if 'tags' not in _json_body() and 'tags' not in _address_or_form():
            raise ValueError(refusals.TAGS_MISSING)
        tags = _tags(_requested('tags'))
    except ValueError as e:
        return _refused('Changing the tags', e, refusals.INVALID_INPUT)
    repo = _get_repo_dir()
    path = _repo_path(repo, filename)
    try:
        with _saving(repo):
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
            data.pop('tags', None)
            if tags:
                data['tags'] = tags
            _write_atomically(path, json.dumps(data, ensure_ascii=False, indent=2, default=str).encode('utf-8'))
    except FileNotFoundError:
        return _fail('Not found', 404)
    except LabelsBusy:
        return _busy()
    return jsonify({'success': True, 'tags': tags})


@bp.route('/api/repository/load', methods=['GET'])
def repo_load():
    name = _requested_name()
    if not name:
        return _fail('No name specified', 400)
    repo = _get_repo_dir()
    try:
        # Not while one is put in place, its image may not be there yet
        with _reading(repo):
            with open(_saved_path(repo, name), 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            return _label_for_designer(data, repo)
    except FileNotFoundError:
        return _fail('Not found', 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to load file', 500)


def _saved_path(repo: str, name: str) -> str:
    """The file of the saved label name in repo, FileNotFoundError for a
    name that leaves no file name, e.g. '..'."""
    filename = secure_filename(name)
    if not filename:
        raise FileNotFoundError(name)
    return os.path.join(repo, filename)


def _label_for_designer(data: dict, image_dir: str) -> dict:
    """A stored label as the label designer loads it: the text as JSON, the
    settings of the first line, and the image from image_dir as base64 in
    image_data, so that the page can put it into its image upload. An
    image that is there but cannot be read raises OSError: without it, the
    label saved again would lose it."""
    text = _saved_lines(data)
    if data.get('print_type') == 'qrcode':
        text = _code_with_text(data, text)
    data['text'] = json.dumps(text)
    data = fill_first_line_fields(text, data)
    if 'label_size' in data:
        data['label_size'] = stored_label_size(data['label_size'])
    # Flags and the text size read as when the label is printed, e.g. "true"
    # as 1, a text size from shrink_text of labels of before. One that is
    # refused stays as it is, the preview tells why.
    for key, default in (('image_fit', 1), ('image_crop', 0), ('image_contrast', 0), ('text_wrap', 0), ('mirror', 0)):
        try:
            data[key] = '1' if _whole_or(data.get(key), default) > 0 else '0'
        except (TypeError, ValueError):
            pass
    if _given(data.get('text_fit'), None) is None:
        try:
            data['text_fit'] = _text_fit(data)
        except ValueError:
            pass
    # The kind of code as the label designer offers it, e.g., ean13 as EAN13
    if 'barcode_type' in data:
        data['barcode_type'] = check_code_type(data['barcode_type'])
    image_ref = data.get('image')
    if isinstance(image_ref, str) and len(image_ref) > 0:
        image_path = os.path.join(image_dir, secure_filename(image_ref))
        if os.path.exists(image_path):
            with open(image_path, 'rb') as imgfh:
                b = imgfh.read()
            # Guess mime from extension
            ext = os.path.splitext(image_path)[1].lower()
            data['image_name'] = image_ref
            data['image_mime'] = REPO_IMAGE_MIME_TYPES.get(ext, 'application/octet-stream')
            data['image_data'] = base64.b64encode(b).decode('ascii')
    return data


def _code_with_text(data: dict, text) -> list:
    """A label of a code alone as a code with its text left empty, which
    prints the same. Its lines were not printed, a code without content of
    its own took them: a 2D code all of them, a barcode the first."""
    data['print_type'] = 'qrcode_text'
    lines = [line for line in text if isinstance(line, dict)] if isinstance(text, list) else []
    if not str(data.get('code_text') or '').strip():
        code = '\n'.join(str(line.get('text', '')) for line in lines).replace('\r\n', '\n').replace('\r', '\n')
        data['code_text'] = code if check_code_type(data.get('barcode_type')) in codes.NAMES else code.split('\n')[0]
    return [dict(lines[0], text='')] if lines else []


@bp.route('/api/repository/delete', methods=['POST'])
def repo_delete():
    name = _requested_name()
    if not name:
        return _fail('No name specified', 400)
    repo = _get_repo_dir()
    try:
        path = _saved_path(repo, name)
        with _saving(repo):
            os.remove(path)
            # Also remove the image file stored alongside the JSON
            _remove_repo_images(repo, os.path.splitext(os.path.basename(path))[0])
        return {'success': True}
    except FileNotFoundError:
        return _fail('Not found', 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to delete file', 500)


def _requested(key: str):
    """A parameter of the request: of the form or query, else of a JSON
    body, so that a parameter of the address can change one of a body that
    stays the same."""
    # The body is read first: parsed as a form, e.g. JSON sent as a form by
    # curl, it is gone. Read as JSON it is no form: e.g. "&name=" in one of
    # its texts must not be taken for a parameter, only the address goes
    # before it.
    body = _json_body()
    for value in (_address_or_form().get(key), body.get(key)):
        if value is not None and value != '':
            return value
    return None


def _address_or_form():
    """The parameters of the address and of a form, those of the address
    only where the body is JSON."""
    _json_body()
    return request.args if request.environ.get('labeldesigner.json_object') else request.values


def _requested_name(key: str = 'name') -> str | None:
    """The name of a stored label in the request, or its id (key). None for
    one that is no text, e.g. a list or a number."""
    name = _requested(key)
    return name if isinstance(name, str) else None


def _option(key: str, stored: dict, default):
    """An option for printing a stored label: of the request, also false or
    0, else as stored with it, else default."""
    value = _requested(key)
    return value if value is not None else stored.get(key) or default


def _cutting(requested, stored: dict | None = None) -> tuple[str, bool, bool]:
    """How the labels of a job are cut: cut after each label, after the last
    one or not at all, the old cut_once as after the last one. half_cut and
    chain only matter on P-touch printers: a half cut between labels that
    are not cut, and the last label left in the printer, cut off by the next
    job. requested(key) is an option of the request, None where it is left
    out. A request that names cut or cut_once is cut its own way, else as
    stored with the label, else after each label."""
    stored = stored or {}
    names_cut = lambda get: _given(get('cut'), None) is not None or _given(get('cut_once'), None) is not None
    get = requested if names_cut(requested) or not names_cut(stored.get) else stored.get
    option = lambda key, default: _given(get(key), default)
    cut = option('cut', None) or ('last' if _whole(option('cut_once', 0)) == 1 else 'each')
    if cut not in CUTS:
        raise ValueError('cut must be each, last or none')
    # A P-touch printer cuts after the last label unless chain printing, also
    # with auto cut off: not cutting is chain printing
    return cut, _whole(option('half_cut', 1)) != 0, _whole(option('chain', 0)) != 0 or cut == 'none'


def _queue_labels(printer, labels, data, files, high_res, fast) -> list[SimpleLabel]:
    """Adds the labels to the queue, the printer cuts them as printer.cut
    says. The labels queued."""
    # Taken before printing: labels of a job that fails may have come out
    asn = paperless.next_asn(current_app.config, _get_repo_dir(), len(labels)) if paperless.uses_asn(data) else None
    # The copies share the text measured, alike where nothing changes
    layouts = {}
    queued = []
    for i, image in enumerate(labels):
        queued.append(create_label_from_request(data, files, i, image, layouts=layouts, asn=asn))
        printer.add_label_to_queue(queued[-1], high_res=high_res, fast=fast)
    return queued


# The preview of a job in the queue, at most this many pixels on its long side
PREVIEW_SIZE = 240
MAX_TITLE_LENGTH = 200
# Where a job comes from as a request may tell it, the routes name the others
TOLD_SOURCES = ('designer', 'mcp')


def _queued(printer: PrinterQueue, *, run, title: str, source: str, labels: int, drawn: Drawn | None = None,
            return_format=None, extra: dict | None = None):
    """Add the job run() prints to the line of the printer and answer that it
    waits there. drawn is its first label as printer.check() drew it: the
    queue shows it, return_format png returns it as base64 (image) as the
    printer gets it."""
    preview = image = None
    if drawn is not None:
        gray = not drawn.label.in_color
        small = drawn.shown.copy()
        small.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
        preview = image_to_png_bytes(small, gray=gray)
        if return_format == 'png':
            image = base64.b64encode(image_to_png_bytes(drawn.image, gray=gray)).decode()
    try:
        job = print_queue.add(printer.device_specifier, _as_in_this_request(run), title=title, labels=labels, source=source,
                              preview=preview, size=print_queue.image_bytes(printer.images()),
                              explain=_as_in_this_request(lambda e: refusals.message(e, refusals.PRINT_FAILED)))
    except print_queue.Full as full:
        return _line_full(printer, str(full))
    result = {'success': True, 'queued': True, 'job': job.id, 'state': job.state, 'start': print_queue.START, **(extra or {})}
    if image is not None:
        result['image'] = image
    return jsonify(result)


def _line_full(printer: PrinterQueue, why: str):
    """The answer to a job print_queue.add() refuses, why as it tells it."""
    current_app.logger.warning('Not queued for %s, %s', printer.device_specifier, why)
    return _fail(BUSY, PRINTER_FAILED)


def _as_in_this_request(call):
    """call as made in this request: with its app, and its connections checked
    as those of the request, see _check_named(). The request itself is not
    kept, a job may wait long after it."""
    app, mode = current_app._get_current_object(), reach.checking_mode()

    def called(*args):
        with app.app_context(), reach.checking(mode):
            return call(*args)
    return called


def _job_title(data: dict, drawn: Drawn | None = None, fallback: str = 'Label') -> str:
    """The title of a print job in the queue: the one of the request, else
    the first line of the first label as printer.check() drew it, its icons
    left out, else the name it is saved under, else fallback."""
    lines = [line for item in (drawn.label.text or [] if drawn else []) if isinstance(item, dict)
             for line in str(item.get('text', '')).splitlines()]
    # Icons are drawn with fonts of their own, a title shows them as boxes
    lines = [''.join(char for char in line if not icons.is_icon(char)) for line in lines]
    name = data.get('name')
    for title in (_requested('title'), *lines, data.get('title'), name.removesuffix('.json') if isinstance(name, str) else None):
        if isinstance(title, str) and (title := ' '.join(title.split())):
            return title[:MAX_TITLE_LENGTH]
    return fallback


def _job_source(default: str) -> str:
    """Where a print job comes from: as the request tells it for the label
    designer and MCP, else default, that of the route."""
    source = _requested('source')
    return source if source in TOLD_SOURCES else default


def _next_asn(data: dict) -> int | None:
    """The archive serial number a preview shows, None without {{asn}}."""
    return paperless.next_asn(current_app.config, _get_repo_dir()) if paperless.uses_asn(data) else None


def _given(value, default):
    """value, else default where it is left out or empty."""
    return default if value is None or value == '' else value


def _leaves_first(value) -> str | None:
    """Which end of the labels leaves the printer first, None where it is
    left out or empty: as the driver prints them."""
    value = _given(value, None)
    if value not in (None, 'top', 'bottom'):
        raise ValueError('leaves_first must be top or bottom')
    return value


def _whole_or(value, default) -> int:
    """value as a whole number like _whole(), default where it is left out
    or empty."""
    return _whole(_given(value, default))


IMAGE_MODES = ('grayscale', 'black_and_white', 'red_and_black', 'colored')


def check_image_mode(value, default: str = 'grayscale') -> str:
    """The image mode of a request, default where it is left out or empty.
    bw, as the webhook was described, is black_and_white. Another one raises
    ValueError, it was printed in black and white."""
    mode = _given(value, default)
    if mode == 'bw':
        mode = 'black_and_white'
    if mode not in IMAGE_MODES:
        raise ValueError(IMAGE_MODE_REFUSED)
    return mode


def check_code_type(value) -> str:
    """The barcode_type of a request, QR where it is left out or empty, as
    /api/barcodes names it, also when given in lowercase, e.g., ean13."""
    value = _given(value, 'QR')
    if isinstance(value, str) and value.upper() in codes.TYPES:
        return value.upper()
    return value


def check_code_correction(code_type: str, value) -> str:
    """The error correction of a request for a code of code_type, also in
    lowercase. Where none is given the first level the code has, L for codes
    without a choice. A level the code does not have raises ValueError."""
    if isinstance(value, str):
        value = value.strip().upper()
    return codes.correction(code_type, value) or 'L'


def _whole(value) -> int:
    """value as a whole number: of a JSON number also 2.0, of text as it is
    written, e.g. 2, true and false as 1 and 0, also as text."""
    if isinstance(value, (bool, int)):
        return int(value)
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return int(value.strip().lower() == 'true')
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{value} is not a whole number")
        return int(value)
    return int(str(value).strip())


def _load_repo_json(name: str) -> tuple[dict, bytes | None]:
    """A saved label and the content of the image saved with it, None
    without one. Read while no label is put in place: one saved meanwhile
    may not have its image in place yet."""
    repo = _get_repo_dir()
    path = _saved_path(repo, name)
    with _reading(repo):
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)
        if 'label_size' in data:
            data['label_size'] = stored_label_size(data['label_size'])
        image, image_bytes = data.get('image'), None
        if isinstance(image, str) and image and os.path.exists(image_path := os.path.join(repo, secure_filename(image))):
            with open(image_path, 'rb') as fh:
                image_bytes = fh.read()
    data['text'] = json.dumps(_saved_lines(data))
    return data, image_bytes


def _saved_lines(data: dict) -> list:
    """The lines of a saved label, none for text that is no list, e.g. null,
    which saving takes."""
    text = data.get('text')
    return text if isinstance(text, list) else []


def _saved_response(respond, failure: str):
    """respond(data, image_bytes) for the saved label the request names and
    the content of its image, else why there is none: failure where it
    cannot be read."""
    name = _requested_name()
    if not name:
        return _fail('No name specified', 400)
    try:
        data, image_bytes = _load_repo_json(name)
    except FileNotFoundError:
        return _fail('Not found', 404)
    except LabelsBusy:
        return _busy()
    except Exception as e:
        current_app.logger.exception(e)
        return _fail(failure, 500)
    return respond(data, image_bytes)


@bp.route('/api/repository/preview', methods=['GET', 'POST'])
def repo_preview():
    return _saved_response(lambda data, image_bytes: _preview_response(data, _get_repo_dir(), image_bytes, on_tape=True),
                           'Failed to preview file')


def _preview_response(data: dict, image_dir: str, image_bytes: bytes | None = None, mark_blanks: bool = False,
                      on_tape: bool = False):
    """The preview of a stored label whose image is in image_dir, or of
    image_bytes where it was read with the label, as PNG or base64
    (return_format). With mark_blanks, blanks without a value show their
    name. With on_tape, it is made as _print_stored_label() prints it."""
    try:
        data['blanks'] = _requested_blanks(data)
        if on_tape:
            data, _ = _stored_as_printed(data, *_resolved_printer(_requested('printer'), _requested('model')))
        images, page_count = load_label_images(data, {}, first_only=True, image_dir=image_dir, image_bytes=image_bytes)
        label = create_label_from_request(data, {}, image=images[0], mark_blanks=mark_blanks, asn=_next_asn(data))
        im = label.generate()
    except Exception as e:
        return _preview_refused(e)

    response = _preview_of(label, im, _requested('return_format') or 'png')
    # Of a PDF each selected page prints as a label of its own
    if page_count and data.get('print_type', 'text') in ('image', 'image_qrcode_text'):
        response.headers.set('X-PDF-Selected-Pages', str(len(selected_pdf_pages(str(data.get('pdf_pages') or ''), page_count))))
    return response


@bp.route('/api/repository/print', methods=['POST'])
def repo_print():
    # Print a saved repository template by name. The server will load the JSON
    # and perform the same printing logic as the /api/print endpoint.
    return _saved_response(lambda data, image_bytes: _print_stored_label(data, _get_repo_dir(), image_bytes),
                           'Failed to load file')


def _stored_as_printed(data: dict, device: str, model: str) -> tuple[dict, dict]:
    """A stored label as it prints, placed on the tape loaded in the printer,
    and the values printer_settings.for_printing() gives for it."""
    # What the label leaves out as set in the printer settings, the
    # quality and which end leaves first unless the request names them:
    # the label designer saves high_res 0 with each label
    requested = {key: _requested(key) for key in ('high_res', 'fast_printing', 'leaves_first')}
    # A quality the request names leaves out the label's own, e.g. one
    # saved by the previous label designer
    if requested['high_res'] is not None or requested['fast_printing'] is not None:
        data = {key: value for key, value in data.items() if key not in ('high_res', 'fast_printing')}
    settings = _settings_of(device)
    loaded = _loaded_tape(device, model, settings)
    kept = printer_settings.for_printing(settings, model, {
        **{key: value for key, value in _sized(data, loaded).items() if key not in requested}, **requested})
    data = {**data, **{key: value for key, value in kept.items() if value is not None}}
    return tape.on_tape(data, loaded), kept


def _print_stored_label(data: dict, image_dir: str, image_bytes: bytes | None = None):
    """Print a stored label whose image is in image_dir, or image_bytes where
    it was read with the label, like /api/print."""
    # Prepare printer queue using requested or default device/model and label_size from data
    try:
        data['blanks'] = _requested_blanks(data)
        device, model = _resolved_printer(_requested('printer'), _requested('model'))
        data, kept = _stored_as_printed(data, device, model)
        printer = _queue(model, device, data['label_size'])

        # Determine printing options (print_count, cutting, high_res)
        print_count = _check_print_count(_whole(_option('print_count', data, 1)))
        printer.cut, printer.half_cut, printer.chain = _cutting(_requested, data)
        check_cut(printer.cut, printer.model)
        high_res = _whole(_option('high_res', data, 0)) != 0
        fast = _whole(_option('fast_printing', data, 0)) != 0
        # Saved with the label, the request overrides it, else as set for labels
        collate = _requested('collate')
        collate = _whole_or(collate if collate is not None else data.get('collate'), _label_settings()['collate'] or 1) != 0
        # Of the request, else as set for the printer, never of the label
        printer.leaves_first = _leaves_first(kept.get('leaves_first'))
        # The label and its image have to be generated with the resolution
        # it is printed with
        data['high_res'] = high_res
    except Exception as e:
        return _refused('Print job', e, refusals.PRINT_FAILED)
    return _print_job(printer, data, {}, print_count, collate, high_res, fast, _requested('return_format'),
                      image_dir=image_dir, image_bytes=image_bytes)


def _print_job(printer: PrinterQueue, data: dict, files, print_count: int, collate: bool, high_res: bool, fast: bool,
               return_format, **stored):
    """Queue print_count copies of the label, of each of its images, see
    _copies(), to be printed. stored tells where the image of a stored label
    is, see load_label_images()."""
    # Refused before archive serial numbers are taken for it
    if full := print_queue.full(printer.device_specifier):
        return _line_full(printer, full)
    try:
        # Decode the image once, all labels of the job share it. Of a PDF
        # each selected page is a label of its own.
        images, _ = load_label_images(data, files, copies=print_count, **stored)
        if full := print_queue.full(printer.device_specifier, print_queue.image_bytes(images)):
            return _line_full(printer, full)
        queued = _queue_labels(printer, _copies(images, print_count, collate), data, files, high_res, fast)
        drawn = printer.check()
    except Exception as e:
        return _refused('Print job', e, refusals.PRINT_FAILED)
    return _queued(printer, run=printer.process_queue, title=_job_title(data, drawn), source=_job_source('api'), labels=len(queued),
                   drawn=drawn, return_format=return_format)


# --- Demo labels --------------------------------------------------------------------------
@bp.route('/api/demos/list', methods=['GET'])
def demo_list():
    return {'categories': _demo_categories()}


@functools.cache
def _demo_categories() -> list[dict]:
    """The demo labels by category as /api/demos/list lists them. They ship
    with the app, read once."""
    return [{'id': category['id'], 'name': category['name'],
             'labels': [{'id': label_id, 'title': data.get('title') or label_id, 'language': data.get('language'),
                         'label_size_id': data.get('label_size'), 'label_size': _label_size_name(data.get('label_size'))}
                        for label_id, data in demos.labels(category['id'])]}
            for category in demos.categories()]


def _demo_response(label_id: str | None, respond, text_as_json: bool = True):
    """respond(label) for the demo label with the id, or why there is none.
    Only fixed messages are returned, never those of exceptions."""
    if not label_id:
        return _fail('No demo label specified', 400)
    try:
        data = demos.load(label_id)
    except FileNotFoundError:
        return _fail('Not found', 404)
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to load the demo label', 500)
    if text_as_json:
        data['text'] = json.dumps(data.get('text', []))
    return respond(data)


@bp.route('/api/demos/load', methods=['GET'])
def demo_load():
    def respond(data):
        try:
            return _label_for_designer(data, demos.IMAGE_DIR)
        except OSError as e:
            current_app.logger.exception(e)
            return _fail('Failed to load the demo label', 500)
    return _demo_response(_requested_name('id'), respond, text_as_json=False)


@bp.route('/api/demos/preview', methods=['GET'])
def demo_preview():
    # As the label designer draws saved labels: a blank without a default,
    # e.g., the name Grocy sends, shows its name
    return _demo_response(_requested_name('id'), lambda data: _preview_response(data, demos.IMAGE_DIR, mark_blanks=True))


@bp.route('/api/demos/print', methods=['POST'])
def demo_print():
    return _demo_response(_requested_name('id'), lambda data: _print_stored_label(data, demos.IMAGE_DIR))


def _copies(images: list, count: int, collate: bool) -> list:
    """The labels of count copies of several files or pages: as sets, ABC
    ABC, collated, else grouped, AA BB CC."""
    if collate:
        return [image for _ in range(count) for image in images]
    return [image for image in images for _ in range(count)]


def _check_print_count(print_count: int) -> int:
    if print_count < 1:
        raise ValueError("print_count must be greater than 0")
    max_count = current_app.config['PRINT_MAX_COUNT']
    if print_count > max_count:
        raise ValueError(f"print_count must not exceed {max_count}")
    return print_count


def _code_kinds() -> list:
    """The kinds of codes, the 2D codes at the top, QR codes first: which
    are 2D and which levels of error correction each has."""
    return [{'type': code, 'name': codes.NAMES.get(code, code), 'two_d': code in codes.NAMES,
             'corrections': list(codes.CORRECTIONS.get(code, ()))} for code in codes.TYPES]


@bp.route('/api/barcodes', methods=['GET'])
def get_barcodes():
    return {'barcodes': codes.TYPES, 'codes': _code_kinds()}


@bp.route('/api/fonts/sample', methods=['GET'])
def font_family_sample():
    """The name of a font family drawn in its regular style, for the font
    menu of the label designer. A family that cannot draw its name, e.g. one
    of symbols, has none."""
    name = request.args.get('family', '')
    fonts = get_fonts()
    try:
        drawn = font_sample(fonts.get_path(fonts.family_font(name)), name)
    except LookupError:
        return _fail('Not found', 404)
    if drawn is None:
        return make_response('', 204)
    response = make_response(drawn)
    response.mimetype = 'image/png'
    response.cache_control.public = True
    response.cache_control.max_age = 86400
    response.add_etag()
    return response.make_conditional(request)


@bp.route('/api/preview', methods=['POST'])
def preview_from_image():
    try:
        values = request.values.to_dict(flat=True)
        model = None
        # Made as printed: the label size and the quality the request leaves
        # out as set for its printer, the label designer sends both
        if any(values.get(key) in (None, '') for key in ('label_size', 'high_res')):
            device, model = _resolved_printer(values.get('printer'), values.get('model'))
            settings = _settings_of(device)
            # Without a label size it is made for the loaded tape, as printed
            if values.get('label_size') in (None, ''):
                values = _sized(values, _loaded_tape(device, model, settings))
            values = printer_settings.for_printing(settings, model, values)
        files = request.files.to_dict(flat=True)
        # Of a PDF the first selected page
        images, page_count = load_label_images(values, files, first_only=True)
        # Only its previews show the blanks still to fill in, never a label
        # printed or saved
        marks, asn = _whole_or(values.get('mark_blanks'), 0) != 0, _next_asn(values)
        label = create_label_from_request(values, files, image=images[0], mark_blanks=marks, asn=asn)
        im = label.generate()
        # The printer would refuse it, the label designer says so before
        if too_long := _length_problem(values, im, model):
            label.warnings.append(f'{too_long}.')
        # On a roll with several labels to a page: how the copies fill the
        # last page, and the first page with them, each with its counter
        size, state = label_of(values.get('label_size', DEFAULT_LABEL_SIZE)), None
        if sheets.per_sheet(size) > 1:
            pages = len(selected_pdf_pages(str(values.get('pdf_pages') or ''), page_count)) if page_count else 1
            copies, per_copy = _count(values.get('print_count')), _count(values.get('labels_per_copy'), pages)
            state = sheets.fill_state(size, sheets.lying(size, im), copies, per_copy, current_app.config['PRINT_MAX_COUNT'])
            layouts = {}
            more = [create_label_from_request(values, files, i, image=images[0], mark_blanks=marks, layouts=layouts, asn=asn).generate()
                    for i in range(1, min(sheets.per_sheet(size), copies * per_copy))]
            im = sheets.page_view(size, [im] + more)
    except HTTPException:
        # e.g. 413 for a request larger than MAX_CONTENT_LENGTH
        raise
    except Exception as e:
        return _preview_refused(e)

    response = _preview_of(label, im, request.values.get('return_format', 'png'))
    # The label designer offers to choose pages of a PDF with several
    if page_count:
        response.headers.set('X-PDF-Pages', str(page_count))
        # and shows how the selected ones print
        response.headers.set('X-PDF-Selected-Pages', str(len(selected_pdf_pages(str(values.get('pdf_pages') or ''), page_count))))
    if state:
        response.headers.set('X-Label-Sheet', json.dumps(state, separators=(',', ':')))
    # It suggests stronger contrast for a light image on the tapes of the VC-500W
    if (images[0] is not None and _palette(values.get('label_size', DEFAULT_LABEL_SIZE)) == 'color'
            and not _image_contrast(values) and looks_faint(images[0])):
        response.headers.set('X-Image-Faint', '1')
    return response


def _count(value, default: int = 1) -> int:
    """A count the preview only tells about, default where it is no whole
    number of at least 1."""
    try:
        count = _whole_or(value, default)
    except (TypeError, ValueError):
        return default
    return count if count >= 1 else default


def _length_problem(values: dict, im, model: str | None = None) -> str:
    """Why the printer of a request refuses the label im as too long, or an
    empty string, also where the request names no printer it knows. model
    is the one _resolved_printer() found where the request was resolved."""
    if model is None:
        try:
            _, model = _printer(values.get('printer') or '', values.get('model'))
        except ValueError:
            return ''
    label_size = values.get('label_size', DEFAULT_LABEL_SIZE)
    high_res = _high_res(values)
    across = _get_label_dimensions(label_size, high_res)[0]
    along = im.height if im.width == across else im.width
    return drivers.for_model(model).length_problem(model, along, high_res)


def _preview_refused(error: Exception):
    """The answer to a preview that cannot be made: a fixed message, the
    details are in the log."""
    _log_refused_request('Preview', error)
    return make_response(jsonify({'message': refusals.message(error, refusals.PREVIEW_FAILED)}),
                         413 if isinstance(error, TextTooLong) else 400)


# The X-Label-Parts header at most, in bytes: nginx takes 4 KB of headers by
# default, the others need some of them
MAX_PARTS_HEADER = 2048
MAX_COLORS_HEADER = 512


def _preview_of(label: SimpleLabel, im, return_format: str):
    """The response with the preview im of label, as PNG or base64
    (return_format), with what it tells about the label."""
    response_data = image_to_png_bytes(im, gray=not label.in_color)
    if return_format == 'base64':
        response_data = base64.b64encode(response_data)
        content_type = 'text/plain'
    else:
        content_type = 'image/png'
    response = make_response(response_data)
    response.headers.set('Content-type', content_type)
    _label_headers(response, label)
    return response


def _label_headers(response, label: SimpleLabel):
    """What a preview tells about the label made, for /api/preview and the
    previews of saved and demo labels alike."""
    # e.g. text that does not fit on the label, shown by the label designer
    if label.warnings:
        response.headers.set('X-Label-Warning', ' '.join(label.warnings))
    # The orientation setting has no effect, the label designer says so
    if label.auto_orientation:
        response.headers.set('X-Label-Orientation', 'auto')
    # The distance between image or code and text, shown by the label
    # designer next to its setting
    if label.image_text_gap:
        response.headers.set('X-Label-Image-Text-Gap', str(label.image_text_gap))
    # Where the image or code went, shown by the label designer with its
    # setting
    if label.image_side:
        response.headers.set('X-Label-Image-Position', label.image_side)
    # The order of its parts as laid out, shown by the label designer with
    # its setting
    if label.parts_order:
        response.headers.set('X-Label-Parts-Order', ','.join(label.parts_order))
        response.headers.set('X-Label-Parts-Direction', label.parts_direction)
    # Where its lines, code and image are, for the label designer to pick one
    # with a click. Left out for many lines: proxies take a few KB of headers.
    parts = json.dumps(label.parts, separators=(',', ':'), sort_keys=False)
    if len(parts) <= MAX_PARTS_HEADER:
        response.headers.set('X-Label-Parts', parts)
    # Colors the tape prints otherwise, for the label designer to say so
    colors = json.dumps(label.printed_as, separators=(',', ':'))
    if label.printed_as and len(colors) <= MAX_COLORS_HEADER:
        response.headers.set('X-Label-Colors', colors)


# Description of the API (OpenAPI), kept next to the routes it describes
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'openapi.json'), encoding='utf-8') as _spec_file:
    OPENAPI_SPEC = json.load(_spec_file)


@bp.route('/api/openapi.json', methods=['GET'])
def openapi_spec():
    return jsonify(OPENAPI_SPEC)


# The folders of labels whose printer settings the drivers were told, all
# of them once, see Driver.note_settings()
_NOTED: set[str] = set()


@bp.route('/api/printer_status', methods=['GET'])
def get_printer_status():
    # Printers on Bluetooth that did not answer are looked for again only
    # when asked, see devices.note_answer()
    if request.args.get('look') == '1':
        look_again()
    added = _added_printers()
    kept = _kept_settings()
    # Also before the first status after a restart: a Twin Turbo is asked for
    # the roll chosen, also one the search finds
    repo = _get_repo_dir()
    if repo not in _NOTED:
        _NOTED.add(repo)
        for device, settings in printer_settings.by_device(kept).items():
            drivers.note_settings(device, settings)
    for device in [device for device, _ in _configured_devices()] + [device for _, device, _ in added]:
        drivers.note_settings(device, printer_settings.of_printer(kept, device))
    # One printer, e.g. on USB, which the label designer asks every 2 s
    if 'printer' in request.args:
        one = status_of_printer(current_app.config, request.args['printer'], added)
        if one is None:
            return {'message': 'No such printer'}, 404
        status = {'printers': [dict(one)]}
        told = status['printers']
    else:
        status = get_ptr_status(current_app.config, added)
        # Copies, the search keeps the states the printers told
        status['printers'] = [dict(printer) for printer in status.get('printers', [])]
        told = [status] + status['printers']
    demo = _demo_model(kept)
    # The VC-500W prints in color, the label designer then offers colors
    for printer in told:
        if printer.get('path') == 'simulation':
            printer.update(model=demo, red_support=drivers.for_model(demo).colors(demo) == 'red')
        model = printer.get('model')
        printer['color_support'] = drivers.for_model(model).colors(model) == 'color'
        # Asked by the label designer every 2 s where that costs nothing
        printer['follow'] = drivers.for_model(model).followed(printer.get('path') or '', model)
        printer['settings'] = printer_settings.of_printer(kept, printer.get('path', ''), model)
        drivers.for_model(model).note_settings(printer.get('path', ''), printer['settings'])
        printer.update(_as_set(printer, model, printer['settings']))
        if model in drivers.MODELS and drivers.for_model(model).capabilities(model)['calibration']:
            printer['calibration'] = printer_settings.calibration_of(kept, printer.get('path', ''))
        # Labels are shown and printed on the tape in the printer
        printed_on = _tape_of(printer, model, printer['settings'])
        printer['tape'] = tape.describe(printed_on)
        printer['takes'] = tape.takes(model)
        # The label size of that tape, for API users
        printer['label_size'], printer['label_size_assumed'] = printed_on.id, printed_on.assumed
        if printer.get('path'):
            printer['queue'] = print_queue.summary(printer['path'])
        # A job of this server, added to what the printer tells of it
        job = progress.state(printer['path']) if printer.get('path') else None
        if job:
            printer['job'] = dict(printer.get('job') or {}, **job)
    return status


def _queue(model: str, device: str, label_size: str) -> PrinterQueue:
    """A print queue for the printer, resolved before, its labels moved as
    it is calibrated, found as set for it and printed on the roll chosen."""
    queue = PrinterQueue(model=model, device_specifier=device, label_size=label_size, resolved=True)
    kept = _kept_settings()
    calibration = printer_settings.calibration_of(kept, device)
    queue.offset = (calibration['across'], calibration['along'])
    settings = printer_settings.of_printer(kept, device, model)
    queue.sensing, queue.side = settings.get('sensing'), settings.get('side')
    return queue


def _kept_settings(changing: bool = False) -> dict:
    """The settings of the printers kept on the server, see printer_settings.
    A file that cannot be read raises OSError when changing, one that holds
    no settings printer_settings.Broken: it is kept with the settings of the
    other printers."""
    try:
        with open(os.path.join(_get_repo_dir(), printer_settings.FILE), encoding='utf-8') as fh:
            content = fh.read()
    except FileNotFoundError:
        return {'printers': {}}
    except UnicodeDecodeError:
        content = None
    except OSError as e:
        if changing:
            raise
        current_app.logger.warning('Cannot read the settings of the printers: %s', e)
        return {'printers': {}}
    if changing and (content is None or not printer_settings.readable(content)):
        raise printer_settings.Broken()
    return printer_settings.parsed(content or '')


def _broken():
    current_app.logger.warning('The settings of the printers in %s are not valid, they were left as they are', printer_settings.FILE)
    return _fail(printer_settings.BROKEN, 500)


def _settings_of(device: str) -> dict:
    """The settings of a printer, see printer_settings.of_printer(). A request
    reads them once: the key of a printer on USB reads its device ID."""
    return printer_settings.of_printer(_kept_settings(), device)


def _as_set(state: dict, model: str, settings: dict) -> dict:
    """The status of a printer with its roll as set in its settings, see
    diecut.as_set(), where it is told how it finds the labels."""
    return diecut.as_set(state, settings.get('sensing')) if printer_settings.told_how(model) else state


def _tape_of(state: dict, model: str, settings: dict) -> tape.Tape:
    """The tape a printer of the model prints on, as its status state tells
    it, else the one set by hand in its settings, see tape.in_printer(). Of
    a printer told how it finds the labels with its roll as set, none while
    nothing is."""
    found = tape.in_printer(_as_set(state, model, settings), model, settings.get('label_size', ''),
                            current_app.config['LABEL_DEFAULT_SIZE'])
    told = printer_settings.told_how(model) and settings.get('sensing') in diecut.SENSINGS
    return dataclasses.replace(found, roll=diecut.ROLLS[settings['sensing']]) if told else found


def _loaded_tape(device: str, model: str, settings: dict) -> tape.Tape:
    """The tape in the printer a label prints on, as the status tells it, else
    the one set by hand in its settings, see _tape_of(). The model is
    the one _resolved_printer() found. The device ? prints on the simulator.
    Only this printer is asked, and only where its driver tells the tape,
    its last answer where it does not answer and for a moment after, see
    devices.unreachable(). A printer asked for its model in this request is
    not asked again, see _told_model()."""
    device = 'simulation' if is_simulation(device) else device
    state = {}
    if device != 'simulation' and drivers.for_model(model).reports_tape:
        state = g.get('told_states', {}).get(device)
        if state is None and devices.unreachable(device):
            # Not asked again a moment after it could not be reached, the job tries it
            state = devices.failed_printer_state(device, TimeoutError(), model)
        elif state is None:
            try:
                state = device_state(device, model)
            except Exception as e:
                current_app.logger.warning('Printer status unavailable', exc_info=True)
                state = devices.failed_printer_state(device, e, model)
            if state.get('status_type') == devices.FAILED_STATUS_TYPE:
                # E.g. switched off: the prints of the next moment do not wait for it
                devices.note_unreached(device)
    return _tape_of(state, model, settings)


def _sized(data: dict, loaded: tape.Tape) -> dict:
    """data with a label size: one left out or empty is that of the loaded
    tape where it is known, else LABEL_DEFAULT_SIZE as the printer's size of
    that paper, see tape.same_paper(). The size set by hand counts only as
    the loaded tape, see tape.in_printer(), not as one
    printer_settings.for_printing() fills in."""
    if data.get('label_size') not in (None, ''):
        return data
    default = tape.same_paper(current_app.config['LABEL_DEFAULT_SIZE'], loaded.model)
    return {**data, 'label_size': loaded.id if loaded.known else default}


@bp.route('/api/settings', methods=['GET', 'POST'])
def printer_settings_route():
    """The settings of a printer, the common ones with its own. POST changes
    only the ones named, an empty value removes one, and answers all."""
    # JSON numbers and true or false as for printing, e.g. 62 or 0, other
    # values as text, refused like a wrong one
    values = {name: str(int(value)) if isinstance(value, (bool, int)) else
              value if value is None or isinstance(value, str) else str(value)
              for name, value in {**request.values.to_dict(), **_json_body()}.items()}
    try:
        requested = values.pop('printer', None), values.pop('model', None)
        if request.method == 'GET':
            return jsonify(printer_settings.of_printer(_kept_settings(), *_printer(*requested)))
        # Checked against the model the prints use
        device, model = _resolved_printer(*requested)
        changes = printer_settings.checked(values, model, is_simulation(device))
        repo = _get_repo_dir()
        with _saving(repo):
            data = printer_settings.changed(_kept_settings(changing=True), device, changes)
            if printer_settings.DEMO_MODEL in changes:
                model = _demo_model(data)
                data = printer_settings.fitted(data, device, model, current_app.config['LABEL_DEFAULT_SIZE'])
            _write_atomically(os.path.join(repo, printer_settings.FILE),
                              json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    except LabelsBusy:
        return _busy()
    except printer_settings.Broken:
        return _broken()
    except OSError as e:
        current_app.logger.warning('Cannot save the settings of the printers: %s', e)
        return _fail(refusals.SETTINGS_NOT_SAVED, 500)
    except ValueError as e:
        return _refused('Saving the settings', e, refusals.INVALID_INPUT)
    settings = printer_settings.of_printer(data, device, model)
    drivers.for_model(model).note_settings(device, settings)
    # Shown at once, from the last answer of the printer: asking it may take
    # long, the next status asks it
    last = devices.status_cache.get(devices.lock_key(device), {})
    loaded = _tape_of(last, model, settings)
    return jsonify({'success': True, 'settings': settings, 'tape': tape.describe(loaded)})


def _label_settings() -> dict:
    """The label settings kept on the server, see
    printer_settings.label_settings(), read once per request: the copies of
    a job are made alike."""
    if not has_request_context():
        return printer_settings.label_settings(_kept_settings(), get_fonts().family_names())
    if 'labeldesigner.label_settings' not in request.environ:
        request.environ['labeldesigner.label_settings'] = printer_settings.label_settings(_kept_settings(), get_fonts().family_names())
    return request.environ['labeldesigner.label_settings']


@bp.route('/api/settings/labels', methods=['GET', 'POST'])
def label_settings_route():
    """The settings of labels, the same for every browser and the API: how
    dates are written, the order of copies of several files and the font of
    new labels. POST changes only the ones named, an empty value removes
    one, and answers all."""
    if request.method == 'GET':
        return jsonify(_label_settings())
    # JSON numbers and true or false as for printing, e.g. 0
    values = {name: str(int(value)) if isinstance(value, (bool, int)) else
              value if value is None or isinstance(value, str) else str(value)
              for name, value in {**request.values.to_dict(), **_json_body()}.items()}
    try:
        changes = printer_settings.checked_label_settings(values, get_fonts().family_names())
        repo = _get_repo_dir()
        with _saving(repo):
            data = printer_settings.changed_label_settings(_kept_settings(changing=True), changes)
            _write_atomically(os.path.join(repo, printer_settings.FILE),
                              json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    except LabelsBusy:
        return _busy()
    except printer_settings.Broken:
        return _broken()
    except OSError as e:
        current_app.logger.warning('Cannot save the label settings: %s', e)
        return _fail(refusals.LABEL_SETTINGS_NOT_SAVED, 500)
    except ValueError as e:
        return _refused('Saving the label settings', e, refusals.INVALID_INPUT)
    return jsonify({'success': True, 'settings': printer_settings.label_settings(data, get_fonts().family_names())})


def _add_mode() -> str:
    """ADD_PRINTERS: local, any or off, off for a value it does not know."""
    mode = current_app.config.get('ADD_PRINTERS')
    if isinstance(mode, str) and mode.strip().lower() in reach.MODES:
        return mode.strip().lower()
    current_app.logger.warning('ADD_PRINTERS is local, any or off, not %r: adding printers is off', mode)
    return 'off'


def _added(kept: dict | None = None) -> list[dict]:
    """The printers added in the Settings, see printer_settings.added(),
    without those at an address that cannot be added, e.g. edited by hand."""
    def addable(address):
        try:
            return adding.address_of(address) == address
        except ValueError:
            return False
    return [entry for entry in printer_settings.added(_kept_settings() if kept is None else kept) if addable(entry['address'])]


def _configured_devices() -> list[tuple[str, str]]:
    """(device, name) of each printer the configuration names."""
    configured = [(device, name) for _, device, name in configured_printers(current_app.config)]
    if not configured and current_app.config['PRINTER_PRINTER'] not in ('?', 'simulation'):
        configured.append((current_app.config['PRINTER_PRINTER'], ''))
    return configured


def _added_printers() -> list[tuple[str, str, str]]:
    """The printers added in the Settings as (model, device, name), none while
    ADD_PRINTERS is off. Every connection to their hosts is checked from now
    on, but to hosts the configuration names too: they are its choice."""
    mode = _add_mode()
    printers = [] if mode == 'off' else [(e['model'], e['address'], e['name']) for e in _added()]
    configured = {adding.host_of(device) for device, _ in _configured_devices()}
    reach.watch({adding.host_of(device) for _, device, _ in printers} - configured - {None}, mode)
    return printers


def _listed_as(address: str, added: list[dict]) -> str | None:
    """The name, else the address, of the configured or added printer at
    address, None if there is none."""
    key = devices.lock_key(address)
    every = _configured_devices() + [(entry['address'], entry['name']) for entry in added]
    return next((name or device for device, name in every if devices.lock_key(device) == key), None)


@bp.route('/api/printers/added', methods=['GET'])
def added_printers():
    """The printers added in the Settings, whether they are used, and what
    ADD_PRINTERS allows."""
    mode = _add_mode()
    return jsonify({'mode': mode, 'allows': adding.ALLOWS[mode],
                    'printers': [dict(entry, used=mode != 'off') for entry in _added()]})


@bp.route('/api/printers/identify', methods=['POST'])
def identify_printer():
    """What answers at an address, without keeping it: the printer, its tape
    and state, and whether it is listed already."""
    try:
        found = adding.identify({**request.values.to_dict(), **_json_body()}.get('address') or '', _add_mode())
    except Exception as e:
        return _refused('Identifying a printer', e, adding.NOT_ASKED)
    state = found.pop('state')
    found.update(errors=state.get('errors') or [], status_type=state.get('status_type') or '',
                 tape=tape.describe(_tape_of(state, found['model'], {})), listed=_listed_as(found['address'], _added()),
                 device_name=state.get('device_name') or '')
    return jsonify({'success': True, 'printer': found})


@bp.route('/api/printers/add', methods=['POST'])
def add_printer():
    """Add the printer at an address, with a name, once it is known what it
    is. It is kept with the printer settings."""
    values = {**request.values.to_dict(), **_json_body()}

    def checked(added: list[dict], address: str, name: str) -> str:
        """The name, ValueError where another printer has it or the address."""
        if _listed_as(address, added) is not None:
            raise ValueError(adding.ALREADY)
        return adding.name_of(name, [n for _, n in _configured_devices()] + [e['name'] for e in added])
    try:
        if _add_mode() == 'off':
            raise ValueError(adding.OFF)
        # Before the printer is asked
        kept = _kept_settings(changing=True)
    except printer_settings.Broken:
        return _broken()
    except OSError as e:
        current_app.logger.warning('Cannot read the settings of the printers: %s', e)
        return _fail(refusals.SETTINGS_NOT_SAVED, 500)
    except ValueError as e:
        return _refused('Adding a printer', e, refusals.INVALID_INPUT)
    try:
        address = adding.address_of(values.get('address') or '')
        name = checked(_added(kept), address, values.get('name'))
        imported = usbip.is_address(address) and usbip.connected(address)
        found = adding.identify(address, _add_mode(), keep=True)
    except Exception as e:
        return _refused('Adding a printer', e, adding.NOT_ASKED)
    repo = _get_repo_dir()
    try:
        with _saving(repo):
            data = _kept_settings(changing=True)
            # Again, another request may have added it meanwhile; a VC-500W
            # given as tcp:// is kept as vc500w://
            checked(_added(data), found['address'], name)
            data = printer_settings.with_added(data, found['address'], found['model'], name)
            _write_atomically(os.path.join(repo, printer_settings.FILE),
                              json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    except Exception as e:
        # It was imported only to be added
        if usbip.is_address(found['address']) and not imported:
            usbip.disconnect(found['address'])
        if isinstance(e, LabelsBusy):
            return _busy()
        if isinstance(e, printer_settings.Broken):
            return _broken()
        if isinstance(e, ValueError):
            return _refused('Adding a printer', e, refusals.INVALID_INPUT)
        current_app.logger.warning('Cannot save the added printer: %s', e)
        return _fail(refusals.SETTINGS_NOT_SAVED, 500)
    return jsonify({'success': True, 'printer': {'address': found['address'], 'model': found['model'], 'name': name}})


@bp.route('/api/printers/remove', methods=['POST'])
def remove_printer():
    """Remove a printer added in the Settings, named by its address or its
    name. Configured printers stay."""
    wanted = str({**request.values.to_dict(), **_json_body()}.get('printer') or '').strip()
    repo = _get_repo_dir()
    try:
        with _saving(repo):
            data = _kept_settings(changing=True)
            entry = next((e for e in _added(data) if e['address'] == wanted or e['name'] and e['name'].casefold() == wanted.casefold()), None)
            if entry is None:
                raise ValueError(adding.NOT_ADDED)
            data = printer_settings.without_added(data, entry['address'])
            _write_atomically(os.path.join(repo, printer_settings.FILE),
                              json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    except LabelsBusy:
        return _busy()
    except printer_settings.Broken:
        return _broken()
    except OSError as e:
        current_app.logger.warning('Cannot remove the added printer: %s', e)
        return _fail(refusals.SETTINGS_NOT_SAVED, 500)
    except ValueError as e:
        return _refused('Removing a printer', e, refusals.INVALID_INPUT)
    # Let go, unless the configuration names it too
    if usbip.is_address(entry['address']) and _listed_as(entry['address'], []) is None:
        usbip.disconnect(entry['address'])
    return jsonify({'success': True})


def _millimeters(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(printer_settings.CALIBRATION_MESSAGE) from None


@bp.route('/api/calibration', methods=['GET', 'POST'])
def calibration_route():
    """The calibration of the print position of a printer. POST sets it from
    the numbers read left and right on a calibration label printed with
    across and along (as kept if left out), or moves it to across and along,
    or notes the roll it holds for. With save=0 it only answers what that
    gives, e.g. for the label designer before the user saves."""
    values = {**request.values.to_dict(), **_json_body()}
    try:
        device, model, driver = _calibrating(values)
        if request.method == 'GET':
            kept = _kept_settings()
            label_size = values.get('label_size') or _loaded_tape(device, model, printer_settings.of_printer(kept, device)).id
            return jsonify({**printer_settings.calibration_of(kept, device), 'kind': driver.calibration_kind(model, label_size)})
        # Numbers read on the label are given on both sides, none left empty
        if any(name in values and values[name] in (None, '') for name in ('left', 'right')):
            raise ValueError(printer_settings.CALIBRATION_MESSAGE)
        given = {name: _millimeters(values[name]) for name in ('left', 'right', 'across', 'along')
                 if values.get(name) not in (None, '')}
        roll, shift = values.get('roll') or None, None

        def worked_out(kept: dict) -> tuple[float, float, str | None]:
            nonlocal shift
            across, along = given.get('across', kept['across']), given.get('along', kept['along'])
            if 'left' in given or 'right' in given:
                if 'left' not in given or 'right' not in given:
                    raise ValueError(printer_settings.CALIBRATION_MESSAGE)
                shift = printer_settings.from_readings(given['left'], given['right'])
                across = round((across - shift) * 4) / 4
            elif not given and not roll:
                raise ValueError(printer_settings.CALIBRATION_MESSAGE)
            return across, along, roll or kept['roll']
        if str(values.get('save', '1')).lower() in ('0', 'false'):
            across, along, roll = worked_out(printer_settings.calibration_of(_kept_settings(), device))
            printer_settings.checked_offsets(across, along, roll)
            return jsonify({'success': True, 'calibration': {'across': across, 'along': along, 'roll': roll}, 'shift': shift})
        repo = _get_repo_dir()
        with _saving(repo):
            data = _kept_settings(changing=True)
            data = printer_settings.calibrated(data, device, *worked_out(printer_settings.calibration_of(data, device)))
            _write_atomically(os.path.join(repo, printer_settings.FILE),
                              json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    except LabelsBusy:
        return _busy()
    except printer_settings.Broken:
        return _broken()
    except OSError as e:
        current_app.logger.warning('Cannot save the calibration of the printer: %s', e)
        return _fail(refusals.SETTINGS_NOT_SAVED, 500)
    except ValueError as e:
        return _refused('Calibrating', e, refusals.INVALID_INPUT)
    return jsonify({'success': True, 'calibration': printer_settings.calibration_of(data, device), 'shift': shift})


def _calibrating(values: dict) -> tuple[str, str, object]:
    """The device, model and driver of the printer a request names, which
    has to offer calibration."""
    device, model = _resolved_printer(values.pop('printer', None), values.pop('model', None))
    driver = drivers.for_model(model)
    if not driver.capabilities(model)['calibration']:
        raise ValueError(printer_settings.NO_CALIBRATION)
    return device, model, driver


@bp.route('/api/calibration/print', methods=['POST'])
def print_calibration():
    """Print the calibration label for the roll in the printer, moved as
    kept for it or by across and along not saved yet."""
    values = {**request.values.to_dict(), **_json_body()}
    try:
        device, model, _ = _calibrating(values)
        label_size = values.get('label_size') or _loaded_tape(device, model, _settings_of(device)).id
        queue = _queue(model, device, label_size)
        offsets = {name: _millimeters(values[name]) for name in ('across', 'along') if values.get(name) not in (None, '')}
        if offsets:
            queue.offset = (offsets.get('across', queue.offset[0]), offsets.get('along', queue.offset[1]))
            printer_settings.checked_offsets(*queue.offset)
    except Exception as e:
        return _refused('Printing the calibration label', e, refusals.PRINT_FAILED)
    return _queued(queue, run=queue.print_calibration, title='Calibration label', source=_job_source('calibration'), labels=1)


def _job_told(told: dict, printing_on: str | None = None) -> dict:
    """A job as print_queue tells it, its preview as the URL of the image
    and, for the printing one, the progress of its printer."""
    told = dict(told, preview=url_for('.queue_preview', job_id=told['id']) if told['preview'] else None)
    if printing_on is not None and told['state'] == 'printing':
        progressing = progress.state(printing_on)
        if progressing:
            told['progress'] = progressing
    return told


def _line_told(told: dict, device: str) -> dict:
    return {'printing': _job_told(told['printing'], device) if told['printing'] else None,
            'waiting': [_job_told(job) for job in told['waiting']], 'held': told['held']}


def _no_such_job():
    return make_response(jsonify({'success': False, 'message': print_queue.NO_SUCH_JOB, 'start': print_queue.START}), 404)


def _queue_device(requested: str) -> str:
    """The device of the printer a request of the queue names, by its device
    or its name as the print requests take them. Never asks a printer."""
    return _named_device(requested, *_known_printers()[:2])


@bp.route('/api/queue', methods=['GET'])
def queue_of_printers():
    """The jobs of the line of the printer, or of every line with a job or
    a hold. Never asks a printer."""
    if request.args.get('printer'):
        try:
            device = _queue_device(request.args['printer'])
        except ValueError as e:
            return _refused('Reading the print queue', e, refusals.INVALID_INPUT)
        return jsonify({**_line_told(print_queue.line(device), device), 'start': print_queue.START})
    return jsonify(printers=[{'printer': told['printer'], **_line_told(told, told['printer'])} for told in print_queue.lines()],
                   start=print_queue.START)


@bp.route('/api/queue/<job_id>', methods=['GET'])
def queue_job(job_id):
    job = print_queue.get(job_id)
    if job is None:
        return _no_such_job()
    return jsonify({**_job_told(job.told()), 'start': print_queue.START})


@bp.route('/api/queue/<job_id>/preview.png', methods=['GET'])
def queue_preview(job_id):
    job = print_queue.get(job_id)
    if job is None:
        return _fail(print_queue.NO_SUCH_JOB, 404)
    # Read once, it is dropped in time
    preview = job.preview
    if preview is None:
        return _fail(print_queue.NO_PREVIEW, 404)
    response = make_response(preview)
    response.headers.set('Content-type', 'image/png')
    return response


@bp.route('/api/queue/<job_id>/remove', methods=['POST'])
def queue_remove(job_id):
    """Take a job that waits out of its line."""
    refused = print_queue.remove(job_id)
    if refused is None:
        return _no_such_job()
    if refused:
        return _fail(refused, 409)
    return jsonify(success=True)


@bp.route('/api/queue/continue', methods=['POST'])
def queue_continue():
    """Print on after a job that did not print held the jobs behind it."""
    values = {**request.values.to_dict(), **_json_body()}
    if not values.get('printer'):
        return _fail(print_queue.PRINTER_REQUIRED, 400)
    try:
        device = _queue_device(values['printer'])
    except ValueError as e:
        return _refused('Continuing the print queue', e, refusals.INVALID_INPUT)
    print_queue.resume(device)
    return jsonify(success=True)


@bp.route('/api/calibration/example.png', methods=['GET'])
def calibration_example():
    """A calibration label printed off-center, which shows where to read it."""
    values = request.args.to_dict()
    try:
        _, _, driver = _calibrating(values)
        image = driver.calibration_example(values.get('kind', ''))
    except ValueError as e:
        return _refused('The example of a calibration label', e, refusals.INVALID_INPUT)
    response = make_response(image)
    response.headers.set('Content-type', 'image/png')
    return response


@bp.route('/api/print', methods=['POST'])
def print_label():
    """
    API to print a label
    returns: JSON
    """
    try:
        device, model = _resolved_printer(request.values.get('printer'), request.values.get('model'), ask=True)
        # What the request leaves out as set in the printer settings, then
        # placed on the tape loaded in the printer
        settings = _settings_of(device)
        loaded = _loaded_tape(device, model, settings)
        values = printer_settings.for_printing(settings, model, _sized(request.values.to_dict(flat=True), loaded))
        values = tape.on_tape(values, loaded)
        printer = _queue(model, device, values['label_size'])
        print_count = _check_print_count(_whole_or(values.get('print_count'), 1))
        printer.cut, printer.half_cut, printer.chain = _cutting(values.get)
        check_cut(printer.cut, printer.model)
        high_res = _whole_or(values.get('high_res'), 0) != 0
        fast = _whole_or(values.get('fast_printing'), 0) != 0
        collate = _whole_or(values.get('collate'), _label_settings()['collate'] or 1) != 0
        printer.leaves_first = _leaves_first(values.get('leaves_first'))
    except HTTPException:
        raise
    except PrinterUnreachable as e:
        return _fail(refusals.message(e, refusals.PRINT_FAILED), PRINTER_FAILED)
    except Exception as e:
        return _refused('Print job', e, refusals.PRINT_FAILED)
    # Every uploaded image, not only the first
    return _print_job(printer, values, request.files, print_count, collate, high_res, fast, request.values.get('return_format'))


def _log_refused_request(what: str, error: Exception):
    """Log why a request was refused. Invalid input like a missing font size
    or an unknown font raises ValueError or LookupError, it is logged without
    a traceback, which would look like a crash in the log. Everything else,
    also KeyError and IndexError, is logged with its traceback."""
    if refusals.is_invalid_input(error):
        current_app.logger.warning('%s refused: %s', what, error)
        current_app.logger.debug('%s refused', what, exc_info=True)
    else:
        current_app.logger.exception(error)


def _refused(what: str, error: Exception, fallback: str, status: int = 400):
    """Log why a request was refused and answer with its fixed message, see
    refusals.message()."""
    _log_refused_request(what, error)
    return _fail(refusals.message(error, fallback), status)


class PrinterUnreachable(Exception):
    """A printer asked for its model that did not tell it, why in plain words."""


def _told_model(device: str) -> str:
    """The model a printer tells when asked, for one the label designer knows
    no model of, e.g. one that did not answer the status query."""
    driver, model = drivers.resolve(device, 'Unknown')
    # A driver is not guessed for a printer it does not take, e.g. on
    # Bluetooth: it would be sent that driver's commands
    if model != 'Unknown' or not any(pattern.fullmatch(device) for pattern in driver.device_patterns):
        return model
    try:
        state = driver.status(device, model)
    except driver.errors as e:
        current_app.logger.info('Status query of %s failed: %s', device, e)
        raise PrinterUnreachable(devices.not_sent(e)) from e
    told = state.get('model', 'Unknown')
    if told == 'Unknown' and state.get('status_type') == devices.BUSY_STATUS_TYPE:
        raise PrinterUnreachable(BUSY)
    # The answer tells the tape as well, see _loaded_tape()
    g.told_states = {device: state}
    return told


# Where the connections a request makes are checked, see _check_named()
CHECKED = 'labelburg.checked'


def _check_named(device: str):
    """A printer on the network a request names that the server was not told
    about: refused unless ADD_PRINTERS allows its address, else every
    connection of the request is checked as for an added one."""
    mode = _add_mode()
    adding.check_named(device, mode)
    if adding.host_of(device) is not None and has_request_context():
        request.environ.setdefault(CHECKED, []).append(reach.check(mode))


@bp.teardown_request
def _stop_checking(error=None):
    # The threads of the server take one request after another
    for token in reversed(request.environ.pop(CHECKED, [])):
        reach.stop(token)


def _printer(requested_device, requested_model, ask: bool = False, shown: bool = False) -> tuple[str, str]:
    """Return the device and model selected by a request, or the configured
    ones. With PRINTERS, the model of a listed device is the configured one,
    and a request may name the printer instead of its device. With ask, a
    printer requested as model Unknown is asked for its model. A printer on
    the network that is neither configured, added nor found by the search,
    nor shown, one the status of the printers lists, is checked by
    ADD_PRINTERS."""
    printers, configured, model = _known_printers()
    device = _named_device(requested_device or configured, printers, configured)
    listed = [d for _, d, _ in printers]
    # By the same spelling only, another one could name any host
    if not shown and device not in (configured, *listed) and device not in found_devices():
        _check_named(device)
    if requested_model:
        model = drivers.canonical(requested_model)
    else:
        # The model a printer tells, e.g. in its device ID, else the configured
        # one; PRINTER_MODEL is that of PRINTER_PRINTER only
        listed_model = next((m for m, d, _ in printers if d == device), None)
        # One the search found is the model it found, e.g. on Bluetooth,
        # where neither its address nor a device ID tells it
        listed_model = listed_model or found_model(device)
        chosen = listed_model or model
        assumed = not listed_model and (device != configured or isinstance(model, Default))
        model = drivers.resolve(device, chosen, assumed)[1]
        if is_simulation(device):
            model = _demo_model(_kept_settings())
    if ask and model == 'Unknown' and not is_simulation(device):
        model = _told_model(device)
    if model not in MODELS:
        # The driver would refuse it without a message
        raise ValueError(UNKNOWN_MODEL if model == 'Unknown' else f"Unknown printer model: {model}")
    return device, model


def _known_printers() -> tuple[list, str, str]:
    """The printers of the configuration and those added in the Settings as
    (model, device, name), with the device and model configured."""
    printers = configured_printers(current_app.config)
    if printers:
        configured, model = printers[0][1], printers[0][0]
    else:
        configured, model = current_app.config['PRINTER_PRINTER'], drivers.canonical(current_app.config['PRINTER_MODEL'])
    # A name of the configuration first
    return printers + _added_printers(), configured, model


def _named_device(requested: str, printers: list, configured: str) -> str:
    """The device a request names by its device or by the name of one of
    printers, refused where it is neither listed nor taken by a driver."""
    named = {n.casefold(): d for _, d, n in reversed(printers) if n}
    device = named.get(requested.casefold(), requested)
    if device not in (configured, 'simulation', '?', *(d for _, d, _ in printers)) and \
            not any(pattern.fullmatch(device) for pattern in DEVICE_PATTERNS):
        raise ValueError(f"Unsupported printer: {device}")
    return device


def _demo_model(kept: dict) -> str:
    """The model of the demo printer: the one chosen in the Settings, else
    the one configured for it, else the configured model."""
    printers = configured_printers(current_app.config)
    configured = printers[0][0] if printers else drivers.canonical(current_app.config['PRINTER_MODEL'])
    return printer_settings.demo_model(kept) or next((m for m, d, _ in printers if d == 'simulation'), configured)


def _resolved_printer(requested_device, requested_model, ask: bool = False) -> tuple[str, str]:
    """The device and model a request prints on, see _printer(). A printer
    known by its address or device ID is that model, which may ask the
    printer, so a request resolves it once and passes it on."""
    device, model = _printer(requested_device, requested_model, ask)
    # _printer() resolves the configured model, not a requested one
    return device, (drivers.resolve(device, model)[1] if requested_model else model)


# Default for create_label_from_request(): load the image from the request
_LOAD_IMAGE = object()


def _cable_flag(d: dict, kind: FormFactor) -> float | None:
    """The diameter of the cable in mm for a cable flag, None without."""
    if _whole_or(d.get('cable_flag'), 0) <= 0:
        return None
    if _label_type(kind) != LabelType.ENDLESS_LABEL:
        raise ValueError(CABLE_FLAG_REFUSED)
    return cable_diameter(d.get('cable_diameter'))


def _flag_wrap(label_size: str, diameter: float | None, high_res: bool) -> dict:
    """The wrap of a cable flag in dots along the tape and its dpi."""
    if diameter is None:
        return {}
    dpi = label_dpi(label_size, high_res)
    return {'flag_wrap': wrap_dots(diameter, dpi), 'flag_dpi': dpi}


# What a label of an image shows, by image mode, and of a code or text, by
# print type
IMAGE_CONTENT = {'grayscale': LabelContent.IMAGE_GRAYSCALE, 'black_and_white': LabelContent.IMAGE_BW,
                 'red_and_black': LabelContent.IMAGE_RED_BLACK, 'colored': LabelContent.IMAGE_COLORED}
CODE_AND_TEXT_CONTENT = {'text': LabelContent.TEXT_ONLY, 'qrcode': LabelContent.QRCODE_ONLY,
                         'qrcode_text': LabelContent.TEXT_QRCODE}


def _label_type(kind: FormFactor) -> LabelType:
    """The label type of a form factor. P-touch tapes are endless,
    they were taken for round die-cut labels and came out 1 dot long."""
    if kind in (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS):
        return LabelType.ENDLESS_LABEL
    if kind == FormFactor.DIE_CUT:
        return LabelType.DIE_CUT_LABEL
    return LabelType.ROUND_DIE_CUT_LABEL


def _palette(label_size: str) -> str:
    label = label_of(label_size)
    return label_palette(label) if label is not None else 'black'


def _printable_color(value: str, palette: str) -> str:
    """A color the label can show: any on the tapes of the VC-500W, red for
    reddish colors on black/red/white labels, black otherwise. The designer
    offers only these, other clients may send any."""
    red, green, blue = parse_color(value or 'black')
    if palette == 'color':
        return value or 'black'
    if palette == 'red' and red - max(green, blue) > 64:
        return 'red'
    return 'black'


def _image_mode(d: dict) -> str:
    """Images stay in color on the tapes of the VC-500W."""
    mode = check_image_mode(d.get('image_mode'))
    return 'colored' if _palette(d.get('label_size', DEFAULT_LABEL_SIZE)) == 'color' else mode


def _image_contrast(d: dict) -> bool:
    """Whether the image gets stronger contrast, only on the tapes of the
    VC-500W, where light drawings print faint."""
    return _palette(d.get('label_size', DEFAULT_LABEL_SIZE)) == 'color' and _whole_or(d.get('image_contrast'), 0) != 0


def create_label_from_request(d: dict = {}, files: dict = {}, counter: int = 0, image=_LOAD_IMAGE, mark_blanks: bool = False, layouts: dict | None = None,
                              asn: int | None = None):
    label_size = d.get('label_size', DEFAULT_LABEL_SIZE)
    size = label_of(label_size)
    if size is None:
        raise LookupError("Unknown label_size")
    kind = size.form_factor
    diameter = _cable_flag(d, kind)
    barcode_type = check_code_type(d.get('barcode_type'))
    context = {
        'label_size': label_size,
        'print_type': d.get('print_type', 'text'),
        'label_orientation': d.get('orientation', 'standard'),
        'kind': kind,
        # Same defaults as the label designer uses
        'margin_top': int(d.get('margin_top', current_app.config['LABEL_DEFAULT_MARGIN_TOP'])),
        'margin_bottom': int(d.get('margin_bottom', current_app.config['LABEL_DEFAULT_MARGIN_BOTTOM'])),
        'margin_left': int(d.get('margin_left', current_app.config['LABEL_DEFAULT_MARGIN_LEFT'])),
        'margin_right': int(d.get('margin_right', current_app.config['LABEL_DEFAULT_MARGIN_RIGHT'])),
        'border_thickness': int(d.get('border_thickness', 0)),
        'border_roundness': int(d.get('border_roundness', 0)),
        'border_distanceX': int(d.get('border_distance_x', 0)),
        'border_distanceY': int(d.get('border_distance_y', 0)),
        'border_color': d.get('border_color', 'black'),
        'text': json.loads(d.get('text', '[]')),
        'barcode_type': barcode_type,
        'qrcode_size': int(d.get('qrcode_size', 10)),
        'qrcode_correction': check_code_correction(barcode_type, d.get('qrcode_correction')),
        'image_mode': _image_mode(d),
        'image_bw_threshold': int(d.get('image_bw_threshold', 70)),
        'image_fit': _whole_or(d.get('image_fit'), 1) > 0,
        'image_crop': _whole_or(d.get('image_crop'), 0) > 0,
        'image_scaling_factor': float(d.get('image_scaling_factor', 100.0)),
        'image_rotation': int(d.get('image_rotation', 0)),
        'print_color': d.get('print_color', 'black'),
        'timestamp': int(d.get('timestamp', 0)),
        # 600 dpi is for QL printers only
        'high_res': _high_res(d),
        'code_text': d.get('code_text', '').strip(),
        'text_fit': _text_fit(d),
        # Empty: an endless label grows with its content
        'label_length': _label_length(d.get('label_length')),
        'text_wrap': _flag(d, 'text_wrap'),
        'mirror': _flag(d, 'mirror'),
        'image_text_gap': _image_text_gap(d.get('image_text_gap')),
        # Left out or empty: above the text across the tape, left of it along
        'image_position': check_image_position(d.get('image_position')),
        # Left out or empty: as at image_position
        'parts_order': check_parts_order(d.get('parts_order')),
        'parts_direction': check_parts_direction(d.get('parts_direction')),
        # Left out or empty: in the middle of a label with room to spare
        'vertical_align': check_vertical_align(d.get('vertical_align')),
        'blanks': _blank_values(d.get('blanks')),
        # The settings of the blanks, saved with a label or sent with it
        'blank_fields': _blank_fields(d.get('fields')),
        # Previews of the label designer show the blanks still to fill in
        'mark_blanks': mark_blanks,
    }

    print_type = context['print_type']
    # Labels of an image, also with text and a code
    image_label = print_type not in ('text', 'qrcode', 'qrcode_text')
    label_content = IMAGE_CONTENT[context['image_mode']] if image_label else CODE_AND_TEXT_CONTENT[print_type]
    # An image with a code as well, on the other side of the text
    with_code = print_type == 'image_qrcode_text'

    label_orientation = LabelOrientation.ROTATED if context['label_orientation'] == 'rotated' else LabelOrientation.STANDARD
    label_type = _label_type(context['kind'])

    width, height = _canvas(label_size, context['high_res'], label_orientation)
    # A length given for an endless label is its size along the tape
    length = _printed_length(context['label_length'], size, diameter, context['high_res'])
    if length is not None:
        if label_orientation == LabelOrientation.STANDARD:
            height = length
        else:
            width = length

    # For each line in text, we determine and add the font path
    fonts = get_fonts()
    default_font = ','.join(fonts.get_default_font())
    settings = _label_settings()
    family = settings['font_family']
    new_font = fonts.family_font(family) if family else default_font
    warnings = []
    for line in context['text']:
        if 'size' not in line or not str(line['size']).isdigit():
            raise ValueError("Font size is required")
        if int(line['size']) < 1:
            raise ValueError("Font size must be at least 1")
        # Lines without a font, e.g. from the API, use the font of new labels
        if not line.get('font'):
            line['font'] = new_font
        try:
            line['path'] = fonts.get_path(line['font'])
        except FontNotFound:
            # e.g. a label saved on an installation with other fonts
            warning = f'The font {_font_name(line["font"])} is not installed, {_font_name(default_font)} is used instead.'
            if warning not in warnings:
                current_app.logger.warning(warning)
                warnings.append(warning)
            line['font'] = default_font
            line['path'] = fonts.get_path(default_font)
        if len(line.get('text', '')) > MAX_TEXT_LENGTH:
            raise TextTooLong("Text is too long")

    palette = _palette(context['label_size'])
    # The colors asked for that the tape prints otherwise, told with the preview
    printed_as = {}

    def printable(value):
        color = _printable_color(value, palette)
        if value and parse_color(value) != parse_color(color):
            printed_as[value] = color
        return color
    fore_color = parse_color(printable(context['print_color']))
    border_color = parse_color(printable(context['border_color']))
    for line in context['text']:
        line['color'] = printable(line.get('color'))

    if image is _LOAD_IMAGE:
        image = load_label_image(d, files)

    margins = (context['margin_left'], context['margin_right'], context['margin_top'], context['margin_bottom'])
    label = SimpleLabel(
        width=width,
        height=height,
        label_content=label_content,
        label_orientation=label_orientation,
        label_type=label_type,
        label_margin=margins,
        fore_color=fore_color,
        text=context['text'],
        barcode_type=context['barcode_type'],
        qr_size=context['qrcode_size'],
        qr_correction=context['qrcode_correction'],
        image=image,
        image_fit=context['image_fit'],
        image_crop=context['image_crop'],
        image_scaling_factor=context['image_scaling_factor'],
        image_rotation=context['image_rotation'],
        border_thickness=context['border_thickness'],
        border_roundness=context['border_roundness'],
        border_distance=(context['border_distanceX'], context['border_distanceY']),
        border_color=border_color,
        timestamp=context['timestamp'],
        counter=counter,
        asn=asn,
        code_text=context['code_text'],
        template_env_vars=_template_env_vars(),
        warnings=warnings,
        shrink_text=context['text_fit'] == 'shrink',
        fill_text=context['text_fit'] == 'fill',
        text_wrap=context['text_wrap'],
        image_text_gap=context['image_text_gap'],
        image_position=context['image_position'],
        parts_order=','.join(context['parts_order'] or ()),
        parts_direction=context['parts_direction'],
        blanks=context['blanks'],
        blank_fields=context['blank_fields'],
        mark_blanks=context['mark_blanks'],
        layouts=layouts,
        with_code=with_code,
        fixed_length=length is not None,
        vertical_align=context['vertical_align'],
        dpi=label_dpi(label_size, context['high_res']),
        **_flag_wrap(label_size, diameter, context['high_res']),
        mirror=context['mirror'],
        date_format=settings['date_format'] or '%Y-%m-%d',
    )
    # In "standard" orientation die-cut labels are landscape, so a portrait
    # image (e.g. a shipping label on a 103 x 164 mm label) came out small and
    # sideways. With auto-fit, an image without text or code is placed in the
    # orientation that makes it largest. Without auto-fit the chosen
    # orientation is kept.
    if (label_type == LabelType.DIE_CUT_LABEL and image_label and image is not None
            and context['image_fit'] and not label.has_lines() and not with_code):
        label.turn_to_fit(*_fitting_orientation(
            image, width, height, label_orientation, margins, context['image_crop'], context['image_rotation']))
    label.printed_as = printed_as
    return label


def _text_fit(d: dict) -> str:
    """How the text is sized: none as set, shrink made smaller where it does
    not fit, fill as large as fits. Without text_fit, shrink_text tells
    shrink or none."""
    value = d.get('text_fit')
    if value is None or value == '':
        return 'shrink' if _flag(d, 'shrink_text') else 'none'
    if value not in ('none', 'shrink', 'fill'):
        raise ValueError("text_fit must be none, shrink or fill")
    return value


def _label_length(value) -> float | None:
    """The length of an endless label of a request in millimeters, as it is
    cut. None (left out, empty or null) lets the label grow with its
    content."""
    if value is None or str(value).strip() == '':
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        length = float(value)
    except (TypeError, ValueError):
        raise ValueError("label_length must be a length in millimeters or empty") from None
    if not math.isfinite(length) or length <= 0:
        raise ValueError("label_length must be a length in millimeters or empty")
    return length


def _printed_length(length: float | None, label, diameter: float | None, high_res: bool = False) -> int | None:
    """Dots along the tape of the print of an endless label of length
    millimeters as cut, the printer feeds paper before and after it. None
    where the label has no length set: die-cut labels, cable flags, which
    hold the label twice, and tapes of printers whose labels grow with their
    content."""
    limits = length_limits(label)
    if length is None or diameter is not None or limits is None:
        return None
    problem = label_length_problem(label, length)
    if problem:
        raise ValueError(problem)
    return round((length - 2 * limits[2]) / 25.4 * label_dpi(label.identifier, high_res))


def _high_res(d: dict) -> bool:
    """Whether a label is made at 600 dpi: high_res 1, not on color tape."""
    return _whole_or(d.get('high_res'), 0) != 0 and _palette(d.get('label_size', DEFAULT_LABEL_SIZE)) != 'color'


def _flag(d: dict, key: str) -> bool:
    """Whether the flag key of a request is set: 1, also true. Left out or
    empty it is not. text_wrap wraps lines wider than the label into
    several lines, mirror prints the label mirror-inverted."""
    try:
        return _whole_or(d.get(key), 0) != 0
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be 0 or 1") from None


def _image_text_gap(value) -> int | None:
    """The distance between image or code and text of a request in dots, also
    a JSON number like 12.0. None (left out, empty or null) lets it follow
    the text size."""
    if value is None or str(value).strip() == '':
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        gap = _whole(value)
    except ValueError:
        raise ValueError("image_text_gap must be a whole number of dots or empty") from None
    if gap < 0:
        raise ValueError("image_text_gap must not be negative")
    return gap


def _json_object(value, valid, message: str) -> dict:
    """value as an object: as given, e.g. saved with a label, or as JSON text
    in a form or query, its numbers as they are written, e.g. 2.50. Without
    a value it is empty. One that is no object, or with a value that valid()
    refuses, raises ValueError(message)."""
    if value is None or value == '':
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value, parse_int=str, parse_float=str)
        except (ValueError, RecursionError):
            # Also nested deeper than Python reads
            value = None
    if not isinstance(value, dict) or not all(valid(v) for v in value.values()):
        raise ValueError(message)
    return value


def _blank_values(value) -> dict[str, str]:
    """The values for the blanks ({{blank:<name>}}) of a label by name: an
    object of texts or numbers."""
    values = _json_object(value, lambda v: isinstance(v, (str, int, float)) and not isinstance(v, bool),
                          refusals.BLANKS_MESSAGE)
    return {name: str(v) for name, v in values.items()}


BLANK_TYPES = ('text', 'number', 'date', 'choice')


def _blank_setting(field) -> bool:
    """Whether field is the settings of a blank: an object, its type one of
    BLANK_TYPES, its format a text, its default a text or number and its
    choices a list, each where given."""
    if not isinstance(field, dict):
        return False
    default = field.get('default')
    return (field.get('type') in (None, *BLANK_TYPES) and isinstance(field.get('format'), (str, type(None)))
            and (default is None or (isinstance(default, (str, int, float)) and not isinstance(default, bool)))
            and isinstance(field.get('choices'), (list, type(None))))


def _blank_fields(value) -> dict:
    """The settings of the blanks by name, e.g. {"Frozen on": {"type":
    "date", "format": "%d.%m.%Y"}}. A date may start at a day moved from
    today, e.g., {"Best before": {"type": "date", "default": "+5d"}}."""
    fields = _json_object(value, _blank_setting, refusals.FIELDS_MESSAGE)
    for name, field in fields.items():
        default = field.get('default')
        # A date moved from today must be one it can be moved by
        if field.get('type') == 'date' and isinstance(default, str) and default.strip()[:1] in ('+', '-'):
            shift_date(datetime.date.today(), default.strip())
        # A blank with a fixed list of choices
        if field.get('type') == 'choice':
            check_blank_choices(name, field)
    return fields


# Parameters of the requests for stored labels, never values of blanks of
# the same name
STORED_LABEL_PARAMETERS = ('name', 'id', 'printer', 'model', 'print_count', 'cut', 'cut_once', 'half_cut', 'chain',
                           'high_res', 'fast_printing', 'collate', 'leaves_first', 'mirror', 'blanks', 'return_format')


def _requested_blanks(data: dict) -> dict[str, str]:
    """The values for the blanks of a stored label: those stored with it,
    over them fields of the request named like a blank, e.g., product as
    Grocy sends it, and over these the blanks of the request, of a JSON body
    or else of the form or query."""
    requested = _blank_values(_requested('blanks'))
    fields = {}
    for name in _blank_names(data):
        if name not in requested and name.split('.')[0] not in STORED_LABEL_PARAMETERS:
            value = _request_field(name)
            if value is not None:
                fields[name] = value
    return {**_blank_values(data.get('blanks')), **fields, **requested}


def _blank_names(data: dict) -> list[str]:
    """The names of the blanks in the text and code of a stored label."""
    text = data.get('text', [])
    if isinstance(text, str):
        try:
            text = json.loads(text)
        except (ValueError, RecursionError):
            text = []
    lines = [line.get('text') for line in text if isinstance(line, dict)] if isinstance(text, list) else []
    names = []
    for value in lines + [data.get('code_text')]:
        for name in BLANK_TEMPLATE.findall(str(value or '')):
            if name not in names:
                names.append(name)
    return names


def _request_field(name: str) -> str | None:
    """The request field named like a blank, as text, from the form or query or
    else a JSON body; dots reach into objects, in a form as brackets, e.g.,
    stock_entry[best_before_date]. None where it is missing or no text or number."""
    keys = name.split('.')
    form = _address_or_form()
    for key in (name, keys[0] + ''.join(f'[{key}]' for key in keys[1:])):
        if key in form:
            return form[key]
    body = _json_body()
    if name in body:
        value = body[name]
    else:
        value = body
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    return str(value)


def _font_name(font: str) -> str:
    """A font "<family>,<style>" as the font dropdown shows it."""
    family, _, style = font.partition(',')
    return f'{family} ({style})'


def load_label_image(d: dict, files: dict) -> Image.Image | None:
    """Return the uploaded image, or the image stored with a repository label,
    converted to the image mode of the request. Of a PDF the first selected
    page."""
    return load_label_images(d, files, first_only=True)[0][0]


def load_label_images(d: dict, files: dict, first_only: bool = False, copies: int = 1,
                      image_dir: str | None = None, image_bytes: bytes | None = None) -> tuple[list[Image.Image | None], int | None]:
    """The images of a label like load_label_image(), of a PDF one for each
    page pdf_pages selects, e.g. "1-3, 5" or "all", each printed as a label
    of its own. The second value is the page count of a PDF, None
    otherwise. Labels without image get [None]. A stored label has its
    image in image_dir, by default the repository, or in image_bytes where
    it was read with the label."""
    image_mode = _image_mode(d)
    bw_threshold = int(d.get('image_bw_threshold', 70))
    contrast = _image_contrast(d)
    # PDFs are rendered at the resolution the label is printed with
    dpi = label_dpi(d.get('label_size', DEFAULT_LABEL_SIZE), _whole_or(d.get('high_res'), 0) != 0)
    pdf_pages = str(d.get('pdf_pages') or '')
    # Pages only make several labels of an image label
    first_only = first_only or d.get('print_type', 'text') not in ('image', 'image_qrcode_text')

    # Several uploads are printed one after the other, each as a label of its
    # own (of a PDF the selected pages). The page count is that of the first.
    uploads = files.getlist('image') if hasattr(files, 'getlist') else [files['image']] if files.get('image') is not None else []
    if uploads:
        # The page counts of PDFs, where they are read before
        counts = [None] * len(uploads)
        if first_only:
            uploads = uploads[:1]
        elif len(uploads) > 1:
            # Checked before any file is rendered
            counts = [pdf_page_count(upload) if _is_pdf(upload, upload.filename) else None for upload in uploads]
            labels = sum(len(selected_pdf_pages(pdf_pages, count)) if count is not None else 1 for count in counts)
            max_count = current_app.config['PRINT_MAX_COUNT']
            if labels * copies > max_count:
                raise PageSelectionError(f"A print job must not have more than {max_count} labels, {len(uploads)} files "
                                         f"with {labels} labels and {copies} copies would be {labels * copies}")
        images, page_count = [], None
        for i, (upload, count) in enumerate(zip(uploads, counts)):
            opened, count = open_images(upload, upload.filename, dpi, pdf_pages, first_only, copies, count)
            images += [_convert_image(image, image_mode, bw_threshold, contrast) for image in opened]
            if i == 0:
                page_count = count
        return images, page_count

    # If no uploaded FileStorage was provided but the data references an
    # image filename (stored in repository), attempt to load it from the
    # repository directory and convert it consistent with uploaded images.
    image_ref = d.get('image')
    if isinstance(image_ref, str) and len(image_ref) > 0:
        try:
            image_path = os.path.join(image_dir or _get_repo_dir(), secure_filename(image_ref))
            if image_bytes is not None or os.path.exists(image_path):
                with (io.BytesIO(image_bytes) if image_bytes is not None else open(image_path, 'rb')) as fh:
                    images, page_count = open_images(fh, image_path, dpi, pdf_pages, first_only, copies)
                    return [_convert_image(image, image_mode, bw_threshold, contrast) for image in images], page_count
        except PageSelectionError:
            raise
        except Exception:
            current_app.logger.exception('Failed to load repository image')
    return [None], None


def _placed_image_size(image: Image.Image, crop: bool, rotation: int) -> tuple[float, float]:
    """Size of the image after the crop and rotation the label applies."""
    if crop:
        image = SimpleLabel._crop_image_to_content(image)
    angle = math.radians(rotation)
    cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
    return image.width * cos + image.height * sin, image.width * sin + image.height * cos


def _fitting_orientation(image: Image.Image, width: int, height: int, orientation: LabelOrientation,
                         margins: tuple = (0, 0, 0, 0), crop: bool = False, rotation: int = 0) -> tuple:
    """Canvas size and orientation of a die-cut label that show the fitted
    image largest: the given ones or the ones turned by 90 degrees."""
    image_width, image_height = _placed_image_size(image, crop, rotation)
    margin_left, margin_right, margin_top, margin_bottom = margins

    def fitted_scale(canvas_width, canvas_height):
        return min(max(canvas_width - margin_left - margin_right, 1) / image_width,
                   max(canvas_height - margin_top - margin_bottom, 1) / image_height)

    if fitted_scale(height, width) > fitted_scale(width, height):
        turned = LabelOrientation.STANDARD if orientation == LabelOrientation.ROTATED else LabelOrientation.ROTATED
        return height, width, turned
    return width, height, orientation


def _template_env_vars() -> frozenset:
    """Environment variables label templates are allowed to insert."""
    allowed = current_app.config.get('LABEL_TEMPLATE_ENV_VARS', '')
    if isinstance(allowed, str):
        allowed = allowed.split(',')
    return frozenset(name.strip() for name in allowed if name.strip())


def _get_label_dimensions(label_size: str, high_res: bool = False):
    label = label_of(label_size)
    if label is None:
        raise LookupError("Unknown label_size")
    # A label is made for one label of a page, see sheets
    dimensions = sheets.piece(label).dots_printable
    if high_res:
        return (2 * dimensions[0], 2 * dimensions[1])
    return dimensions


def _canvas(label_size: str, high_res: bool, orientation: LabelOrientation) -> tuple[int, int]:
    """Width and height of the canvas of a label: its longer side across the
    canvas, rotated along it."""
    short, long = sorted(_get_label_dimensions(label_size, high_res))
    return (short, long) if orientation == LabelOrientation.ROTATED else (long, short)


def _convert_image(img: Image.Image, image_mode: str, bw_threshold: int = 70, contrast: bool = False) -> Image.Image:
    if image_mode == 'grayscale':
        return convert_image_to_grayscale(img)
    elif image_mode == 'red_and_black':
        return convert_image_to_red_and_black(img)
    elif image_mode == 'colored':
        return stronger_contrast(img) if contrast else img
    else:
        return convert_image_to_bw(img, bw_threshold)


def _image_label(img: Image.Image, label_size: str, label_orientation: LabelOrientation, image_mode: str,
                 bw_threshold: int = 70, contrast: bool = False, high_res: bool = False) -> SimpleLabel:
    """A label of img alone, converted to image_mode and scaled to the
    label size. On die-cut labels the image is turned like with auto-fit in
    the label designer."""
    label_type = _label_type(label_of(label_size).form_factor)
    width, height = _canvas(label_size, high_res, label_orientation)
    img = _convert_image(img, image_mode, bw_threshold, contrast)
    if label_type == LabelType.DIE_CUT_LABEL:
        width, height, label_orientation = _fitting_orientation(img, width, height, label_orientation)
    image = _scale_image_to_label(img, width, height, label_type == LabelType.ENDLESS_LABEL,
                                  label_orientation == LabelOrientation.ROTATED)
    return SimpleLabel(width=width, height=height, label_content=IMAGE_CONTENT.get(image_mode, LabelContent.IMAGE_BW),
                       label_orientation=label_orientation, label_type=label_type, image=image, image_fit=True, text=[])


def _scale_image_to_label(img: Image.Image, width: int, height: int, endless: bool, rotated: bool) -> Image.Image:
    """img scaled to a canvas of width and height."""
    img_width, img_height = img.size
    if endless:
        # For endless labels, scale to fill the fixed dimension and let length vary
        if rotated:
            scale = height / img_height
        else:
            scale = width / img_width
    else:
        # For die-cut labels, fit within both dimensions
        scale = min(width / img_width, height / img_height)

    new_size = (max(1, int(img_width * scale)), max(1, int(img_height * scale)))
    check_image_size(*new_size)
    return img.resize(new_size, Image.Resampling.LANCZOS)


@bp.route('/api/webhook/print', methods=['POST'])
def webhook_print():
    """
    Webhook endpoint for external services to print images directly.

    URL: POST /labeldesigner/api/webhook/print

    Accepts multiple images via multipart form-data or JSON with base64-encoded
    images. Each image is scaled to fit the target label dimensions and printed
    as one job with a cut after each label. On die-cut labels each image is
    turned to the orientation that shows it largest.

    Authentication:
        The webhook is disabled unless the WEBHOOK_PASSWORD environment variable
        (or config key) is set. Generate a secure password with:

            python3 -c "import secrets; print(secrets.token_urlsafe(32))"

        Then export it before starting the server:

            export WEBHOOK_PASSWORD="<generated-password>"

        Authenticate requests using one of:
        - Authorization header: ``Authorization: Bearer <password>``
        - Form field: ``password=<password>``
        - JSON body field: ``"password": "<password>"``

        The password is not accepted as query parameter because URLs end up
        in access logs.

    Multipart form-data:
        - images: one or more image files (field name "images")
        - label_size: label size identifier (default: that of the tape the printer reports, else as set in the printer settings, else from config)
        - orientation: "standard" or "rotated" (default: from config)
        - image_mode: "grayscale", "black_and_white" (also "bw"), "red_and_black", "colored" (default: from config)
        - bw_threshold: black/white threshold 0-255 (default: from config)
        - high_res: 0 or 1 for 600 DPI (default: as the quality set in the printer settings, else 0)
        - fast_printing: 0 or 1, 1 prints faster and a little less sharp (default: as the quality set in the printer settings, else 0)
        - image_contrast: 0 or 1, 1 prints the images with stronger contrast on the tapes of the VC-500W (default: 0)
        - leaves_first: "top" or "bottom", the end of the labels that leaves the printer first (default: as set in the printer settings, else as the driver prints them)
        - printer: printer device specifier (default: from config)
        - model: printer model (default: from config)

    JSON payload:
        {
            "images": [
                {"data": "<base64>", "mime": "image/png"},
                ...
            ],
            "label_size": "62",
            "orientation": "standard",
            "image_mode": "grayscale",
            "bw_threshold": 70,
            "high_res": 0,
            "printer": "...",
            "model": "..."
        }
    """
    # Webhook is disabled unless WEBHOOK_PASSWORD is configured
    webhook_password = current_app.config.get('WEBHOOK_PASSWORD', '')
    if not webhook_password:
        return _fail('Webhook is not enabled', 403)

    # The JSON body, read once, none for a multipart upload
    jdata = _json_body()

    # Authenticate: accept password via Authorization Bearer token, form field, or JSON field
    provided = (request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
                or request.form.get('password', '')
                or jdata.get('password', ''))
    if not provided or not hmac.compare_digest(str(provided).encode(), webhook_password.encode()):
        return _fail('Unauthorized', 401)

    # Collect images from request: multipart file uploads first, otherwise
    # a JSON payload with base64 images. Checked before the printer is asked
    # for its tape.
    uploaded_files = [f for f in request.files.getlist('images') if f.filename]
    json_images = jdata.get('images') if isinstance(jdata.get('images'), list) else []
    max_count = current_app.config['PRINT_MAX_COUNT']
    if len(uploaded_files or json_images) > max_count:
        return _fail(f'Too many images, at most {max_count} per request', 400)
    # Plain base64 strings or objects with them as data
    encoded = [entry if isinstance(entry, str) else entry['data'] for entry in json_images
               if isinstance(entry, str) or isinstance(entry, dict) and 'data' in entry]
    if not uploaded_files and not encoded:
        return _fail('No images provided', 400)

    try:
        # Merge parameters from query/form and JSON body (query/form takes precedence)
        def _param(key, default):
            return _given(_requested(key), default)

        device, model = _resolved_printer(_param('printer', None), _param('model', None))
        settings = _settings_of(device)
        requested = {key: _requested(key) for key in ('label_size', 'high_res', 'fast_printing', 'leaves_first')}
        requested['label_size'] = stored_label_size(requested['label_size'])
        # Validate label_size, before the printer is asked for its tape
        if requested['label_size'] is not None and label_of(requested['label_size']) is None:
            current_app.logger.warning('Webhook print job refused: unknown label_size %r', requested['label_size'])
            return _fail('Unknown label_size', 400)
        loaded = _loaded_tape(device, model, settings)
        # The quality and which end leaves first the request leaves out as set in the printer settings
        given = printer_settings.for_printing(settings, model, _sized(requested, loaded))
        orientation = _param('orientation', current_app.config['LABEL_DEFAULT_ORIENTATION'])
        image_mode = _param('image_mode', current_app.config['IMAGE_DEFAULT_MODE'])
        bw_threshold = int(_param('bw_threshold', current_app.config['IMAGE_DEFAULT_BW_THRESHOLD']))
        high_res = _whole_or(given['high_res'], 0) != 0
        fast = _whole_or(given['fast_printing'], 0) != 0

        placed = tape.on_tape({'label_size': given['label_size'], 'orientation': orientation}, loaded)
        label_size, orientation = placed['label_size'], placed['orientation']
        contrast = _image_contrast({'label_size': label_size, 'image_contrast': _requested('image_contrast')})
        try:
            image_mode = check_image_mode(image_mode, current_app.config['IMAGE_DEFAULT_MODE'])
        except ValueError:
            return _fail(IMAGE_MODE_REFUSED, 400)
        try:
            leaves_first = _leaves_first(given['leaves_first'])
        except ValueError as e:
            return _fail(refusals.message(e, refusals.INVALID_INPUT), 400)
        # Images stay in color on the tapes of the VC-500W, printed at its resolution
        color = _palette(label_size) == 'color'
        if color:
            image_mode, high_res = 'colored', False
        # The message is returned without an exception, those may contain
        # details that should not reach the client
        problem = label_size_problem(label_size, model) or resolution_problem(model, high_res)
        if problem:
            return _fail(problem, 400)

        label_orientation = LabelOrientation.ROTATED if orientation == 'rotated' else LabelOrientation.STANDARD

    except ValueError as e:
        return _refused('Webhook print job', e, refusals.INVALID_INPUT)
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Invalid request parameters', 400)

    def prepare(img: Image.Image) -> SimpleLabel:
        # Converted and scaled right away, so only label sized images are kept
        return _image_label(img, label_size, label_orientation, image_mode, bw_threshold, contrast, high_res)

    labels = []
    dpi = label_dpi(label_size, high_res)
    try:
        for f in uploaded_files:
            labels.append(prepare(open_image(f, f.filename, dpi)))

        if not uploaded_files:
            for entry in encoded:
                labels.append(prepare(open_image(BytesIO(base64.b64decode(entry)), '', dpi)))

    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to read images', 400)

    # Queue each image
    try:
        printer = _queue(model, device, label_size)
        printer.leaves_first = leaves_first
        for label in labels:
            printer.add_label_to_queue(label, high_res=high_res, fast=fast)
        drawn = printer.check()
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to print labels', 400)
    return _queued(printer, run=printer.process_queue, title=_job_title({}, drawn), source=_job_source('webhook'), labels=len(labels),
                   drawn=drawn, extra={'count': len(labels)})


# --- Homebox ------------------------------------------------------------------
# Homebox gets its labels from an external label service with GET and runs a
# print command for them, which sends the image back with POST. Homebox
# replaces the query of the service address, so a saved label is named in
# its path.
HOMEBOX_DEMO = 'storage/013-homebox-62.json'
HOMEBOX_NO_IMAGE = 'Send the label as an image, e.g. the file Homebox made'


def _homebox_printer() -> tuple[str, str, dict, tape.Tape]:
    """The printer labels for Homebox are made for, the first one as in the
    label designer: its device, its model as prints resolve it, its
    settings and the tape loaded in it, as its status tells it."""
    status = get_ptr_status(current_app.config, _added_printers())
    printers = status.get('printers') or [status]
    chosen = next((p for p in printers if p.get('path') == status.get('selected')), printers[0])
    device = chosen.get('path')
    model = _demo_model(_kept_settings()) if is_simulation(device) else drivers.resolve(device, chosen.get('model'))[1]
    settings = _settings_of(device)
    return device, model, settings, _tape_of(chosen, model, settings)


def _as_set_for_printer(data: dict, settings: dict, model: str) -> dict:
    """A label as printed on the printer: in the quality and leaving first
    as set in its printer settings rather than as saved with the label, cut
    as set there where the label leaves it out."""
    own = {key: value for key, value in data.items() if key not in ('high_res', 'fast_printing', 'leaves_first')}
    kept = printer_settings.for_printing(settings, model, own)
    return {**{key: value for key, value in data.items() if key != 'leaves_first'}, **kept}


def _homebox_label(name: str | None, label_size: str) -> tuple[dict, str, bytes | None]:
    """The saved label name, else the demo label "Homebox" on label_size:
    wide, i.e. along endless tape and across die-cut labels. With the folder
    of its image and the image read with it."""
    if name is None:
        data = demos.load(HOMEBOX_DEMO)
        endless = _label_type(label_of(label_size).form_factor) == LabelType.ENDLESS_LABEL
        data.update(label_size=label_size, orientation='rotated' if endless else 'standard', text=json.dumps(data['text']))
        return data, demos.IMAGE_DIR, None
    data, image_bytes = _load_repo_json(name if name.endswith('.json') else f'{name}.json')
    return data, _get_repo_dir(), image_bytes


def _along_endless_tape(img: Image.Image, label_size: str, high_res: bool) -> LabelOrientation:
    """How an image is printed on endless tape: as made where a side is as
    wide as the tape, e.g. a label made here, else its longer side along
    the tape."""
    across = _get_label_dimensions(label_size, high_res)[0]
    if img.width == across:
        return LabelOrientation.STANDARD
    if img.height == across or img.width > img.height:
        return LabelOrientation.ROTATED
    return LabelOrientation.STANDARD


@bp.route('/api/homebox', methods=['GET', 'POST'])
@bp.route('/api/homebox/<name>', methods=['GET', 'POST'])
def homebox(name: str | None = None):
    """GET: the label for Homebox as PNG, the saved label name or the demo
    label "Homebox" on the tape in the printer, its blanks filled with the
    fields Homebox sends, e.g. TitleText and URL. POST: print the image of
    the body on the same label."""
    try:
        device, model, settings, loaded = _homebox_printer()
        data, image_dir, image_bytes = _homebox_label(name, loaded.id)
        # Made as printed, at the resolution of the quality set and on the
        # tape loaded in the printer
        data = tape.on_tape(_as_set_for_printer(_sized(data, loaded), settings, model), loaded)
    except FileNotFoundError:
        return _fail('Not found', 404)
    except LabelsBusy:
        return _busy()
    except (devices.OtherPrinterOnly, ipp.NotOnIpp) as e:
        # A printer of a model that cannot print at its address
        return _refused('Homebox label', e, refusals.INVALID_INPUT)
    except Exception as e:
        current_app.logger.exception(e)
        return _fail('Failed to load file', 500)
    if request.method == 'GET':
        return _preview_response(data, image_dir, image_bytes)

    label_size = data.get('label_size', DEFAULT_LABEL_SIZE)
    high_res = _high_res(data)
    try:
        img = open_image(BytesIO(request.get_data()), '', label_dpi(label_size, high_res))
    except Exception as e:
        _log_refused_request('Homebox label', e)
        return _fail(HOMEBOX_NO_IMAGE, 400)
    try:
        device, model = _printer(device, model, shown=True)
        printer = _queue(model, device, label_size)
        printer.cut, printer.half_cut, printer.chain = _cutting(_requested, data)
        check_cut(printer.cut, printer.model)
        printer.leaves_first = _leaves_first(_requested('leaves_first') or data.get('leaves_first'))
        if _label_type(label_of(label_size).form_factor) == LabelType.ENDLESS_LABEL:
            orientation = _along_endless_tape(img, label_size, high_res)
        else:
            orientation = LabelOrientation.ROTATED if data.get('orientation') == 'rotated' else LabelOrientation.STANDARD
        # Printed as it is shown, dithered like the labels made here
        image_mode = {'color': 'colored', 'red': 'red_and_black'}.get(_palette(label_size), 'grayscale')
        printer.add_label_to_queue(_image_label(img, label_size, orientation, image_mode, high_res=high_res),
                                   high_res=high_res, fast=_whole(_option('fast_printing', data, 0)) != 0)
        drawn = printer.check()
    except Exception as e:
        return _refused('Print job', e, refusals.PRINT_FAILED)
    return _queued(printer, run=printer.process_queue, title=_job_title({'name': name}, drawn), source=_job_source('homebox'),
                   labels=1, drawn=drawn)
