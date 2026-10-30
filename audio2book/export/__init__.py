"""Экспорт книги во все выбранные форматы."""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from typing import Callable, List, Optional

from ..core.project import Book
from ..core.settings import Settings
from .epub import write_epub
from .fb2 import write_fb2
from .simple import write_docx, write_lrc, write_srt, write_txt

_NOWINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_calibre(custom: str = "") -> Optional[str]:
    if custom and os.path.isfile(custom):
        return custom
    exe = "ebook-convert.exe" if sys.platform == "win32" else "ebook-convert"
    found = shutil.which(exe)
    if found:
        return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            for cand in glob.glob(os.path.join(base, "Calibre*", exe)):
                return cand
        for cand in glob.glob(os.path.join(os.path.expanduser("~"), "*", "Calibre*", exe)):
            return cand
    return None


def write_azw3(epub_path: str, path: str, calibre: Optional[str]) -> str:
    if not calibre:
        raise RuntimeError("для AZW3 нужен Calibre (бесплатно: calibre-ebook.com). "
                           "EPUB можно отправить на Kindle и так — через Send to Kindle.")
    r = subprocess.run([calibre, epub_path, path, "--no-inline-toc"],
                       capture_output=True, creationflags=_NOWINDOW, timeout=1800)
    if r.returncode != 0 or not os.path.isfile(path):
        raise RuntimeError("Calibre: " + r.stderr.decode("utf-8", "replace").strip()[-400:])
    return path


def export_book(book: Book, folder: str, formats: List[str], settings: Settings,
                log: Callable[[str], None] = print) -> List[str]:
    from ..core.pipeline import book_basename
    os.makedirs(folder, exist_ok=True)
    base = book_basename(book)
    titles = settings.chapter_titles_in_text
    out: List[str] = []
    epub_path = os.path.join(folder, base + ".epub")
    epub_tmp = None

    def done(paths):
        paths = paths if isinstance(paths, list) else [paths]
        out.extend(paths)
        for p in paths[:3]:
            log(f"  ✓ {os.path.basename(p)}")
        if len(paths) > 3:
            log(f"  ✓ … и ещё {len(paths) - 3}")

    log(f"Экспорт в {folder}")
    for fmt in formats:
        try:
            if fmt == "epub":
                done(write_epub(book, epub_path, titles))
            elif fmt == "fb2":
                done(write_fb2(book, os.path.join(folder, base + ".fb2"), titles))
            elif fmt == "txt":
                done(write_txt(book, os.path.join(folder, base + ".txt"), titles))
            elif fmt == "docx":
                done(write_docx(book, os.path.join(folder, base + ".docx"), titles))
            elif fmt == "srt":
                done(write_srt(book, folder, base))
            elif fmt == "lrc":
                done(write_lrc(book, folder, base))
            elif fmt == "azw3":
                src = epub_path
                if "epub" not in formats or not os.path.isfile(epub_path):
                    epub_tmp = os.path.join(folder, base + ".tmp.epub")
                    src = write_epub(book, epub_tmp, titles)
                done(write_azw3(src, os.path.join(folder, base + ".azw3"), find_calibre(settings.calibre_path)))
        except Exception as e:  # один формат не должен ронять остальные
            log(f"  ✗ {fmt.upper()}: {e}")
    if epub_tmp and os.path.exists(epub_tmp):
        os.remove(epub_tmp)
    return out
