"""FictionBook 2.0 (FB2) — привычный формат русскоязычных читалок."""
from __future__ import annotations

import base64
import datetime as _dt
import os
import re
import uuid
from html import escape
from typing import List, Tuple

from ..core.project import Book
from ..core.settings import APP_NAME, APP_VERSION

_PATRONYMIC = re.compile(r"(вич|вна|ична|инична|оглы|кызы)$", re.I)


def split_author(name: str) -> Tuple[str, str, str]:
    """(имя, отчество, фамилия). Понимает «Лев Толстой», «Лев Николаевич Толстой»,
    «Толстой Лев Николаевич»."""
    parts = name.split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return "", "", parts[0]
    if len(parts) == 2:
        return parts[0], "", parts[1]
    if len(parts) == 3:
        if _PATRONYMIC.search(parts[1]):
            return parts[0], parts[1], parts[2]
        if _PATRONYMIC.search(parts[2]):
            return parts[1], parts[2], parts[0]
    return " ".join(parts[:-1]), "", parts[-1]


def _authors_xml(author: str, tag: str = "author") -> List[str]:
    out = []
    names = [a.strip() for a in re.split(r"[;,&]| и ", author) if a.strip()] or [""]
    for n in names:
        first, middle, last = split_author(n)
        x = f"<{tag}>"
        if first:
            x += f"<first-name>{escape(first)}</first-name>"
        if middle:
            x += f"<middle-name>{escape(middle)}</middle-name>"
        x += f"<last-name>{escape(last or 'Неизвестный')}</last-name></{tag}>"
        out.append(x)
    return out


def write_fb2(book: Book, path: str, titles_in_text: bool = True) -> str:
    lang = book.language or "ru"
    today = _dt.date.today()
    cover_id = None
    binary = ""
    if book.cover and os.path.isfile(book.cover):
        ext = os.path.splitext(book.cover)[1].lower()
        cover_id = "cover" + (".png" if ext == ".png" else ".jpg")
        mt = "image/png" if ext == ".png" else "image/jpeg"
        with open(book.cover, "rb") as f:
            data = base64.encodebytes(f.read()).decode("ascii")
        binary = f'<binary id="{cover_id}" content-type="{mt}">\n{data}</binary>\n'

    ti = ["<title-info>", "<genre>prose_contemporary</genre>"]
    ti += _authors_xml(book.author)
    ti.append(f"<book-title>{escape(book.title)}</book-title>")
    if book.annotation:
        ti.append(f"<annotation><p>{escape(book.annotation)}</p></annotation>")
    if book.year:
        ti.append(f"<date>{escape(book.year)}</date>")
    if cover_id:
        ti.append(f'<coverpage><image l:href="#{cover_id}"/></coverpage>')
    ti.append(f"<lang>{lang}</lang>")
    ti.append("</title-info>")

    di = ["<document-info>", f"<author><nickname>{APP_NAME}</nickname></author>",
          f"<program-used>{APP_NAME} {APP_VERSION}</program-used>",
          f'<date value="{today.isoformat()}">{today.strftime("%d.%m.%Y")}</date>',
          f"<id>{uuid.uuid5(uuid.NAMESPACE_URL, f'audio2book:{book.author}:{book.title}')}</id>",
          "<version>1.0</version>",
          "<history><p>Текст распознан из аудиокниги"
          + (f", читает {escape(book.narrator)}" if book.narrator else "") + "</p></history>",
          "</document-info>"]

    body = ["<body>", "<title>"]
    if book.author:
        body.append(f"<p>{escape(book.author)}</p>")
    body.append(f"<p>{escape(book.title)}</p></title>")
    for ch in book.chapters:
        body.append("<section>")
        if titles_in_text:
            body.append(f"<title><p>{escape(ch.title)}</p></title>")
        paras = ch.text_paragraphs()
        body += [f"<p>{escape(p)}</p>" for p in paras] or ["<empty-line/>"]
        body.append("</section>")
    body.append("</body>")

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
           'xmlns:l="http://www.w3.org/1999/xlink">\n<description>\n'
           + "\n".join(ti) + "\n" + "\n".join(di) + "\n</description>\n"
           + "\n".join(body) + "\n" + binary + "</FictionBook>\n")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(tmp, path)
    return path
