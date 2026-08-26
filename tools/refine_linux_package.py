from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor drifted in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    build = ROOT / "build-fontblind-linux.sh"
    text = build.read_text(encoding="utf-8")

    old = '''APPDIR="$BUILD_ROOT/FontBlind.AppDir"
make_layout "$APPDIR"

PORTABLE_NAME='''
    new = '''APPDIR="$BUILD_ROOT/FontBlind.AppDir"
make_layout "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
ln -s ../../AppRun "$APPDIR/usr/bin/FontBlind"
find "$APPDIR" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

PORTABLE_NAME='''
    if old in text:
        text = text.replace(old, new, 1)

    text = text.replace(
        'sha256sum "$PORTABLE_ARCHIVE" > "$PORTABLE_ARCHIVE.sha256"',
        '(cd "$OUTPUT_ROOT" && sha256sum "$(basename "$PORTABLE_ARCHIVE")" > "$(basename "$PORTABLE_ARCHIVE").sha256")',
        1,
    )
    text = text.replace(
        'sha256sum "$APPIMAGE" > "$APPIMAGE.sha256"',
        '(cd "$OUTPUT_ROOT" && sha256sum "$(basename "$APPIMAGE")" > "$(basename "$APPIMAGE").sha256")',
        1,
    )
    text = text.replace(
        'sha256sum -c "$PORTABLE_ARCHIVE.sha256"\nsha256sum -c "$APPIMAGE.sha256"',
        '(cd "$OUTPUT_ROOT" && sha256sum -c "$(basename "$PORTABLE_ARCHIVE").sha256")\n(cd "$OUTPUT_ROOT" && sha256sum -c "$(basename "$APPIMAGE").sha256")',
        1,
    )
    build.write_text(text, encoding="utf-8")
    build.chmod(0o755)

    workflow = ROOT / ".github" / "workflows" / "tests.yml"
    workflow_text = workflow.read_text(encoding="utf-8")
    workflow_text = workflow_text.replace(
        '''          for receipt in output/linux/*.sha256; do
            sha256sum -c "$receipt"
          done''',
        '''          (
            cd output/linux
            for receipt in *.sha256; do
              sha256sum -c "$receipt"
            done
          )''',
        1,
    )
    workflow.write_text(workflow_text, encoding="utf-8")

    test_path = ROOT / "tests" / "test_linux_packaging.py"
    test_text = test_path.read_text(encoding="utf-8")
    marker = '        self.assertIn("FONTBLIND_CORPUS_DIR", text)\n'
    addition = (
        marker
        + '        self.assertIn(\'cd "$OUTPUT_ROOT"\', text)\n'
        + '        self.assertIn("usr/bin/FontBlind", text)\n'
    )
    if 'self.assertIn("usr/bin/FontBlind", text)' not in test_text:
        if marker not in test_text:
            raise RuntimeError("Linux package test anchor drifted")
        test_text = test_text.replace(marker, addition, 1)
        test_path.write_text(test_text, encoding="utf-8")

    for relative in (
        ".github/workflows/refine-linux-package.yml",
        "tools/refine_linux_package.py",
    ):
        (ROOT / relative).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
