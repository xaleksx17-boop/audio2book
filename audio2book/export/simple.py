"""TXT, DOCX, SRT, LRC."""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Dict, List

from ..core.project import Book, Segment


def write_txt(book: Book, path: str, titles_in_text: bool = True) -> str:
    lines: List[str] = []
    if book.author:
        lines.append(book.author)
    lines += [book.title, ""]
    if book.annotation:
        lines += [book.annotation, ""]
    for ch in book.chapters:
        if titles_in_text:
            lines += ["", ch.title, ""]
        for p in ch.text_paragraphs():
            lines += ["    " + p]
    # utf-8-sig и CRLF — чтобы и старый Блокнот, и читалки открыли без «кракозябр»
    with open(path, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")
    return path


def write_docx(book: Book, path: str, titles_in_text: bool = True) -> str:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.shared import Cm, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_height, sec.page_width = Cm(29.7), Cm(21.0)
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(2.0)
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    pf = normal.paragraph_format
    pf.first_line_indent = Cm(1.0)
    pf.space_after = Pt(0)
    pf.line_spacing = 1.15
    for st in ("Title", "Heading 1"):
        doc.styles[st].font.name = "Times New Roman"

    if book.cover and os.path.isfile(book.cover):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
        try:
            p.add_run().add_picture(book.cover, width=Cm(12))
        except Exception:
            pass
    if book.author:
        p = doc.add_paragraph(book.author)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
    t = doc.add_paragraph(book.title, style="Title")
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if book.narrator:
        p = doc.add_paragraph(f"Читает: {book.narrator}")
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent = Cm(0)
    if book.annotation:
        doc.add_paragraph(book.annotation)

    for ch in book.chapters:
        if titles_in_text:
            h = doc.add_heading(ch.title, level=1)
            h.paragraph_format.page_break_before = True
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        else:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        for para in ch.text_paragraphs():
            p = doc.add_paragraph(para)
            p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    doc.core_properties.title = book.title
    doc.core_properties.author = book.author
    doc.save(path)
    return path


# --- субтитры: по одному файлу на каждый исходный аудиофайл -----------------
def _segments_by_source(book: Book) -> "OrderedDict[str, List[Segment]]":
    out: "OrderedDict[str, List[Segment]]" = OrderedDict()
    for ch in book.chapters:
        out.setdefault(ch.source, []).extend(ch.segments)
    return out


def _srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _lrc_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{m:02d}:{s:02d}.{cs:02d}"


def _sub_paths(book: Book, folder: str, base: str, ext: str) -> Dict[str, str]:
    groups = _segments_by_source(book)
    if len(groups) == 1:
        return {next(iter(groups)): os.path.join(folder, base + ext)}
    sub = os.path.join(folder, f"{base} — {ext.strip('.').upper()}")
    os.makedirs(sub, exist_ok=True)
    return {src: os.path.join(sub, os.path.splitext(os.path.basename(src))[0] + ext) for src in groups}


def write_srt(book: Book, folder: str, base: str) -> List[str]:
    paths = _sub_paths(book, folder, base, ".srt")
    written = []
    for src, segs in _segments_by_source(book).items():
        lines = []
        for i, s in enumerate(segs, 1):
            lines += [str(i), f"{_srt_time(s.start)} --> {_srt_time(max(s.end, s.start + 0.5))}", s.text, ""]
        with open(paths[src], "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write("\n".join(lines))
        written.append(paths[src])
    return written


def write_lrc(book: Book, folder: str, base: str) -> List[str]:
    """LRC кладётся рядом с аудио под тем же именем — тогда плееры
    (AIMP, foobar2000, MusicBee, многие Android-плееры) показывают текст при прослушивании."""
    paths = _sub_paths(book, folder, base, ".lrc")
    written = []
    for src, segs in _segments_by_source(book).items():
        chapter_title = next((c.title for c in book.chapters if c.source == src), "")
        lines = [f"[ti:{chapter_title or book.title}]", f"[al:{book.title}]"]
        if book.author:
            lines.append(f"[ar:{book.author}]")
        lines.append("[by:Audio2Book]")
        lines += [f"[{_lrc_time(s.start)}]{s.text}" for s in segs]
        with open(paths[src], "w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write("\n".join(lines) + "\n")
        written.append(paths[src])
    return written
