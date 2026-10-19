"""Write a PyInstaller version-info file (the exe's Details tab: product, version, copyright).

usage: make_version.py <out.txt> <original filename> <description>
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from binarytotext import __version__  # noqa: E402

TEMPLATE = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Ki Sub Kim'),
      StringStruct('FileDescription', {desc!r}),
      StringStruct('FileVersion', {ver!r}),
      StringStruct('InternalName', {name!r}),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Ki Sub Kim. MIT License.'),
      StringStruct('OriginalFilename', {name!r}),
      StringStruct('ProductName', 'BinaryToText'),
      StringStruct('ProductVersion', {ver!r}),
      StringStruct('Comments', 'https://github.com/kisubkim/BanaryToText')])]),
    VarFileInfo([VarStruct('Translation', [0x0409, 1200])])
  ]
)
"""


def version_tuple(v: str) -> tuple[int, int, int, int]:
    parts = [int(p) for p in v.split(".") if p.isdigit()][:4]
    return tuple(parts + [0] * (4 - len(parts)))


if __name__ == "__main__":
    out, name, desc = sys.argv[1:4]
    with open(out, "w", encoding="utf-8") as f:
        f.write(TEMPLATE.format(nums=version_tuple(__version__), ver=__version__, name=name, desc=desc))
