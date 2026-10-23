from io import BytesIO
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import fixture
import core
from calibre.ebooks.metadata.book.base import Metadata
from calibre.library import db as open_library
from qt.core import QApplication

app = QApplication([])


class DatabaseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='metadata-audit-database-recovery-', dir=None)
        self.root = Path(self.folder.name)
        self.library = open_library(str(self.root / 'library'))
        self.db = self.library.new_api
        self.original = fixture('<dc:title>Old title</dc:title>')
        source = self.root / 'source.epub'
        source.write_bytes(self.original)
        metadata = Metadata('Correct title', ['Brandon Sanderson'])
        metadata.publisher = 'Test publisher'
        metadata.series, metadata.series_index = 'Test series', 1
        ids, _ = self.db.add_books([(metadata, {'EPUB': str(source)})], run_hooks=False)
        self.book_id = ids[0]
        self.db.add_format(self.book_id, 'ORIGINAL_EPUB', BytesIO(self.original), replace=True, run_hooks=False)
        self.target = core.target_metadata(self.db.get_metadata(self.book_id))
        self.digest = core.fingerprint(self.original)
        self.updated = core.rewrite(self.original, self.target, {'title'})
        self.formats = self.db.formats(self.book_id)
        self.settings = self.root / 'settings'
        self.settings.mkdir()

    def tearDown(self):
        self.library.close()
        self.folder.cleanup()

    def save(self):
        with patch('calibre.constants.config_dir', str(self.settings)):
            return core.save_to_database(self.db, self.book_id, self.digest, self.target, self.updated,
                                         temporary_backup=True)

    def test_real_partial_write_is_restored_without_touching_database_fields_or_original_epub(self):
        write = self.db.add_format
        calls = []

        def partial_write(book_id, fmt, stream, **kwargs):
            calls.append(book_id)
            if len(calls) == 1:
                write(book_id, fmt, BytesIO(b'partial EPUB'), **kwargs)
                raise OSError('Interrupted write')
            return write(book_id, fmt, stream, **kwargs)

        with patch.object(self.db, 'add_format', side_effect=partial_write):
            with self.assertRaisesRegex(ValueError, 'Original EPUB restored and verified'):
                self.save()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.db.format(self.book_id, 'EPUB'), self.original)
        self.assertEqual(self.db.format(self.book_id, 'ORIGINAL_EPUB'), self.original)
        self.assertCountEqual(self.db.formats(self.book_id), self.formats)
        self.assertEqual(core.target_metadata(self.db.get_metadata(self.book_id)), self.target)
        self.assertEqual(list(self.settings.rglob('*.epub')), [])

    def test_failed_restore_leaves_original_copy_available_for_manual_recovery(self):
        write = self.db.add_format

        def broken_write(book_id, fmt, stream, **kwargs):
            write(book_id, fmt, BytesIO(b'partial EPUB'), **kwargs)
            raise OSError('Disk unavailable')

        with patch.object(self.db, 'add_format', side_effect=broken_write):
            with self.assertRaisesRegex(ValueError, 'Backup path for manual recovery') as result:
                self.save()
        backups = list(self.settings.rglob('*.epub'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertIn(str(backups[0]), str(result.exception))
        write(self.book_id, 'EPUB', BytesIO(backups[0].read_bytes()), replace=True, run_hooks=False)
        self.assertEqual(self.db.format(self.book_id, 'EPUB'), self.original)
        self.assertEqual(self.db.format(self.book_id, 'ORIGINAL_EPUB'), self.original)
        self.assertCountEqual(self.db.formats(self.book_id), self.formats)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(DatabaseRecoveryTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
