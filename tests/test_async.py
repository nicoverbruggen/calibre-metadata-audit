from pathlib import Path
from threading import Barrier, Event, get_ident
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch
import sys
import time
import unittest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from test_core import fixture
import calibre.customize.ui
from calibre_plugins.metadata_audit import ui
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

    def test_rewrites_overlap_and_database_calls_stay_on_one_thread(self):
        db, gui, dialog = self.make_dialog()
        db.gate.set()
        barrier = Barrier(3)
        rewrite_threads, database_threads = set(), set()
        original_rewrite = ui.rewrite

        def concurrent_rewrite(*args):
            rewrite_threads.add(get_ident())
            barrier.wait(timeout=3)
            return original_rewrite(*args)

        def record(method):
            def call(*args, **kwargs):
                database_threads.add(get_ident())
                return method(*args, **kwargs)
            return call

        with ExitStack() as stack:
            stack.enter_context(patch.object(ui.os, 'cpu_count', return_value=8))
            stack.enter_context(patch.object(ui, 'rewrite', side_effect=concurrent_rewrite))
            for name in ('get_metadata', 'format', 'add_format'):
                stack.enter_context(patch.object(db, name, side_effect=record(getattr(db, name))))
            stack.enter_context(patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes))
            stack.enter_context(patch.object(QMessageBox, 'information'))
            dialog.apply_checked()
            pump_until(lambda: dialog.worker is None)
        self.assertEqual(dialog.errors, [])
        self.assertEqual(len(rewrite_threads), 3)
        self.assertEqual(len(database_threads), 1)
        self.assertTrue(rewrite_threads.isdisjoint(database_threads))
        self.assertNotIn(get_ident(), database_threads)
        self.assertEqual(db.writes, [1, 2, 3])
        dialog.reject()

    def test_failed_preparation_does_not_abort_other_books(self):
        db, gui, dialog = self.make_dialog()
        db.gate.set()
        original_rewrite = ui.rewrite

        def fail_second(data, target, fields):
            if target['series_index'] == '2':
                raise ValueError('Deliberate preparation failure')
            return original_rewrite(data, target, fields)

        with patch.object(ui, 'rewrite', side_effect=fail_second), patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes), patch.object(QMessageBox, 'information'):
            dialog.apply_checked()
            pump_until(lambda: dialog.worker is None)
        self.assertEqual(db.writes, [1, 3])
        self.assertEqual(len(dialog.errors), 1)
        self.assertIn('Deliberate preparation failure', dialog.errors[0])
        dialog.reject()

    def test_large_cpu_count_does_not_prefetch_more_than_four_books(self):
        db = Database()
        for book_id in (4, 5, 6):
            db.data[book_id] = db.data[1]
        db.gate.set()
        jobs = [(book_id, frozenset({'title'}), ui.fingerprint(data), ui.target_metadata(db.get_metadata(book_id)))
                for book_id, data in db.data.items()]
        release = Event()
        started = set()
        reads = []
        original_rewrite, original_format = ui.rewrite, db.format

        def blocked_rewrite(data, target, fields):
            started.add(target['series_index'])
            if not release.wait(3):
                raise TimeoutError('Preparation was never released')
            return original_rewrite(data, target, fields)

        def record_read(book_id, fmt):
            reads.append(book_id)
            return original_format(book_id, fmt)

        worker = ui.ApplyWorker(db, jobs)
        try:
            with patch.object(ui.os, 'cpu_count', return_value=128), patch.object(ui, 'rewrite', side_effect=blocked_rewrite), patch.object(db, 'format', side_effect=record_read):
                worker.start()
                pump_until(lambda: len(started) == 4)
                self.assertEqual(reads, [1, 2, 3, 4])
                self.assertEqual(db.writes, [])
                release.set()
                pump_until(lambda: not worker.isRunning())
            self.assertEqual(worker.errors, [])
            self.assertEqual(worker.done, [1, 2, 3, 4, 5, 6])
        finally:
            release.set()
            worker.wait()

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
