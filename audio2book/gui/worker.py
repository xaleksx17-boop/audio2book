"""Фоновый поток распознавания. Интерфейс не подвисает, пауза/стоп — мгновенно
(между фрагментами), модель остаётся в памяти между книгами."""
from __future__ import annotations

import sys
import threading
import time
import traceback

from PySide6.QtCore import QObject, Signal, Slot

from ..core import pipeline
from ..core.audio import SourcesMissing
from ..core.pipeline import Callbacks, Job
from ..core.settings import Settings
from ..core.transcriber import Cancelled, Transcriber


def _keep_awake(on: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


class Worker(QObject):
    log = Signal(str)
    progress = Signal(str, float, str)      # job_id, 0..1, «×3.2, осталось ~1:20:00»
    partial = Signal(str)                   # свежераспознанный фрагмент
    job_info = Signal(str, str, str, int, float, str)  # id, title, author, глав, длительность, project_path
    finished = Signal(str, str, str)        # job_id, статус, сообщение
    sources_missing = Signal(str, str)      # job_id, пример пропавшего файла

    def __init__(self):
        super().__init__()
        self.pause_ev = threading.Event()
        self.stop_ev = threading.Event()
        self._tr: Transcriber | None = None
        self._tr_key = None

    # вызываются из GUI-потока — только флаги
    def pause(self, on: bool) -> None:
        if on:
            self.pause_ev.set()
        else:
            self.pause_ev.clear()

    def stop(self) -> None:
        self.stop_ev.set()
        self.pause_ev.clear()

    def _transcriber(self, s: Settings) -> Transcriber:
        key = (s.model, s.device, s.compute_type, s.models_dir, s.cpu_threads)
        if self._tr is None or key != self._tr_key:
            self._tr = Transcriber(s.model, s.device, s.compute_type, s.models_dir, s.cpu_threads,
                                   log=self.log.emit)
            self._tr_key = key
        return self._tr

    @Slot(object, object)
    def run(self, job: Job, settings: Settings) -> None:
        self.stop_ev.clear()
        self.pause_ev.clear()
        worker = self

        class CB(Callbacks):
            last_emit = 0.0

            def log(self, msg):
                worker.log.emit(msg)

            def progress(self, frac, info):
                now = time.monotonic()
                if now - CB.last_emit > 0.3 or frac >= 1.0:
                    CB.last_emit = now
                    worker.progress.emit(job.id, frac, info)

            def partial(self, text):
                worker.partial.emit(text)

            def check(self):
                while worker.pause_ev.is_set() and not worker.stop_ev.is_set():
                    time.sleep(0.15)
                if worker.stop_ev.is_set():
                    raise Cancelled()

        _keep_awake(settings.keep_awake)
        settings.apply_env()
        try:
            cb = CB()
            book = pipeline.prepare_project(job, settings, cb)
            self.job_info.emit(job.id, book.title, book.author, len(book.chapters), book.duration, job.project_path)
            if book.chapters and not book.done:
                cb.check()
                pipeline.transcribe_book(job, book, settings, self._transcriber(settings), cb)
            from ..export import export_book
            import os
            export_book(book, os.path.dirname(job.project_path), job.formats or settings.formats, settings, cb.log)
            self.progress.emit(job.id, 1.0, "")
            self.finished.emit(job.id, pipeline.STATUS_DONE, "")
        except Cancelled:
            self.log.emit("Остановлено. Сделанное сохранено — «Старт» продолжит с этого места.")
            self.finished.emit(job.id, pipeline.STATUS_STOP, "")
        except SourcesMissing as e:
            self.log.emit("ОШИБКА: " + str(e))
            self.finished.emit(job.id, pipeline.STATUS_ERR, "Файлы книги не найдены")
            self.sources_missing.emit(job.id, e.missing[0])
        except Exception as e:
            self.log.emit("ОШИБКА: " + str(e))
            self.log.emit(traceback.format_exc(limit=4))
            self.finished.emit(job.id, pipeline.STATUS_ERR, str(e))
        finally:
            _keep_awake(False)
