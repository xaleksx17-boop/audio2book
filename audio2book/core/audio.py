"""Работа с аудио через ffmpeg/ffprobe: поиск файлов, теги, главы, обложка,
декодирование в 16 кГц моно для Whisper кусками с разрезом по паузам."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import numpy as np

from .project import Book, Chapter

AUDIO_EXT = {".mp3", ".m4a", ".m4b", ".aac", ".ogg", ".oga", ".opus", ".flac",
             ".wav", ".wma", ".mka", ".mp4", ".webm", ".ape", ".wv"}
COVER_NAMES = ("cover", "folder", "front", "обложка", "albumart")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
SAMPLE_RATE = 16000

# На Windows не показывать чёрное окно консоли при запуске ffmpeg
_NOWINDOW = 0x08000000 if sys.platform == "win32" else 0


class AudioError(RuntimeError):
    pass


def _app_dirs() -> List[str]:
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
        if hasattr(sys, "_MEIPASS"):
            dirs.append(sys._MEIPASS)  # type: ignore[attr-defined]
    dirs.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return dirs


def find_tool(name: str) -> str:
    """ffmpeg/ffprobe: сначала из папки программы (ffmpeg\\ffmpeg.exe), потом из PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _app_dirs():
        for cand in (os.path.join(d, "ffmpeg", exe), os.path.join(d, exe)):
            if os.path.isfile(cand):
                return cand
    found = shutil.which(name)
    if found:
        return found
    raise AudioError(f"Не найден {exe}. Положите его в папку ffmpeg рядом с программой или добавьте в PATH.")


def _run(args: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, creationflags=_NOWINDOW, **kw)


# --- сортировка «как человек»: 2.mp3 раньше 10.mp3 -----------------------------
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def collect_audio_files(paths: List[str]) -> List[str]:
    files: List[str] = []
    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, names in os.walk(p):
                for n in names:
                    if os.path.splitext(n)[1].lower() in AUDIO_EXT:
                        files.append(os.path.join(root, n))
        elif os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXT:
            files.append(p)
    # сортируем по относительному пути: подпапки «CD1», «CD2» тоже по порядку
    files = sorted(set(os.path.abspath(f) for f in files), key=lambda f: natural_key(f))
    return files


# --- ffprobe -----------------------------------------------------------------
@dataclass
class ProbeInfo:
    path: str
    duration: float
    tags: Dict[str, str] = field(default_factory=dict)
    chapters: List[Tuple[float, float, str]] = field(default_factory=list)
    has_cover: bool = False


def probe(path: str) -> ProbeInfo:
    r = _run([find_tool("ffprobe"), "-v", "error", "-print_format", "json",
              "-show_format", "-show_chapters", "-show_streams", path])
    if r.returncode != 0:
        raise AudioError(f"ffprobe не смог прочитать {os.path.basename(path)}: "
                         f"{r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    data = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
    fmt = data.get("format", {})
    tags = {k.lower(): str(v).strip() for k, v in (fmt.get("tags") or {}).items()}
    duration = float(fmt.get("duration") or 0.0)
    has_audio = False
    has_cover = False
    for st in data.get("streams", []):
        if st.get("codec_type") == "audio":
            has_audio = True
            if not duration and st.get("duration"):
                duration = float(st["duration"])
            for k, v in (st.get("tags") or {}).items():   # ogg/opus хранят теги в потоке
                tags.setdefault(k.lower(), str(v).strip())
        elif st.get("codec_type") == "video" and (st.get("disposition") or {}).get("attached_pic"):
            has_cover = True
    if not has_audio:
        raise AudioError(f"В файле нет звука: {os.path.basename(path)}")
    chapters = []
    for ch in data.get("chapters", []):
        start, end = float(ch.get("start_time", 0)), float(ch.get("end_time", 0))
        title = str((ch.get("tags") or {}).get("title", "")).strip()
        if end - start > 0.5:
            chapters.append((start, end, title))
    return ProbeInfo(path, duration, tags, chapters, has_cover)


# --- построение книги ----------------------------------------------------------
_JUNK_TITLE = re.compile(r"^(track|трек|chapter|глава|part|часть)?\s*\d+$", re.I)


def _title_from_filename(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"^\s*\d{1,4}\s*[-.)_ ]\s*", "", stem)  # «01 - Глава» -> «Глава»
    return stem.strip() or os.path.splitext(os.path.basename(path))[0]


def _good_title(t: str) -> bool:
    return bool(t) and not _JUNK_TITLE.match(t.strip())


def build_book(files: List[str]) -> Tuple[Book, List[ProbeInfo]]:
    """Книга из списка файлов: каждый файл — глава; если в файле (m4b/mka/mp3)
    есть оглавление — каждая его глава становится главой книги."""
    if not files:
        raise AudioError("Нет аудиофайлов")
    infos = [probe(f) for f in files]
    t0 = infos[0].tags
    book = Book()
    book.title = (t0.get("album") or "").strip() or \
        (t0.get("title") if len(files) == 1 and t0.get("title") else "") or \
        os.path.basename(os.path.dirname(files[0])) or "Без названия"
    book.author = t0.get("album_artist") or t0.get("artist") or t0.get("author") or t0.get("composer") or ""
    book.narrator = t0.get("composer", "") if t0.get("composer") != book.author else ""
    book.year = (t0.get("date") or t0.get("year") or "")[:4]
    book.annotation = t0.get("comment") or t0.get("description") or t0.get("synopsis") or ""

    n = 0
    for info in infos:
        if len(info.chapters) > 1:
            for i, (s, e, title) in enumerate(info.chapters):
                n += 1
                book.chapters.append(Chapter(
                    title=title if _good_title(title) else f"Глава {n}",
                    source=info.path, start=s, end=min(e, info.duration) if info.duration else e))
        else:
            n += 1
            tag_title = info.tags.get("title", "")
            title = tag_title if _good_title(tag_title) and tag_title != book.title else _title_from_filename(info.path)
            if not _good_title(title):
                title = f"Глава {n}"
            book.chapters.append(Chapter(title=title, source=info.path, start=0.0, end=info.duration))
    return book, infos


def extract_cover(files: List[str], infos: List[ProbeInfo], dest_noext: str) -> Optional[str]:
    """Обложка: картинка в папке с книгой, иначе встроенная в аудиофайл."""
    folders = []
    for f in files:
        d = os.path.dirname(f)
        if d not in folders:
            folders.append(d)
    for d in folders + [os.path.dirname(d) for d in folders]:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        imgs = [n for n in names if os.path.splitext(n)[1].lower() in IMAGE_EXT]
        imgs.sort(key=lambda n: (not os.path.splitext(n)[0].lower().startswith(COVER_NAMES), natural_key(n)))
        if imgs:
            src = os.path.join(d, imgs[0])
            ext = os.path.splitext(src)[1].lower()
            if ext in (".jpg", ".jpeg", ".png"):
                dst = dest_noext + (".png" if ext == ".png" else ".jpg")
                shutil.copyfile(src, dst)
                return dst
            dst = dest_noext + ".jpg"   # webp/bmp -> jpg, читалки их не любят
            r = _run([find_tool("ffmpeg"), "-y", "-v", "error", "-i", src, dst])
            if r.returncode == 0 and os.path.isfile(dst):
                return dst
    for info in infos:
        if info.has_cover:
            dst = dest_noext + ".jpg"
            r = _run([find_tool("ffmpeg"), "-y", "-v", "error", "-i", info.path,
                      "-an", "-map", "0:v:0", "-frames:v", "1", dst])
            if r.returncode == 0 and os.path.isfile(dst) and os.path.getsize(dst) > 0:
                return dst
    return None


# --- декодирование -------------------------------------------------------------
def decode(path: str, start: float = 0.0, end: Optional[float] = None) -> np.ndarray:
    """Кусок файла как float32 моно 16 кГц."""
    args = [find_tool("ffmpeg"), "-nostdin", "-v", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", path]
    if end is not None:
        args += ["-t", f"{max(0.0, end - start):.3f}"]
    args += ["-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    r = _run(args)
    if r.returncode != 0:
        raise AudioError(f"ffmpeg: {r.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return np.frombuffer(r.stdout, np.int16).astype(np.float32) / 32768.0


def _quietest_point(audio: np.ndarray, lo: int, hi: int) -> int:
    """Индекс самой тихой 0.2-секундной точки в [lo, hi) — там и режем,
    чтобы не разрубить слово пополам."""
    lo, hi = max(0, lo), min(len(audio), hi)
    win = int(0.2 * SAMPLE_RATE)
    if hi - lo <= win * 2:
        return hi
    seg = audio[lo:hi]
    n = (len(seg) // win) * win
    energy = np.sqrt(np.mean(seg[:n].reshape(-1, win) ** 2, axis=1))
    i = int(np.argmin(energy))
    return lo + i * win + win // 2


def iter_chunks(path: str, start: float, end: float, chunk_sec: float = 900.0,
                look_sec: float = 20.0) -> Iterator[Tuple[float, np.ndarray]]:
    """Декодирует [start, end) кусками ~chunk_sec, разрезая в тишине.
    Возвращает (абсолютное время начала куска, звук)."""
    pos = start
    while pos < end - 0.05:
        want_end = min(end, pos + chunk_sec + look_sec)
        audio = decode(path, pos, want_end)
        if len(audio) == 0:
            break
        if want_end >= end - 0.05:
            yield pos, audio
            break
        target = int(chunk_sec * SAMPLE_RATE)
        look = int(look_sec * SAMPLE_RATE)
        cut = _quietest_point(audio, target - look, target + look)
        cut = max(cut, int(SAMPLE_RATE))  # защита от зацикливания
        yield pos, audio[:cut]
        pos += cut / SAMPLE_RATE


def fmt_duration(sec: float) -> str:
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
