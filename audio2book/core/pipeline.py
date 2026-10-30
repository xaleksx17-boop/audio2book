"""Конвейер одного задания: файлы -> проект -> распознавание -> текст -> экспорт."""
from __future__ import annotations

import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

from . import audio
from .project import PROJECT_EXT, Book, Segment
from .settings import Settings
from .textproc import is_garbage, postprocess
from .transcriber import Cancelled, Transcriber

STATUS_WAIT = "В очереди"
STATUS_RUN = "Распознаётся"
STATUS_PAUSE = "Пауза"
STATUS_DONE = "Готово"
STATUS_STOP = "Остановлено"
STATUS_ERR = "Ошибка"


@dataclass
class Job:
    inputs: List[str]
    title: str = ""
    author: str = ""
    formats: List[str] = field(default_factory=list)
    output_dir: str = ""
    project_path: str = ""
    status: str = STATUS_WAIT
    progress: float = 0.0
    duration: float = 0.0
    chapters: int = 0
    message: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        j = Job(inputs=list(d.get("inputs", [])))
        for k, v in d.items():
            if hasattr(j, k):
                setattr(j, k, v)
        if j.status == STATUS_RUN:   # программа была закрыта во время работы
            j.status = STATUS_STOP
        return j


def safe_filename(name: str, limit: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return (name[:limit].rstrip(" .")) or "Книга"


def book_basename(book: Book) -> str:
    return safe_filename(f"{book.author} - {book.title}" if book.author else book.title)


class Callbacks:
    """Связь с интерфейсом. Методы переопределяются в GUI-потоке."""

    def log(self, msg: str) -> None:
        print(msg)

    def progress(self, frac: float, info: str) -> None:
        pass

    def check(self) -> None:
        """Ждать при паузе, бросить Cancelled при остановке."""

    def partial(self, text: str) -> None:
        """Только что распознанный фрагмент (для живого просмотра)."""


def prepare_project(job: Job, settings: Settings, cb: Callbacks) -> Book:
    if job.project_path and os.path.isfile(job.project_path):
        book = Book.load(job.project_path)
        cb.log(f"Открыт проект «{book.title}» — продолжаю с места остановки")
        return book
    files = audio.collect_audio_files(job.inputs)
    if not files and job.inputs and not any(os.path.exists(p) for p in job.inputs):
        raise audio.SourcesMissing(list(job.inputs))
    if not files:
        raise audio.AudioError("В выбранных файлах/папках нет аудио")
    cb.log(f"Найдено файлов: {len(files)}. Читаю теги и главы…")
    book, infos = audio.build_book(files)
    if job.title.strip():
        book.title = job.title.strip()
    if job.author.strip():
        book.author = job.author.strip()
    book.language = settings.language if settings.language != "auto" else "ru"
    folder = os.path.join(job.output_dir or settings.output_dir, book_basename(book))
    os.makedirs(folder, exist_ok=True)
    try:
        book.cover = audio.extract_cover(files, infos, os.path.join(folder, "cover"))
    except audio.AudioError as e:
        cb.log(f"Обложка не извлечена: {e}")
    job.project_path = os.path.join(folder, book_basename(book) + PROJECT_EXT)
    book.save(job.project_path)
    cb.log(f"Книга: «{book.title}»{' — ' + book.author if book.author else ''}, "
           f"глав: {len(book.chapters)}, длительность {audio.fmt_duration(book.duration)}"
           f"{', обложка найдена' if book.cover else ''}")
    return book


def transcribe_book(job: Job, book: Book, settings: Settings, tr: Transcriber, cb: Callbacks) -> None:
    missing = audio.missing_sources(book.chapters)
    if missing:
        raise audio.SourcesMissing(missing)
    total = max(book.duration, 1.0)
    t_start = time.monotonic()
    done_at_start = book.processed_seconds()
    book.model = os.path.basename(settings.model)

    for idx, ch in enumerate(book.chapters, 1):
        if ch.done:
            continue
        end = ch.end if ch.end else audio.probe(ch.source).duration
        ch.end = end
        pos = ch.resume_at if ch.resume_at is not None else ch.start
        cb.log(f"[{idx}/{len(book.chapters)}] {ch.title}"
               + (f" (продолжение с {audio.fmt_duration(pos - ch.start)})" if pos > ch.start else ""))

        for offset, samples in audio.iter_chunks(ch.source, pos, end):
            chunk_len = len(samples) / audio.SAMPLE_RATE

            def on_segment(s, e, text):
                # processed_seconds() уже учитывает всё до начала текущего куска
                processed = book.processed_seconds() + e
                frac = min(processed / total, 0.999)
                elapsed = time.monotonic() - t_start
                done_now = processed - done_at_start
                info = ""
                if elapsed > 5 and done_now > 1:
                    speed = done_now / elapsed
                    left = (total - processed) / speed
                    info = f"×{speed:.1f}, осталось ~{audio.fmt_duration(left)}"
                cb.progress(frac, info)
                if not is_garbage(text):
                    cb.partial(text)

            raw = tr.transcribe(samples, language=settings.language, beam_size=settings.beam_size,
                                initial_prompt=settings.initial_prompt or None, vad=settings.vad,
                                on_segment=on_segment, check=cb.check)
            ch.segments.extend(Segment(offset + s, offset + e, t) for s, e, t in raw)
            ch.resume_at = offset + chunk_len
            book.save(job.project_path)

        ch.segments = postprocess(ch.segments, settings.pause_para, settings.max_sentences)
        ch.done, ch.resume_at = True, None
        book.save(job.project_path)
        cb.progress(min(book.processed_seconds() / total, 0.999), "")


def run_job(job: Job, settings: Settings, tr: Transcriber, cb: Callbacks) -> Book:
    from ..export import export_book
    book = prepare_project(job, settings, cb)
    job.duration, job.chapters = book.duration, len(book.chapters)
    if not job.title:
        job.title = book.title
    if not job.author:
        job.author = book.author
    transcribe_book(job, book, settings, tr, cb)
    folder = os.path.dirname(job.project_path)
    export_book(book, folder, job.formats or settings.formats, settings, cb.log)
    cb.progress(1.0, "")
    return book


__all__ = ["Job", "Callbacks", "Cancelled", "run_job", "prepare_project", "transcribe_book",
           "book_basename", "safe_filename"]
