#!/usr/bin/env python3
"""Fix the frozen-runtime corpus model check to use the public contract."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "release_gauntlet.py"


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    old = '''            corpus_result = post_font("/api/process", source_bytes)
            exact_checks(corpus_result, blind_checks)
            require(bool(corpus_result.get("variable")) is source_variable, f"release corpus asset {asset_id} changed font model")
            corpus_paths = download(corpus_result, f"corpus-{asset_id}")
            native = corpus_paths["native"]
            decoded = root / f"corpus-{asset_id}-decoded{pathlib.Path(corpus_result['native']['filename']).suffix}"
'''
    new = '''            corpus_result = post_font("/api/process", source_bytes)
            exact_checks(corpus_result, blind_checks)
            # The public result deliberately omits private format/color flags.
            # A variable Blind output is represented by reviewed public axes,
            # then independently confirmed from the downloaded native font.
            public_variable = bool(corpus_result.get("axes"))
            require(public_variable is source_variable, f"release corpus asset {asset_id} changed public font model")
            corpus_paths = download(corpus_result, f"corpus-{asset_id}")
            native = corpus_paths["native"]
            native_font = TTFont(str(native), lazy=False)
            try:
                require(("fvar" in native_font) is source_variable, f"release corpus asset {asset_id} changed native font model")
            finally:
                native_font.close()
            decoded = root / f"corpus-{asset_id}-decoded{pathlib.Path(corpus_result['native']['filename']).suffix}"
'''
    if text.count(old) != 1:
        raise RuntimeError("release corpus model-check anchor drifted")
    TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
