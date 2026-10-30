# PyInstaller: python -m PyInstaller packaging/audio2book.spec --noconfirm
# Перед сборкой положите ffmpeg.exe, ffprobe.exe (и их dll) в папку ffmpeg в корне проекта.
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas, binaries, hidden = [], [], []
for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "tokenizers", "av", "docx"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hidden += h

datas += [(os.path.join(ROOT, "packaging", "icon.png"), "packaging"),
          (os.path.join(ROOT, "packaging", "icon.ico"), "packaging")]
ff = os.path.join(ROOT, "ffmpeg")
if os.path.isdir(ff):
    for n in os.listdir(ff):
        if n.lower().endswith((".exe", ".dll")):
            binaries.append((os.path.join(ff, n), "ffmpeg"))

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["PySide6.QtMultimedia"],
    excludes=["tkinter", "matplotlib", "torch", "IPython", "pytest", "onnxruntime.quantization",
              "onnxruntime.transformers", "onnxruntime.tools", "PySide6.QtWebEngineCore",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Audio2Book",
    icon=os.path.join(ROOT, "packaging", "icon.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Audio2Book")
