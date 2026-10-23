from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import fixture, TARGET
import core


class Database:
    def __init__(self, data, fault=None, restore_fault=None):
        self.data = data
        self.fault, self.restore_fault = fault, restore_fault
        self.writes = 0

    def get_metadata(self, book_id):
        return SimpleNamespace(title=TARGET['title'], authors=TARGET['authors'], publisher=TARGET['publisher'],
                               series=TARGET['series'], series_index=2.5)

    def format(self, book_id, fmt):
        return self.data

    def add_format(self, book_id, fmt, stream, **kwargs):
        self.writes += 1
        data = stream.read()
        fault = self.fault if self.writes == 1 else self.restore_fault
        if fault:
            self.data = b'partial write'
            if fault == 'raise':
                raise OSError('Injected write failure')
            if fault == 'false':
                return False
            return True
        self.data = data
        return True


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='metadata-audit-recovery-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name) / 'metadata-audit-recovery'
        self.root.mkdir()
        self.patch_temp = patch('calibre.constants.config_dir', self.folder.name)
        self.patch_temp.start()
        self.addCleanup(self.patch_temp.stop)
        self.original = fixture('<dc:title>Old title</dc:title>')
        self.updated = core.rewrite(self.original, TARGET, {'title'})
        self.digest = core.fingerprint(self.original)

    def save(self, db, enabled=True):
        return core.save_to_database(db, 1, self.digest, TARGET, self.updated, temporary_backup=enabled)

    def test_partial_writes_false_returns_and_failed_readback_restore_exact_original(self):
        for fault in ('raise', 'false', 'corrupt'):
            with self.subTest(fault=fault):
                db = Database(self.original, fault=fault)
                with self.assertRaisesRegex(ValueError, 'Original EPUB restored and verified'):
                    self.save(db)
                self.assertEqual(db.data, self.original)
                self.assertEqual(db.writes, 2)
                self.assertEqual(list(self.root.iterdir()), [])

    def test_failed_restore_keeps_verified_original_and_reports_its_path(self):
        for fault in ('raise', 'false', 'corrupt'):
            with self.subTest(fault=fault):
                db = Database(self.original, fault='raise', restore_fault=fault)
                with self.assertRaisesRegex(ValueError, 'Automatic restore failed') as result:
                    self.save(db)
                backups = list(self.root.glob('*.epub'))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), self.original)
                self.assertIn(str(backups[0]), str(result.exception))
                backups[0].unlink()

    def test_backup_disk_failure_prevents_any_library_write(self):
        db = Database(self.original)
        with patch.object(core.os, 'fsync', side_effect=OSError('No space left on device')):
            with self.assertRaisesRegex(OSError, 'No space left'):
                self.save(db)
        self.assertEqual(db.writes, 0)
        self.assertEqual(db.data, self.original)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_truncated_backup_prevents_any_library_write(self):
        db = Database(self.original)
        create = core.tempfile.NamedTemporaryFile

        def truncate_backup(*args, **kwargs):
            stream = create(*args, **kwargs)
            write = stream.write
            stream.write = lambda data: write(data[:len(data) // 2])
            return stream

        with patch.object(core.tempfile, 'NamedTemporaryFile', side_effect=truncate_backup):
            with self.assertRaisesRegex(ValueError, 'temporary backup could not be verified'):
                self.save(db)
        self.assertEqual(db.writes, 0)
        self.assertEqual(db.data, self.original)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_success_is_not_rolled_back_when_backup_cleanup_fails(self):
        db = Database(self.original)
        with patch.object(Path, 'unlink', side_effect=PermissionError('Cannot delete backup')):
            warning = self.save(db)
        self.assertEqual(db.data, self.updated)
        self.assertEqual(db.writes, 1)
        backups = list(self.root.glob('*.epub'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertIn(str(backups[0]), warning)

    def test_success_removes_only_its_own_backup(self):
        unrelated = self.root / 'unrelated.epub'
        unrelated.write_bytes(b'keep')
        db = Database(self.original)
        self.assertIsNone(self.save(db))
        self.assertEqual(db.data, self.updated)
        self.assertEqual(list(self.root.iterdir()), [unrelated])
        self.assertEqual(unrelated.read_bytes(), b'keep')

    def test_disabled_backup_preserves_original_failure_behavior(self):
        db = Database(self.original, fault='raise')
        with self.assertRaisesRegex(OSError, 'Injected write failure'):
            self.save(db, enabled=False)
        self.assertEqual(db.writes, 1)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_stale_epub_is_never_replaced_or_restored(self):
        db = Database(b'a newer external edit')
        with self.assertRaisesRegex(ValueError, 'EPUB changed'):
            self.save(db)
        self.assertEqual(db.data, b'a newer external edit')
        self.assertEqual(db.writes, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_corrupt_backup_is_not_used_for_restoration(self):
        db = Database(self.original, fault='raise')
        original_write = db.add_format

        def corrupt_backup(*args, **kwargs):
            if db.writes == 0:
                for path in self.root.glob('*.epub'):
                    path.write_bytes(b'damaged backup')
            return original_write(*args, **kwargs)

        with patch.object(db, 'add_format', side_effect=corrupt_backup):
            with self.assertRaisesRegex(ValueError, 'backup failed its checksum check'):
                self.save(db)
        self.assertEqual(db.writes, 1)
        self.assertEqual(len(list(self.root.glob('*.epub'))), 1)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RecoveryTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
