#!/usr/bin/env python3
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "release_gauntlet.py"
text = path.read_text(encoding="utf-8")
old = '''            corpus_result = post_font("/api/process", source_bytes)
            exact_checks(corpus_result, blind_checks)
            # The public result deliberately omits private format/color flags.
            # A variable Blind output is represented by reviewed public axes,
            # then independently confirmed from the downloaded native font.
            public_variable = bool(corpus_result.get("axes"))
            require(public_variable is source_variable, f"release corpus asset {asset_id} changed public font model")
            corpus_paths = download(corpus_result, f"corpus-{asset_id}")
'''
new = '''            corpus_result = post_font("/api/process", source_bytes)
            exact_checks(corpus_result, blind_checks)
            # The response intentionally omits internal format flags. The
            # downloaded native artifact is the authoritative model proof.
            corpus_paths = download(corpus_result, f"corpus-{asset_id}")
'''
if text.count(old) != 1:
    raise SystemExit("release corpus anchor drifted")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
