"""Редактор распознанного текста с прослушиванием фрагментов."""
from __future__ import annotations

import os
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QKeySequence, QPixmap, QTextOption
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QStyle, QStyledItemDelegate, QTableView,
                               QTabWidget, QTextBrowser, QToolBar, QVBoxLayout, QWidget)

from ..core.audio import fmt_duration
from ..core.project import Book, Chapter
from ..core.settings import FORMATS, Settings

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAS_MM = True
except Exception:  # pragma: no cover
    HAS_MM = False


class SegmentModel(QAbstractTableModel):
    HEAD = ["¶", "Время", "Текст"]
    edited = Signal()

    def __init__(self):
        super().__init__()
        self.ch: Chapter | None = None
        self.playing = -1

    def set_chapter(self, ch):
        self.beginResetModel()
        self.ch, self.playing = ch, -1
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() or not self.ch else len(self.ch.segments)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, s, o, role=Qt.DisplayRole):
        if o == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEAD[s]
        if o == Qt.Horizontal and role == Qt.ToolTipRole and s == 0:
            return "С этого фрагмента начинается новый абзац"

    def data(self, idx, role=Qt.DisplayRole):
        seg = self.ch.segments[idx.row()]
        c = idx.column()
        if role == Qt.CheckStateRole and c == 0:
            return Qt.Checked if seg.para else Qt.Unchecked
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == 1:
                return fmt_duration(seg.start - self.ch.start)
            if c == 2:
                return seg.text
        if role == Qt.BackgroundRole and idx.row() == self.playing:
            from PySide6.QtGui import QColor
            return QColor(255, 236, 160)
        if role == Qt.ForegroundRole and idx.row() == self.playing:
            from PySide6.QtGui import QColor
            return QColor(0, 0, 0)
        return None

    def flags(self, idx):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if idx.column() == 0:
            f |= Qt.ItemIsUserCheckable
        if idx.column() == 2:
            f |= Qt.ItemIsEditable
        return f

    def setData(self, idx, value, role=Qt.EditRole):
        seg = self.ch.segments[idx.row()]
        if idx.column() == 0 and role == Qt.CheckStateRole:
            seg.para = Qt.CheckState(value) == Qt.Checked
        elif idx.column() == 2 and role == Qt.EditRole:
            seg.text = str(value).replace("\n", " ").strip()
        else:
            return False
        self.dataChanged.emit(idx, idx)
        self.edited.emit()
        return True

    def set_playing(self, row):
        old, self.playing = self.playing, row
        for r in (old, row):
            if 0 <= r < self.rowCount():
                self.dataChanged.emit(self.index(r, 0), self.index(r, 2))


class WrapDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        ed = QPlainTextEdit(parent)
        ed.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        return ed

    def setEditorData(self, ed, index):
        ed.setPlainText(index.data(Qt.EditRole) or "")

    def setModelData(self, ed, model, index):
        model.setData(index, ed.toPlainText(), Qt.EditRole)


class EditorWindow(QMainWindow):
    saved = Signal(object)

    def __init__(self, project_path: str, settings: Settings, parent=None):
        super().__init__(parent)
        self.path = project_path
        self.settings = settings
        self.book = Book.load(project_path)
        self.dirty = False
        self.cur = -1
        self.play_to = None
        self.follow = False
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.resize(1150, 760)
        self._build()
        self._load_meta()
        self.chapters.setCurrentRow(0)
        self._title()

    # ------------------------------------------------------------------ UI
    def _build(self):
        st = self.style()
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def act(text, sp, slot, sc=None):
            a = QAction(st.standardIcon(sp), text, self)
            a.triggered.connect(slot)
            if sc:
                a.setShortcut(QKeySequence(sc))
            tb.addAction(a)
            return a

        act("Сохранить", QStyle.SP_DialogSaveButton, self.save, "Ctrl+S")
        act("Экспорт…", QStyle.SP_ArrowRight, self.export)
        tb.addSeparator()
        self.a_play = act("Фрагмент", QStyle.SP_MediaPlay, self.play_segment, "F2")
        self.a_play.setToolTip("Прослушать выделенный фрагмент (F2)")
        self.a_follow = act("Слушать отсюда", QStyle.SP_MediaSeekForward, self.play_from, "F3")
        self.a_stop = act("Стоп", QStyle.SP_MediaStop, self.stop_play, "Esc")
        self.rate = QComboBox()
        for r in ("0.75", "1.0", "1.25", "1.5", "2.0"):
            self.rate.addItem("×" + r, float(r))
        self.rate.setCurrentIndex(1)
        self.rate.currentIndexChanged.connect(lambda: self.player and self.player.setPlaybackRate(self.rate.currentData()))
        tb.addWidget(self.rate)
        tb.addSeparator()
        act("Новый абзац", QStyle.SP_ArrowDown, self.toggle_para, "Ctrl+Return")
        act("Объединить со следующим", QStyle.SP_ArrowUp, self.merge_next, "Ctrl+J")
        if not HAS_MM:
            for a in (self.a_play, self.a_follow, self.a_stop):
                a.setEnabled(False)

        central = QWidget()
        lay = QVBoxLayout(central)

        # метаданные книги
        meta = QHBoxLayout()
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(90, 130)
        self.cover_lbl.setStyleSheet("border: 1px solid #999;")
        self.cover_lbl.setAlignment(Qt.AlignCenter)
        cv = QVBoxLayout()
        cv.addWidget(self.cover_lbl)
        b = QPushButton("Обложка…")
        b.clicked.connect(self.change_cover)
        cv.addWidget(b)
        meta.addLayout(cv)
        form = QFormLayout()
        self.e_title, self.e_author, self.e_narr, self.e_year = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.e_ann = QPlainTextEdit()
        self.e_ann.setMaximumHeight(52)
        form.addRow("Название:", self.e_title)
        form.addRow("Автор:", self.e_author)
        r = QHBoxLayout()
        r.addWidget(self.e_narr, 3)
        r.addWidget(QLabel("Год:"))
        self.e_year.setMaximumWidth(70)
        r.addWidget(self.e_year)
        form.addRow("Читает:", r)
        form.addRow("Аннотация:", self.e_ann)
        for w in (self.e_title, self.e_author, self.e_narr, self.e_year):
            w.textEdited.connect(self._mark)
        self.e_ann.textChanged.connect(self._mark_ann)
        meta.addLayout(form, 1)
        lay.addLayout(meta)

        # поиск и замена
        fr = QHBoxLayout()
        self.f_find, self.f_repl = QLineEdit(), QLineEdit()
        self.f_find.setPlaceholderText("Найти (например, неверно распознанное имя)")
        self.f_repl.setPlaceholderText("Заменить на")
        self.f_case = QCheckBox("Учитывать регистр")
        self.f_word = QCheckBox("Слово целиком")
        self.f_word.setChecked(True)
        bf, br = QPushButton("Найти далее"), QPushButton("Заменить во всей книге")
        bf.clicked.connect(self.find_next)
        self.f_find.returnPressed.connect(self.find_next)
        br.clicked.connect(self.replace_all)
        for w in (self.f_find, self.f_repl):
            fr.addWidget(w, 1)
        for w in (self.f_case, self.f_word, bf, br):
            fr.addWidget(w)
        lay.addLayout(fr)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Главы (двойной щелчок — переименовать):"))
        self.chapters = QListWidget()
        self.chapters.currentRowChanged.connect(self.show_chapter)
        self.chapters.itemChanged.connect(self._chapter_renamed)
        ll.addWidget(self.chapters)
        split.addWidget(left)

        self.tabs = QTabWidget()
        self.model = SegmentModel()
        self.model.edited.connect(self._mark)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.setItemDelegateForColumn(2, WrapDelegate(self.view))
        self.view.setWordWrap(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Fixed)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.view.setColumnWidth(0, 30)
        self.view.setColumnWidth(1, 70)
        self.view.verticalHeader().setVisible(False)
        self.view.doubleClicked.connect(lambda i: i.column() == 1 and self.play_segment())
        self.tabs.addTab(self.view, "Фрагменты (двойной щелчок по тексту — правка, по времени — прослушать)")
        self.preview = QTextBrowser()
        self.tabs.addTab(self.preview, "Как будет в книге")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self._render_preview())
        split.addWidget(self.tabs)
        split.setSizes([260, 890])
        lay.addWidget(split, 1)
        self.setCentralWidget(central)

        self.player = None          # создаётся при первом прослушивании
        self._pending_seek = None

    def _ensure_player(self):
        if self.player is None and HAS_MM:
            self.player = QMediaPlayer(self)
            self.audio_out = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_out)
            self.player.positionChanged.connect(self._on_pos)
            self.player.mediaStatusChanged.connect(self._on_status)
            self.player.errorOccurred.connect(
                lambda e, s: self.statusBar().showMessage("Ошибка воспроизведения: " + s))
        return self.player

    def _title(self):
        self.setWindowTitle(("* " if self.dirty else "") + f"Редактор — {self.book.title}")

    def _mark(self, *_):
        if not self.dirty:
            self.dirty = True
            self._title()

    def _mark_ann(self):
        if self.e_ann.toPlainText() != self.book.annotation:
            self._mark()

    def _load_meta(self):
        b = self.book
        self.e_title.setText(b.title)
        self.e_author.setText(b.author)
        self.e_narr.setText(b.narrator)
        self.e_year.setText(b.year)
        self.e_ann.blockSignals(True)
        self.e_ann.setPlainText(b.annotation)
        self.e_ann.blockSignals(False)
        self._show_cover()
        self.chapters.blockSignals(True)
        self.chapters.clear()
        for ch in b.chapters:
            it = QListWidgetItem(ch.title + ("" if ch.done else "  (не распознана)"))
            it.setFlags(it.flags() | Qt.ItemIsEditable)
            self.chapters.addItem(it)
        self.chapters.blockSignals(False)

    def _show_cover(self):
        if self.book.cover and os.path.isfile(self.book.cover):
            pm = QPixmap(self.book.cover)
            self.cover_lbl.setPixmap(pm.scaled(self.cover_lbl.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.cover_lbl.setText("нет\nобложки")

    def change_cover(self):
        f, _ = QFileDialog.getOpenFileName(self, "Обложка", "", "Картинки (*.jpg *.jpeg *.png)")
        if not f:
            return
        ext = ".png" if f.lower().endswith(".png") else ".jpg"
        dst = os.path.join(os.path.dirname(self.path), "cover" + ext)
        if os.path.abspath(f) != os.path.abspath(dst):
            import shutil
            shutil.copyfile(f, dst)
        self.book.cover = dst
        self._show_cover()
        self._mark()

    def show_chapter(self, row):
        self.stop_play()
        self.cur = row
        if 0 <= row < len(self.book.chapters):
            self.model.set_chapter(self.book.chapters[row])
            self.view.resizeRowsToContents()
            if self.tabs.currentIndex() == 1:
                self._render_preview()

    def _chapter_renamed(self, item):
        r = self.chapters.row(item)
        t = item.text().replace("  (не распознана)", "").strip()
        if t and t != self.book.chapters[r].title:
            self.book.chapters[r].title = t
            self._mark()

    def _render_preview(self):
        if self.cur < 0:
            return
        from html import escape
        ch = self.book.chapters[self.cur]
        html = f"<h2 style='text-align:center'>{escape(ch.title)}</h2>" + "".join(
            f"<p style='text-indent:1.5em; margin:0 0 4px 0'>{escape(p)}</p>" for p in ch.text_paragraphs())
        self.preview.setHtml(f"<div style='font-family:Georgia,serif; font-size:12pt'>{html}</div>")

    # ------------------------------------------------------------ правка
    def _sel_row(self):
        idx = self.view.currentIndex()
        return idx.row() if idx.isValid() else -1

    def toggle_para(self):
        r = self._sel_row()
        if r >= 0:
            i = self.model.index(r, 0)
            seg = self.model.ch.segments[r]
            self.model.setData(i, (Qt.Unchecked if seg.para else Qt.Checked).value, Qt.CheckStateRole)

    def merge_next(self):
        r = self._sel_row()
        ch = self.model.ch
        if r < 0 or r + 1 >= len(ch.segments):
            return
        a, b = ch.segments[r], ch.segments[r + 1]
        a.text = (a.text.rstrip() + " " + b.text.lstrip()).strip()
        a.end = b.end
        del ch.segments[r + 1]
        self.model.set_chapter(ch)
        self.view.resizeRowsToContents()
        self.view.selectRow(r)
        self._mark()

    def _pattern(self):
        q = self.f_find.text()
        if not q:
            return None
        p = re.escape(q)
        if self.f_word.isChecked():
            p = r"(?<!\w)" + p + r"(?!\w)"
        return re.compile(p, 0 if self.f_case.isChecked() else re.I)

    def find_next(self):
        pat = self._pattern()
        if not pat:
            return
        chs = self.book.chapters
        start_ch, start_row = max(self.cur, 0), self._sel_row() + 1
        for k in range(len(chs) + 1):
            ci = (start_ch + k) % len(chs)
            segs = chs[ci].segments
            for ri in range(start_row if k == 0 else 0, len(segs)):
                if pat.search(segs[ri].text):
                    if ci != self.cur:
                        self.chapters.setCurrentRow(ci)
                    self.view.selectRow(ri)
                    self.view.scrollTo(self.model.index(ri, 2))
                    return
        self.statusBar().showMessage("Не найдено", 4000)

    def replace_all(self):
        pat = self._pattern()
        if not pat:
            return
        repl = self.f_repl.text()
        n = 0
        for ch in self.book.chapters:
            for seg in ch.segments:
                new, k = pat.subn(lambda m: _match_case(m.group(0), repl), seg.text)
                if k:
                    seg.text, n = new, n + k
            if pat.search(ch.title):
                ch.title = pat.sub(lambda m: _match_case(m.group(0), repl), ch.title)
        if n:
            self._mark()
            self._collect_meta()
            row = self.cur
            self._load_meta()
            self.chapters.setCurrentRow(row)
        self.statusBar().showMessage(f"Заменено: {n}", 6000)

    # ------------------------------------------------------------ звук
    def _seek_play(self, src, sec):
        if not self._ensure_player():
            return
        url = QUrl.fromLocalFile(src)
        self.player.setPlaybackRate(self.rate.currentData())
        if self.player.source() != url:
            self._pending_seek = int(sec * 1000)
            self.player.setSource(url)
        else:
            self.player.setPosition(int(sec * 1000))
            self.player.play()

    def _on_status(self, st):
        if self._pending_seek is not None and st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia):
            self.player.setPosition(self._pending_seek)
            self._pending_seek = None
            self.player.play()

    def play_segment(self):
        r = self._sel_row()
        if r < 0 or not self.model.ch:
            return
        seg = self.model.ch.segments[r]
        self.follow = False
        self.play_to = seg.end + 0.15
        self.model.set_playing(r)
        self._seek_play(self.model.ch.source, max(0.0, seg.start - 0.1))

    def play_from(self):
        r = max(self._sel_row(), 0)
        if not self.model.ch or not self.model.ch.segments:
            return
        self.follow = True
        self.play_to = self.model.ch.end
        self._seek_play(self.model.ch.source, self.model.ch.segments[r].start)

    def stop_play(self):
        if self.player:
            self.player.pause()
        self.play_to = None
        self.model.set_playing(-1)

    def _on_pos(self, ms):
        t = ms / 1000.0
        if self.play_to is not None and t >= self.play_to:
            self.stop_play()
            return
        if self.follow and self.model.ch:
            segs = self.model.ch.segments
            row = next((i for i in range(len(segs) - 1, -1, -1) if segs[i].start <= t), -1)
            if row != self.model.playing:
                self.model.set_playing(row)
                if row >= 0:
                    self.view.scrollTo(self.model.index(row, 2))

    # ------------------------------------------------------------ сохранение
    def _collect_meta(self):
        b = self.book
        b.title = self.e_title.text().strip() or b.title
        b.author = self.e_author.text().strip()
        b.narrator = self.e_narr.text().strip()
        b.year = self.e_year.text().strip()
        b.annotation = self.e_ann.toPlainText().strip()

    def save(self):
        self._collect_meta()
        try:
            self.book.save(self.path)
        except OSError as e:
            QMessageBox.critical(self, "Сохранение", str(e))
            return False
        self.dirty = False
        self._title()
        self.statusBar().showMessage("Сохранено", 3000)
        self.saved.emit(self.book)
        return True

    def maybe_save(self):
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Сохранить?", "Сохранить изменения в тексте книги?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        if r == QMessageBox.Discard:
            self.book = Book.load(self.path)
            self.dirty = False
            return True
        return False

    def export(self):
        if not self.save():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Экспорт")
        v = QVBoxLayout(dlg)
        boxes = {}
        for k, t in FORMATS:
            cb = QCheckBox(t)
            cb.setChecked(k in self.settings.formats)
            boxes[k] = cb
            v.addWidget(cb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if not dlg.exec():
            return
        fmts = [k for k, cb in boxes.items() if cb.isChecked()]
        from ..export import export_book
        log = []
        folder = os.path.dirname(self.path)
        export_book(self.book, folder, fmts, self.settings, log.append)
        box = QMessageBox(self)
        box.setWindowTitle("Экспорт")
        box.setText("\n".join(log))
        open_btn = box.addButton("Открыть папку", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        box.exec()
        if box.clickedButton() is open_btn:
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def closeEvent(self, e):
        if self.maybe_save():
            self.stop_play()
            e.accept()
        else:
            e.ignore()


def _match_case(found: str, repl: str) -> str:
    """При замене без учёта регистра сохраняем заглавную букву: «иван»→«Ивана» в начале фразы."""
    if found[:1].isupper() and repl[:1].islower():
        return repl[:1].upper() + repl[1:]
    return repl
