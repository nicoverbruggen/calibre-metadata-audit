from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch
import sys
import time
import unittest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_core import fixture
import calibre.customize.ui
from calibre_plugins.metadata_audit.ui import AuditDialog
from qt.core import QApplication, QMainWindow, QMessageBox, QTimer

app = QApplication([])


class Database:
    def __init__(self, fail=()):
        self.data = {i: fixture('<dc:title>Old</dc:title><dc:creator>Wrong</dc:creator>') for i in (1, 2, 3)}
        self.writes = []
        self.gate = Event()
        self.started = Event()
        self.fail = set(fail)

    def all_book_ids(self):
        return list(self.data)

    def formats(self, book_id):
        return ('EPUB',)

    def format(self, book_id, fmt):
        return self.data[book_id]

    def get_metadata(self, book_id):
        return SimpleNamespace(title='Correct', authors=['Author'], publisher='Publisher', series='Series', series_index=book_id)

    def add_format(self, book_id, fmt, stream, **kwargs):
        self.started.set()
        if not self.gate.wait(3):
            raise TimeoutError('The GUI never released the worker')
        if book_id in self.fail:
            raise ValueError('Deliberate test failure')
        self.data[book_id] = stream.read()
        self.writes.append(book_id)
        return True


def pump_until(predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    if not predicate():
        raise AssertionError('Qt operation did not finish in time')


class AsyncTests(unittest.TestCase):
    def make_dialog(self, fail=()):
        db = Database(fail)
        gui = QMainWindow()
        gui.current_db = SimpleNamespace(new_api=db)
        gui.library_view = SimpleNamespace(model=lambda: SimpleNamespace(refresh_ids=lambda ids: None))
        dialog = AuditDialog(gui)
        dialog.start_scan([1, 2, 3])
        pump_until(lambda: not dialog.timer.isActive())
        dialog.check_all(True)
        return db, gui, dialog

    def test_main_thread_stays_responsive_and_progress_reaches_total(self):
        db, gui, dialog = self.make_dialog()
        ticks = []
        heartbeat = QTimer()
        heartbeat.timeout.connect(lambda: ticks.append(1))
        heartbeat.start(5)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
            dialog.apply_checked()
            self.assertIsNotNone(dialog.worker)
            self.assertFalse(dialog.scan_library.isEnabled())
            QTimer.singleShot(100, db.gate.set)
            pump_until(lambda: dialog.worker is None)
        heartbeat.stop()
        self.assertGreater(len(ticks), 3)
        self.assertEqual(db.writes, [1, 2, 3])
        self.assertEqual(dialog.progress.value(), 3)
        self.assertTrue(dialog.scan_library.isEnabled())
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.reject()

    def test_stop_keeps_current_book_and_skips_remaining_books(self):
        db, gui, dialog = self.make_dialog()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
            dialog.apply_checked()
            pump_until(db.started.is_set)
            dialog.stop_work()
            db.gate.set()
            pump_until(lambda: dialog.worker is None)
        self.assertEqual(db.writes, [1])
        self.assertIn('2 not processed', dialog.status.text())
        self.assertTrue(dialog.scan_library.isEnabled())
        dialog.reject()

    def test_closing_waits_for_current_book_before_destroying_worker(self):
        db, gui, dialog = self.make_dialog()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information') as report:
            dialog.apply_checked()
            pump_until(db.started.is_set)
            dialog.reject()
            self.assertTrue(dialog.close_when_finished)
            self.assertIsNotNone(dialog.worker)
            db.gate.set()
            pump_until(lambda: dialog.worker is None)
        self.assertEqual(db.writes, [1])
        report.assert_not_called()

    def test_failure_does_not_abort_other_books(self):
        db, gui, dialog = self.make_dialog(fail=(2,))
        db.gate.set()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
            dialog.apply_checked()
            pump_until(lambda: dialog.worker is None)
        self.assertEqual(db.writes, [1, 3])
        self.assertEqual(len(dialog.errors), 1)
        self.assertIn('1 failures', dialog.status.text())
        self.assertTrue(dialog.error_button.isEnabled())
        dialog.reject()


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AsyncTests))
    raise SystemExit(0 if result.wasSuccessful() else 1)
