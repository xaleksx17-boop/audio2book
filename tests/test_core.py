"""Проверка конвейера без настоящей модели: синтетическое аудио (тон + паузы),
m4b с оглавлением и обложкой, папка mp3, поддельный распознаватель.

Запуск:  python -m pytest tests -q   (или просто python tests/test_core.py)
"""
import os
import subprocess
import sys
import tempfile
import zipfile
import xml.dom.minidom

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio2book.core import audio  # noqa: E402
from audio2book.core.pipeline import Callbacks, Job, run_job  # noqa: E402
from audio2book.core.project import Book, Segment  # noqa: E402
from audio2book.core.settings import Settings  # noqa: E402
from audio2book.core.textproc import postprocess  # noqa: E402

PHRASES = ["жили-были дед да баба", "и была у них курочка ряба", "снесла курочка яичко",
           "Субтитры сделал DimaTorzok", "не простое а золотое", "дед бил бил не разбил"]


class FakeTranscriber:
    """Каждые ~3 секунды звука — фраза. Повторяет поведение Whisper: сегменты
    относительно начала куска, вызывает on_segment и check."""
    calls = 0

    def transcribe(self, samples, language=None, beam_size=5, initial_prompt=None, vad=True,
                   on_segment=None, check=None):
        dur = len(samples) / audio.SAMPLE_RATE
        out, t, i = [], 0.2, FakeTranscriber.calls
        while t + 2.5 <= dur:
            if check:
                check()
            text = PHRASES[i % len(PHRASES)]
            out.append((t, t + 2.5, text))
            if on_segment:
                on_segment(t, t + 2.5, text)
            t += 3.0 + (2.0 if i % 4 == 3 else 0)  # иногда длинная пауза -> абзац
            i += 1
        FakeTranscriber.calls = i
        return out


def ff(*args):
    subprocess.run([audio.find_tool("ffmpeg"), "-y", "-v", "error", *args], check=True)


def make_fixtures(tmp):
    cover = os.path.join(tmp, "src_cover.jpg")
    ff("-f", "lavfi", "-i", "color=c=steelblue:s=300x450:d=1", "-frames:v", "1", cover)
    # m4b: 3 главы по 40 c, тон с паузами
    wav = os.path.join(tmp, "tone.wav")
    ff("-f", "lavfi", "-i", "sine=f=440:d=120", "-af", "volume='if(lt(mod(t,3),2.5),1,0)':eval=frame", wav)
    meta = os.path.join(tmp, "meta.txt")
    with open(meta, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\ntitle=Курочка Ряба\nalbum=Курочка Ряба\nartist=Народная сказка\n")
        for i in range(3):
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={i*40000}\nEND={(i+1)*40000}\ntitle=Часть {['первая','вторая','третья'][i]}\n")
    m4b = os.path.join(tmp, "book.m4b")
    ff("-i", wav, "-i", meta, "-i", cover, "-map", "0:a", "-map", "2:v", "-map_metadata", "1",
       "-map_chapters", "1", "-c:a", "aac", "-b:a", "48k", "-c:v", "copy", "-disposition:v", "attached_pic", m4b)
    # папка mp3: 02 раньше 10
    d = os.path.join(tmp, "Пушкин - Сказки")
    os.makedirs(d)
    for n in ("10", "02", "01"):
        ff("-i", wav, "-t", "20", "-metadata", "album=Сказки", "-metadata", "artist=Александр Сергеевич Пушкин",
           "-metadata", f"title=Track {n}", os.path.join(d, f"{n} - Сказка {int(n)}.mp3"))
    ff("-f", "lavfi", "-i", "color=c=red:s=200x300:d=1", "-frames:v", "1", os.path.join(d, "folder.jpg"))
    return m4b, d


def check_outputs(folder, base):
    epub = os.path.join(folder, base + ".epub")
    with zipfile.ZipFile(epub) as z:
        assert z.namelist()[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        for n in z.namelist():
            if n.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                xml.dom.minidom.parseString(z.read(n))   # валидный XML
        assert any(n.startswith("OEBPS/images/cover") for n in z.namelist())
    xml.dom.minidom.parse(os.path.join(folder, base + ".fb2"))
    txt = open(os.path.join(folder, base + ".txt"), encoding="utf-8-sig").read()
    assert "DimaTorzok" not in txt and "Жили-были дед да баба" in txt, txt[:300]
    assert os.path.getsize(os.path.join(folder, base + ".docx")) > 5000


def test_textproc():
    segs = [Segment(0, 2, "привет , мир"), Segment(2.1, 3, "Продолжение следует..."),
            Segment(5, 6, "это тест"), Segment(6, 7, "это тест"), Segment(7, 8, "это тест"),
            Segment(8, 9, "это тест"), Segment(9.1, 10, "- да, - сказал он")]
    out = postprocess(segs, pause_para=1.0, min_para_chars=0)
    texts = [s.text for s in out]
    assert texts[0] == "Привет, мир."[:-1] or texts[0].startswith("Привет, мир"), texts
    assert all("Продолжение" not in t for t in texts)
    assert texts.count("Это тест") + texts.count("это тест") <= 2, texts
    assert texts[-1].startswith("— Да, — сказал он"), texts
    assert out[0].para and out[1].para       # пауза 2 c -> новый абзац


def test_pipeline():
    tmp = tempfile.mkdtemp(prefix="a2b-test-")
    m4b, mp3dir = make_fixtures(tmp)
    s = Settings(output_dir=os.path.join(tmp, "out"), formats=["epub", "fb2", "txt", "docx", "srt", "lrc", "azw3"])
    logs = []

    class CB(Callbacks):
        def log(self, m):
            logs.append(m)

    # 1) m4b с главами и встроенной обложкой; маленькие куски, чтобы проверить разрезку
    audio_iter = audio.iter_chunks
    audio.iter_chunks = lambda p, a, b, chunk_sec=900.0, look_sec=20.0: audio_iter(p, a, b, 15.0, 3.0)
    try:
        job = Job(inputs=[m4b])
        book = run_job(job, s, FakeTranscriber(), CB())
    finally:
        audio.iter_chunks = audio_iter
    assert [c.title for c in book.chapters] == ["Часть первая", "Часть вторая", "Часть третья"]
    assert book.title == "Курочка Ряба" and book.author == "Народная сказка" and book.cover
    for c in book.chapters:
        assert c.done and c.segments
        assert all(c.start - 0.01 <= seg.start <= c.end + 0.01 for seg in c.segments), c.title
    folder = os.path.dirname(job.project_path)
    check_outputs(folder, "Народная сказка - Курочка Ряба")
    assert os.path.isfile(os.path.join(folder, "Народная сказка - Курочка Ряба.srt"))
    # проект переоткрывается
    assert Book.load(job.project_path).chapters[1].segments

    # 2) папка mp3: естественная сортировка, теги, обложка из folder.jpg
    job2 = Job(inputs=[mp3dir])
    book2 = run_job(job2, s, FakeTranscriber(), CB())
    assert [c.title for c in book2.chapters] == ["Сказка 1", "Сказка 2", "Сказка 10"], [c.title for c in book2.chapters]
    assert book2.author == "Александр Сергеевич Пушкин" and book2.cover.endswith(".jpg")
    folder2 = os.path.dirname(job2.project_path)
    check_outputs(folder2, "Александр Сергеевич Пушкин - Сказки")
    lrc_dir = os.path.join(folder2, "Александр Сергеевич Пушкин - Сказки — LRC")
    assert sorted(os.listdir(lrc_dir)) == ["01 - Сказка 1.lrc", "02 - Сказка 2.lrc", "10 - Сказка 10.lrc"]
    fb2 = open(os.path.join(folder2, "Александр Сергеевич Пушкин - Сказки.fb2"), encoding="utf-8").read()
    assert "<first-name>Александр</first-name><middle-name>Сергеевич</middle-name><last-name>Пушкин</last-name>" in fb2
    print("\n".join(logs))
    print("OK:", folder, folder2)


def test_resume():
    """Остановка посреди книги и продолжение с того же места."""
    from audio2book.core.transcriber import Cancelled
    tmp = tempfile.mkdtemp(prefix="a2b-resume-")
    m4b, _ = make_fixtures(tmp)
    s = Settings(output_dir=os.path.join(tmp, "out"), formats=["txt"])
    n = {"seg": 0}

    class StopAt(Callbacks):
        def check(self):
            n["seg"] += 1
            if n["seg"] == 20:
                raise Cancelled()

        def log(self, m):
            pass

    job = Job(inputs=[m4b])
    try:
        run_job(job, s, FakeTranscriber(), StopAt())
        raise AssertionError("должно было остановиться")
    except Cancelled:
        pass
    partial = Book.load(job.project_path)
    assert not partial.done and 0 < partial.processed_seconds() < partial.duration
    book = run_job(job, s, FakeTranscriber(), Callbacks())
    assert book.done
    starts = [seg.start for c in book.chapters for seg in c.segments]
    assert starts == sorted(starts), "сегменты не должны дублироваться"


if __name__ == "__main__":
    test_textproc()
    test_pipeline()
    test_resume()
    print("ВСЕ ТЕСТЫ ПРОЙДЕНЫ")
