"""What the README and the guides tell users to do is where they look."""
import glob
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_no_guide_names_a_page_that_is_gone():
    # The Settings are on the label designer at /labeldesigner/ itself
    files = [os.path.join(ROOT, 'README.md'), os.path.join(ROOT, 'config.py')] + glob.glob(os.path.join(ROOT, 'docs', '*.md'))
    found = []
    for path in files:
        with open(path, encoding='utf-8') as fh:
            found += [(os.path.relpath(path, ROOT), number) for number, line in enumerate(fh, 1) if '/labeldesigner/next' in line]
    assert found == []


def test_every_guide_gives_the_printer_to_continue_a_queue():
    # The route answers 400 without it
    for path in glob.glob(os.path.join(ROOT, 'docs', '*.md')) + [os.path.join(ROOT, 'README.md')]:
        with open(path, encoding='utf-8') as fh:
            text = fh.read()
        assert text.count('/queue/continue`') == text.count('/queue/continue` with `printer`'), path
