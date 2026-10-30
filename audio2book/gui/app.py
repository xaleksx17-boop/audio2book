"""Точка входа графического приложения."""
from __future__ import annotations

import os
import sys


def _resource(name: str) -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "packaging", name)


def main() -> int:
    from ..core.settings import APP_NAME, Settings
    from ..core.transcriber import setup_cuda_dlls

    setup_cuda_dlls()
    settings = Settings.load()
    settings.apply_env()       # до импорта faster_whisper/huggingface_hub

    if sys.platform == "win32":
        try:  # своя иконка на панели задач, а не python.exe
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_NAME}.App")
        except Exception:
            pass

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    icon = _resource("icon.png")
    if os.path.isfile(icon):
        app.setWindowIcon(QIcon(icon))
    win = MainWindow(settings)
    win.show()
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args:                      # «Открыть с помощью…» / перетаскивание на ярлык
        win.add_paths(args)
    code = app.exec()
    if win.thread.isRunning():   # распознавание не успело остановиться — не ждём
        os._exit(code)
    return code
