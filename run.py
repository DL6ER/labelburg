#!/usr/bin/env python
# -*- coding: utf-8 -*-

import signal
import sys

from app import create_app, parse_args

app = create_app()


def exit_on_sigterm():
    # Leave through sys.exit so that the exit handlers, e.g. the print queue's, run
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))


if __name__ == "__main__":
    parse_args(app)
    exit_on_sigterm()
    app.run(host = app.config['SERVER_HOST'], port = app.config['SERVER_PORT'])
