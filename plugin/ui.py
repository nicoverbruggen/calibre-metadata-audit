# SPDX-License-Identifier: GPL-3.0-only
from collections import defaultdict
import traceback

from qt.core import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QThread, QTimer,
    Qt, QVBoxLayout, pyqtSignal,
)
from calibre.gui2 import gprefs
from calibre.gui2.actions import InterfaceAction

from calibre_plugins.metadata_audit.core import (
    apply_to_database, differences, fingerprint, target_metadata,
)


class ApplyWorker(QThread):
    progress = pyqtSignal(int, int, str)

    def __init__(self, db, jobs, parent=None):
        super().__init__(parent)
        self.db, self.jobs = db, jobs
        self.done, self.errors = [], []
        self.processed = 0
        self.cancelled = False

    def run(self):
        for book_id, fields, digest, target in self.jobs:
            if self.isInterruptionRequested():
                break
            self.progress.emit(self.processed, len(self.jobs), target['title'])
            try:
                apply_to_database(self.db, book_id, digest, target, fields)
                self.done.append(book_id)
            except Exception:
                self.errors.append(f'Book {book_id}:\n{traceback.format_exc()}')
            self.processed += 1
            self.progress.emit(self.processed, len(self.jobs), target['title'])
        self.cancelled = self.processed < len(self.jobs)


class MetadataAuditAction(InterfaceAction):
    name = 'Metadata Audit'
    action_spec = ('Metadata Audit', 'edit_input.png', 'Compare Calibre metadata with EPUB files', None)
    dont_add_to = frozenset(['context-menu-device'])

    def genesis(self):
        self.qaction.triggered.connect(self.open_audit)

    def gui_layout_complete(self):
        if gprefs.get('metadata-audit-toolbar-configured', False):
            return
        changed = False
        with gprefs:
            for key in ('action-layout-toolbar', 'action-layout-toolbar-device'):
                actions = list(gprefs[key])
                if self.name not in actions:
                    gprefs[key] = actions + [self.name]
                    changed = True
            # Run once so later user customizations, including removal, stay intact.
            gprefs['metadata-audit-toolbar-configured'] = True
        if changed:
            self.gui.bars_manager.init_bars()
            self.gui.bars_manager.update_bars()

    def open_audit(self):
        if self.gui.current_view() is not self.gui.library_view:
            QMessageBox.information(self.gui, 'Metadata Audit', 'Open a Calibre library first.')
            return
        dialog = AuditDialog(self.gui)
        dialog.exec()


class AuditDialog(QDialog):
    def __init__(self, gui):
        super().__init__(gui)
        self.gui = gui
        self.db = gui.current_db.new_api
        self.snapshots = {}
        self.pending = []
        self.rows = []
        self.errors = []
        self.scanned = 0
        self.worker = None
        self.close_when_finished = False
        self.setWindowTitle('Metadata Audit')
        self.resize(1180, 650)
        layout = QVBoxLayout(self)
        hint = QLabel('Compare the current EPUB with Calibre. Check individual differences to copy Calibre values into the EPUB.\n'
                      'Review the values first: the database can also contain mistakes. Covers and contributor credits are preserved.')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        self.scan_selected = QPushButton('Scan selected books')
        self.scan_library = QPushButton('Scan whole library')
        self.stop = QPushButton('Stop')
        self.stop.setEnabled(False)
        for button in (self.scan_selected, self.scan_library, self.stop):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(['Apply', 'ID', 'Book', 'Field', 'Calibre database', 'Inside EPUB'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setSortingEnabled(False)
        header = self.table.horizontalHeader()
        for index in (0, 1, 3):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        for index in (2, 4, 5):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        self.status = QLabel('Choose selected books or the whole library. Nothing is checked by default.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        footer = QHBoxLayout()
        self.select_all = QPushButton('Check all differences')
        self.select_none = QPushButton('Uncheck all')
        self.error_button = QPushButton('Show scan errors')
        self.error_button.setEnabled(False)
        self.apply_button = QPushButton('Apply checked differences')
        self.apply_button.setEnabled(False)
        self.close_button = QPushButton('Close')
        for button in (self.select_all, self.select_none, self.error_button):
            footer.addWidget(button)
        footer.addStretch()
        footer.addWidget(self.apply_button)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.scan_next)
        self.scan_selected.clicked.connect(self.start_selected)
        self.scan_library.clicked.connect(lambda: self.start_scan(sorted(self.db.all_book_ids())))
        self.stop.clicked.connect(self.stop_work)
        self.select_all.clicked.connect(lambda: self.check_all(True))
        self.select_none.clicked.connect(lambda: self.check_all(False))
        self.table.itemChanged.connect(self.update_apply_button)
        self.apply_button.clicked.connect(self.apply_checked)
        self.error_button.clicked.connect(self.show_errors)
        self.close_button.clicked.connect(self.reject)

    def start_selected(self):
        ids = list(self.gui.library_view.get_selected_ids())
        if not ids:
            QMessageBox.information(self, 'Metadata Audit', 'Select books in the Calibre library first.')
            return
        self.start_scan(ids)

    def start_scan(self, ids):
        if self.worker is not None or self.timer.isActive():
            return
        if self.gui.current_db.new_api is not self.db:
            QMessageBox.warning(self, 'Metadata Audit', 'The active library changed. Close this window and open it again.')
            return
        self.table.setRowCount(0)
        self.rows, self.snapshots, self.errors = [], {}, []
        self.pending = list(ids)
        self.total, self.scanned, self.with_epub = len(ids), 0, 0
        self.progress.hide()
        self.set_busy(True)
        self.status.setText(f'Scanning {self.total} books…')
        self.timer.start(0)

    def set_busy(self, busy):
        for button in (self.scan_selected, self.scan_library, self.select_all, self.select_none):
            button.setEnabled(not busy)
        self.stop.setEnabled(busy)
        self.table.setEnabled(not busy)
        self.update_apply_button()

    def scan_next(self):
        if not self.pending:
            self.stop_scan()
            return
        book_id = self.pending.pop(0)
        try:
            if 'EPUB' in self.db.formats(book_id):
                self.with_epub += 1
                data = self.db.format(book_id, 'EPUB')
                if data is None:
                    raise ValueError('Cannot read EPUB.')
                target = target_metadata(self.db.get_metadata(book_id))
                changes = differences(data, target)
                if changes:
                    self.snapshots[book_id] = (fingerprint(data), target)
                for change in changes:
                    self.add_row(book_id, target['title'], change)
        except Exception:
            self.errors.append(f'Book {book_id}:\n{traceback.format_exc()}')
        self.scanned += 1
        self.status.setText(f'Scanned {self.scanned}/{self.total} books. '
                            f'{len(self.snapshots)} books have differences; {len(self.errors)} errors.')

    def add_row(self, book_id, title, change):
        row = self.table.rowCount()
        self.table.insertRow(row)
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable)
        check.setCheckState(Qt.CheckState.Unchecked)
        self.table.setItem(row, 0, check)
        for col, value in enumerate((str(book_id), title, change['field'].capitalize(), change['database'], change['epub']), 1):
            item = QTableWidgetItem(value.replace('\n', ' | '))
            item.setToolTip(value)
            self.table.setItem(row, col, item)
        self.rows.append((book_id, change['field']))

    def stop_scan(self):
        interrupted = bool(self.pending)
        self.timer.stop()
        self.pending = []
        self.set_busy(False)
        self.error_button.setEnabled(bool(self.errors))
        self.status.setText(f'{"Stopped" if interrupted else "Finished"}: {self.scanned} books checked, '
                            f'{self.with_epub} EPUBs. {len(self.snapshots)} books have {len(self.rows)} field differences. '
                            f'{len(self.errors)} errors. Review and check the fields you want to change.')

    def stop_work(self):
        if self.worker is not None:
            self.worker.requestInterruption()
            self.stop.setEnabled(False)
            self.status.setText('Stopping after the current book. Completed changes will be kept.')
        else:
            self.stop_scan()

    def check_all(self, checked):
        if self.worker is not None:
            return
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def update_apply_button(self, *args):
        checked = any(self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                      for row in range(self.table.rowCount()))
        self.apply_button.setEnabled(self.worker is None and not self.timer.isActive() and checked)

    def apply_checked(self):
        if self.worker is not None or self.timer.isActive() or self.gui.current_db.new_api is not self.db:
            return
        chosen = defaultdict(set)
        for row, (book_id, field) in enumerate(self.rows):
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked:
                chosen[book_id].add(field)
        if not chosen:
            return
        count = sum(len(fields) for fields in chosen.values())
        answer = QMessageBox.question(self, 'Apply metadata changes',
            f'Copy {count} checked fields from Calibre into {len(chosen)} EPUBs?\n\n'
            'This replaces the current EPUB files without creating ORIGINAL_EPUB backups. '
            'Existing backups are left alone. Covers and story content stay unchanged.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        jobs = []
        for book_id, fields in chosen.items():
            digest, target = self.snapshots[book_id]
            jobs.append((book_id, frozenset(fields), digest, target))
        self.worker = ApplyWorker(self.db, jobs, self)
        self.worker.progress.connect(self.apply_progress)
        self.worker.finished.connect(self.apply_finished)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.progress.show()
        self.stop.setText('Stop after current book')
        self.close_when_finished = False
        self.set_busy(True)
        self.status.setText(f'Applying metadata to {len(jobs)} EPUBs…')
        self.worker.start()

    def apply_progress(self, processed, total, title):
        if self.worker is None:
            return
        self.progress.setValue(processed)
        if self.worker is not None and self.worker.isInterruptionRequested():
            self.status.setText(f'Finishing the current book before stopping ({processed}/{total} processed).')
        else:
            self.status.setText(f'Applying {processed}/{total} EPUBs: {title}')

    def apply_finished(self):
        worker = self.worker
        if worker is None:
            return
        done, errors = worker.done, worker.errors
        skipped = len(worker.jobs) - worker.processed
        self.worker = None
        worker.deleteLater()
        self.stop.setText('Stop')
        if done:
            self.gui.library_view.model().refresh_ids(done)
        if errors:
            self.errors.extend(errors)
            self.error_button.setEnabled(True)
        result = f'Updated {len(done)} EPUBs. {len(errors)} failures. {skipped} not processed.'
        # Old checkboxes and hashes must not be reused after a partial or complete write.
        self.table.setRowCount(0)
        self.rows, self.snapshots = [], {}
        self.set_busy(False)
        self.status.setText(result + ' Scan again to check the files.')
        if self.close_when_finished:
            super().reject()
        else:
            QMessageBox.information(self, 'Metadata Audit', result
                + '\nScan again to see the remaining differences.'
                + (' Use Show scan errors for details.' if errors else ''))

    def show_errors(self):
        dialog = QMessageBox(self)
        dialog.setWindowTitle('Metadata Audit errors')
        dialog.setText(f'{len(self.errors)} errors. Other books can still be reviewed.')
        dialog.setDetailedText('\n\n'.join(self.errors))
        dialog.exec()

    def reject(self):
        if self.worker is not None:
            self.close_when_finished = True
            self.stop_work()
            return
        self.timer.stop()
        super().reject()
