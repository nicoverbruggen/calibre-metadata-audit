import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'plugin'))
import unittest
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_STORED
import warnings

from core import NS, apply_to_database, archive_metadata, differences, fingerprint, read_values, rewrite


def fixture(metadata, version='3.0'):
    data = BytesIO()
    with ZipFile(data, 'w') as z:
        z.writestr('mimetype', b'application/epub+zip', compress_type=ZIP_STORED)
        z.writestr('META-INF/container.xml', b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        z.writestr('OEBPS/content.opf', f'<package xmlns="{NS["o"]}" version="{version}" unique-identifier="uid"><metadata xmlns:dc="{NS["dc"]}"><dc:identifier id="uid">urn:uuid:fixture</dc:identifier>{metadata}</metadata><manifest><item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/></spine></package>')
        z.writestr('OEBPS/cover.jpg', b'cover must remain unchanged')
        z.writestr('OEBPS/chapter.xhtml', b'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title></head><body><p>Story must remain unchanged.</p></body></html>')
    return data.getvalue()


TARGET = {'title': 'Correct & Complete', 'authors': ['Brandon Sanderson'],
          'publisher': 'Dragonsteel', 'series': 'Example', 'series_index': '2.5'}


class CoreTests(unittest.TestCase):
    def test_extra_creator_and_all_fields(self):
        source = fixture('<dc:title>Wrong</dc:title><dc:creator>Wrong Author</dc:creator><meta property="dcterms:creator">Mary Zaia</meta><dc:publisher>Wrong</dc:publisher>')
        self.assertEqual({r['field'] for r in differences(source, TARGET)}, {'title', 'authors', 'publisher', 'series'})
        updated = rewrite(source, TARGET, {'title', 'authors', 'publisher', 'series'})
        self.assertEqual(differences(updated, TARGET), [])
        _, _, m = archive_metadata(updated)
        self.assertEqual(read_values(m)['extra_authors'], ['Brandon Sanderson'])

    def test_conflicting_extra_title_and_publisher(self):
        source = fixture('<dc:title>Correct &amp; Complete</dc:title><meta property="dcterms:title">Wrong title</meta><dc:publisher>Dragonsteel</dc:publisher><meta property="dcterms:publisher">Wrong publisher</meta>')
        self.assertTrue({'title', 'publisher'} <= {x['field'] for x in differences(source, TARGET)})
        updated = rewrite(source, TARGET, {'title', 'publisher'})
        self.assertFalse({'title', 'publisher'} & {x['field'] for x in differences(updated, TARGET)})

    def test_fixing_extra_author_preserves_primary_credit_and_sort(self):
        source = fixture('<dc:creator id="a">Brandon Sanderson</dc:creator><meta property="role" refines="#a">aut</meta><meta property="file-as" refines="#a">Sanderson, Brandon</meta><meta property="dcterms:creator">Mary Zaia</meta>')
        updated = rewrite(source, TARGET, {'authors'})
        _, _, m = archive_metadata(updated)
        self.assertEqual(m.find('dc:creator', NS).get('id'), 'a')
        self.assertTrue(m.xpath('o:meta[@refines="#a" and @property="file-as" and text()="Sanderson, Brandon"]', namespaces=NS))

    def test_preserve_illustrator_translator_and_nonseries_collection(self):
        source = fixture('''<dc:creator id="a">Wrong</dc:creator><meta property="role" refines="#a">aut</meta>
          <dc:creator id="i">Illustrator</dc:creator><meta property="role" refines="#i">ill</meta>
          <dc:creator id="t">Translator</dc:creator><meta property="role" refines="#t">trl</meta>
          <meta property="belongs-to-collection" id="c">Cosmere</meta><meta refines="#c" property="collection-type">set</meta>''')
        updated = rewrite(source, TARGET, {'authors', 'series'})
        _, _, m = archive_metadata(updated)
        self.assertEqual([x.text for x in m.findall('dc:creator', NS)], ['Illustrator', 'Translator', 'Brandon Sanderson'])
        self.assertTrue(m.xpath('o:meta[text()="Cosmere"]', namespaces=NS))

    def test_selected_fields_only(self):
        source = fixture('<dc:title>Do not change me</dc:title><dc:creator>Someone</dc:creator><dc:publisher>Keep publisher</dc:publisher>')
        updated = rewrite(source, TARGET, {'series'})
        _, _, m = archive_metadata(updated)
        self.assertEqual(read_values(m)['title'], 'Do not change me')
        self.assertEqual(read_values(m)['authors'], ['Someone'])
        self.assertEqual(read_values(m)['publisher'], ['Keep publisher'])

    def test_epub2_and_fractional_number(self):
        updated = rewrite(fixture('', '2.0'), TARGET, {'authors', 'series'})
        _, root, m = archive_metadata(updated)
        self.assertEqual(m.find('dc:creator', NS).get('{%s}role' % NS['o']), 'aut')
        self.assertFalse(m.xpath('o:meta[@property="belongs-to-collection"]', namespaces=NS))
        self.assertEqual(read_values(m)['series'], [('Example', '2.5')])

    def test_clear_series_only_when_requested(self):
        source = rewrite(fixture(''), TARGET, {'series'})
        target = dict(TARGET, series='', series_index=None)
        updated = rewrite(source, target, {'series'})
        self.assertEqual(read_values(archive_metadata(updated)[2])['series'], [])

    def test_punctuation_and_multiple_authors_not_false_mismatch(self):
        target = dict(TARGET, authors=['One Person', 'Two People'])
        source = fixture('<dc:creator>One Person and Two People</dc:creator>')
        self.assertNotIn('authors', {x['field'] for x in differences(source, target)})
        source = fixture('<dc:creator>Someone Else</dc:creator><meta property="dcterms:creator">Mary Zaia</meta>')
        updated = rewrite(source, target, {'authors'})
        self.assertNotIn('authors', {x['field'] for x in differences(updated, target)})

    def test_split_subtitle_matches_combined_title(self):
        source = fixture('<dc:title id="t">Correct</dc:title><meta refines="#t" property="title-type">main</meta><dc:title id="s">Complete</dc:title><meta refines="#s" property="title-type">subtitle</meta>')
        target = dict(TARGET, title='Correct: Complete')
        self.assertNotIn('title', {x['field'] for x in differences(source, target)})

    def test_empty_database_value_refused(self):
        with self.assertRaises(ValueError):
            rewrite(fixture(''), dict(TARGET, authors=[]), {'authors'})

    def test_duplicate_zip_entry_refused(self):
        data = BytesIO(fixture(''))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with ZipFile(data, 'a') as z:
                z.writestr('OEBPS/content.opf', b'not XML')
        with self.assertRaises(ValueError):
            differences(data.getvalue(), TARGET)

    def test_missing_package_refused(self):
        with self.assertRaises(Exception):
            differences(b'not a ZIP', TARGET)

    def test_backup_and_content_are_preserved(self):
        source = fixture('<dc:title>Old</dc:title>')
        updated = rewrite(source, TARGET, {'title'})
        with ZipFile(BytesIO(source)) as a, ZipFile(BytesIO(updated)) as b:
            self.assertEqual(a.namelist(), b.namelist())
            for name in a.namelist():
                if name != 'OEBPS/content.opf':
                    self.assertEqual(a.read(name), b.read(name))
            self.assertEqual(b.infolist()[0].filename, 'mimetype')
            self.assertEqual(b.infolist()[0].compress_type, ZIP_STORED)

    def test_stale_epub_and_database_refused_without_writes(self):
        source = fixture('')
        class Database:
            wrote = False
            def get_metadata(self, book_id):
                return SimpleNamespace(title=TARGET['title'], authors=TARGET['authors'], publisher=TARGET['publisher'], series=TARGET['series'], series_index=2.5)
            def format(self, book_id, fmt):
                return source
            def add_format(self, *args, **kwargs):
                self.wrote = True
        db = Database()
        with self.assertRaisesRegex(ValueError, 'EPUB changed'):
            apply_to_database(db, 1, 'stale', TARGET, {'title'})
        with self.assertRaisesRegex(ValueError, 'metadata changed'):
            apply_to_database(db, 1, fingerprint(source), dict(TARGET, title='Outdated'), {'title'})
        self.assertFalse(db.wrote)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(CoreTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
