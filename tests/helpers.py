"""Helpers shared by the tests of the API: a client of the app, an example
label and the comparison with reference images."""

import io
import json
import os
import socket
import threading
from typing import Union

from PIL import Image
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from app import create_app
from config import Config
from app.labeldesigner import print_queue
from app.labeldesigner.routes import restore_print_queue


UPDATE_IMAGES = False


EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': 62,
    'orientation': 'standard',
    'print_count': '1',
    'cut_once': '0',
    # Margins and border the reference images were created with
    'margin_top': '12',
    'margin_bottom': '12',
    'margin_left': '20',
    'margin_right': '20',
    'border_thickness': '1',
}


def pixels(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.convert('RGB').tobytes()


def verify_image(response_data: bytes, expected_image_path: str):
    # Compare generated preview with the image in file (if it exists). The
    # pixels count, not how the PNG stores them.
    if not UPDATE_IMAGES and os.path.isfile('tests/images/' + expected_image_path):
        with open('tests/images/' + expected_image_path, 'rb') as f:
            expected_data = f.read()
        if pixels(response_data) != pixels(expected_data):
            # Save image for debugging purposes
            failed_image_path = 'tests/' + 'FAILED_' + expected_image_path
            with open(failed_image_path, 'wb') as f:
                f.write(response_data)
            raise AssertionError("Generated image does not match expected image")
        return

    # Write image into file (missing reference image or UPDATE_IMAGES)
    with open('tests/images/' + expected_image_path, 'wb') as f:
        f.write(response_data)


def make_client(tmp_path, empty_repo: bool = False, model: Union[str, None] = None,
                printer: Union[str, None] = None) -> FlaskClient:
    config = Config
    if empty_repo:
        # point repository to temporary folder, already as the app starts:
        # it opens the print queue kept there
        repo_dir = tmp_path / 'label_repo'
        os.makedirs(repo_dir, exist_ok=True)
        config = type('EmptyRepo', (Config,), {'LABEL_REPOSITORY_DIR': str(repo_dir)})
    app = create_app(config)
    # As the server starts, the jobs left in its queue print
    restore_print_queue(app)
    # Bind app context
    app.app_context().push()
    app.config['TESTING'] = True
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
    if model:
        app.config['PRINTER_MODEL'] = model
    if printer:
        app.config['PRINTER_PRINTER'] = printer
    return app.test_client()


TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Test', 'size': '40', 'align': 'center'}])


def _upload(image: Image.Image, filename: str) -> FileStorage:
    buffer = io.BytesIO()
    if filename.endswith('.pdf'):
        image.save(buffer, 'PDF', resolution=150)
    else:
        image.save(buffer, 'PNG')
    buffer.seek(0)
    return FileStorage(stream=buffer, filename=filename)


def _red_square() -> Image.Image:
    image = Image.new('RGB', (300, 150), 'white')
    image.paste(Image.new('RGB', (200, 100), (255, 0, 0)), (50, 25))
    return image


def _image_and_text_boxes(png: bytes):
    """The boxes of a mid-gray image and of the black text on a label. The
    edges of the text have a few pixels of the same gray, they are left
    out."""
    from PIL import ImageFilter
    image = Image.open(io.BytesIO(png)).convert('L')
    gray = image.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.MinFilter(5)).getbbox()
    return (gray[0] - 2, gray[1] - 2, gray[2] + 2, gray[3] + 2), image.point(lambda v: 255 if v < 100 else 0).getbbox()


def _pdf(*sizes) -> bytes:
    """A PDF with a black page of each size, so the pages can be told apart
    by their aspect ratio."""
    pages = [Image.new('L', size, 'black') for size in sizes]
    pdf = io.BytesIO()
    pages[0].save(pdf, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return pdf.getvalue()


def outcome(client: FlaskClient, response) -> tuple[str, str]:
    """The state of the print job a print request added and the message of
    a job that was not printed."""
    job = client.get(f"/labeldesigner/api/queue/{response.get_json()['job']}").get_json()
    return job['state'], job['message']


def printing() -> bool:
    """Whether this thread prints the jobs of the queue, rather than a request
    drawing the first label of its job to check it."""
    return threading.current_thread().name.startswith(print_queue.WORKER_PREFIX)


def sent_raster(monkeypatch) -> list:
    """The raster data of every job printed on a Brother printer, also on the
    simulator."""
    from app.labeldesigner.drivers.brother import BrotherRaster
    sent = []
    render = BrotherRaster.render

    def capture(self, queue, entries, keep=None):
        job = render(self, queue, entries, keep)
        if printing():
            sent.append(job.data)
        return job
    monkeypatch.setattr(BrotherRaster, 'render', capture)
    return sent


def without_status(monkeypatch):
    """Prints do not ask the printer for its tape, which a fake printer
    counting its traffic would see."""
    monkeypatch.setattr('app.labeldesigner.routes.device_state', lambda device, model: {})


def within(seconds: float, action):
    """What action() returns or raises, failing the test where it takes
    longer than seconds instead of waiting for it."""
    result = {}

    def run():
        try:
            result['value'] = action()
        except BaseException as e:
            result['error'] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f'Still running after {seconds} s'
    if 'error' in result:
        raise result['error']
    return result.get('value')


class Answering:
    """A server on localhost that answers each connection with send(connection)
    once it has read the request, until it is closed."""

    def __init__(self, send):
        self.send, self.closed = send, threading.Event()
        self.server = socket.create_server(('127.0.0.1', 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(connection,), daemon=True).start()

    def _answer(self, connection):
        with connection:
            try:
                connection.settimeout(2)
                connection.recv(65536)
                self.send(connection)
            except OSError:
                pass

    def close(self):
        self.closed.set()
        self.server.close()


def printed_days_ago(job_id: str, days: float):
    """The label printed of a job kept as printed days ago, e.g. to tell
    whether it shows another date today."""
    from app.labeldesigner import print_store
    path = os.path.join(print_store.history_folder(), print_store.HISTORY_FILE)
    with print_store._history_locked():
        with open(path, encoding='utf-8') as fh:
            entries = [json.loads(line) for line in fh]
        for entry in entries:
            if entry['id'] == job_id:
                entry['started_at'] = entry.get('started_at', entry['ended_at']) - days * 86400
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(''.join(json.dumps(entry) + '\n' for entry in entries))
