#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import signal
import sys

from flask.helpers import get_debug_flag
from werkzeug.serving import is_running_from_reloader

from app import create_app, parse_args
from app.labeldesigner.routes import restore_print_queue

app = create_app()


def exit_on_sigterm():
    # Leave through sys.exit so that the exit handlers, e.g. the print queue's, run
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


def serves() -> bool:
    """Whether this process serves the requests: in debug mode Flask runs
    them in a second process and restarts it on changes."""
    debug = get_debug_flag() if 'FLASK_DEBUG' in os.environ else app.debug
    return not debug or is_running_from_reloader()


def main(argv=None):
    parse_args(app, argv)
    exit_on_sigterm()
    # The jobs that waited when the server stopped print now, on the printer
    # of the command line
    if serves():
        restore_print_queue(app)
    app.run(host = app.config['SERVER_HOST'], port = app.config['SERVER_PORT'])


if __name__ == "__main__":
    main()
