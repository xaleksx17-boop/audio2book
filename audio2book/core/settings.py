"""Настройки программы и очередь заданий (JSON в %APPDATA%\\Audio2Book)."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import List

from .secrets import protect, unprotect

APP_NAME = "Audio2Book"
APP_TITLE = "Аудиокнига → Электронная книга"
APP_VERSION = "1.0.0"

FORMATS = [
    ("epub", "EPUB"),
    ("fb2", "FB2"),
    ("azw3", "Kindle (AZW3)"),
    ("docx", "DOCX"),
    ("txt", "TXT"),
    ("srt", "SRT (субтитры)"),
    ("lrc", "LRC (текст для плеера)"),
]


def config_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def default_models_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or config_dir()
        return os.path.join(base, APP_NAME, "models")
    return os.path.join(config_dir(), "models")


def default_output_dir() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "Аудиокниги в текст")


SECRET_FIELDS = ("deepseek_key", "gigachat_key", "yandex_key", "claude_key", "openai_key")


@dataclass
class Settings:
    model: str = "large-v3-turbo"
    device: str = "auto"            # auto | cuda | cpu
    compute_type: str = "auto"      # auto | float16 | int8_float16 | int8 | float32
    cpu_threads: int = 0            # 0 = все ядра
    language: str = "ru"            # ru | en | ... | auto
    beam_size: int = 5
    vad: bool = True
    initial_prompt: str = ""        # пусто = подсказка по умолчанию для языка
    models_dir: str = field(default_factory=default_models_dir)
    hf_endpoint: str = ""           # зеркало Hugging Face, напр. https://hf-mirror.com
    output_dir: str = field(default_factory=default_output_dir)
    formats: List[str] = field(default_factory=lambda: ["epub", "fb2"])
    pause_para: float = 1.5
    max_sentences: int = 7
    chapter_titles_in_text: bool = True
    calibre_path: str = ""          # ebook-convert.exe, пусто = искать автоматически
    keep_awake: bool = True         # не давать Windows уснуть во время работы
    # --- перевод ---
    translate_to: str = ""          # "" = не переводить; иначе код языка (ru, en, ...)
    tr_engine: str = "offline"      # offline | deepseek | gigachat | yandexgpt | claude | openai
    tr_offline_model: str = "OpenNMT/nllb-200-distilled-1.3B-ct2-int8"
    tr_device: str = "auto"
    tr_glossary: str = ""           # «Ivan = Иван», по строке на пару
    deepseek_key: str = ""
    deepseek_model: str = "deepseek-flash"
    gigachat_key: str = ""          # «Ключ авторизации» из личного кабинета
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat-2-Pro"
    yandex_key: str = ""
    yandex_folder: str = ""
    yandex_model: str = "yandexgpt/latest"
    claude_key: str = ""
    claude_model: str = "claude-haiku-5-5"
    openai_base: str = "https://openrouter.ai/api/v1"
    openai_key: str = ""
    openai_model: str = ""

    @staticmethod
    def path() -> str:
        return os.path.join(config_dir(), "settings.json")

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            with open(cls.path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            for k, v in data.items():
                if hasattr(s, k):
                    setattr(s, k, unprotect(v) if k in SECRET_FIELDS else v)
        except (OSError, ValueError):
            pass
        return s

    def save(self) -> None:
        d = asdict(self)
        for k in SECRET_FIELDS:   # ключи API — зашифрованы для текущего пользователя Windows
            d[k] = protect(d[k])
        with open(self.path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

    def apply_env(self) -> None:
        if self.hf_endpoint.strip():
            os.environ["HF_ENDPOINT"] = self.hf_endpoint.strip().rstrip("/")
        else:
            os.environ.pop("HF_ENDPOINT", None)
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        # если huggingface_hub уже загружен — поправить и его константы
        hub = sys.modules.get("huggingface_hub.constants")
        if hub is not None:
            ep = (self.hf_endpoint.strip().rstrip("/") or "https://huggingface.co")
            hub.ENDPOINT = ep
            hub.HUGGINGFACE_CO_URL_TEMPLATE = ep + "/{repo_id}/resolve/{revision}/{filename}"
