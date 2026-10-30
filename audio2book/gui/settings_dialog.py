"""Окно настроек."""
from __future__ import annotations

import os

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..core.settings import Settings
from ..core.transcriber import MODELS, cuda_available

LANGS = [("ru", "Русский"), ("en", "Английский"), ("uk", "Украинский"), ("be", "Белорусский"),
         ("kk", "Казахский"), ("de", "Немецкий"), ("fr", "Французский"), ("es", "Испанский"),
         ("it", "Итальянский"), ("pl", "Польский"), ("auto", "Определять автоматически")]


def _path_row(edit: QLineEdit, title: str, folder: bool = True, filt: str = "") -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton("…")
    b.setFixedWidth(32)

    def pick():
        if folder:
            p = QFileDialog.getExistingDirectory(w, title, edit.text())
        else:
            p, _ = QFileDialog.getOpenFileName(w, title, edit.text(), filt)
        if p:
            edit.setText(os.path.normpath(p))
    b.clicked.connect(pick)
    h.addWidget(b)
    return w


class SettingsDialog(QDialog):
    def __init__(self, s: Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(620)
        v = QVBoxLayout(self)

        g = QGroupBox("Распознавание речи")
        f = QFormLayout(g)
        self.model = QComboBox()
        self.model.setEditable(True)
        for k, t in MODELS:
            self.model.addItem(t, k)
        i = self.model.findData(s.model)
        if i >= 0:
            self.model.setCurrentIndex(i)
        else:
            self.model.setEditText(s.model)
        mrow = QWidget()
        mh = QHBoxLayout(mrow)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.model, 1)
        bm = QPushButton("Своя модель…")
        bm.setToolTip("Папка с моделью в формате faster-whisper (CTranslate2), например скачанная вручную")
        bm.clicked.connect(self._pick_model)
        mh.addWidget(bm)
        f.addRow("Модель:", mrow)

        self.device = QComboBox()
        for k, t in (("auto", "Автоматически"), ("cuda", "Видеокарта NVIDIA (CUDA)"), ("cpu", "Процессор")):
            self.device.addItem(t, k)
        self.device.setCurrentIndex(max(0, self.device.findData(s.device)))
        gpu = QLabel("✓ видеокарта найдена" if cuda_available() else "видеокарта NVIDIA не найдена")
        gpu.setStyleSheet("color: gray")
        drow = QWidget()
        dh = QHBoxLayout(drow)
        dh.setContentsMargins(0, 0, 0, 0)
        dh.addWidget(self.device, 1)
        dh.addWidget(gpu)
        f.addRow("Устройство:", drow)

        self.ctype = QComboBox()
        for k, t in (("auto", "Автоматически (GPU: float16, CPU: int8)"), ("float16", "float16"),
                     ("int8_float16", "int8_float16 (меньше видеопамяти)"), ("int8", "int8"),
                     ("float32", "float32")):
            self.ctype.addItem(t, k)
        self.ctype.setCurrentIndex(max(0, self.ctype.findData(s.compute_type)))
        f.addRow("Точность вычислений:", self.ctype)

        self.threads = QSpinBox()
        self.threads.setRange(0, 128)
        self.threads.setSpecialValueText("все ядра")
        self.threads.setValue(s.cpu_threads)
        f.addRow("Потоков процессора:", self.threads)

        self.lang = QComboBox()
        for k, t in LANGS:
            self.lang.addItem(t, k)
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.language)))
        f.addRow("Язык книги:", self.lang)

        self.beam = QSpinBox()
        self.beam.setRange(1, 10)
        self.beam.setValue(s.beam_size)
        self.beam.setToolTip("Больше — чуть точнее, но медленнее. 5 — хороший баланс, 1 — максимально быстро")
        f.addRow("Тщательность (beam):", self.beam)

        self.vad = QCheckBox("Пропускать тишину и музыку (VAD) — быстрее и меньше «мусора»")
        self.vad.setChecked(s.vad)
        f.addRow("", self.vad)

        self.prompt = QLineEdit(s.initial_prompt)
        self.prompt.setPlaceholderText("по умолчанию — образец литературного текста с пунктуацией")
        self.prompt.setToolTip("Подсказка модели: стиль текста, редкие имена героев, термины книги")
        f.addRow("Подсказка (имена, термины):", self.prompt)
        v.addWidget(g)

        g2 = QGroupBox("Текст и экспорт")
        f2 = QFormLayout(g2)
        self.pause = QDoubleSpinBox()
        self.pause.setRange(0.5, 10.0)
        self.pause.setSingleStep(0.1)
        self.pause.setSuffix(" с")
        self.pause.setValue(s.pause_para)
        self.pause.setToolTip("Пауза чтеца, после которой начинается новый абзац")
        f2.addRow("Пауза для нового абзаца:", self.pause)
        self.maxs = QSpinBox()
        self.maxs.setRange(2, 50)
        self.maxs.setValue(s.max_sentences)
        f2.addRow("Не больше предложений в абзаце:", self.maxs)
        self.titles = QCheckBox("Заголовки глав в тексте книги")
        self.titles.setChecked(s.chapter_titles_in_text)
        f2.addRow("", self.titles)
        self.out = QLineEdit(s.output_dir)
        f2.addRow("Папка для книг:", _path_row(self.out, "Папка для готовых книг"))
        self.calibre = QLineEdit(s.calibre_path)
        self.calibre.setPlaceholderText("найдётся автоматически, если Calibre установлен")
        f2.addRow("Calibre (для Kindle AZW3):",
                  _path_row(self.calibre, "ebook-convert.exe", folder=False, filt="ebook-convert.exe (ebook-convert.exe)"))
        v.addWidget(g2)

        g3 = QGroupBox("Модели и система")
        f3 = QFormLayout(g3)
        self.models_dir = QLineEdit(s.models_dir)
        f3.addRow("Папка моделей:", _path_row(self.models_dir, "Папка для моделей Whisper"))
        self.hf = QLineEdit(s.hf_endpoint)
        self.hf.setPlaceholderText("пусто = huggingface.co; если не качает — https://hf-mirror.com")
        f3.addRow("Сервер загрузки моделей:", self.hf)
        self.awake = QCheckBox("Не давать компьютеру уснуть во время распознавания")
        self.awake.setChecked(s.keep_awake)
        f3.addRow("", self.awake)
        v.addWidget(g3)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _pick_model(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с моделью faster-whisper (model.bin, config.json)")
        if d:
            if not os.path.isfile(os.path.join(d, "model.bin")):
                from PySide6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Модель", "В папке нет model.bin — это не модель faster-whisper.")
                return
            self.model.setEditText(os.path.normpath(d))

    def accept(self):
        s = self.s
        i = self.model.currentIndex()
        txt = self.model.currentText().strip()
        s.model = self.model.itemData(i) if i >= 0 and self.model.itemText(i) == txt else txt
        s.device = self.device.currentData()
        s.compute_type = self.ctype.currentData()
        s.cpu_threads = self.threads.value()
        s.language = self.lang.currentData()
        s.beam_size = self.beam.value()
        s.vad = self.vad.isChecked()
        s.initial_prompt = self.prompt.text().strip()
        s.pause_para = self.pause.value()
        s.max_sentences = self.maxs.value()
        s.chapter_titles_in_text = self.titles.isChecked()
        s.output_dir = self.out.text().strip() or s.output_dir
        s.calibre_path = self.calibre.text().strip()
        s.models_dir = self.models_dir.text().strip() or s.models_dir
        s.hf_endpoint = self.hf.text().strip()
        s.keep_awake = self.awake.isChecked()
        super().accept()
