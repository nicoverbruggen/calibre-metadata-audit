from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.build_plugin import FILES, ROOT, build, plugin_version, release_source


class BuildTests(unittest.TestCase):
    def test_normal_build_uses_source_version(self):
        with tempfile.TemporaryDirectory() as folder:
            output = build(Path(folder))
            self.assertEqual(output.name, f'Metadata-Audit-{plugin_version()}.zip')
            with ZipFile(output) as archive:
                self.assertEqual(archive.namelist(), list(FILES))
                self.assertIsNone(archive.testzip())
                for name, source in FILES.items():
                    self.assertEqual(archive.read(name), source.read_bytes())

    def test_release_tag_sets_embedded_version_and_filename_without_changing_sources(self):
        originals = {name: source.read_bytes() for name, source in FILES.items()}
        with tempfile.TemporaryDirectory() as folder:
            output = build(Path(folder), release_tag='v0.1.0')
            self.assertEqual(output.name, 'Metadata-Audit-0.1.0.zip')
            with ZipFile(output) as archive:
                self.assertEqual(plugin_version(archive.read('__init__.py')), '0.1.0')
                for name, original in originals.items():
                    if name != '__init__.py':
                        self.assertEqual(archive.read(name), original)
        for name, source in FILES.items():
            self.assertEqual(source.read_bytes(), originals[name])

    def test_invalid_release_tags_are_rejected_without_build_outputs(self):
        for tag in ('', '0.1.0', 'v1.2', 'v01.2.3', 'v1.2.3-rc1', 'v1.2.3+build', 'v1.2.3\n', 'v1.2.3;echo bad'):
            with self.subTest(tag=tag), tempfile.TemporaryDirectory() as folder:
                with self.assertRaisesRegex(ValueError, 'Release tags must use'):
                    build(Path(folder), release_tag=tag)
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_version_stamp_preserves_unicode_and_surrounding_source(self):
        source = "class MetadataAuditPlugin:\n    label = 'Café'; version = (\n        9, 8, 7\n    )  # keep this\n".encode('utf-8')
        updated = release_source(source, 'v12.0.34')
        self.assertEqual(updated, source.replace(b'(\n        9, 8, 7\n    )', b'(12, 0, 34)'))
        self.assertEqual(plugin_version(updated), '12.0.34')
        compile(updated, '__init__.py', 'exec')

    def test_release_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as folder:
            first = build(Path(folder) / 'first', release_tag='v0.1.0').read_bytes()
            second = build(Path(folder) / 'second', release_tag='v0.1.0').read_bytes()
            self.assertEqual(first, second)

    def test_cli_accepts_release_tag(self):
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, str(ROOT / 'tools/build_plugin.py'),
                            '--output-dir', folder, '--release-tag', 'v0.1.0'],
                           check=True, capture_output=True, text=True)
            with ZipFile(Path(folder) / 'Metadata-Audit-0.1.0.zip') as archive:
                self.assertEqual(plugin_version(archive.read('__init__.py')), '0.1.0')


if __name__ == '__main__':
    unittest.main()
