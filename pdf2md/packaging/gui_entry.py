"""Entry point for the pdf2md-gui.exe window build (PyInstaller)."""

import multiprocessing
import sys

from pdf2md.gui import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
