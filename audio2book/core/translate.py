"""Перевод распознанного текста на другой язык.

Офлайн: NLLB-200 (Meta) в формате CTranslate2 — тот же движок, что и у распознавания,
работает на видеокарте или процессоре, ~200 языков.
Онлайн: языковые модели по API — DeepSeek, GigaChat, YandexGPT, Claude и любой
OpenAI-совместимый сервис (OpenRouter, LM Studio, Ollama…). Лучше передают стиль.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable, Dict, List, Optional, Tuple

# код ISO — название — код NLLB
LANGS: List[Tuple[str, str, str]] = [
    ("ru", "Русский", "rus_Cyrl"), ("en", "Английский", "eng_Latn"), ("uk", "Украинский", "ukr_Cyrl"),
    ("be", "Белорусский", "bel_Cyrl"), ("kk", "Казахский", "kaz_Cyrl"), ("de", "Немецкий", "deu_Latn"),
    ("fr", "Французский", "fra_Latn"), ("es", "Испанский", "spa_Latn"), ("it", "Итальянский", "ita_Latn"),
    ("pl", "Польский", "pol_Latn"), ("pt", "Португальский", "por_Latn"), ("tr", "Турецкий", "tur_Latn"),
    ("cs", "Чешский", "ces_Latn"), ("zh", "Китайский", "zho_Hans"), ("ja", "Японский", "jpn_Jpan"),
    ("ko", "Корейский", "kor_Hang"),
]
LANG_NAME = {c: n for c, n, _ in LANGS}
NLLB_CODE = {c: k for c, _, k in LANGS}
# название языка в родительном падеже для подсказки модели
_LANG_GEN = {"ru": "русского", "en": "английского", "uk": "украинского", "be": "белорусского",
             "kk": "казахского", "de": "немецкого", "fr": "французского", "es": "испанского",
             "it": "итальянского", "pl": "польского", "pt": "португальского", "tr": "турецкого",
             "cs": "чешского", "zh": "китайского", "ja": "японского", "ko": "корейского"}

_LANG_ACC = {"ru": "русский", "en": "английский", "uk": "украинский", "be": "белорусский",
             "kk": "казахский", "de": "немецкий", "fr": "французский", "es": "испанский",
             "it": "итальянский", "pl": "польский", "pt": "португальский", "tr": "турецкий",
             "cs": "чешский", "zh": "китайский", "ja": "японский", "ko": "корейский"}

ENGINES = [
    ("offline", "Офлайн — NLLB-200 (бесплатно, без интернета)"),
    ("deepseek", "DeepSeek (API)"),
    ("gigachat", "GigaChat — Сбер (API)"),
    ("yandexgpt", "YandexGPT — Яндекс (API)"),
    ("claude", "Claude — Anthropic (API)"),
    ("openai", "Другой OpenAI-совместимый (OpenRouter, LM Studio, Ollama…)"),
]
ENGINE_NAME = dict(ENGINES)

OFFLINE_MODELS = [
    ("OpenNMT/nllb-200-distilled-1.3B-ct2-int8", "NLLB-200 1.3B — точнее (~1,4 ГБ)"),
    ("olob0/nllb-200-distilled-600M-ct2-int8_float16", "NLLB-200 600M — быстрее (~0,65 ГБ)"),
]


class TranslateError(RuntimeError):
    pass


Check = Optional[Callable[[], None]]
Log = Callable[[str], None]


def split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?…])\s+(?=[\"«“(—–-]?\s*[A-ZА-ЯЁ0-9])", text.strip())
    return [p for p in parts if p.strip()]


def parse_glossary(text: str) -> List[Tuple[str, str]]:
    """«Ivan = Иван» по одной паре в строке."""
    out = []
    for line in (text or "").splitlines():
        if "=" in line:
            a, b = line.split("=", 1)
            if a.strip() and b.strip():
                out.append((a.strip(), b.strip()))
    return out


# ============================================================ офлайн: NLLB-200
class OfflineTranslator:
    name = "offline"

    def __init__(self, model: str, models_dir: str, device: str = "auto", log: Log = print):
        self.model_ref, self.models_dir, self.device_pref, self.log = model, models_dir, device, log
        self._tr = None
        self._tok = None

    def _model_dir(self) -> str:
        if os.path.isdir(self.model_ref):
            return self.model_ref
        from huggingface_hub import snapshot_download
        self.log(f"Загрузка модели перевода {self.model_ref} — при первом запуске она скачивается…")
        return snapshot_download(self.model_ref, cache_dir=self.models_dir)

    def load(self) -> None:
        if self._tr is not None:
            return
        import ctranslate2
        from tokenizers import Tokenizer
        from .transcriber import best_cuda_compute_type, cuda_available
        path = self._model_dir()
        tok = os.path.join(path, "tokenizer.json")
        if not os.path.isfile(tok):
            raise TranslateError(f"В папке модели перевода нет tokenizer.json: {path}")
        device = self.device_pref if self.device_pref != "auto" else ("cuda" if cuda_available() else "cpu")
        ct = best_cuda_compute_type() if device == "cuda" else "int8"
        try:
            self._tr = ctranslate2.Translator(path, device=device, compute_type=ct)
        except (ValueError, RuntimeError) as e:
            if device != "cuda":
                raise
            self.log(f"Перевод на видеокарте не запустился ({e}) — перевожу на процессоре")
            device, ct = "cpu", "int8"
            self._tr = ctranslate2.Translator(path, device=device, compute_type=ct)
        self._tok = Tokenizer.from_file(tok)
        self.log(f"Модель перевода загружена: {device.upper()}, {ct}")

    def _encode(self, text: str, src: str) -> List[str]:
        toks = self._tok.encode(text, add_special_tokens=False).tokens
        return [src] + toks[:400] + ["</s>"]

    def _decode(self, tokens: List[str]) -> str:
        ids = [self._tok.token_to_id(t) for t in tokens]
        return self._tok.decode([i for i in ids if i is not None], skip_special_tokens=True).strip()

    def translate(self, paras: List[str], src: str, tgt: str, check: Check = None,
                  glossary: Optional[List[Tuple[str, str]]] = None, **_) -> List[str]:
        self.load()
        s, t = NLLB_CODE.get(src), NLLB_CODE.get(tgt)
        if not s or not t:
            raise TranslateError(f"Офлайн-модель не знает язык: {src if not s else tgt}")
        sents, owner = [], []
        for i, p in enumerate(paras):
            for snt in split_sentences(p) or [p]:
                sents.append(snt)
                owner.append(i)
        out_sents: List[str] = []
        step = 32
        for k in range(0, len(sents), step):
            if check:
                check()
            batch = [self._encode(x, s) for x in sents[k:k + step]]
            res = self._tr.translate_batch(batch, target_prefix=[[t]] * len(batch), beam_size=4,
                                           max_batch_size=16, max_decoding_length=512,
                                           repetition_penalty=1.1, no_repeat_ngram_size=6)
            for r in res:
                hyp = r.hypotheses[0]
                if hyp and hyp[0] == t:
                    hyp = hyp[1:]
                out_sents.append(self._decode(hyp))
        joined = [""] * len(paras)
        for i, txt in zip(owner, out_sents):
            joined[i] = (joined[i] + " " + txt).strip()
        if glossary:
            joined = [_apply_glossary(x, glossary) for x in joined]
        return joined


def _apply_glossary(text: str, glossary: List[Tuple[str, str]]) -> str:
    for a, b in glossary:
        text = re.sub(r"(?<!\w)" + re.escape(a) + r"(?!\w)", b, text)
    return text


# ============================================================ онлайн: языковые модели
_SSL = ssl.create_default_context()   # на Windows включает системное хранилище сертификатов


def _http(url: str, data: bytes, headers: Dict[str, str], timeout: int = 240) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:500]
        raise _HttpErr(e.code, body) from None
    except ssl.SSLCertVerificationError as e:
        raise TranslateError(
            "Не удалось проверить сертификат сервера. Для GigaChat установите в Windows "
            "сертификаты Минцифры (Russian Trusted Root CA) с сайта gosuslugi.ru/crt и перезапустите программу. "
            f"({e.reason})") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise _NetErr(str(getattr(e, "reason", e))) from None


class _HttpErr(Exception):
    def __init__(self, code: int, body: str):
        super().__init__(f"HTTP {code}: {body}")
        self.code, self.body = code, body


class _NetErr(Exception):
    pass


_MARK = re.compile(r"\[\[(\d+)\]\]")


def build_prompt(src: str, tgt: str, title: str, author: str,
                 glossary: List[Tuple[str, str]]) -> str:
    g = ""
    if glossary:
        g = "\nПереводи имена и термины строго так:\n" + "\n".join(f"- {a} → {b}" for a, b in glossary)
    book = f"«{title}»" + (f", автор {author}" if author else "")
    return (f"Ты профессиональный литературный переводчик. Переведи фрагмент книги {book} "
            f"с {_LANG_GEN.get(src, src)} языка на {_LANG_ACC.get(tgt, tgt)} язык.\n"
            "Текст получен распознаванием аудиокниги: если видишь явную ошибку распознавания, переводи по смыслу.\n"
            "Сохраняй стиль автора, оформление диалогов, абзацы и единое написание имён героев.\n"
            "Каждый абзац начинается с метки вида [[N]] — сохрани метки и порядок, по одному переводу на метку. "
            "Не объединяй и не пропускай абзацы. Не добавляй пояснений, примечаний и заголовков — только перевод."
            + g)


def parse_marked(text: str, n: int) -> Optional[List[str]]:
    pieces = _MARK.split(text)
    found: Dict[int, str] = {}
    for i in range(1, len(pieces) - 1, 2):
        try:
            k = int(pieces[i])
        except ValueError:
            continue
        found[k] = pieces[i + 1].strip()
    if all(k in found and found[k] for k in range(1, n + 1)):
        return [found[k] for k in range(1, n + 1)]
    return None


class LLMTranslator:
    """Общая часть для всех сервисов на языковых моделях: пачки абзацев с метками [[N]],
    повтор при сбоях сети и лимитах, деление пачки пополам, если модель сбилась с меток."""
    name = "llm"
    chunk_chars = 3500

    def __init__(self, log: Log = print):
        self.log = log
        self.title, self.author = "", ""

    def chat(self, system: str, user: str) -> str:   # переопределяется
        raise NotImplementedError

    def _chat_retry(self, system: str, user: str, check: Check) -> str:
        delay = 5
        for attempt in range(6):
            if check:
                check()
            try:
                return self.chat(system, user)
            except _HttpErr as e:
                if e.code in (401, 403):
                    raise TranslateError(f"{self.label}: доступ запрещён — проверьте API-ключ ({e.body[:200]})")
                if e.code == 402:
                    raise TranslateError(f"{self.label}: недостаточно средств на балансе")
                if e.code == 404:
                    raise TranslateError(f"{self.label}: модель или адрес не найдены — проверьте настройки ({e.body[:200]})")
                if e.code == 400:
                    raise TranslateError(f"{self.label}: запрос отклонён ({e.body[:300]})")
                self.log(f"{self.label}: {e.code}, повтор через {delay} с…")
            except _NetErr as e:
                self.log(f"{self.label}: нет связи ({e}), повтор через {delay} с…")
            for _ in range(delay * 4):
                if check:
                    check()
                time.sleep(0.25)
            delay = min(delay * 2, 120)
        raise TranslateError(f"{self.label}: сервис не отвечает, попробуйте позже")

    def _translate_group(self, group: List[str], system: str, check: Check) -> List[str]:
        user = "\n\n".join(f"[[{i}]] {p}" for i, p in enumerate(group, 1))
        raw = self._chat_retry(system, user, check)
        out = parse_marked(raw, len(group))
        if out is not None:
            return out
        if len(group) == 1:
            return [_MARK.sub("", raw).strip()]
        mid = len(group) // 2
        return (self._translate_group(group[:mid], system, check)
                + self._translate_group(group[mid:], system, check))

    def translate(self, paras: List[str], src: str, tgt: str, check: Check = None,
                  glossary: Optional[List[Tuple[str, str]]] = None, **_) -> List[str]:
        system = build_prompt(src, tgt, self.title, self.author, glossary or [])
        out: List[str] = []
        group: List[str] = []
        size = 0
        for p in paras:
            if group and size + len(p) > self.chunk_chars:
                out += self._translate_group(group, system, check)
                group, size = [], 0
            group.append(p)
            size += len(p)
        if group:
            out += self._translate_group(group, system, check)
        return out


class OpenAICompatTranslator(LLMTranslator):
    def __init__(self, label: str, base_url: str, key: str, model: str,
                 extra_headers: Optional[Dict[str, str]] = None, log: Log = print):
        super().__init__(log)
        if not model:
            raise TranslateError(f"{label}: не указана модель в настройках перевода")
        self.label, self.url, self.key, self.model = label, base_url.rstrip("/") + "/chat/completions", key, model
        self.extra = extra_headers or {}

    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json", **self.extra}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        return h

    def chat(self, system: str, user: str) -> str:
        body = {"model": self.model, "temperature": 0.3, "max_tokens": 4096,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        d = _http(self.url, json.dumps(body).encode("utf-8"), self.headers())
        try:
            return d["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise TranslateError(f"{self.label}: неожиданный ответ {str(d)[:300]}")


class GigaChatTranslator(OpenAICompatTranslator):
    OAUTH = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    API = "https://gigachat.devices.sberbank.ru/api/v1"

    def __init__(self, auth_key: str, scope: str, model: str, log: Log = print):
        if not auth_key:
            raise TranslateError("GigaChat: не указан ключ авторизации в настройках перевода")
        super().__init__("GigaChat", self.API, "", model, log=log)
        self.auth_key, self.scope = auth_key.strip(), scope or "GIGACHAT_API_PERS"
        self._token, self._exp = "", 0.0

    def headers(self) -> Dict[str, str]:
        if not self._token or time.time() > self._exp - 60:
            d = _http(self.OAUTH, f"scope={self.scope}".encode(), {
                "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                "RqUID": str(uuid.uuid4()), "Authorization": "Basic " + self.auth_key})
            self._token = d.get("access_token", "")
            exp = d.get("expires_at", 0)
            self._exp = exp / 1000 if exp > 1e11 else (exp or time.time() + 1500)
        h = super().headers()
        h["Authorization"] = "Bearer " + self._token
        return h


class ClaudeTranslator(LLMTranslator):
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, key: str, model: str, log: Log = print):
        super().__init__(log)
        if not key:
            raise TranslateError("Claude: не указан API-ключ в настройках перевода")
        self.label, self.key, self.model = "Claude", key, model or "claude-haiku-5-5"

    def chat(self, system: str, user: str) -> str:
        body = {"model": self.model, "max_tokens": 4096, "temperature": 0.3, "system": system,
                "messages": [{"role": "user", "content": user}]}
        d = _http(self.URL, json.dumps(body).encode("utf-8"), {
            "content-type": "application/json", "x-api-key": self.key, "anthropic-version": "2023-06-01"})
        try:
            return "".join(b.get("text", "") for b in d["content"] if b.get("type") == "text")
        except (KeyError, TypeError):
            raise TranslateError(f"Claude: неожиданный ответ {str(d)[:300]}")


def make_translator(s, log: Log = print):
    """Переводчик по настройкам (объект Settings)."""
    e = s.tr_engine
    if e == "offline":
        return OfflineTranslator(s.tr_offline_model, s.models_dir, s.tr_device, log)
    if e == "deepseek":
        if not s.deepseek_key:
            raise TranslateError("DeepSeek: не указан API-ключ в настройках перевода")
        return OpenAICompatTranslator("DeepSeek", "https://api.deepseek.com", s.deepseek_key,
                                      s.deepseek_model or "deepseek-flash", log=log)
    if e == "gigachat":
        return GigaChatTranslator(s.gigachat_key, s.gigachat_scope, s.gigachat_model or "GigaChat-2-Pro", log)
    if e == "yandexgpt":
        if not s.yandex_key or not s.yandex_folder:
            raise TranslateError("YandexGPT: укажите API-ключ и идентификатор каталога в настройках перевода")
        model = s.yandex_model or "yandexgpt/latest"
        if not model.startswith("gpt://"):
            model = f"gpt://{s.yandex_folder}/{model}"
        return OpenAICompatTranslator("YandexGPT", "https://llm.api.cloud.yandex.net/v1", s.yandex_key, model,
                                      {"OpenAI-Project": s.yandex_folder}, log=log)
    if e == "claude":
        return ClaudeTranslator(s.claude_key, s.claude_model, log)
    if e == "openai":
        if not s.openai_base:
            raise TranslateError("Не указан адрес сервиса (base URL) в настройках перевода")
        return OpenAICompatTranslator("API", s.openai_base, s.openai_key, s.openai_model, log=log)
    raise TranslateError(f"Неизвестный способ перевода: {e}")
