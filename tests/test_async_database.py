from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import time
import unittest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_core import fixture
import calibre.customize.ui
from calibre.library import db as open_library
from calibre.ebooks.metadata.book.base import Metadata
from calibre_plugins.metadata_audit.core import differences, target_metadata
from calibre_plugins.metadata_audit.ui import AuditDialog
from qt.core import QApplication, QMainWindow, QMessageBox

app = QApplication([])
check = unittest.TestCase()
root = Path(tempfile.mkdtemp(prefix='async-db-', dir=HERE))
source = root/'fixture.epub'
source.write_bytes(fixture('<dc:title>Wrong</dc:title><dc:creator>Wrong Author</dc:creator>'))
library = open_library(str(root/'library'))
db = library.new_api
try:
    books = []
    for i in range(3):
        mi = Metadata('Correct title ' + str(i + 1), ['Brandon Sanderson'])
        mi.publisher, mi.series, mi.series_index = 'Test publisher', 'Test series', i + 1
        books.append((mi, {'EPUB': str(source)}))
    ids, duplicates = db.add_books(books, run_hooks=False)
    check.assertEqual(len(ids), 3)
    gui = QMainWindow()
    gui.current_db = SimpleNamespace(new_api=db)
    refreshed = []
    gui.library_view = SimpleNamespace(model=lambda: SimpleNamespace(refresh_ids=lambda ids: refreshed.extend(ids)))
    dialog = AuditDialog(gui)
    dialog.start_scan(ids)
    deadline = time.monotonic() + 10
    while dialog.timer.isActive() and time.monotonic() < deadline:
        app.processEvents()
    check.assertFalse(dialog.timer.isActive())
    check.assertEqual(len(dialog.rows), 12)
    dialog.check_all(True)
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
        dialog.apply_checked()
        while dialog.worker is not None and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
    check.assertIsNone(dialog.worker)
    check.assertEqual(dialog.errors, [])
    check.assertEqual(set(refreshed), set(ids))
    for book_id in ids:
        check.assertEqual(differences(db.format(book_id, 'EPUB'), target_metadata(db.get_metadata(book_id))), [])
        check.assertEqual(db.formats(book_id), ('EPUB',))
    dialog.reject()
    print('Actual Calibre database: three background writes, metadata readback and no backup formats verified.')
finally:
    library.close()
