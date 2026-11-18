"""Archive serial numbers (ASN) of Paperless-ngx for the labels stuck on
documents before they are scanned. {{asn}} on a label is the next free one,
counted on for each label. Paperless knows only the ASNs of the documents it
has, so the next one not printed yet is kept in the folder of the labels:
labels printed again before their documents are scanned get numbers of their
own."""

import json
import os
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .drivers.devices import flock_within
from .label import ASN_TEMPLATE

# The next ASN not printed yet, in the folder of the labels
STATE_FILE = '.next_asn'
TIMEOUT = 5
LOCK_TIMEOUT = 10
# A preview takes the answer of Paperless for this long
PREVIEW_CACHE = 30

UNREACHABLE = 'Paperless-ngx cannot be reached, so the next archive serial number is unknown'
REFUSED = 'Paperless-ngx refused the token in PAPERLESS_TOKEN'
BUSY = 'Another label with an archive serial number is being printed, try again'

_lock = threading.Lock()
# (when, URL, next ASN) of the last answer, for previews
_answered = (0.0, '', 0)


def uses_asn(data: dict) -> bool:
    """Whether the text or code of a label has {{asn}}."""
    text = data.get('text', '')
    if not isinstance(text, str):
        text = json.dumps(text)
    return any(ASN_TEMPLATE.search(str(value or '')) for value in (text, data.get('code_text')))


def _from_paperless(config, preview: bool) -> int | None:
    """The next ASN Paperless gives, None without PAPERLESS_URL. Raises
    ValueError with UNREACHABLE or REFUSED where it does not tell."""
    global _answered
    url = (config.get('PAPERLESS_URL') or '').rstrip('/')
    if not url:
        return None
    asked, answered_url, number = _answered
    if preview and answered_url == url and time.monotonic() - asked < PREVIEW_CACHE:
        return number
    request = Request(url + '/api/documents/next_asn/',
                      headers={'Authorization': f"Token {config.get('PAPERLESS_TOKEN') or ''}", 'Accept': 'application/json'})
    try:
        with urlopen(request, timeout=TIMEOUT) as answer:
            number = json.loads(answer.read())
    except HTTPError as e:
        raise ValueError(REFUSED if e.code in (401, 403) else UNREACHABLE) from e
    except (OSError, ValueError) as e:
        raise ValueError(UNREACHABLE) from e
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise ValueError(UNREACHABLE)
    _answered = (time.monotonic(), url, number)
    return number


def next_asn(config, folder: str, count: int = 0) -> int:
    """The next ASN: the next one of Paperless, or one after the last printed
    where that is higher. With count, that many are taken for a job."""
    paperless = _from_paperless(config, preview=not count)
    path = os.path.join(folder, STATE_FILE)
    if not count:
        return max(paperless or 1, _read(path))
    with _lock:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Also against other processes of the server
            if not flock_within(fd, LOCK_TIMEOUT):
                raise ValueError(BUSY)
            start = max(paperless or 1, _read(path))
            os.ftruncate(fd, 0)
            os.pwrite(fd, f'{start + count}\n'.encode(), 0)
            return start
        finally:
            os.close(fd)


def _read(path: str) -> int:
    try:
        with open(path, encoding='ascii') as state:
            return max(int(state.read().strip() or 1), 1)
    except (OSError, ValueError):
        return 1
