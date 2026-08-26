#!/usr/bin/env python3
"""Parallel corpus gauntlet for both FontBlind engines."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from fontTools.ttLib import TTFont

from fontblind import PROGRAM_VERSION, rebuild_outline_font, recreate_font

FONT_SUFFIXES = {".ttf", ".otf"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def discover(paths: Iterable[Path]) -> tuple[list[Path], int]:
    files: set[Path] = set()
    for supplied in paths:
        path = supplied.expanduser()
        if path.is_file() and path.suffix.lower() in FONT_SUFFIXES:
            files.add(path.resolve())
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in FONT_SUFFIXES:
                    files.add(candidate.resolve())
    ordered = sorted(files, key=lambda item: str(item))
    by_digest: dict[str, Path] = {}
    for path in ordered:
        by_digest.setdefault(sha256(path), path)
    return sorted(by_digest.values(), key=lambda item: str(item)), len(ordered)


def font_features(path: Path) -> tuple[str, list[str], bool]:
    font = TTFont(str(path), lazy=True, recalcBBoxes=False, recalcTimestamp=False)
    try:
        tags = sorted(str(tag) for tag in font.reader.keys()) if font.reader else []
        if "glyf" in font:
            kind = "glyf"
        elif "CFF " in font:
            kind = "CFF1"
        elif "CFF2" in font:
            kind = "CFF2"
        elif any(tag in font for tag in ("CBDT", "sbix", "SVG ")):
            kind = "bitmap/color"
        else:
            kind = "other"
        outline_capable = any(tag in font for tag in ("glyf", "CFF ", "CFF2"))
        return kind, tags, outline_capable
    finally:
        font.close()


def test_one(task: tuple[int, str, int, str, str]) -> dict[str, Any]:
    index, path_text, loops, work_root_text, mode = task
    path = Path(path_text)
    work_root = Path(work_root_text)
    started = time.perf_counter()
    kind, tags, outline_capable = font_features(path)
    out_dir = work_root / f"{index:05d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "index": index,
        "source": str(path),
        "kind": kind,
        "tags": tags,
        "outline_capable": outline_capable,
        "mode": mode,
    }
    try:
        operations = 0
        if mode in {"clone", "both"}:
            clone_out = out_dir / f"clone{path.suffix.lower()}"
            clone = recreate_font(path, clone_out, overwrite=True, verify_rounds=loops)
            result["clone"] = {
                "ok": True,
                "idempotent": bool(clone["idempotent"]),
                "sha256": clone["sha256"],
                "changed_tables": clone["equivalence"]["changed_tables"],
                "removed_tables": clone["equivalence"]["removed_tables"],
                "identity_tokens_checked": clone["audit"]["source_identity_tokens_checked"],
            }
            operations += loops
        if mode in {"outline", "both"}:
            if not outline_capable:
                result["outline"] = {"ok": None, "skipped": "font has no glyf/CFF/CFF2 outlines"}
            else:
                suffix = ".ttf" if kind == "glyf" else ".otf"
                outline_out = out_dir / f"outline{suffix}"
                outline = rebuild_outline_font(path, outline_out, overwrite=True, verify_rounds=loops)
                result["outline"] = {
                    "ok": True,
                    "sha256": outline["output_sha256"],
                    "outline_format": outline["outline_format"],
                    "glyphs": outline["glyphs"],
                    "checks": outline["equivalence"]["checks"],
                    "identity_tokens_checked": outline["audit_source_identity_tokens_checked"],
                }
                operations += loops
        result.update(
            {
                "ok": True,
                "seconds": round(time.perf_counter() - started, 4),
                "operations": operations,
            }
        )
        return result
    except Exception as exc:
        result.update(
            {
                "ok": False,
                "seconds": round(time.perf_counter() - started, 4),
                "operations": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run repeated FontBlind clone/outline corpus loops")
    parser.add_argument("paths", nargs="+", type=Path, help="font files or directories")
    parser.add_argument("--mode", choices=["clone", "outline", "both"], default="both")
    parser.add_argument("--loops", type=int, default=3, help="rebuild rounds per font (2-20; default: 3)")
    parser.add_argument("--offset", type=int, default=0, help="skip the first N unique fonts")
    parser.add_argument("--limit", type=int, help="test at most N fonts after --offset")
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument("--work-dir", type=Path, help="keep working files in this directory")
    parser.add_argument("--keep", action="store_true", help="do not delete generated outputs")
    parser.add_argument("--json", type=Path, help="write the full JSON report here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 2 <= args.loops <= 20:
        raise SystemExit("--loops must be between 2 and 20")
    if args.offset < 0:
        raise SystemExit("--offset cannot be negative")
    if args.workers < 1:
        raise SystemExit("--workers must be at least 1")

    unique_fonts, discovered_files = discover(args.paths)
    selected = unique_fonts[args.offset :]
    if args.limit is not None:
        selected = selected[: args.limit]
    if not selected:
        raise SystemExit("no TTF/OTF files selected")

    own_temp = args.work_dir is None
    temp_ctx = tempfile.TemporaryDirectory(prefix="fontblind-corpus-") if own_temp else None
    work_root = Path(temp_ctx.name) if temp_ctx else args.work_dir.resolve()
    work_root.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    tasks = [(i, str(path), args.loops, str(work_root), args.mode) for i, path in enumerate(selected, 1)]

    try:
        if args.workers == 1:
            for task in tasks:
                results.append(test_one(task))
                failures = sum(not item["ok"] for item in results)
                print(f"[{len(results)}/{len(tasks)}] failures={failures}", file=sys.stderr, flush=True)
        else:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(test_one, task): task[0] for task in tasks}
                for completed, future in enumerate(as_completed(futures), 1):
                    results.append(future.result())
                    if completed % 10 == 0 or completed == len(tasks):
                        failures = sum(not item["ok"] for item in results)
                        print(f"[{completed}/{len(tasks)}] failures={failures}", file=sys.stderr, flush=True)

        results.sort(key=lambda item: item["index"])
        failures = [item for item in results if not item["ok"]]
        kinds = Counter(item["kind"] for item in results)
        feature_counts: Counter[str] = Counter()
        for item in results:
            feature_counts.update(item["tags"])
        outline_skipped = sum(item.get("outline", {}).get("ok") is None for item in results)
        operations = sum(int(item.get("operations", 0)) for item in results)

        report = {
            "program": "fontblind-dual-corpus-gauntlet",
            "program_version": PROGRAM_VERSION,
            "mode": args.mode,
            "ok": not failures,
            "files_discovered": discovered_files,
            "unique_binaries_discovered": len(unique_fonts),
            "offset": args.offset,
            "fonts_tested": len(results),
            "loops_per_engine_per_font": args.loops,
            "rebuild_operations_completed": operations,
            "passed": len(results) - len(failures),
            "failed": len(failures),
            "outline_skipped_no_outlines": outline_skipped,
            "kind_counts": dict(sorted(kinds.items())),
            "feature_table_counts": dict(sorted(feature_counts.items())),
            "workers": args.workers,
            "seconds": round(time.perf_counter() - started, 3),
            "failures": failures,
            "results": results,
        }
        rendered = json.dumps(report, indent=2, ensure_ascii=False)
        print(rendered)
        if args.json:
            args.json.parent.mkdir(parents=True, exist_ok=True)
            args.json.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report["ok"] else 1
    finally:
        if temp_ctx is not None and not args.keep:
            temp_ctx.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
