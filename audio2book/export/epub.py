"""EPUB 3 с обратной совместимостью с EPUB 2 (toc.ncx, guide, meta cover) —
так файл открывается и старыми читалками, и принимается Send to Kindle."""
from __future__ import annotations

import datetime as _dt
import os
import uuid
import zipfile
from html import escape

from ..core.project import Book
from ..core.settings import APP_NAME, APP_VERSION

CSS = """body { font-family: serif; line-height: 1.45; margin: 0 4%; }
h1 { font-size: 1.6em; text-align: center; margin: 2em 0 1.2em; page-break-before: always; }
h2 { font-size: 1.3em; text-align: center; margin: 1.5em 0 1em; }
p { text-indent: 1.5em; margin: 0 0 0.3em; text-align: justify; }
.title-page { text-align: center; margin-top: 25%; }
.title-page .author { font-size: 1.2em; text-indent: 0; text-align: center; }
.title-page .title { font-size: 2em; font-weight: bold; text-indent: 0; text-align: center; margin: 1em 0; }
.title-page .note { font-size: 0.8em; text-indent: 0; text-align: center; color: #666; margin-top: 3em; }
.cover { text-align: center; margin: 0; padding: 0; }
.cover img { max-width: 100%; max-height: 100%; }
"""


def _xhtml(title: str, body: str, lang: str) -> str:
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'xml:lang="{lang}" lang="{lang}">\n<head>\n<meta charset="utf-8"/>\n'
            f'<title>{escape(title)}</title>\n<link rel="stylesheet" type="text/css" href="style.css"/>\n'
            f'</head>\n<body>\n{body}\n</body>\n</html>\n')


def book_uid(book: Book) -> str:
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"audio2book:{book.author}:{book.title}"))


def write_epub(book: Book, path: str, titles_in_text: bool = True) -> str:
    lang = book.language or "ru"
    uid = book_uid(book)
    modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    items, spine, nav_points = [], [], []

    cover_name = None
    if book.cover and os.path.isfile(book.cover):
        ext = os.path.splitext(book.cover)[1].lower()
        cover_name = "images/cover" + (".png" if ext == ".png" else ".jpg")
        mt = "image/png" if ext == ".png" else "image/jpeg"
        items.append(f'<item id="cover-image" href="{cover_name}" media-type="{mt}" properties="cover-image"/>')
        items.append('<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover" linear="yes"/>')

    files = {}
    if cover_name:
        files["cover.xhtml"] = _xhtml("Обложка", f'<div class="cover"><img src="{cover_name}" alt="Обложка"/></div>', lang)

    tp = ['<div class="title-page">']
    if book.author:
        tp.append(f'<p class="author">{escape(book.author)}</p>')
    tp.append(f'<p class="title">{escape(book.title)}</p>')
    if book.narrator:
        tp.append(f'<p class="note">Читает: {escape(book.narrator)}</p>')
    tp.append(f'<p class="note">Текст распознан из аудиокниги программой {APP_NAME}</p></div>')
    if book.annotation:
        tp.append(f'<h2>Аннотация</h2><p>{escape(book.annotation)}</p>')
    files["title.xhtml"] = _xhtml(book.title, "\n".join(tp), lang)
    items.append('<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>')
    spine.append('<itemref idref="titlepage"/>')

    for i, ch in enumerate(book.chapters, 1):
        name = f"chap{i:04d}.xhtml"
        body = []
        if titles_in_text:
            body.append(f'<h1 id="c{i}">{escape(ch.title)}</h1>')
        else:
            body.append(f'<div id="c{i}"></div>')
        body += [f"<p>{escape(p)}</p>" for p in ch.text_paragraphs()]
        files[name] = _xhtml(ch.title, "\n".join(body), lang)
        items.append(f'<item id="ch{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="ch{i}"/>')
        nav_points.append((name, ch.title))

    nav_li = "\n".join(f'<li><a href="{n}">{escape(t)}</a></li>' for n, t in nav_points)
    files["nav.xhtml"] = _xhtml("Оглавление",
                                f'<nav epub:type="toc" id="toc"><h1>Оглавление</h1><ol>\n{nav_li}\n</ol></nav>', lang)
    items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
    items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
    items.append('<item id="css" href="style.css" media-type="text/css"/>')

    ncx_points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{escape(t)}</text></navLabel>'
        f'<content src="{n}"/></navPoint>' for i, (n, t) in enumerate(nav_points, 1))
    ncx = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
           f'<head><meta name="dtb:uid" content="{uid}"/><meta name="dtb:depth" content="1"/>'
           '<meta name="dtb:totalPageCount" content="0"/><meta name="dtb:maxPageNumber" content="0"/></head>\n'
           f'<docTitle><text>{escape(book.title)}</text></docTitle>\n<navMap>\n{ncx_points}\n</navMap>\n</ncx>\n')

    meta = [f'<dc:identifier id="bookid">{uid}</dc:identifier>',
            f'<dc:title>{escape(book.title)}</dc:title>',
            f'<dc:language>{lang}</dc:language>',
            f'<meta property="dcterms:modified">{modified}</meta>',
            f'<dc:contributor>{APP_NAME} {APP_VERSION}</dc:contributor>']
    if book.author:
        meta.append(f'<dc:creator>{escape(book.author)}</dc:creator>')
    if book.annotation:
        meta.append(f'<dc:description>{escape(book.annotation)}</dc:description>')
    if book.year:
        meta.append(f'<dc:date>{escape(book.year)}</dc:date>')
    if cover_name:
        meta.append('<meta name="cover" content="cover-image"/>')
    guide = ('<guide><reference type="cover" title="Обложка" href="cover.xhtml"/>'
             '<reference type="text" title="Текст" href="title.xhtml"/></guide>') if cover_name else \
        '<guide><reference type="text" title="Текст" href="title.xhtml"/></guide>'
    opf = ('<?xml version="1.0" encoding="utf-8"?>\n'
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n' + "\n".join(meta) + "\n</metadata>\n"
           "<manifest>\n" + "\n".join(items) + "\n</manifest>\n"
           '<spine toc="ncx">\n' + "\n".join(spine) + "\n</spine>\n" + guide + "\n</package>\n")

    container = ('<?xml version="1.0" encoding="utf-8"?>\n'
                 '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>\n')

    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for name, content in files.items():
            z.writestr("OEBPS/" + name, content, compress_type=zipfile.ZIP_DEFLATED)
        if cover_name:
            z.write(book.cover, "OEBPS/" + cover_name, compress_type=zipfile.ZIP_STORED)
    os.replace(tmp, path)
    return path
