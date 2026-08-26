from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def patch_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    text = path.read_text(encoding="utf-8")
    if "## Unreleased\n\n- Add one verified Linux browser-app runtime" not in text:
        marker = "# Changelog\n"
        if not text.startswith(marker):
            raise RuntimeError("CHANGELOG heading drifted")
        addition = (
            "# Changelog\n\n"
            "## Unreleased\n\n"
            "- Add one verified Linux browser-app runtime, packaged as a reproducible AppImage and portable tarball.\n"
            "- Reuse the hardened frozen server, release gauntlet, zero-ID boundaries, and pinned multiscript corpus rather than forking the product.\n"
            "- Add Linux launcher lifecycle, loopback-readiness, malformed-protocol, and package-contract tests.\n"
        )
        text = addition + text[len(marker):].lstrip("\n")
        path.write_text(text, encoding="utf-8")


def patch_readme() -> None:
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")
    old = "FontBlind is one local-only macOS app with three tools:"
    new = (
        "FontBlind is one local-only font utility with three tools. The native wrapper targets macOS; "
        "Linux uses the same hardened runtime in a browser-hosted AppImage or portable bundle:"
    )
    if old in text:
        text = text.replace(old, new, 1)
    if "## Linux\n" not in text:
        marker = "## Download\n"
        if marker not in text:
            raise RuntimeError("README download heading drifted")
        section = (
            "## Linux\n\n"
            "Linux packaging lives in `build-fontblind-linux.sh`. It produces a self-contained AppImage and portable tarball, then launches the exact package with browser opening disabled as a final smoke check. The x86_64 package is release-cleared in CI; see `docs/LINUX.md`.\n\n"
        )
        text = text.replace(marker, section + marker, 1)
    path.write_text(text, encoding="utf-8")


def patch_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "tests.yml"
    text = path.read_text(encoding="utf-8")
    if "name: Linux x86_64 package" in text:
        return
    job = r'''

  linux-package:
    name: Linux x86_64 package
    runs-on: ubuntu-24.04
    timeout-minutes: 45
    needs: test
    env:
      FONTBLIND_CORPUS_DIR: ${{ github.workspace }}/tests/corpus/cache
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97
        with:
          python-version: "3.12"
          cache: pip
      - name: Fetch pinned release corpus
        run: python tools/fetch_corpus.py --output "$FONTBLIND_CORPUS_DIR"
      - name: Verify pinned release corpus
        run: python tools/fetch_corpus.py --output "$FONTBLIND_CORPUS_DIR" --verify-only
      - name: Build and exercise Linux packages
        run: bash ./build-fontblind-linux.sh
      - name: Verify Linux artifacts
        run: |
          test "$(find output/linux -maxdepth 1 -name 'FontBlind-*.AppImage' | wc -l | tr -d ' ')" = "1"
          test "$(find output/linux -maxdepth 1 -name 'FontBlind-*-linux-*.tar.gz' | wc -l | tr -d ' ')" = "1"
          for receipt in output/linux/*.sha256; do
            sha256sum -c "$receipt"
          done
          tar -tzf output/linux/FontBlind-*-linux-*.tar.gz >/dev/null
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02
        with:
          name: fontblind-linux-x86_64
          path: |
            output/linux/*.AppImage
            output/linux/*.AppImage.sha256
            output/linux/*.tar.gz
            output/linux/*.tar.gz.sha256
          if-no-files-found: error
          retention-days: 14
'''
    path.write_text(text.rstrip() + job + "\n", encoding="utf-8")


def main() -> int:
    patch_changelog()
    patch_readme()
    patch_workflow()
    for relative in ("build-fontblind-linux.sh", "linux/fontblind-launcher.sh"):
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"missing Linux source: {relative}")
        path.chmod(0o755)

    remove = (
        ".github/PULL_REQUEST_TEMPLATE_LINUX.md",
        ".github/workflows/export-linux-worktree.yml",
        ".github/workflows/apply-linux-foundation.yml",
        ".github/workflows/finalize-linux-foundation.yml",
        "tools/apply_linux_foundation.py",
        "tools/finalize_linux_foundation.py",
        "docs/LINUX_SCOPE.md",
        "docs/LINUX_RELEASE_NOTES_DRAFT.md",
        "docs/LINUX_DECISIONS.md",
        "docs/LINUX_NEXT.md",
        "docs/LINUX_SECURITY.md",
        "docs/LINUX_PACKAGE_LAYOUT.md",
        "docs/LINUX_TEST_MATRIX.md",
        "docs/LINUX_BUILD_PROVENANCE.md",
        "docs/LINUX_BROWSER_BOUNDARY.md",
        "docs/LINUX_FAILURE_POLICY.md",
        "docs/LINUX_ARTIFACTS.md",
        "docs/LINUX_LOCAL_ONLY.md",
        "docs/LINUX_NON_GOALS.md",
        "docs/LINUX_SUPPORT_POLICY.md",
        "docs/LINUX_OPERATOR_NOTES.md",
    )
    for relative in remove:
        (ROOT / relative).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
