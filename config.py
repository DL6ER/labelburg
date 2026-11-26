"""
This are the default settings. (DONT CHANGE THIS FILE)
Adjust your settings in 'instance/application.py'. Settings listed in
ENV_OVERRIDES in app/__init__.py can also be set with environment variables
of the same name, which take precedence over the files.
"""

import os

basedir = os.path.abspath(os.path.dirname(__file__))

class Config(object):
    DEBUG = False
    LOG_LEVEL = 'WARNING'

    # Host names the server answers besides those of the home network (IP
    # addresses, single-label names and LOCAL_SUFFIXES in app/hosts.py),
    # e.g. the public name behind a reverse proxy (comma-separated,
    # ".example.com" with its subdomains). Requests for other hosts are
    # refused, which protects against DNS rebinding attacks. MCP_HOSTS, its
    # former name, is still read.
    HOSTS = None

    # Host names the label designer is reached by (comma-separated, e.g.
    # "labels.local,192.168.0.10"). When set, only these are answered, not
    # those of the home network and HOSTS.
    TRUSTED_HOSTS = None

    # Several printers for one label designer, as (model, device) pairs, e.g.
    # [('QL-700', 'usb://0x04f9:0x2042'), ('QL-1100', 'file:///dev/usb/lp1')]
    # or the environment variable PRINTERS="QL-700=usb://0x04f9:0x2042,...".
    # A third item names a printer, e.g., ('QL-700', 'usb://...', 'Kitchen'),
    # in the variable "Kitchen:QL-700=usb://...". The label designer shows the
    # name, requests may choose the printer by it.
    # When set, PRINTER_PRINTER, PRINTER_MODEL and the automatic detection are
    # not used, requests that name no printer go to the first one. The demo
    # printer is listed only when configured, e.g. 'QL-700=simulation'.
    PRINTERS = []

    # Printers the search does not find, e.g. shared over USB/IP, may be
    # added in the Settings of the label designer: 'local' only at addresses
    # of the local network, 'any' also elsewhere, 'off' not at all, printers
    # added before are then not used. Anyone who reaches the label designer
    # can add them, there is no login. The same holds for network printers
    # requests name by their address that are neither configured, added nor
    # found by the search.
    ADD_PRINTERS = 'local'

    # Number of reverse proxies in front of the server whose X-Forwarded-For,
    # -Proto, -Host and -Prefix headers are trusted, e.g. 1 for nginx serving
    # the label designer below a path. 0 ignores these headers.
    PROXY_COUNT = 0

    SERVER_PORT = 8013
    SERVER_HOST = '0.0.0.0'

    PRINTER_MODEL = 'QL-800'
    PRINTER_PRINTER = '?'

    LABEL_DEFAULT_ORIENTATION = 'standard'
    LABEL_DEFAULT_SIZE = '62'
    LABEL_DEFAULT_FONT_SIZE = 70
    LABEL_DEFAULT_QR_SIZE = 10
    LABEL_DEFAULT_LINE_SPACING = 100
    LABEL_DEFAULT_FONT_FAMILY = 'DejaVu Serif'
    LABEL_DEFAULT_FONT_STYLE = 'Book'

    IMAGE_DEFAULT_MODE = 'grayscale'
    IMAGE_DEFAULT_BW_THRESHOLD = 70

    # In dots on 62 mm wide labels, other labels get them in proportion to
    # their width
    LABEL_DEFAULT_MARGIN_TOP = 24
    LABEL_DEFAULT_MARGIN_BOTTOM = 24
    LABEL_DEFAULT_MARGIN_LEFT = 35
    LABEL_DEFAULT_MARGIN_RIGHT = 35

    LABEL_REPOSITORY_DIR = os.path.join(basedir, 'labels')

    # Environment variables label templates may insert via {{env:<var>}}
    # (comma-separated). All other variables are replaced by an empty string,
    # otherwise anyone with access to the preview could read secrets like
    # WEBHOOK_PASSWORD.
    LABEL_TEMPLATE_ENV_VARS = ''

    # Paperless-ngx, where {{asn}} takes the next archive serial number from,
    # and an API token of a user there
    PAPERLESS_URL = ''
    PAPERLESS_TOKEN = ''

    # Maximum number of labels a single print request may produce
    PRINT_MAX_COUNT = 100

    # The MCP endpoint /mcp for AI assistants, where its packages are
    # installed (requirements-mcp.txt)
    MCP_ENABLED = True

    # Most labels an AI assistant may print with one call to the MCP endpoint
    MCP_MAX_COUNT = 10

    # A token the MCP endpoint then asks for as "Authorization: Bearer
    # <token>", e.g. the one Home Assistant gets from the discovery of an app
    MCP_TOKEN = None

    # Maximum size of a request (uploaded images, JSON payloads) in bytes
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    # Folder with fonts to offer in addition to the system fonts. Without the
    # system fonts (SYSTEM_FONTS = False) only those of this folder are
    # offered, e.g. a few chosen ones linked into it.
    FONT_FOLDER = ''
    SYSTEM_FONTS = True

    # Webhook print endpoint password. The webhook is disabled when empty.
    # Generate a secure password with:
    #   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    WEBHOOK_PASSWORD = ''
