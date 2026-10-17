"""PyInstaller entry for BinaryToText.exe (GUI; an optional argument opens that file)."""

import sys

from binarytotext.gui.app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else None))
