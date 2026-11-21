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
