import base64
import hashlib
import io
import json
import os
import tarfile

import pytest

from tools import update_fonts

# Two icons in the area labels take them from, and "blank" outside of it
MDI_CSS = '''.mdi-blank::before {
  content: "\\F68C";
}
.mdi-lightbulb::before {
  content: "\\f0335";
}
.mdi-account::before {
  content: "\\F0004";
}
'''


def tarball(files: dict) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w:gz') as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f'package/{path}')
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def integrity(data: bytes) -> str:
    return 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """The package on a fake npm registry, and a lock file naming it."""
    packages = {
        '@mdi/font': tarball({
            'fonts/materialdesignicons-webfont.ttf': b'mdi font',
            'LICENSE': b'mdi license',
            'css/materialdesignicons.css': MDI_CSS.encode(),
        }),
    }
    lock = {'lockfileVersion': 3, 'packages': {'': {}}}
    urls = {}
    for name, data in packages.items():
        url = f"https://registry.npmjs.org/{name}/-/{name.split('/')[1]}-1.2.3.tgz"
        urls[url] = data
        lock['packages'][f'node_modules/{name}'] = {'version': '1.2.3', 'resolved': url, 'integrity': integrity(data)}
    fetched = []

    def urlopen(url, timeout):
        fetched.append(url)
        return io.BytesIO(urls[url])
    monkeypatch.setattr(update_fonts.urllib.request, 'urlopen', urlopen)
    lock_path = tmp_path / 'package-lock.json'
    lock_path.write_text(json.dumps(lock))
    static = tmp_path / 'static'

    class Registry:
        def write_lock(self):
            lock_path.write_text(json.dumps(lock))

        def run(self, *argv):
            return update_fonts.main(list(argv), static=str(static), lock_path=str(lock_path))
    registry = Registry()
    registry.lock, registry.static, registry.fetched = lock, static, fetched
    return registry


def test_files_of_the_locked_versions_written(registry):
    assert registry.run() == 0
    static = registry.static
    assert (static / 'mdi/materialdesignicons-webfont.ttf').read_bytes() == b'mdi font'
    assert (static / 'mdi/LICENSE').read_bytes() == b'mdi license'
    assert json.loads((static / 'mdi/icons.json').read_text()) == {
        'version': '1.2.3', 'icons': {'account': 'F0004', 'lightbulb': 'F0335'}}
    assert sorted(path.relative_to(static).as_posix() for path in static.rglob('*') if path.is_file()) == sorted(
        ['mdi/materialdesignicons-webfont.ttf', 'mdi/LICENSE', 'mdi/icons.json'])


def test_check_names_the_files_that_differ(registry, capsys):
    assert registry.run() == 0
    assert registry.run('--check') == 0
    font = registry.static / 'mdi/materialdesignicons-webfont.ttf'
    font.write_bytes(b'mdi font of before')
    (registry.static / 'mdi/icons.json').unlink()
    capsys.readouterr()
    assert registry.run('--check') == 1
    err = capsys.readouterr().err
    assert 'mdi/materialdesignicons-webfont.ttf' in err and 'mdi/icons.json' in err
    assert 'mdi/LICENSE' not in err
    # Only checked, nothing written
    assert font.read_bytes() == b'mdi font of before'
    assert not (registry.static / 'mdi/icons.json').exists()
    assert registry.run() == 0
    assert registry.run('--check') == 0


def test_package_not_matching_its_integrity_is_refused(registry):
    package = registry.lock['packages']['node_modules/@mdi/font']
    package['integrity'] = integrity(b'another package')
    registry.write_lock()
    with pytest.raises(ValueError, match='integrity'):
        registry.run()
    assert not (registry.static / 'mdi').exists()


def test_package_only_from_the_npm_registry(registry):
    package = registry.lock['packages']['node_modules/@mdi/font']
    package['resolved'] = 'https://registry.example.com/@mdi/font/-/font-1.2.3.tgz'
    registry.write_lock()
    with pytest.raises(ValueError, match='npm registry'):
        registry.run()
    assert package['resolved'] not in registry.fetched


def test_lock_file_names_the_versions_in_the_repository():
    locked = update_fonts.locked()
    static = os.path.join(os.path.dirname(__file__), '..', 'app', 'static')
    with open(os.path.join(static, 'mdi', 'icons.json'), encoding='utf-8') as f:
        assert json.load(f)['version'] == locked['@mdi/font']['version']
    assert list(locked) == ['@mdi/font']
