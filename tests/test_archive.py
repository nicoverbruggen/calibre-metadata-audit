from io import BytesIO, UnsupportedOperation
from pathlib import Path
import struct
import sys
import unittest
from zipfile import BadZipFile, ZipFile, ZipInfo, ZIP_DEFLATED, ZIP_STORED

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import fixture, TARGET
from core import archive_metadata, rewrite
from calibre.utils.zipfile import ZipFile as RawZipFile


class Unseekable(BytesIO):
    def seek(self, *args):
        raise UnsupportedOperation('Not seekable')


class ArchiveTests(unittest.TestCase):
    def make_archive(self, unseekable=False, zip64=False):
        output = Unseekable() if unseekable else BytesIO()
        with ZipFile(BytesIO(fixture('<dc:title>Old title</dc:title>'))) as source, ZipFile(output, 'w') as dest:
            dest.comment = b'Preserve the archive comment'
            for info in source.infolist():
                dest.writestr(info, source.read(info))
            image = ZipInfo('OEBPS/art/illustration-\u00e9.png', date_time=(2020, 3, 4, 5, 6, 8))
            image.compress_type = ZIP_DEFLATED
            image.comment = b'Keep the entry comment'
            image.external_attr = 0o100644 << 16
            if zip64:
                with dest.open(image, 'w', force_zip64=True) as stream:
                    stream.write(b'unchanged artwork' * 4000)
            else:
                dest.writestr(image, b'unchanged artwork' * 4000)
        return output.getvalue()

    def verify_roundtrip(self, data):
        updated = rewrite(data, TARGET, {'title'})
        opf = archive_metadata(data)[0]
        with ZipFile(BytesIO(data)) as before, ZipFile(BytesIO(updated)) as after:
            self.assertEqual(before.namelist(), after.namelist())
            self.assertEqual(after.comment, before.comment)
            self.assertIsNone(after.testzip())
            self.assertEqual(after.infolist()[0].filename, 'mimetype')
            self.assertEqual(after.infolist()[0].compress_type, ZIP_STORED)
            for info in before.infolist():
                if info.filename != opf:
                    self.assertEqual(before.read(info), after.read(info.filename))
                    saved = after.getinfo(info.filename)
                    self.assertEqual((saved.compress_type, saved.date_time, saved.comment, saved.external_attr),
                                     (info.compress_type, info.date_time, info.comment, info.external_attr))
        with RawZipFile(BytesIO(data)) as before, RawZipFile(BytesIO(updated)) as after:
            for name in before.namelist():
                if name != opf:
                    self.assertEqual(before.read_raw(name), after.read_raw(name))

    def test_compressed_assets_comments_names_and_attributes_are_preserved(self):
        self.verify_roundtrip(self.make_archive())

    def test_data_descriptor_archives_are_preserved(self):
        data = self.make_archive(unseekable=True)
        with ZipFile(BytesIO(data)) as archive:
            self.assertTrue(archive.getinfo('OEBPS/art/illustration-\u00e9.png').flag_bits & 8)
        self.verify_roundtrip(data)

    def test_zip64_local_headers_are_supported(self):
        self.verify_roundtrip(self.make_archive(zip64=True))

    def test_corrupt_original_content_is_not_silently_copied(self):
        data = bytearray(self.make_archive())
        with ZipFile(BytesIO(data)) as archive:
            entry = archive.getinfo('OEBPS/cover.jpg')
            name_length, extra_length = struct.unpack_from('<HH', data, entry.header_offset + 26)
            start = entry.header_offset + 30 + name_length + extra_length
        data[start] ^= 1
        with self.assertRaises((ValueError, BadZipFile)):
            rewrite(bytes(data), TARGET, {'title'})


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ArchiveTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
