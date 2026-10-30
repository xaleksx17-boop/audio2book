"""Очистка распознанного текста: мусор Whisper, повторы, пунктуация, абзацы."""
from __future__ import annotations

import re
from typing import List

from .project import Segment

# Типичные «галлюцинации» Whisper на тишине/музыке (взяты из титров YouTube)
_GARBAGE = [
    r"субтитр\w*", r"dimatorzok", r"dima\s*torzok", r"продолжение следует",
    r"спасибо за (просмотр|внимание)", r"редактор субтитров", r"корректор\s+\w\.\s*\w+",
    r"подпис\w+ на (наш )?канал", r"ставьте лайк\w*", r"amara\.org",
    r"thank(s| you) for watching", r"please subscribe", r"music", r"музыка", r"♪+",
    r"синхронизация\s*:?", r"перевод\w*\s+и\s+субтитр\w*", r"озвуч\w+ .{0,40}студи\w+",
]
_GARBAGE_RE = re.compile(r"^[\s\W]*(" + "|".join(_GARBAGE) + r")[\s\W]*$", re.I)
_GARBAGE_PART_RE = re.compile(
    r"(субтитры (сделал|создавал|подогнал)\w*|редактор субтитров|dima\s*torzok)[^.!?…]*[.!?…]?", re.I)

_TERMINAL = ".!?…»\")"


def _norm(t: str) -> str:
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


def clean_text(t: str) -> str:
    t = t.replace(" ", " ")
    t = _GARBAGE_PART_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s+([,.;:!?…»)])", r"\1", t)       # пробел перед знаком
    t = re.sub(r"([«(])\s+", r"\1", t)              # пробел после открывающей
    t = re.sub(r"\.{3,}", "…", t)
    t = re.sub(r"([,;:])(?=[^\s\d»\"')])", r"\1 ", t)  # «слово,слово» -> «слово, слово»
    t = re.sub(r"\s-\s", " — ", t)                  # дефис как тире
    t = re.sub(r"^-\s", "— ", t)
    t = re.sub(r"([!?])\1{2,}", r"\1", t)
    return t


def is_garbage(t: str) -> bool:
    return not _norm(t) or bool(_GARBAGE_RE.match(t))


def postprocess(segments: List[Segment], pause_para: float = 1.5, max_sentences: int = 7,
                min_para_chars: int = 200, sentence_gap: float = 1.0) -> List[Segment]:
    """Чистка + разметка абзацев.

    Новый абзац начинается, если перед сегментом пауза дольше pause_para
    (чтец переводит дыхание между абзацами) и уже набрано min_para_chars, либо если
    в текущем абзаце накопилось max_sentences предложений и предыдущее закончено."""
    out: List[Segment] = []
    repeat_norm, repeat_count = None, 0
    for seg in segments:
        text = clean_text(seg.text)
        if is_garbage(text):
            continue
        n = _norm(text)
        if n == repeat_norm:                      # «зацикливание» модели
            repeat_count += 1
            if repeat_count >= 2:
                continue
        else:
            repeat_norm, repeat_count = n, 0
        out.append(Segment(seg.start, seg.end, text, False))

    # длинная пауза после фразы без знака в конце — это конец предложения
    for i in range(len(out) - 1):
        cur, nxt = out[i], out[i + 1]
        if nxt.start - cur.end >= sentence_gap and cur.text[-1:] not in _TERMINAL + ",;:—-":
            cur.text = cur.text.rstrip() + "."

    # заглавная буква в начале предложения (и после тире реплики)
    prev_end = True
    for seg in out:
        if prev_end and seg.text:
            m = re.search(r"[^\W\d_]", seg.text)
            if m and seg.text[:m.start()].strip(" —–-«\"(") == "" and m.group().islower():
                seg.text = seg.text[:m.start()] + m.group().upper() + seg.text[m.end():]
        prev_end = seg.text.rstrip()[-1:] in _TERMINAL if seg.text else prev_end

    sentences, chars = 0, 0
    for i, seg in enumerate(out):
        if i == 0:
            seg.para = True
        else:
            prev = out[i - 1]
            gap = seg.start - prev.end
            prev_closed = prev.text.rstrip()[-1:] in _TERMINAL
            starts_dialog = seg.text.startswith(("—", "–", "-"))
            if prev_closed and ((gap >= pause_para and chars >= min_para_chars)
                                or sentences >= max_sentences
                                or (starts_dialog and gap >= 0.4)):
                seg.para = True
        if seg.para:
            sentences, chars = 0, 0
        sentences += len(re.findall(r"[.!?…](\s|$)", seg.text))
        chars += len(seg.text)

    # абзац должен заканчиваться знаком препинания
    for i, seg in enumerate(out):
        last_in_para = i + 1 == len(out) or out[i + 1].para
        if last_in_para and seg.text and seg.text.rstrip()[-1] not in _TERMINAL + ",;:":
            seg.text = seg.text.rstrip() + "."
    return out
