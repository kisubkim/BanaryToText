"""PyInstaller entry for btt.exe (command line only, built without Qt)."""

import sys

from binarytotext.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--help"], gui=False))
