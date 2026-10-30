"""Книгу начали распознавать, потом папку переместили — продолжаем с нового места."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audio2book.core import audio  # noqa: E402
from audio2book.core.pipeline import Callbacks, Job, run_job  # noqa: E402
from audio2book.core.project import Book  # noqa: E402
from audio2book.core.settings import Settings  # noqa: E402
from audio2book.core.transcriber import Cancelled  # noqa: E402
from test_core import FakeTranscriber, make_fixtures  # noqa: E402


def test_moved_folder():
    tmp = tempfile.mkdtemp(prefix="a2b-move-")
    _, mp3dir = make_fixtures(tmp)
    s = Settings(output_dir=os.path.join(tmp, "out"), formats=["txt"])
    n = {"i": 0}

    class StopEarly(Callbacks):
        def check(self):
            n["i"] += 1
            if n["i"] == 8:
                raise Cancelled()

        def log(self, m):
            pass

    job = Job(inputs=[mp3dir])
    try:
        run_job(job, s, FakeTranscriber(), StopEarly())
    except Cancelled:
        pass

    new_dir = os.path.join(tmp, "Перенесено", "Сказки (новая)")
    shutil.move(mp3dir, new_dir)

    try:
        run_job(job, s, FakeTranscriber(), Callbacks())
        raise AssertionError("должна быть ошибка о пропавших файлах")
    except audio.SourcesMissing as e:
        assert e.missing and "Пушкин - Сказки" in e.missing[0]

    book = Book.load(job.project_path)
    fixed, still = audio.relink_sources(book.chapters, new_dir)
    assert fixed == 3 and not still, (fixed, still)
    book.save(job.project_path)
    done = run_job(job, s, FakeTranscriber(), Callbacks())
    assert done.done and all(c.source.startswith(new_dir) for c in done.chapters)


def test_missing_inputs_before_project():
    tmp = tempfile.mkdtemp(prefix="a2b-gone-")
    job = Job(inputs=[os.path.join(tmp, "нет такой папки")])
    try:
        run_job(job, Settings(output_dir=tmp, formats=["txt"]), FakeTranscriber(), Callbacks())
        raise AssertionError
    except audio.SourcesMissing:
        pass
