from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import BadZipFile, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import fixture, TARGET
from test_recovery import Database
import core


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = core.rewrite(fixture('<dc:title>Old</dc:title>'), TARGET, {'title'})
        self.digest = core.fingerprint(self.data)

    def test_unselected_field_differences_do_not_fail_validation(self):
        db = Database(self.data)
        core.validate_saved_book(db, 1, self.digest, {'title'})
        self.assertEqual(db.writes, 0)

    def test_selected_field_discrepancy_is_reported_even_when_saved_hash_matches(self):
        wrong = core.rewrite(self.data, dict(TARGET, title='Wrong title'), {'title'})
        db = Database(wrong)
        with self.assertRaisesRegex(ValueError, 'Selected metadata differs from Calibre: title') as result:
            core.validate_saved_book(db, 1, core.fingerprint(wrong), {'title'})
        self.assertIn('Wrong title', str(result.exception))
        self.assertEqual(db.data, wrong)
        self.assertEqual(db.writes, 0)

    def test_current_database_values_are_used(self):
        db = Database(self.data)
        original = db.get_metadata

        def changed_metadata(book_id):
            metadata = original(book_id)
            metadata.title = 'A later database edit'
            return metadata

        db.get_metadata = changed_metadata
        with self.assertRaisesRegex(ValueError, 'A later database edit'):
            core.validate_saved_book(db, 1, self.digest, {'title'})
        self.assertEqual(db.writes, 0)

    def test_later_content_change_is_flagged_without_overwriting_it(self):
        output = BytesIO()
        with ZipFile(BytesIO(self.data)) as source, ZipFile(output, 'w') as dest:
            for info in source.infolist():
                content = b'a later chapter edit' if info.filename.endswith('chapter.xhtml') else source.read(info)
                dest.writestr(info, content)
        changed = output.getvalue()
        db = Database(changed)
        with self.assertRaisesRegex(ValueError, 'EPUB changed after saving'):
            core.validate_saved_book(db, 1, self.digest, {'title'})
        self.assertEqual(db.data, changed)
        self.assertEqual(db.writes, 0)

    def test_missing_or_invalid_saved_epub_is_flagged(self):
        for content, error in ((None, ValueError), (b'invalid ZIP', BadZipFile)):
            with self.subTest(content=content):
                db = Database(content)
                with self.assertRaises(error):
                    core.validate_saved_book(db, 1, self.digest, {'title'})
                self.assertEqual(db.writes, 0)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ValidationTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
