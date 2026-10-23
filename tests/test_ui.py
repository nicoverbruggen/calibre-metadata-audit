from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
import time
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_core import fixture
import calibre.customize.ui
from calibre_plugins.metadata_audit.ui import AuditDialog
from qt.core import QApplication, QMainWindow, QMessageBox, Qt
def require(condition, message="Verification failed"):
    if not condition:
        raise AssertionError(message)


app = QApplication([])

def wait_for_apply(dialog):
    deadline = time.monotonic() + 10
    while dialog.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    require(dialog.worker is None, 'Background apply did not finish')

class Database:

    def __init__(self):
        self.data = fixture('<dc:title>Old title</dc:title><dc:creator>Brandon Sanderson</dc:creator><meta property="dcterms:creator">Mary Zaia</meta><dc:publisher>Tor</dc:publisher>')
        self.writes = 0

    def all_book_ids(self):
        return [1]

    def formats(self, book_id):
        return ('EPUB',)

    def format(self, book_id, fmt):
        return self.data

    def get_metadata(self, book_id):
        return SimpleNamespace(title='Correct title', authors=['Brandon Sanderson'], publisher='Tor', series='Mistborn', series_index=3.0)

    def add_format(self, book_id, fmt, stream, **kwargs):
        require(kwargs == {'replace': True, 'run_hooks': False})
        self.data = stream.read()
        self.writes += 1
        return True
db = Database()
gui = QMainWindow()
gui.current_db = SimpleNamespace(new_api=db)
gui.library_view = SimpleNamespace(get_selected_ids=lambda: [1], model=lambda: SimpleNamespace(refresh_ids=lambda ids: None))
dialog = AuditDialog(gui)
dialog.start_selected()
while dialog.timer.isActive():
    app.processEvents()
require(len(dialog.rows) == 3, dialog.rows)
require(not dialog.apply_button.isEnabled())
require(db.writes == 0)
dialog.show()
app.processEvents()
dialog.grab().save(str(HERE / 'plugin-preview.png'))
row = next((i for i, (_, field) in enumerate(dialog.rows) if field == 'authors'))
dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
require(dialog.apply_button.isEnabled())
with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
    dialog.apply_checked()
require(db.writes == 0)
with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
    dialog.apply_checked()
    wait_for_apply(dialog)
require(db.writes == 1)
require(b'Mary Zaia' not in db.data)
require(b'Old title' in db.data)
require(not dialog.apply_button.isEnabled())
dialog.start_selected()
while dialog.timer.isActive():
    app.processEvents()
require({field for _, field in dialog.rows} == {'title', 'series'})
dialog.reject()
print('Headless UI verified: scan, unchecked defaults, cancel, selected-field apply, readback, and rescan.')
