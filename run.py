import multiprocessing
import sys

from audio2book.gui.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
