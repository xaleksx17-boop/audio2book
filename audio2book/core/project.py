"""Модель проекта: книга -> главы -> сегменты распознанного текста.

Проект хранится в JSON рядом с результатами (<имя>.a2b.json), поэтому
конвертацию можно прервать и продолжить позже, а текст — поправить
в редакторе и переэкспортировать без повторного распознавания.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import List, Optional

PROJECT_EXT = ".a2b.json"
PROJECT_VERSION = 1


@dataclass
class Segment:
    start: float          # секунды от начала исходного файла
    end: float
    text: str
    para: bool = False    # с этого сегмента начинается новый абзац

    def to_dict(self):
        return {"s": round(self.start, 2), "e": round(self.end, 2), "t": self.text, "p": self.para}

    @staticmethod
    def from_dict(d):
        return Segment(d["s"], d["e"], d["t"], bool(d.get("p", False)))


@dataclass
class Chapter:
    title: str
    source: str                   # путь к аудиофайлу
    start: float = 0.0            # начало главы в файле, сек
    end: Optional[float] = None   # конец главы в файле (None = до конца файла)
    segments: List[Segment] = field(default_factory=list)
    done: bool = False
    resume_at: Optional[float] = None  # где продолжать распознавание после паузы/сбоя
    tr_title: str = ""                   # перевод названия главы
    tr_paras: List[str] = field(default_factory=list)  # перевод по абзацам (как text_paragraphs)
    tr_src: List[str] = field(default_factory=list)    # какие абзацы оригинала переведены

    @property
    def duration(self) -> float:
        return max(0.0, (self.end or 0.0) - self.start)

    def text_paragraphs(self) -> List[str]:
        paras: List[List[str]] = []
        for seg in self.segments:
            t = seg.text.strip()
            if not t:
                continue
            if seg.para or not paras:
                paras.append([t])
            else:
                paras[-1].append(t)
        return [" ".join(p) for p in paras]

    def to_dict(self):
        return {
            "title": self.title, "source": self.source, "start": self.start, "end": self.end,
            "done": self.done, "resume_at": self.resume_at,
            "segments": [s.to_dict() for s in self.segments],
            "tr_title": self.tr_title, "tr_paras": self.tr_paras, "tr_src": self.tr_src,
        }

    @staticmethod
    def from_dict(d):
        return Chapter(
            title=d["title"], source=d["source"], start=d.get("start", 0.0), end=d.get("end"),
            segments=[Segment.from_dict(s) for s in d.get("segments", [])],
            done=bool(d.get("done", False)), resume_at=d.get("resume_at"),
            tr_title=d.get("tr_title", ""), tr_paras=list(d.get("tr_paras", [])),
            tr_src=list(d.get("tr_src", [])),
        )

    def translation_current(self) -> bool:
        """Перевод есть и сделан с текущего текста главы (после правок оригинала — устарел)."""
        return bool(self.tr_paras) and self.tr_src == self.text_paragraphs()


@dataclass
class Book:
    title: str = "Без названия"
    author: str = ""
    language: str = "ru"
    cover: Optional[str] = None       # путь к картинке обложки (в папке проекта)
    annotation: str = ""
    year: str = ""
    narrator: str = ""
    chapters: List[Chapter] = field(default_factory=list)
    model: str = ""                   # какой моделью распознано (для справки)
    tr_lang: str = ""                 # язык перевода ("" — перевода нет)
    tr_title: str = ""
    tr_engine: str = ""

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.chapters)

    @property
    def done(self) -> bool:
        return bool(self.chapters) and all(c.done for c in self.chapters)

    def processed_seconds(self) -> float:
        total = 0.0
        for c in self.chapters:
            if c.done:
                total += c.duration
            elif c.resume_at is not None:
                total += max(0.0, c.resume_at - c.start)
        return total

    @property
    def translated(self) -> bool:
        return bool(self.tr_lang) and bool(self.chapters) and all(
            c.translation_current() or not c.text_paragraphs() for c in self.chapters)

    def translated_copy(self) -> "Book":
        """Книга-перевод для экспорта: те же главы, но текст и названия переведены."""
        b = Book(title=self.tr_title or self.title, author=self.author, language=self.tr_lang,
                 cover=self.cover, annotation="", year=self.year, narrator=self.narrator, model=self.model)
        for c in self.chapters:
            nc = Chapter(title=c.tr_title or c.title, source=c.source, start=c.start, end=c.end, done=True)
            nc.segments = [Segment(c.start, c.start, p, True) for p in c.tr_paras]
            b.chapters.append(nc)
        return b

    def to_dict(self):
        d = asdict(self)
        d["chapters"] = [c.to_dict() for c in self.chapters]
        d["version"] = PROJECT_VERSION
        return d

    @staticmethod
    def from_dict(d):
        b = Book(
            title=d.get("title", "Без названия"), author=d.get("author", ""),
            language=d.get("language", "ru"), cover=d.get("cover"),
            annotation=d.get("annotation", ""), year=d.get("year", ""),
            narrator=d.get("narrator", ""), model=d.get("model", ""),
            tr_lang=d.get("tr_lang", ""), tr_title=d.get("tr_title", ""), tr_engine=d.get("tr_engine", ""),
        )
        b.chapters = [Chapter.from_dict(c) for c in d.get("chapters", [])]
        return b

    # --- файл проекта -------------------------------------------------------
    def save(self, path: str) -> None:
        """Атомарная запись: сначала во временный файл, потом замена,
        чтобы сбой питания посреди записи не убил многочасовую работу."""
        folder = os.path.dirname(os.path.abspath(path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".a2b-", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def load(path: str) -> "Book":
        with open(path, "r", encoding="utf-8") as f:
            return Book.from_dict(json.load(f))
