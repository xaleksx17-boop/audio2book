"""Главное окно: очередь книг, форматы, журнал, живой просмотр текста."""
from __future__ import annotations

import json
import os
import shutil
import sys

from PySide6.QtCore import QObject, QRunnable, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QSplitter, QStyle, QTableWidget, QTableWidgetItem,
                               QTabWidget, QToolBar, QVBoxLayout, QWidget)

from ..core import audio
from ..core.pipeline import (STATUS_DONE, STATUS_ERR, STATUS_PAUSE, STATUS_RUN, STATUS_STOP,
                             STATUS_WAIT, Job)
from ..core.project import PROJECT_EXT, Book
from ..core.settings import APP_TITLE, APP_VERSION, FORMATS, Settings, config_dir
from ..core.transcriber import MODELS, gpu_status
from .worker import Worker

COL_TITLE, COL_AUTHOR, COL_CH, COL_DUR, COL_STATUS, COL_PROG, COL_INFO = range(7)


class _InfoSignals(QObject):
    done = Signal(str, str, str, int, float, str)   # job_id, title, author, chapters, duration, error


class InfoTask(QRunnable):
    """Читает теги/главы в фоне, чтобы сразу показать книгу в очереди."""

    def __init__(self, job: Job, sig: _InfoSignals):
        super().__init__()
        self.job, self.sig = job, sig

    def run(self):
        try:
            if self.job.project_path and os.path.isfile(self.job.project_path):
                b = Book.load(self.job.project_path)
            else:
                b, _ = audio.build_book(audio.collect_audio_files(self.job.inputs))
            self.sig.done.emit(self.job.id, b.title, b.author, len(b.chapters), b.duration, "")
        except Exception as e:
            self.sig.done.emit(self.job.id, "", "", 0, 0.0, str(e))


class MainWindow(QMainWindow):
    start_job = Signal(object, object)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.jobs: list[Job] = []
        self.running: Job | None = None
        self.paused = False
        self.editors = {}
        self.setWindowTitle(f"{APP_TITLE} {APP_VERSION}")
        self.resize(1100, 720)
        self.setAcceptDrops(True)

        self.thread = QThread(self)
        self.worker = Worker()
        self.worker.moveToThread(self.thread)
        self.start_job.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.on_progress)
        self.worker.partial.connect(self.on_partial)
        self.worker.job_info.connect(self.on_job_info)
        self.worker.finished.connect(self.on_finished)
        self.thread.start()

        self.info_sig = _InfoSignals()
        self.info_sig.done.connect(self.on_info)

        self._build_ui()
        self.load_queue()
        self.update_buttons()
        self.statusBar().showMessage(gpu_status()[1])

    # ------------------------------------------------------------------ UI
    def _icon(self, sp):
        return self.style().standardIcon(sp)

    def _build_ui(self):
        tb = QToolBar("Основное")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def act(text, sp, slot, shortcut=None):
            a = QAction(self._icon(sp), text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        self.a_add = act("Файлы…", QStyle.SP_FileIcon, self.add_files, "Ctrl+O")
        self.a_addf = act("Папка…", QStyle.SP_DirOpenIcon, self.add_folder, "Ctrl+Shift+O")
        self.a_del = act("Убрать", QStyle.SP_TrashIcon, self.remove_selected, "Delete")
        tb.addSeparator()
        self.a_start = act("Старт", QStyle.SP_MediaPlay, self.start, "F5")
        self.a_pause = act("Пауза", QStyle.SP_MediaPause, self.toggle_pause, "F6")
        self.a_stop = act("Стоп", QStyle.SP_MediaStop, self.stop)
        tb.addSeparator()
        self.a_edit = act("Редактор", QStyle.SP_FileDialogDetailedView, self.open_editor, "Ctrl+E")
        self.a_folder = act("Папка книги", QStyle.SP_DirLinkIcon, self.open_folder)
        tb.addSeparator()
        act("Настройки", QStyle.SP_FileDialogInfoView, self.open_settings, "Ctrl+,")

        central = QWidget()
        lay = QVBoxLayout(central)

        row = QHBoxLayout()
        row.addWidget(QLabel("Модель:"))
        self.model_cb = QComboBox()
        for key, text in MODELS:
            self.model_cb.addItem(text, key)
        if self.model_cb.findData(self.settings.model) < 0:
            self.model_cb.addItem(os.path.basename(self.settings.model) + " (своя)", self.settings.model)
        self.model_cb.setCurrentIndex(self.model_cb.findData(self.settings.model))
        self.model_cb.currentIndexChanged.connect(self._model_changed)
        row.addWidget(self.model_cb)
        row.addSpacing(20)
        row.addWidget(QLabel("Форматы:"))
        self.fmt_boxes = {}
        for key, text in FORMATS:
            cb = QCheckBox(text)
            cb.setChecked(key in self.settings.formats)
            cb.toggled.connect(self._formats_changed)
            self.fmt_boxes[key] = cb
            row.addWidget(cb)
        row.addStretch(1)
        lay.addLayout(row)

        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Книга", "Автор", "Глав", "Длит.", "Статус", "Прогресс", ""])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        h.setSectionResizeMode(COL_AUTHOR, QHeaderView.Interactive)
        self.table.setColumnWidth(COL_AUTHOR, 200)
        self.table.setColumnWidth(COL_CH, 50)
        self.table.setColumnWidth(COL_DUR, 70)
        self.table.setColumnWidth(COL_STATUS, 110)
        self.table.setColumnWidth(COL_PROG, 150)
        self.table.setColumnWidth(COL_INFO, 190)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.table.cellDoubleClicked.connect(self._double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        split.addWidget(self.table)

        self.hint = QLabel("Перетащите сюда файлы аудиокниги (mp3, m4b, m4a, ogg, flac…) или папку с ними.\n"
                           "Несколько файлов одной книги = одна книга, каждый файл — глава; "
                           "оглавление m4b тоже превращается в главы.")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setStyleSheet("color: gray; padding: 8px;")
        lay.addWidget(self.hint)

        tabs = QTabWidget()
        self.live = QPlainTextEdit(readOnly=True)
        self.live.setPlaceholderText("Здесь появляется текст по мере распознавания")
        self.live.setMaximumBlockCount(2000)
        self.logw = QPlainTextEdit(readOnly=True)
        self.logw.setMaximumBlockCount(5000)
        tabs.addTab(self.live, "Распознанный текст")
        tabs.addTab(self.logw, "Журнал")
        split.addWidget(tabs)
        split.setSizes([380, 260])
        lay.addWidget(split, 1)
        self.setCentralWidget(central)

    # ------------------------------------------------------------ queue I/O
    def queue_path(self):
        return os.path.join(config_dir(), "queue.json")

    def load_queue(self):
        try:
            with open(self.queue_path(), encoding="utf-8") as f:
                for d in json.load(f):
                    self._add_job(Job.from_dict(d), probe=False)
        except (OSError, ValueError):
            pass

    def save_queue(self):
        try:
            with open(self.queue_path(), "w", encoding="utf-8") as f:
                json.dump([j.to_dict() for j in self.jobs], f, ensure_ascii=False, indent=1)
        except OSError:
            pass

    # ------------------------------------------------------------ adding
    def add_files(self):
        exts = " ".join("*" + e for e in sorted(audio.AUDIO_EXT))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Файлы одной аудиокниги", "",
            f"Аудиокниги ({exts});;Проект Audio2Book (*{PROJECT_EXT});;Все файлы (*.*)")
        if files:
            self.add_paths(files)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с аудиокнигой (или с несколькими книгами)")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        projects = [p for p in paths if p.endswith(PROJECT_EXT)]
        for p in projects:
            self._add_job(Job(inputs=[], project_path=p, formats=list(self.settings.formats)))
        paths = [p for p in paths if not p.endswith(PROJECT_EXT)]
        dirs = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]
        if files:
            self._add_job(Job(inputs=files, formats=list(self.settings.formats)))
        for d in dirs:
            subs = self._book_subfolders(d)
            if subs and QMessageBox.question(
                    self, "Несколько книг?",
                    f"В папке «{os.path.basename(d)}» {len(subs)} подпапок с аудио.\n\n"
                    "Добавить каждую подпапку как отдельную книгу?\n"
                    "(«Нет» — вся папка будет одной книгой)") == QMessageBox.Yes:
                for s in subs:
                    self._add_job(Job(inputs=[s], formats=list(self.settings.formats)))
            else:
                self._add_job(Job(inputs=[d], formats=list(self.settings.formats)))
        self.save_queue()

    @staticmethod
    def _book_subfolders(d):
        try:
            entries = os.listdir(d)
        except OSError:
            return []
        if any(os.path.splitext(e)[1].lower() in audio.AUDIO_EXT for e in entries):
            return []
        subs = [os.path.join(d, e) for e in sorted(entries, key=audio.natural_key)
                if os.path.isdir(os.path.join(d, e)) and audio.collect_audio_files([os.path.join(d, e)])]
        # «CD1/CD2» или «Часть 1/Часть 2» — это части одной книги, не спрашиваем
        import re
        if len(subs) < 2 or all(re.match(r"^(cd|disc|диск|часть|part|том)\s*\d+", os.path.basename(s), re.I)
                                for s in subs):
            return []
        return subs

    def _add_job(self, job: Job, probe=True):
        self.jobs.append(job)
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        name = job.title or (os.path.basename(job.inputs[0].rstrip("\\/")) if job.inputs
                             else os.path.basename(job.project_path).replace(PROJECT_EXT, ""))
        it = QTableWidgetItem(name)
        it.setData(Qt.UserRole, job.id)
        self.table.setItem(r, COL_TITLE, it)
        self.table.setItem(r, COL_AUTHOR, QTableWidgetItem(job.author))
        for c in (COL_CH, COL_DUR, COL_STATUS, COL_INFO):
            ro = QTableWidgetItem("")
            ro.setFlags(ro.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, c, ro)
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setTextVisible(True)
        self.table.setCellWidget(r, COL_PROG, bar)
        self.table.blockSignals(False)
        self._refresh_row(job)
        if probe and not job.title:
            QThreadPool.globalInstance().start(InfoTask(job, self.info_sig))
        self.hint.setVisible(False)

    def _row(self, job_id):
        for r in range(self.table.rowCount()):
            if self.table.item(r, COL_TITLE).data(Qt.UserRole) == job_id:
                return r
        return -1

    def _job(self, job_id):
        return next((j for j in self.jobs if j.id == job_id), None)

    def _refresh_row(self, job: Job):
        r = self._row(job.id)
        if r < 0:
            return
        self.table.blockSignals(True)
        if job.title:
            self.table.item(r, COL_TITLE).setText(job.title)
        self.table.item(r, COL_AUTHOR).setText(job.author)
        self.table.item(r, COL_CH).setText(str(job.chapters) if job.chapters else "")
        self.table.item(r, COL_DUR).setText(audio.fmt_duration(job.duration) if job.duration else "")
        self.table.item(r, COL_STATUS).setText(job.status)
        self.table.item(r, COL_STATUS).setToolTip(job.message)
        self.table.item(r, COL_INFO).setText(job.message if job.status == STATUS_ERR else "")
        self.table.cellWidget(r, COL_PROG).setValue(int(job.progress * 1000))
        self.table.cellWidget(r, COL_PROG).setFormat(f"{job.progress * 100:.1f}%")
        self.table.blockSignals(False)

    def on_info(self, job_id, title, author, chapters, duration, err):
        job = self._job(job_id)
        if not job:
            return
        if err:
            job.status, job.message = STATUS_ERR, err
        else:
            job.title = job.title or title
            job.author = job.author or author
            job.chapters, job.duration = chapters, duration
        self._refresh_row(job)
        self.save_queue()

    def _item_changed(self, item):
        job = self._job(self.table.item(item.row(), COL_TITLE).data(Qt.UserRole))
        if not job:
            return
        if item.column() == COL_TITLE:
            job.title = item.text().strip()
        elif item.column() == COL_AUTHOR:
            job.author = item.text().strip()
        self.save_queue()

    def selected_jobs(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self._job(self.table.item(r, COL_TITLE).data(Qt.UserRole)) for r in rows]

    def remove_selected(self):
        for job in self.selected_jobs():
            if job is self.running:
                continue
            self.table.removeRow(self._row(job.id))
            self.jobs.remove(job)
        self.save_queue()
        self.hint.setVisible(not self.jobs)

    # ------------------------------------------------------------ run control
    def start(self):
        if self.running and self.paused:
            self.toggle_pause()
            return
        if self.running:
            return
        sel = [j for j in self.selected_jobs() if j.status != STATUS_DONE]
        pending = sel or [j for j in self.jobs if j.status in (STATUS_WAIT, STATUS_STOP, STATUS_PAUSE)]
        if not pending:
            if not self.jobs:
                self.add_folder()
            return
        job = pending[0]
        job.status, job.message = STATUS_RUN, ""
        job.formats = [k for k, cb in self.fmt_boxes.items() if cb.isChecked()] or job.formats
        if job.project_path and job.project_path in self.editors:
            ed = self.editors[job.project_path]
            if ed.isVisible() and not ed.maybe_save():
                job.status = STATUS_STOP
                return
        self.running, self.paused = job, False
        self._refresh_row(job)
        self.live.clear()
        self.append_log(f"\n=== {job.title or 'Книга'} ===")
        self.start_job.emit(job, Settings(**self.settings.__dict__))
        self.update_buttons()

    def toggle_pause(self):
        if not self.running:
            return
        self.paused = not self.paused
        self.worker.pause(self.paused)
        self.running.status = STATUS_PAUSE if self.paused else STATUS_RUN
        self._refresh_row(self.running)
        self.update_buttons()

    def stop(self):
        if self.running:
            self.worker.stop()

    def on_progress(self, job_id, frac, info):
        job = self._job(job_id)
        if not job:
            return
        job.progress = frac
        r = self._row(job_id)
        if r >= 0:
            bar = self.table.cellWidget(r, COL_PROG)
            bar.setValue(int(frac * 1000))
            bar.setFormat(f"{frac * 100:.1f}%")
            if info:
                self.table.item(r, COL_INFO).setText(info)

    def on_partial(self, text):
        self.live.appendPlainText(text)

    def on_job_info(self, job_id, title, author, chapters, duration, project_path):
        job = self._job(job_id)
        if job:
            job.title = job.title or title
            job.author = job.author or author
            job.chapters, job.duration, job.project_path = chapters, duration, project_path
            self._refresh_row(job)
            self.save_queue()

    def on_finished(self, job_id, status, message):
        job = self._job(job_id)
        if job:
            job.status, job.message = status, message
            if status == STATUS_DONE:
                job.progress = 1.0
            self._refresh_row(job)
            r = self._row(job_id)
            if r >= 0 and status != STATUS_ERR:
                self.table.item(r, COL_INFO).setText("")
        self.running, self.paused = None, False
        self.save_queue()
        self.update_buttons()
        if status == STATUS_DONE:
            self.append_log("Готово.")
            if any(j.status == STATUS_WAIT for j in self.jobs):
                QTimer.singleShot(300, self.start)
            else:
                self.statusBar().showMessage("Все книги в очереди обработаны", 15000)
                QApplication_alert(self)
        elif status == STATUS_ERR and any(j.status == STATUS_WAIT for j in self.jobs):
            QTimer.singleShot(300, self.start)

    def update_buttons(self):
        running = self.running is not None
        self.a_start.setEnabled(not running or self.paused)
        self.a_start.setText("Продолжить" if self.paused else "Старт")
        self.a_pause.setEnabled(running and not self.paused)
        self.a_stop.setEnabled(running)
        sel = self.selected_jobs()
        has_proj = bool(sel) and bool(sel[0].project_path) and os.path.isfile(sel[0].project_path)
        self.a_edit.setEnabled(has_proj)
        self.a_folder.setEnabled(has_proj)
        self.a_del.setEnabled(bool(sel) and all(j is not self.running for j in sel))
        self.model_cb.setEnabled(not running)

    # ------------------------------------------------------------ misc
    def append_log(self, msg):
        self.logw.appendPlainText(msg)

    def _model_changed(self):
        self.settings.model = self.model_cb.currentData()
        self.settings.save()

    def _formats_changed(self):
        self.settings.formats = [k for k, cb in self.fmt_boxes.items() if cb.isChecked()]
        self.settings.save()

    def _double_click(self, row, col):
        if col in (COL_TITLE, COL_AUTHOR):
            return
        self.open_editor()

    def _context_menu(self, pos):
        sel = self.selected_jobs()
        if not sel:
            return
        job = sel[0]
        has_proj = bool(job.project_path) and os.path.isfile(job.project_path)
        m = QMenu(self)
        m.addAction("Открыть в редакторе", self.open_editor).setEnabled(has_proj)
        m.addAction("Открыть папку книги", self.open_folder).setEnabled(has_proj)
        m.addAction("Экспортировать заново", self.reexport).setEnabled(has_proj and job is not self.running)
        m.addSeparator()
        m.addAction("Распознать заново (с нуля)", self.restart_job).setEnabled(job is not self.running)
        m.addAction("Убрать из очереди", self.remove_selected).setEnabled(job is not self.running)
        m.exec(self.table.viewport().mapToGlobal(pos))

    def open_folder(self):
        sel = self.selected_jobs()
        if sel and sel[0].project_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(sel[0].project_path)))

    def open_editor(self):
        sel = self.selected_jobs()
        if not sel or not sel[0].project_path or not os.path.isfile(sel[0].project_path):
            return
        job = sel[0]
        if job is self.running and not self.paused:
            QMessageBox.information(self, "Редактор", "Книга сейчас распознаётся. Поставьте на паузу или "
                                    "дождитесь окончания — иначе правки перезапишутся.")
            return
        from .editor import EditorWindow
        ed = self.editors.get(job.project_path)
        if ed is None or not ed.isVisible():
            ed = EditorWindow(job.project_path, self.settings, self)
            ed.saved.connect(lambda b, j=job: self._editor_saved(j, b))
            self.editors[job.project_path] = ed
        ed.show()
        ed.raise_()
        ed.activateWindow()

    def _editor_saved(self, job, book):
        job.title, job.author = book.title, book.author
        self._refresh_row(job)
        self.save_queue()

    def reexport(self):
        from ..export import export_book
        job = self.selected_jobs()[0]
        book = Book.load(job.project_path)
        fmts = [k for k, cb in self.fmt_boxes.items() if cb.isChecked()]
        self.append_log(f"\n=== Экспорт: {book.title} ===")
        export_book(book, os.path.dirname(job.project_path), fmts, self.settings, self.append_log)

    def restart_job(self):
        job = self.selected_jobs()[0]
        if job.project_path and os.path.isfile(job.project_path):
            if QMessageBox.question(self, "Распознать заново",
                                    "Весь распознанный текст и правки этой книги будут удалены. Продолжить?") \
                    != QMessageBox.Yes:
                return
            os.remove(job.project_path)
        if not job.inputs:
            QMessageBox.warning(self, "Распознать заново", "Для этой книги не известны исходные файлы.")
            return
        job.project_path, job.progress, job.status, job.message = "", 0.0, STATUS_WAIT, ""
        self._refresh_row(job)
        self.save_queue()

    def open_settings(self):
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            self.settings.save()
            self.settings.apply_env()
            i = self.model_cb.findData(self.settings.model)
            if i < 0:
                self.model_cb.addItem(os.path.basename(self.settings.model) + " (своя)", self.settings.model)
                i = self.model_cb.count() - 1
            self.model_cb.blockSignals(True)
            self.model_cb.setCurrentIndex(i)
            self.model_cb.blockSignals(False)

    # drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    def closeEvent(self, e):
        if self.running:
            if QMessageBox.question(self, "Выход", "Идёт распознавание. Остановить и выйти?\n"
                                    "Сделанное сохранится, при следующем запуске можно продолжить.") \
                    != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.stop()
        for ed in list(self.editors.values()):
            if ed.isVisible() and not ed.close():
                e.ignore()
                return
        self.save_queue()
        self.thread.quit()
        self.thread.wait(10000)
        e.accept()


def QApplication_alert(w):
    from PySide6.QtWidgets import QApplication
    QApplication.alert(w, 0)
