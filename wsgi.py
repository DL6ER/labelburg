#!/usr/bin/env python
# -*- coding: utf-8 -*-

from app import create_app
from app.labeldesigner.routes import restore_print_queue

app = create_app()
# The jobs that waited when the server stopped print now
restore_print_queue(app)

if __name__ == "__main__":
    app.run()
