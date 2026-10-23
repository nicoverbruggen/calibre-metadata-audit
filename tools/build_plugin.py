# SPDX-License-Identifier: GPL-3.0-only
"""Build an installable Calibre plugin ZIP without importing or running Calibre."""

import argparse
import ast
from pathlib import Path
import re
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


def version_expression(source):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == 'MetadataAuditPlugin':
            for statement in node.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == 'version'
                    for target in statement.targets
                ):
                    return statement.value
    raise ValueError('Plugin version was not found.')


def plugin_version(source=None):
    if source is None:
        source = FILES['__init__.py'].read_bytes()
    value = ast.literal_eval(version_expression(source))
    if not isinstance(value, tuple) or len(value) != 3 or any(
        type(part) is not int or part < 0 for part in value
    ):
        raise ValueError('Plugin version must contain three non-negative integers.')
    return '.'.join(str(part) for part in value)


def release_source(source, tag):
    match = re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag)
    if match is None:
        raise ValueError('Release tags must use vMAJOR.MINOR.PATCH, for example v0.1.0.')
    version = tuple(int(part) for part in match.groups())
    node = version_expression(source)
    # AST columns are UTF-8 byte offsets. Preserve the rest of the source exactly.
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
    return source[:start] + repr(version).encode('ascii') + source[end:]


def build(output_dir, release_tag=None):
    payload = {}
    for name, path in FILES.items():
        if path.is_symlink():
            raise ValueError('Refusing to package a symlink: ' + name)
        payload[name] = path.read_bytes()
        if name.endswith('.py'):
            compile(payload[name], name, 'exec')
    if release_tag is not None:
        payload['__init__.py'] = release_source(payload['__init__.py'], release_tag)
        compile(payload['__init__.py'], '__init__.py', 'exec')
    if payload['plugin-import-name-metadata_audit.txt']:
        raise ValueError('The plugin import marker must be empty.')
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f'Metadata-Audit-{plugin_version(payload["__init__.py"])}.zip'
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
    parser.add_argument('--release-tag', help='Set the packaged plugin version from a vMAJOR.MINOR.PATCH tag.')
    args = parser.parse_args()
    print(build(args.output_dir, release_tag=args.release_tag))
