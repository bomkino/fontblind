#!/usr/bin/env python3
"""Wire the pinned representative corpus into the exact frozen-runtime gate."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label} anchor drifted")
    return text.replace(old, new, 1)


def patch_tests() -> None:
    path = ROOT / "tests" / "test_corpus.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'CORPUS_DIR = Path(os.environ.get("FONTBLIND_CORPUS_DIR", ROOT / "tests" / "corpus" / "cache"))\n',
        'CORPUS_DIR = Path(os.environ.get("FONTBLIND_CORPUS_DIR", ROOT / "tests" / "corpus" / "cache"))\n'
        'RUN_FULL_CORPUS = os.environ.get("FONTBLIND_RUN_FULL_CORPUS") == "1"\n',
        "corpus opt-in",
    )
    text = replace_once(
        text,
        '@unittest.skipUnless(CORPUS_DIR.is_dir(), "pinned release corpus unavailable; run tools/fetch_corpus.py")\n',
        '@unittest.skipUnless(\n'
        '    CORPUS_DIR.is_dir() and RUN_FULL_CORPUS,\n'
        '    "full pinned corpus gate disabled; set FONTBLIND_RUN_FULL_CORPUS=1",\n'
        ')\n',
        "corpus class decorator",
    )
    path.write_text(text, encoding="utf-8")


def patch_build() -> None:
    path = ROOT / "build-fontblind-app.command"
    text = path.read_text(encoding="utf-8")
    old = '"$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT"\n'
    new = '''if [[ -n "${FONTBLIND_CORPUS_DIR:-}" ]]; then
  "$PYTHON" "$APP_DIR/tools/fetch_corpus.py" --output "$FONTBLIND_CORPUS_DIR" --verify-only
  "$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT" "$FONTBLIND_CORPUS_DIR"
else
  "$PYTHON" "$APP_DIR/release_gauntlet.py" "$SERVER_URL" "$SMOKE_ROOT"
fi
'''
    path.write_text(replace_once(text, old, new, "release gauntlet invocation"), encoding="utf-8")


def patch_gauntlet() -> None:
    path = ROOT / "release_gauntlet.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    if len(arguments) != 3:\n        return 64\n    import http.client\n',
        '    if len(arguments) not in {3, 4}:\n        return 64\n    import hashlib\n    import http.client\n',
        "gauntlet argument contract",
    )
    text = replace_once(
        text,
        '    import struct\n    import sys\n    import zipfile\n',
        '    import struct\n    import zipfile\n',
        "duplicate sys import",
    )
    text = replace_once(
        text,
        '    from fontTools.ttLib import TTFont\n    from tests.test_lab import write_fixture_font\n',
        '    from fontTools.ttLib import TTFont\n\n'
        '    from fontblind_pipeline import _decode_woff2, _harfbuzz_shape\n'
        '    from fontblind_policy import assert_strict_output\n'
        '    from tests.test_lab import write_fixture_font\n',
        "gauntlet proof imports",
    )
    text = replace_once(
        text,
        '    root = pathlib.Path(arguments[2])\n    host = server.hostname or "127.0.0.1"\n',
        '    root = pathlib.Path(arguments[2])\n'
        '    corpus_root = pathlib.Path(arguments[3]) if len(arguments) == 4 else None\n'
        '    host = server.hostname or "127.0.0.1"\n',
        "gauntlet corpus root",
    )
    corpus_block = r'''

    if corpus_root is not None:
        manifest_path = pathlib.Path(__file__).resolve().parent / "tests" / "corpus" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        probes = {
            "Latin": "AVATAR office affinity ffi fl Á V̈ — 0123456789",
            "Arabic": "السَّلَامُ عَلَيْكُمْ العربية",
            "Devanagari": "नमस्ते दुनिया क्षत्रिय प्रज्ञा",
            "Hebrew": "שָׁלוֹם עוֹלָם בְּרָכָה",
            "Thai": "สวัสดีชาวโลก ภาษาไทย",
        }
        blind_checks = {
            "source_identity_removed",
            "embedding_flags_cleared",
            "outline_flavor_retained",
            "functional_clone_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "source_discarded",
        }
        for asset in manifest.get("assets", []):
            require(isinstance(asset, dict), "release corpus manifest contains a malformed asset")
            asset_id = asset.get("id")
            filename = asset.get("filename")
            script = asset.get("script")
            expected_size = asset.get("size")
            expected_sha256 = asset.get("sha256")
            require(
                isinstance(asset_id, str)
                and isinstance(filename, str)
                and isinstance(script, str)
                and script in probes
                and isinstance(expected_size, int)
                and isinstance(expected_sha256, str),
                "release corpus manifest contains an incomplete asset",
            )
            source = corpus_root / filename
            require(source.is_file() and not source.is_symlink(), f"release corpus asset {asset_id} is unavailable")
            source_bytes = source.read_bytes()
            require(len(source_bytes) == expected_size, f"release corpus asset {asset_id} changed size")
            require(hashlib.sha256(source_bytes).hexdigest() == expected_sha256, f"release corpus asset {asset_id} changed digest")

            source_font = TTFont(str(source), lazy=False)
            try:
                source_variable = "fvar" in source_font
            finally:
                source_font.close()

            corpus_result = post_font("/api/process", source_bytes)
            exact_checks(corpus_result, blind_checks)
            require(bool(corpus_result.get("variable")) is source_variable, f"release corpus asset {asset_id} changed font model")
            corpus_paths = download(corpus_result, f"corpus-{asset_id}")
            native = corpus_paths["native"]
            decoded = root / f"corpus-{asset_id}-decoded{pathlib.Path(corpus_result['native']['filename']).suffix}"
            assert_strict_output(native, source)
            _decode_woff2(corpus_paths["web"], decoded)
            assert_strict_output(decoded, source)
            source_shape = _harfbuzz_shape(source, probes[script])
            require(len(source_shape) > 1 and any(glyph_id != 0 for glyph_id, *_rest in source_shape), f"release corpus probe {asset_id} did not shape")
            require(source_shape == _harfbuzz_shape(native, probes[script]), f"release corpus native shaping drifted for {asset_id}")
            require(source_shape == _harfbuzz_shape(decoded, probes[script]), f"release corpus WOFF2 shaping drifted for {asset_id}")
            delete_job(corpus_result)
'''
    text = replace_once(
        text,
        '    status, _headers, _payload = request("GET", variable["native"]["url"])\n'
        '    require(status == 404, "a deleted variable parent remained downloadable")\n'
        '    return 0\n',
        '    status, _headers, _payload = request("GET", variable["native"]["url"])\n'
        '    require(status == 404, "a deleted variable parent remained downloadable")\n'
        + corpus_block
        + '\n    return 0\n',
        "gauntlet corpus insertion",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_tests()
    patch_build()
    patch_gauntlet()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
