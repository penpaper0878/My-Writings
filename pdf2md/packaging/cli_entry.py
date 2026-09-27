"""Entry point for the pdf2md.exe command-line build (PyInstaller)."""

import multiprocessing
import sys

from pdf2md.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
