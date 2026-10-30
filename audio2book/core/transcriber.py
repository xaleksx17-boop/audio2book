"""Офлайн-распознавание речи через faster-whisper (CTranslate2).

Модели скачиваются при первом запуске в папку моделей (Hugging Face,
можно указать зеркало) или берутся из локальной папки, если её
указать вместо названия модели."""
from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional, Tuple

import numpy as np

MODELS = [
    ("large-v3-turbo", "large-v3-turbo — быстро и точно (рекомендуется, ~1,6 ГБ)"),
    ("large-v3", "large-v3 — максимальная точность, медленно (~3 ГБ)"),
    ("medium", "medium — средняя (~1,5 ГБ)"),
    ("small", "small — быстрая, для слабых ПК (~0,5 ГБ)"),
    ("base", "base — очень быстрая, низкая точность"),
    ("tiny", "tiny — для проверки"),
]

# Подсказка задаёт стиль: литературный текст с пунктуацией и «ё».
DEFAULT_PROMPT = {
    "ru": "Глава первая. Он посмотрел на неё и тихо сказал: «Ну что ж, начнём». "
          "— Хорошо, — ответила она. Текст книги с правильной пунктуацией.",
    "en": "Chapter one. He looked at her and said quietly, \"Well, let's begin.\"",
}

RawSegment = Tuple[float, float, str]


def setup_cuda_dlls() -> None:
    """Если рядом с программой лежит папка cuda (cuBLAS/cuDNN), подключаем её."""
    if sys.platform != "win32":
        return
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cuda = os.path.join(base, "cuda")
    if os.path.isdir(cuda):
        try:
            os.add_dll_directory(cuda)
        except (OSError, AttributeError):
            pass
        os.environ["PATH"] = cuda + os.pathsep + os.environ.get("PATH", "")


def cuda_available() -> bool:
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def best_cuda_compute_type() -> str:
    """Лучший тип вычислений, который реально поддерживает видеокарта.
    RTX 20xx и новее — float16; GTX 10xx (Pascal) float16 не умеют — для них int8."""
    try:
        import ctranslate2
        sup = ctranslate2.get_supported_compute_types("cuda")
    except Exception:
        return "float16"
    for t in ("float16", "int8_float16", "int8", "int8_float32", "float32"):
        if t in sup:
            return t
    return "float32"


def _nvidia_driver_present() -> bool:
    if sys.platform != "win32":
        return False
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.isfile(os.path.join(sysroot, "System32", "nvcuda.dll"))


def gpu_status() -> Tuple[bool, str]:
    """(видеокарта готова, понятное сообщение для строки состояния)."""
    if cuda_available():
        ct = best_cuda_compute_type()
        return True, f"Видеокарта NVIDIA найдена — распознавание на ней ({ct})"
    if _nvidia_driver_present():
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if not os.path.isdir(os.path.join(base, "cuda")):
            return False, ("Видеокарта NVIDIA есть, но нет CUDA-пакета — распознавание на процессоре. "
                           f"Распакуйте Audio2Book-CUDA-pack.zip: папку cuda — в {base}")
        return False, ("CUDA-пакет на месте, но видеокарта не запустилась — обновите драйвер NVIDIA. "
                       "Пока распознавание на процессоре")
    return False, "Видеокарта NVIDIA не найдена — распознавание на процессоре (медленнее)"


class Cancelled(Exception):
    pass


class Transcriber:
    def __init__(self, model: str = "large-v3-turbo", device: str = "auto",
                 compute_type: str = "auto", models_dir: Optional[str] = None,
                 cpu_threads: int = 0, log: Callable[[str], None] = print):
        self.model_name = model
        self.device_pref = device
        self.compute_pref = compute_type
        self.models_dir = models_dir
        self.cpu_threads = cpu_threads
        self.log = log
        self._model = None
        self.device = None
        self.compute_type = None

    # --- загрузка ---------------------------------------------------------
    def _choose(self) -> Tuple[str, str]:
        device = self.device_pref
        if device == "auto":
            device = "cuda" if cuda_available() else "cpu"
        ct = self.compute_pref
        if ct == "auto":
            ct = best_cuda_compute_type() if device == "cuda" else "int8"
        return device, ct

    def load(self, device: Optional[str] = None, compute_type: Optional[str] = None) -> None:
        from faster_whisper import WhisperModel
        d, ct = self._choose()
        if device:
            d, ct = device, compute_type or ("int8" if device == "cpu" else best_cuda_compute_type())
        if self._model is not None and (d, ct) == (self.device, self.compute_type):
            return
        name = self.model_name
        local = os.path.isdir(name)
        self.log(f"Загрузка модели {os.path.basename(name) if local else name} ({d}, {ct})"
                 + ("" if local else " — при первом запуске она скачивается, это может занять время") + "…")
        if self.models_dir:
            os.makedirs(self.models_dir, exist_ok=True)
        try:
            self._model = WhisperModel(name, device=d, compute_type=ct,
                                       download_root=None if local else self.models_dir,
                                       cpu_threads=self.cpu_threads)
        except ValueError as e:
            # «float16 не поддерживается этой видеокартой» и т.п. — берём то, что поддерживается
            if d != "cuda" or "compute type" not in str(e).lower():
                raise
            fallback = best_cuda_compute_type()
            if fallback == ct:
                fallback = "float32"
            self.log(f"Видеокарта не поддерживает {ct}, использую {fallback}")
            ct = fallback
            self._model = WhisperModel(name, device=d, compute_type=ct,
                                       download_root=None if local else self.models_dir,
                                       cpu_threads=self.cpu_threads)
        self.device, self.compute_type = d, ct
        self.log(f"Модель загружена: {d.upper()}, {ct}")

    def unload(self) -> None:
        self._model = None

    # --- распознавание ------------------------------------------------------
    def transcribe(self, audio: np.ndarray, language: Optional[str] = "ru", beam_size: int = 5,
                   initial_prompt: Optional[str] = None, vad: bool = True,
                   on_segment: Optional[Callable[[float, float, str], None]] = None,
                   check: Optional[Callable[[], None]] = None) -> List[RawSegment]:
        """Возвращает сегменты (start, end, text) относительно начала audio.
        check() вызывается между сегментами: может ждать (пауза) или бросить Cancelled."""
        if self._model is None:
            self.load()
        try:
            return self._run(audio, language, beam_size, initial_prompt, vad, on_segment, check)
        except Cancelled:
            raise
        except RuntimeError as e:
            msg = str(e).lower()
            if self.device == "cuda" and any(k in msg for k in ("cuda", "cudnn", "cublas", "out of memory")):
                self.log(f"Видеокарта недоступна ({e}). Переключаюсь на процессор.")
                self._model = None
                self.load(device="cpu", compute_type="int8")
                return self._run(audio, language, beam_size, initial_prompt, vad, on_segment, check)
            raise

    def _run(self, audio, language, beam_size, initial_prompt, vad, on_segment, check) -> List[RawSegment]:
        lang = None if language in (None, "", "auto") else language
        prompt = initial_prompt if initial_prompt is not None else DEFAULT_PROMPT.get(lang or "", None)
        segments, _info = self._model.transcribe(
            audio,
            language=lang,
            beam_size=beam_size,
            initial_prompt=prompt or None,
            vad_filter=vad,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300} if vad else None,
            # Не тащить контекст между окнами: меньше «зацикливаний» на длинных записях
            condition_on_previous_text=False,
            compression_ratio_threshold=2.4,
            no_speech_threshold=0.6,
        )
        out: List[RawSegment] = []
        for seg in segments:          # генератор: распознавание идёт по мере чтения
            if check:
                check()
            text = seg.text.strip()
            if text:
                out.append((float(seg.start), float(seg.end), text))
                if on_segment:
                    on_segment(float(seg.start), float(seg.end), text)
        return out
