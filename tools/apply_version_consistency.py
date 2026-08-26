from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    (ROOT / path).write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


fontblind = read("fontblind.py")
fontblind = replace_once(
    fontblind,
    "from fontTools.ttLib import TTLibError\n\n",
    "from fontTools.ttLib import TTLibError\n\nfrom fontblind_version import PROGRAM_VERSION\n\n",
    label="top-level version import",
)
fontblind = replace_once(
    fontblind,
    'PROGRAM_VERSION = "3.1.0"\n',
    "",
    label="top-level stale version",
)
write("fontblind.py", fontblind)

surgical = read("fontblind_surgical.py")
surgical = replace_once(
    surgical,
    "from fontTools.ttLib import TTFont, TTLibError, newTable\n\n",
    "from fontTools.ttLib import TTFont, TTLibError, newTable\n\nfrom fontblind_version import PROGRAM_VERSION\n\n",
    label="surgical version import",
)
surgical = replace_once(
    surgical,
    'PROGRAM_VERSION = "3.1.0"\n',
    "",
    label="surgical stale version",
)
write("fontblind_surgical.py", surgical)

outline = read("fontblind_outline.py")
outline = replace_once(
    outline,
    "from fontTools.varLib.instancer import (\n",
    "from fontTools.varLib.instancer import (\n",
    label="outline stable anchor",
)
outline = replace_once(
    outline,
    ")\n\nfrom fontblind_surgical import (\n",
    ")\n\nfrom fontblind_version import PROGRAM_VERSION\nfrom fontblind_surgical import (\n",
    label="outline version import",
)
outline = replace_once(
    outline,
    'OUTLINE_PROGRAM_VERSION = "3.1.0"\n',
    "OUTLINE_PROGRAM_VERSION = PROGRAM_VERSION\n",
    label="outline stale version",
)
write("fontblind_outline.py", outline)

web = read("fontblind_web.py")
web = replace_once(
    web,
    "from fontTools.ttLib import TTFont, TTLibError\n\n",
    "from fontTools.ttLib import TTFont, TTLibError\n\nfrom fontblind_version import PROGRAM_VERSION\n\n",
    label="web version import",
)
web = replace_once(
    web,
    'PROGRAM_VERSION = "3.4.0"\n\n',
    "",
    label="web duplicate version",
)
write("fontblind_web.py", web)

pyproject = read("pyproject.toml")
pyproject = replace_once(
    pyproject,
    '  "fontblind_web",\n',
    '  "fontblind_version",\n  "fontblind_web",\n',
    label="version package module",
)
write("pyproject.toml", pyproject)

changelog = read("CHANGELOG.md")
changelog = replace_once(
    changelog,
    "## Unreleased\n\n",
    "## Unreleased\n\n- Centralize the release version so every CLI, engine, package, and report identifies the same build.\n",
    label="version changelog",
)
write("CHANGELOG.md", changelog)

for helper in ("tools/apply_version_consistency.py",):
    (ROOT / helper).unlink(missing_ok=True)
