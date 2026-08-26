#!/usr/bin/env python3
"""Create reviewed Gate 7 transformed files without mutating the checkout."""
from __future__ import annotations

import shutil
from pathlib import Path
from textwrap import indent


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "gate7-output"


def _pipeline() -> str:
    path = ROOT / "tests" / "test_pipeline.py"
    pipeline = path.read_text(encoding="utf-8")
    if "import os\n" not in pipeline:
        pipeline = pipeline.replace("import tempfile\n", "import os\nimport tempfile\n", 1)
    old = '''FONT_ROOTS = (Path("/System/Library/Fonts"), Path("/Library/Fonts"))


def find_sample(required_table: str) -> Path | None:
    for root in FONT_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted((*root.rglob("*.ttf"), *root.rglob("*.otf"))):
            try:
                font = TTFont(str(path), lazy=True)
                has_table = required_table in font
                font.close()
                if has_table:
                    inspect_strict_source(path)
                    return path
            except Exception:
                continue
    return None


TTF_SAMPLE = find_sample("glyf")
CFF_SAMPLE = find_sample("CFF ")
VARIABLE_SAMPLE = find_sample("fvar")
'''
    new = '''FONT_ROOTS = (Path("/System/Library/Fonts"), Path("/Library/Fonts"))
CORPUS_ROOT = Path(
    os.environ.get(
        "FONTBLIND_CORPUS_DIR",
        Path(__file__).resolve().parent / "corpus" / "cache",
    )
)


def corpus_sample(filename: str, required_table: str) -> Path | None:
    path = CORPUS_ROOT / filename
    if not path.is_file():
        return None
    try:
        font = TTFont(str(path), lazy=True)
        has_table = required_table in font
        font.close()
        if has_table:
            inspect_strict_source(path)
            return path
    except Exception:
        return None
    return None


def find_sample(required_table: str) -> Path | None:
    for root in FONT_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted((*root.rglob("*.ttf"), *root.rglob("*.otf"))):
            try:
                font = TTFont(str(path), lazy=True)
                has_table = required_table in font
                font.close()
                if has_table:
                    inspect_strict_source(path)
                    return path
            except Exception:
                continue
    return None


TTF_SAMPLE = corpus_sample("latin-static.ttf", "glyf") or find_sample("glyf")
CFF_SAMPLE = corpus_sample("cff-static.otf", "CFF ") or find_sample("CFF ")
VARIABLE_SAMPLE = corpus_sample("arabic-variable.ttf", "fvar") or find_sample("fvar")
'''
    if old not in pipeline:
        raise RuntimeError("test_pipeline sample-discovery anchor drifted")
    return pipeline.replace(old, new, 1)


def _release_files() -> tuple[str, str]:
    build_path = ROOT / "build-fontblind-app.command"
    build = build_path.read_text(encoding="utf-8")
    start_marker = '"$PYTHON" - "$SERVER_URL" "$SMOKE_ROOT" <<\'PY\'\n'
    end_marker = '\nPY\n\nkill "$SERVER_PID"'
    start = build.find(start_marker)
    end = build.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise RuntimeError("native smoke here-document anchor drifted")
    body = build[start + len(start_marker):end]
    body_lines = body.splitlines()
    if body_lines and body_lines[0].strip() == "from __future__ import annotations":
        body_lines = body_lines[1:]
    body = "\n".join(body_lines).lstrip("\n")
    body = body.replace("sys.argv[1]", "arguments[1]").replace("sys.argv[2]", "arguments[2]")
    release = '''#!/usr/bin/env python3
"""Reusable exact-runtime product gauntlet for source and frozen servers."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if len(arguments) != 3:
        return 64
'''
    release += indent(body, "    ") + "\n    return 0\n\n\nif __name__ == \"__main__\":\n    raise SystemExit(main())\n"

    invocation = '"$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT"'
    build = build[:start] + invocation + build[end + len("\nPY"):]
    slash = chr(92)
    hidden_anchor = "  --hidden-import=fontblind_instance " + slash + "\n"
    hidden_insert = (
        hidden_anchor
        + "  --hidden-import=fontblind_instance_proof " + slash + "\n"
        + "  --hidden-import=fontblind_instance_verified " + slash + "\n"
    )
    if hidden_anchor not in build:
        raise RuntimeError("PyInstaller hidden-import anchor drifted")
    return build.replace(hidden_anchor, hidden_insert, 1), release


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "tests").mkdir(parents=True)
    (OUTPUT / "tests" / "test_pipeline.py").write_text(_pipeline(), encoding="utf-8")
    build, release = _release_files()
    (OUTPUT / "build-fontblind-app.command").write_text(build, encoding="utf-8")
    (OUTPUT / "release_gauntlet.py").write_text(release, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
