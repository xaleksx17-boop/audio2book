"""Окно настроек."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QStackedWidget,
                               QStyle, QTabWidget, QVBoxLayout, QWidget)

from ..core.settings import Settings
from ..core.transcriber import MODELS, gpu_status
from ..core.translate import ENGINES, OFFLINE_MODELS

LANGS = [("ru", "Русский"), ("en", "Английский"), ("uk", "Украинский"), ("be", "Белорусский"),
         ("kk", "Казахский"), ("de", "Немецкий"), ("fr", "Французский"), ("es", "Испанский"),
         ("it", "Итальянский"), ("pl", "Польский"), ("auto", "Определять автоматически")]


def _page(*widgets) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


def _secret(value: str) -> QLineEdit:
    e = QLineEdit(value)
    e.setEchoMode(QLineEdit.Password)
    e.setPlaceholderText("вставьте ключ (Ctrl+V)")
    act = e.addAction(e.style().standardIcon(QStyle.SP_FileDialogContentsView), QLineEdit.TrailingPosition)
    act.setToolTip("Показать / скрыть")
    act.triggered.connect(lambda: e.setEchoMode(
        QLineEdit.Normal if e.echoMode() == QLineEdit.Password else QLineEdit.Password))
    return e


def _combo(items, current: str, editable: bool = True) -> QComboBox:
    c = QComboBox()
    c.setEditable(editable)
    for it in items:
        key, text = it if isinstance(it, tuple) else (it, it)
        c.addItem(text, key)
    i = c.findData(current)
    if i >= 0:
        c.setCurrentIndex(i)
    elif editable:
        c.setEditText(current)
    return c


def _combo_value(c: QComboBox) -> str:
    i = c.currentIndex()
    txt = c.currentText().strip()
    if i >= 0 and c.itemText(i) == txt:
        return c.itemData(i)
    return txt


def _note(html: str) -> QLabel:
    lb = QLabel(html)
    lb.setWordWrap(True)
    lb.setOpenExternalLinks(True)
    lb.setTextFormat(Qt.RichText)
    lb.setStyleSheet("color: gray")
    return lb


class _CheckSignal(QObject):
    done = Signal(bool, str)


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
        self.tabs = QTabWidget()
        v.addWidget(self.tabs)

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
        ok, msg = gpu_status()
        gpu = QLabel("✓ видеокарта найдена" if ok else "видеокарта не используется")
        gpu.setToolTip(msg)
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
        self.tabs.addTab(_page(g), "Распознавание")

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
        self.tabs.addTab(_page(g2), "Текст и экспорт")
        self.tabs.addTab(self._build_translate_tab(), "Перевод")

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
        self.tabs.addTab(_page(g3), "Система")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    # ------------------------------------------------------------ перевод
    def _build_translate_tab(self) -> QWidget:
        s = self.s
        w = QWidget()
        v = QVBoxLayout(w)
        top = QFormLayout()
        self.tr_engine = _combo(ENGINES, s.tr_engine, editable=False)
        top.addRow("Способ перевода:", self.tr_engine)
        v.addLayout(top)

        self.tr_stack = QStackedWidget()
        # офлайн
        pg = QWidget()
        f = QFormLayout(pg)
        self.tr_off_model = _combo(OFFLINE_MODELS, s.tr_offline_model)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.tr_off_model, 1)
        b = QPushButton("Своя модель…")
        b.clicked.connect(self._pick_tr_model)
        h.addWidget(b)
        f.addRow("Модель:", row)
        self.tr_device = _combo([("auto", "Автоматически"), ("cuda", "Видеокарта NVIDIA"), ("cpu", "Процессор")],
                                s.tr_device, editable=False)
        f.addRow("Устройство:", self.tr_device)
        f.addRow(_note("Модель NLLB-200 (Meta) скачивается при первом переводе в папку моделей. "
                       "Переводит между ~200 языками без интернета; дословнее, чем онлайн-модели. "
                       "Лицензия модели — CC BY-NC 4.0: только некоммерческое использование."))
        self.tr_stack.addWidget(pg)
        # DeepSeek
        pg = QWidget()
        f = QFormLayout(pg)
        self.ds_key = _secret(s.deepseek_key)
        self.ds_model = _combo([("deepseek-flash", "deepseek-flash — дёшево и быстро"),
                                ("deepseek-v4-pro", "deepseek-v4-pro — точнее, дороже")], s.deepseek_model)
        f.addRow("API-ключ:", self.ds_key)
        f.addRow("Модель:", self.ds_model)
        f.addRow(_note('Ключ: <a href="https://platform.deepseek.com/api_keys">platform.deepseek.com</a>. '
                       "10-часовая книга — примерно $0,2–0,4 на deepseek-flash "
                       "(в часы пик днём по будням вдвое дороже)."))
        self.tr_stack.addWidget(pg)
        # GigaChat
        pg = QWidget()
        f = QFormLayout(pg)
        self.gc_key = _secret(s.gigachat_key)
        self.gc_scope = _combo([("GIGACHAT_API_PERS", "Физическое лицо (GIGACHAT_API_PERS)"),
                                ("GIGACHAT_API_B2B", "Юрлицо, пакеты (GIGACHAT_API_B2B)"),
                                ("GIGACHAT_API_CORP", "Юрлицо, оплата по факту (GIGACHAT_API_CORP)")],
                               s.gigachat_scope, editable=False)
        self.gc_model = _combo([("GigaChat-2", "GigaChat-2 (Lite)"), ("GigaChat-2-Pro", "GigaChat-2-Pro"),
                                ("GigaChat-2-Max", "GigaChat-2-Max")], s.gigachat_model)
        f.addRow("Ключ авторизации:", self.gc_key)
        f.addRow("Тип доступа:", self.gc_scope)
        f.addRow("Модель:", self.gc_model)
        f.addRow(_note('Ключ: <a href="https://developers.sber.ru/studio">developers.sber.ru/studio</a> → проект '
                       "GigaChat API → «Ключ авторизации». Физлицам бесплатно на 12 месяцев: 40 млн токенов Pro, "
                       "25 млн Max, 250 млн Lite — хватит на десятки книг. Нужны сертификаты Минцифры в Windows "
                       '(<a href="https://www.gosuslugi.ru/crt">gosuslugi.ru/crt</a>).'))
        self.tr_stack.addWidget(pg)
        # YandexGPT
        pg = QWidget()
        f = QFormLayout(pg)
        self.ya_key = _secret(s.yandex_key)
        self.ya_folder = QLineEdit(s.yandex_folder)
        self.ya_folder.setPlaceholderText("b1g… — идентификатор каталога")
        self.ya_model = _combo([("yandexgpt/latest", "YandexGPT Pro"), ("yandexgpt-lite/latest", "YandexGPT Lite")],
                               s.yandex_model)
        f.addRow("API-ключ:", self.ya_key)
        f.addRow("Каталог (folder ID):", self.ya_folder)
        f.addRow("Модель:", self.ya_model)
        f.addRow(_note('Ключ и каталог: <a href="https://aistudio.yandex.ru">aistudio.yandex.ru</a> '
                       "(сервисный аккаунт с ролью ai.languageModels.user → API-ключ). Оплата в рублях; "
                       "10-часовая книга — примерно 300 ₽ на Pro, 80 ₽ на Lite."))
        self.tr_stack.addWidget(pg)
        # Claude
        pg = QWidget()
        f = QFormLayout(pg)
        self.cl_key = _secret(s.claude_key)
        self.cl_model = _combo([("claude-haiku-5-5", "Claude Haiku 5.5 — дёшево"),
                                ("claude-sonnet-5-5", "Claude Sonnet 5.5 — лучший литературный перевод"),
                                ("claude-opus-5-5", "Claude Opus 5.5 — максимум, дорого")], s.claude_model)
        f.addRow("API-ключ:", self.cl_key)
        f.addRow("Модель:", self.cl_model)
        f.addRow(_note('Ключ: <a href="https://console.anthropic.com">console.anthropic.com</a>. '
                       "10-часовая книга — примерно $0,15 на Haiku 5.5, $3 на Sonnet 5.5. "
                       "Официально сервис в России не предоставляется."))
        self.tr_stack.addWidget(pg)
        # OpenAI-совместимый
        pg = QWidget()
        f = QFormLayout(pg)
        self.oa_base = QLineEdit(s.openai_base)
        self.oa_key = _secret(s.openai_key)
        self.oa_model = QLineEdit(s.openai_model)
        self.oa_model.setPlaceholderText("например qwen/qwen3.6-plus или имя модели в LM Studio")
        f.addRow("Адрес (base URL):", self.oa_base)
        f.addRow("API-ключ:", self.oa_key)
        f.addRow("Модель:", self.oa_model)
        f.addRow(_note("Любой сервис с протоколом OpenAI: OpenRouter (https://openrouter.ai/api/v1), "
                       "LM Studio на этом компьютере (http://localhost:1234/v1, ключ не нужен), "
                       "Ollama (http://localhost:11434/v1)."))
        self.tr_stack.addWidget(pg)
        v.addWidget(self.tr_stack)
        self.tr_engine.currentIndexChanged.connect(self.tr_stack.setCurrentIndex)
        self.tr_stack.setCurrentIndex(self.tr_engine.currentIndex())

        g = QGroupBox("Имена и термины книги")
        gl = QVBoxLayout(g)
        self.tr_glossary = QPlainTextEdit(s.tr_glossary)
        self.tr_glossary.setPlaceholderText("Как переводить имена героев — по одному в строке:\n"
                                            "Harry Potter = Гарри Поттер\nHogwarts = Хогвартс")
        self.tr_glossary.setMaximumHeight(90)
        gl.addWidget(self.tr_glossary)
        v.addWidget(g)

        row = QHBoxLayout()
        self.tr_check_btn = QPushButton("Проверить перевод")
        self.tr_check_btn.clicked.connect(self._check_translate)
        row.addWidget(self.tr_check_btn)
        self.tr_check_res = QLabel("")
        self.tr_check_res.setWordWrap(True)
        row.addWidget(self.tr_check_res, 1)
        v.addLayout(row)
        v.addWidget(_note("Ключи хранятся зашифрованными (Windows DPAPI) — прочитать их можно только "
                          "под вашей учётной записью на этом компьютере."))
        v.addStretch(1)
        return w

    def _pick_tr_model(self):
        d = QFileDialog.getExistingDirectory(self, "Папка с моделью перевода (model.bin, tokenizer.json)")
        if d:
            self.tr_off_model.setEditText(os.path.normpath(d))

    def _apply_translate(self, s: Settings) -> None:
        s.tr_engine = self.tr_engine.currentData()
        s.tr_offline_model = _combo_value(self.tr_off_model)
        s.tr_device = self.tr_device.currentData()
        s.tr_glossary = self.tr_glossary.toPlainText().strip()
        s.deepseek_key, s.deepseek_model = self.ds_key.text().strip(), _combo_value(self.ds_model)
        s.gigachat_key, s.gigachat_scope = self.gc_key.text().strip(), self.gc_scope.currentData()
        s.gigachat_model = _combo_value(self.gc_model)
        s.yandex_key, s.yandex_folder = self.ya_key.text().strip(), self.ya_folder.text().strip()
        s.yandex_model = _combo_value(self.ya_model)
        s.claude_key, s.claude_model = self.cl_key.text().strip(), _combo_value(self.cl_model)
        s.openai_base, s.openai_key = self.oa_base.text().strip(), self.oa_key.text().strip()
        s.openai_model = self.oa_model.text().strip()

    def _check_translate(self):
        import copy
        import threading
        from ..core.translate import make_translator
        tmp = copy.copy(self.s)
        self._apply_translate(tmp)
        tmp.apply_env()
        if tmp.tr_engine == "offline":
            if QMessageBox.question(self, "Проверка", "Для офлайн-перевода модель будет скачана, если её ещё нет "
                                    "(0,6–1,4 ГБ). Продолжить?") != QMessageBox.Yes:
                return
        self.tr_check_btn.setEnabled(False)
        self.tr_check_res.setStyleSheet("")
        self.tr_check_res.setText("Перевожу пробную фразу…")
        sig = self._check_sig = _CheckSignal()   # без родителя: поток может пережить окно
        sig.done.connect(self._check_done)

        def work():
            try:
                tr = make_translator(tmp, log=lambda m: None)
                tr.title, tr.author = "Проверка", ""
                out = tr.translate(["The old man looked at the sea and smiled: «Tomorrow will be a good day.»"],
                                   "en", "ru")
                sig.done.emit(True, out[0])
            except Exception as e:
                sig.done.emit(False, str(e))
        threading.Thread(target=work, daemon=True).start()

    def _check_done(self, ok: bool, text: str):
        self.tr_check_btn.setEnabled(True)
        self.tr_check_res.setStyleSheet("color: #2e7d32" if ok else "color: #c62828")
        self.tr_check_res.setText(("✓ " if ok else "✗ ") + text)

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
        self._apply_translate(s)
        super().accept()
