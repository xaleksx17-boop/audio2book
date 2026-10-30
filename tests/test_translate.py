"""Перевод: разбор ответов моделей, повторы при сбое меток, запросы к сервисам,
продолжение после остановки и экспорт книги-перевода. Без сети и без настоящих моделей."""
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio2book.core import translate as T  # noqa: E402
from audio2book.core.pipeline import Callbacks, Job, run_job  # noqa: E402
from audio2book.core.project import Book  # noqa: E402
from audio2book.core.settings import Settings  # noqa: E402
from audio2book.core.transcriber import Cancelled  # noqa: E402
from test_core import FakeTranscriber, make_fixtures  # noqa: E402


class FakeLLM(T.LLMTranslator):
    """«Переводит» в верхний регистр. Каждый 3-й ответ теряет метку — проверка деления пачки."""
    label = "Fake"

    def __init__(self):
        super().__init__(log=lambda m: None)
        self.calls = 0

    def chat(self, system, user):
        self.calls += 1
        assert "[[1]]" in user and "литературный переводчик" in system
        items = T._MARK.split(user)
        pairs = [(items[i], items[i + 1].strip()) for i in range(1, len(items) - 1, 2)]
        if self.calls % 3 == 0 and len(pairs) > 1:
            pairs = pairs[:-1]           # модель «потеряла» последний абзац
        return "\n\n".join(f"[[{n}]] {txt.upper()}" for n, txt in pairs)


def test_parse_marked():
    assert T.parse_marked("[[1]] Раз\n\n[[2]] Два", 2) == ["Раз", "Два"]
    assert T.parse_marked("[[1]] Раз", 2) is None
    assert T.parse_marked("Вот перевод:\n[[1]] A\n[[2]] B\n", 2) == ["A", "B"]


def test_llm_split_on_broken_marks():
    f = FakeLLM()
    f.chunk_chars = 50
    paras = [f"абзац номер {i} с текстом." for i in range(12)]
    out = f.translate(paras, "ru", "en")
    assert out == [p.upper() for p in paras]


def test_glossary_and_prompt():
    g = T.parse_glossary("Ivan = Иван\nплохая строка\nLondon=Лондон")
    assert g == [("Ivan", "Иван"), ("London", "Лондон")]
    p = T.build_prompt("en", "ru", "Book", "Author", g)
    assert "с английского языка на русский язык." in p
    assert "Ivan → Иван" in p
    assert T._apply_glossary("Ivan met Ivanov", g) == "Иван met Ivanov"


def test_request_formats(monkeypatch=None):
    calls = []

    def fake_http(url, data, headers, timeout=240):
        calls.append((url, json.loads(data) if data.startswith(b"{") else data.decode(), headers))
        if "oauth" in url:
            return {"access_token": "TOKEN", "expires_at": 9999999999999}
        if "anthropic" in url:
            return {"content": [{"type": "text", "text": "[[1]] Hello"}]}
        return {"choices": [{"message": {"content": "[[1]] Hello"}}]}

    old = T._http
    T._http = fake_http
    try:
        s = Settings(tr_engine="deepseek", deepseek_key="sk-1")
        assert T.make_translator(s).translate(["Привет"], "ru", "en") == ["Hello"]
        url, body, h = calls[-1]
        assert url == "https://api.deepseek.com/chat/completions" and body["model"] == "deepseek-flash"
        assert h["Authorization"] == "Bearer sk-1"

        s = Settings(tr_engine="gigachat", gigachat_key="QUJD")
        T.make_translator(s).translate(["Привет"], "ru", "en")
        (o_url, o_body, o_h), (c_url, c_body, c_h) = calls[-2], calls[-1]
        assert o_url.endswith("/api/v2/oauth") and o_body == "scope=GIGACHAT_API_PERS"
        assert o_h["Authorization"] == "Basic QUJD" and len(o_h["RqUID"]) == 36
        assert c_url == "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
        assert c_h["Authorization"] == "Bearer TOKEN" and c_body["model"] == "GigaChat-2-Pro"

        s = Settings(tr_engine="yandexgpt", yandex_key="AQV", yandex_folder="b1g")
        T.make_translator(s).translate(["Привет"], "ru", "en")
        url, body, h = calls[-1]
        assert url == "https://llm.api.cloud.yandex.net/v1/chat/completions"
        assert body["model"] == "gpt://b1g/yandexgpt/latest" and h["OpenAI-Project"] == "b1g"

        s = Settings(tr_engine="claude", claude_key="sk-ant")
        T.make_translator(s).translate(["Привет"], "ru", "en")
        url, body, h = calls[-1]
        assert url == "https://api.anthropic.com/v1/messages" and body["model"] == "claude-haiku-5-5"
        assert h["x-api-key"] == "sk-ant" and h["anthropic-version"] == "2023-06-01" and body["system"]

        s = Settings(tr_engine="openai", openai_base="http://localhost:1234/v1/", openai_model="local")
        T.make_translator(s).translate(["Привет"], "ru", "en")
        assert calls[-1][0] == "http://localhost:1234/v1/chat/completions"
        assert "Authorization" not in calls[-1][2]

        for bad in (Settings(tr_engine="deepseek"), Settings(tr_engine="yandexgpt", yandex_key="x"),
                    Settings(tr_engine="claude"), Settings(tr_engine="gigachat")):
            try:
                T.make_translator(bad)
                raise AssertionError(bad.tr_engine)
            except T.TranslateError:
                pass
    finally:
        T._http = old


def test_http_errors_are_friendly():
    f = FakeLLM()

    def boom(system, user):
        raise T._HttpErr(401, '{"error":"invalid key"}')
    f.chat = boom
    try:
        f.translate(["x"], "ru", "en")
        raise AssertionError
    except T.TranslateError as e:
        assert "API-ключ" in str(e)


def test_offline_tokenization():
    """OfflineTranslator с ненастоящей моделью: проверяем сборку токенов и разбор ответа."""
    from tokenizers import Tokenizer, models, pre_tokenizers, decoders
    vocab = {"<unk>": 0, "</s>": 1, "rus_Cyrl": 2, "eng_Latn": 3, "▁Привет": 4, "▁мир.": 5,
             "▁Как": 6, "▁дела?": 7, "▁Hello": 8, "▁world.": 9}
    tok = Tokenizer(models.WordLevel(vocab, unk_token="<unk>"))
    tok.pre_tokenizer = pre_tokenizers.Metaspace()
    tok.decoder = decoders.Metaspace()
    tok.add_special_tokens(["</s>", "rus_Cyrl", "eng_Latn"])

    class R:
        def __init__(self, h):
            self.hypotheses = [h]

    seen = []

    class FakeCT2:
        def translate_batch(self, batch, target_prefix, **kw):
            seen.extend(batch)
            assert all(tp == ["eng_Latn"] for tp in target_prefix)
            return [R(["eng_Latn", "▁Hello", "▁world."]) for _ in batch]

    o = T.OfflineTranslator("x", "y", log=lambda m: None)
    o._tr, o._tok = FakeCT2(), tok
    out = o.translate(["Привет мир. Как дела?", "Привет мир."], "ru", "en")
    assert seen[0] == ["rus_Cyrl", "▁Привет", "▁мир.", "</s>"] and len(seen) == 3
    assert out == ["Hello world. Hello world.", "Hello world."], out


def test_pipeline_translation_resume_and_export():
    tmp = tempfile.mkdtemp(prefix="a2b-tr-")
    m4b, _ = make_fixtures(tmp)
    s = Settings(output_dir=os.path.join(tmp, "out"), formats=["epub", "fb2", "txt", "srt"])
    fake = FakeLLM()
    n = {"c": 0}

    class StopInTranslation(Callbacks):
        def check(self):
            if fake.calls >= 2:
                n["c"] += 1
                if n["c"] == 1:
                    raise Cancelled()

        def log(self, m):
            pass

    job = Job(inputs=[m4b], translate_to="en")
    try:
        run_job(job, s, FakeTranscriber(), StopInTranslation(), make_translator=lambda: fake)
        raise AssertionError("должно было остановиться")
    except Cancelled:
        pass
    mid = Book.load(job.project_path)
    assert mid.done and mid.tr_lang == "en" and not mid.translated
    calls_before = fake.calls

    book = run_job(job, s, FakeTranscriber(), Callbacks(), make_translator=lambda: fake)
    assert book.translated and fake.calls > calls_before
    for c in book.chapters:
        assert len(c.tr_paras) == len(c.text_paragraphs()) and c.tr_title == c.title.upper()
    folder = os.path.dirname(job.project_path)
    files = sorted(os.listdir(folder))
    tr_base = "Народная сказка - КУРОЧКА РЯБА [en]"
    assert tr_base + ".epub" in files and tr_base + ".fb2" in files and tr_base + ".txt" in files, files
    assert tr_base + ".srt" not in files
    with zipfile.ZipFile(os.path.join(folder, tr_base + ".epub")) as z:
        opf = z.read("OEBPS/content.opf").decode()
        assert "<dc:language>en</dc:language>" in opf and "КУРОЧКА РЯБА" in opf

    # правка оригинала делает перевод главы устаревшим — переводится только она
    b = Book.load(job.project_path)
    b.chapters[1].segments[0].text = "Совсем другой текст."
    b.save(job.project_path)
    before = fake.calls
    run_job(job, s, FakeTranscriber(), Callbacks(), make_translator=lambda: fake)
    assert fake.calls - before <= 3   # одна глава (+ деление пачки из-за «сбоя меток»)
    assert Book.load(job.project_path).translated


def test_secrets_roundtrip():
    from audio2book.core import secrets
    assert secrets.unprotect(secrets.protect("abc")) == "abc"
    assert secrets.unprotect("dpapi:@@@") == ""


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
