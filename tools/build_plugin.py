# SPDX-License-Identifier: GPL-3.0-only
"""Build an installable Calibre plugin ZIP without importing or running Calibre."""

import argparse
import ast
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
FILES = {
    '__init__.py': ROOT / 'plugin' / '__init__.py',
    'core.py': ROOT / 'plugin' / 'core.py',
    'ui.py': ROOT / 'plugin' / 'ui.py',
    'plugin-import-name-metadata_audit.txt': ROOT / 'plugin' / 'plugin-import-name-metadata_audit.txt',
    'LICENSE': ROOT / 'LICENSE',
    'README.md': ROOT / 'README.md',
}


def plugin_version():
    tree = ast.parse(FILES['__init__.py'].read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MetadataAuditPlugin':
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'version'
                    for target in statement.targets
                ):
                    value = ast.literal_eval(statement.value)
                    if not isinstance(value, tuple) or len(value) != 3 or any(
                        type(part) is not int or part < 0 for part in value
                    ):
                        raise ValueError('Plugin version must contain three non-negative integers.')
                    return '.'.join(str(part) for part in value)
    raise ValueError('Plugin version was not found.')


def build(output_dir):
    payload = {}
    for name, path in FILES.items():
        if path.is_symlink():
            raise ValueError('Refusing to package a symlink: ' + name)
        payload[name] = path.read_bytes()
        if name.endswith('.py'):
            compile(payload[name], name, 'exec')
    if payload['plugin-import-name-metadata_audit.txt']:
        raise ValueError('The plugin import marker must be empty.')
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f'Metadata-Audit-{plugin_version()}.zip'
    with tempfile.NamedTemporaryFile(dir=output_dir, suffix='.zip', delete=False) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with ZipFile(temporary_path, 'w') as archive:
            for name, data in payload.items():
                info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=9)
        with ZipFile(temporary_path) as archive:
            if archive.namelist() != list(FILES) or archive.testzip() is not None:
                raise ValueError('Plugin ZIP verification failed.')
            for name, data in payload.items():
                if archive.read(name) != data:
                    raise ValueError('Packaged file differs from source: ' + name)
        temporary_path.replace(output)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    print(build(args.output_dir))
