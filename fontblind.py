#!/usr/bin/env python3
"""FontBlind dual-mode TTF/OTF metadata removal tool.

Modes:
  clone    Surgical, preservation-first functional clone.
  outline  Fresh static reconstruction from glyph geometry.
  both     Produce both outputs from one source font.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from fontTools.ttLib import TTLibError

from fontblind_version import PROGRAM_VERSION

import fontblind_surgical as surgical
from fontblind_outline import (
    gauntlet_outline_font,
    parse_location,
    rebuild_outline_font,
    verify_outline_equivalence,
)

FontBlindError = surgical.FontBlindError


def __getattr__(name: str) -> Any:
    """Expose the surgical API through the top-level module for compatibility."""
    try:
        return getattr(surgical, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(surgical)))


def _jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _print_json(value: Any) -> None:
    print(json.dumps(_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False))


def _write_report(path: str | None, value: Any) -> None:
    if not path:
        return
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(_jsonable(value), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _source_outline_suffix(source: Path) -> str:
    font = surgical._open_font(source, lazy=True)
    try:
        return ".ttf" if "glyf" in font else ".otf"
    finally:
        font.close()


def _cmd_clone(args: argparse.Namespace) -> int:
    result = surgical.recreate_font(
        Path(args.input),
        Path(args.output),
        overwrite=args.overwrite,
        verify_rounds=args.verify_rounds,
    )
    result["mode"] = "clone"
    _write_report(args.report, result)
    _print_json(result)
    return 0


def _cmd_outline(args: argparse.Namespace) -> int:
    result = rebuild_outline_font(
        Path(args.input),
        Path(args.output),
        overwrite=args.overwrite,
        verify_rounds=args.verify_rounds,
        location=parse_location(args.location),
    )
    _write_report(args.report, result)
    _print_json(result)
    return 0


def _commit_output_pair(
    pairs: Sequence[tuple[Path, Path]],
    work_dir: Path,
) -> None:
    """Commit two verified outputs with rollback if either replacement fails."""
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for index, (_, destination) in enumerate(pairs):
            if destination.exists():
                backup = work_dir / f"backup-{index}{destination.suffix or '.font'}"
                os.replace(destination, backup)
                backups[destination] = backup
        for temporary, destination in pairs:
            os.replace(temporary, destination)
            committed.append(destination)
    except Exception:
        for destination in committed:
            destination.unlink(missing_ok=True)
        for destination, backup in backups.items():
            if backup.exists():
                os.replace(backup, destination)
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def _cmd_both(args: argparse.Namespace) -> int:
    source = Path(args.input).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.prefix or source.stem
    clone_suffix = source.suffix.lower() if source.suffix.lower() in {".ttf", ".otf"} else ".otf"
    outline_suffix = _source_outline_suffix(source)
    clone_output = output_dir / f"{stem}.clone{clone_suffix}"
    outline_output = output_dir / f"{stem}.outline{outline_suffix}"

    for destination in (clone_output, outline_output):
        if destination.exists() and not args.overwrite:
            raise FontBlindError(f"output already exists: {destination} (use --overwrite)")
        try:
            if source.resolve() == destination.resolve():
                raise FontBlindError("input and output paths must be different")
        except FileNotFoundError:
            pass

    with tempfile.TemporaryDirectory(prefix=".fontblind-both-", dir=str(output_dir)) as temp_text:
        work = Path(temp_text)
        clone_temp = work / clone_output.name
        outline_temp = work / outline_output.name

        # Neither final destination is touched unless both engines finish all
        # equivalence, metadata, and idempotence checks successfully.
        clone = surgical.recreate_font(
            source,
            clone_temp,
            overwrite=True,
            verify_rounds=args.clone_rounds,
        )
        clone["mode"] = "clone"
        outline = rebuild_outline_font(
            source,
            outline_temp,
            overwrite=True,
            verify_rounds=args.outline_rounds,
            location=parse_location(args.location),
        )

        _commit_output_pair(
            ((clone_temp, clone_output), (outline_temp, outline_output)),
            work,
        )

    clone["output"] = str(clone_output)
    outline["output"] = str(outline_output)
    result = {
        "mode": "both",
        "source": str(source),
        "clone_output": str(clone_output),
        "outline_output": str(outline_output),
        "clone": clone,
        "outline": outline,
    }
    _write_report(args.report, result)
    _print_json(result)
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    report = surgical.audit_font(Path(args.font), Path(args.source) if args.source else None)
    _write_report(args.report, report)
    _print_json(report)
    return 0 if report.ok else 1


def _cmd_verify(args: argparse.Namespace) -> int:
    source = Path(args.source)
    output = Path(args.output)
    if args.mode == "clone":
        report = surgical.verify_equivalence(source, output)
    else:
        report = verify_outline_equivalence(source, output, location=parse_location(args.location))
    _write_report(args.report, report)
    _print_json(report)
    return 0 if report.ok else 1


def _cmd_gauntlet(args: argparse.Namespace) -> int:
    source = Path(args.font)
    location = parse_location(args.location)
    if args.mode == "clone":
        result = surgical.gauntlet_font(
            source,
            rounds=args.rounds,
            glyph_samples=args.glyph_samples,
            ppems=args.ppem,
        )
        result["mode"] = "clone"
    elif args.mode == "outline":
        result = gauntlet_outline_font(
            source,
            rounds=args.rounds,
            glyph_samples=args.glyph_samples,
            ppems=args.ppem,
            location=location,
        )
    else:
        clone = surgical.gauntlet_font(
            source,
            rounds=args.rounds,
            glyph_samples=args.glyph_samples,
            ppems=args.ppem,
        )
        clone["mode"] = "clone"
        outline = gauntlet_outline_font(
            source,
            rounds=args.rounds,
            glyph_samples=args.glyph_samples,
            ppems=args.ppem,
            location=location,
        )
        result = {"mode": "both", "source": str(source), "clone": clone, "outline": outline}
    _write_report(args.report, result)
    _print_json(result)
    return 0


def _add_common_output_options(parser: argparse.ArgumentParser, *, default_rounds: int = 3) -> None:
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output")
    parser.add_argument(
        "--verify-rounds",
        type=int,
        default=default_rounds,
        help=f"require byte-idempotence for this many rebuild rounds (default: {default_rounds})",
    )
    parser.add_argument("--report", help="also write the JSON report to this path")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fontblind",
        description=(
            "Create either a surgical functional clone or a fresh outline reconstruction "
            "of a standalone TTF/OTF while neutralizing identity/provenance metadata."
        ),
    )
    parser.add_argument("--version", action="version", version=f"fontblind {PROGRAM_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    clone = subparsers.add_parser(
        "clone",
        aliases=["surgical", "recreate"],
        help="preserve functional font behavior while surgically removing metadata",
    )
    clone.add_argument("input", help="source .ttf or .otf")
    clone.add_argument("output", help="output .ttf or .otf")
    _add_common_output_options(clone)
    clone.set_defaults(func=_cmd_clone)

    outline = subparsers.add_parser(
        "outline",
        aliases=["rebuild"],
        help="build fresh unhinted outlines while retaining GID-stable runtime behavior",
    )
    outline.add_argument("input", help="source .ttf or .otf")
    outline.add_argument("output", help="fresh output font")
    outline.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="AXIS=VALUE",
        help="variable-font instance coordinate; repeat for multiple axes",
    )
    _add_common_output_options(outline)
    outline.set_defaults(func=_cmd_outline)

    both = subparsers.add_parser("both", help="generate surgical-clone and outline-rebuild outputs")
    both.add_argument("input", help="source .ttf or .otf")
    both.add_argument("output_dir", help="directory that receives both fonts")
    both.add_argument("--prefix", help="output filename prefix; defaults to the source stem")
    both.add_argument("--overwrite", action="store_true", help="replace existing outputs")
    both.add_argument("--clone-rounds", type=int, default=3, help="surgical-clone idempotence rounds")
    both.add_argument("--outline-rounds", type=int, default=3, help="outline-rebuild idempotence rounds")
    both.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="AXIS=VALUE",
        help="variable-font coordinate for the static outline output",
    )
    both.add_argument("--report", help="also write the combined JSON report to this path")
    both.set_defaults(func=_cmd_both)

    audit = subparsers.add_parser("audit", help="audit either output for generic metadata and source-name leakage")
    audit.add_argument("font", help="font to audit")
    audit.add_argument("--source", help="original source font for identity-leak checks")
    audit.add_argument("--report", help="also write the JSON report to this path")
    audit.set_defaults(func=_cmd_audit)

    verify = subparsers.add_parser("verify", help="verify clone or outline equivalence")
    verify.add_argument("source")
    verify.add_argument("output")
    verify.add_argument("--mode", choices=["clone", "outline"], required=True)
    verify.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="AXIS=VALUE",
        help="source variable-font coordinate used for an outline rebuild",
    )
    verify.add_argument("--report", help="also write the JSON report to this path")
    verify.set_defaults(func=_cmd_verify)

    gauntlet = subparsers.add_parser("gauntlet", help="run repeated idempotence, structure, and raster loops")
    gauntlet.add_argument("font")
    gauntlet.add_argument("--mode", choices=["clone", "outline", "both"], default="both")
    gauntlet.add_argument("--rounds", type=int, default=5)
    gauntlet.add_argument("--glyph-samples", type=int, default=64)
    gauntlet.add_argument("--ppem", type=int, nargs="+", default=[9, 12, 16, 24, 48])
    gauntlet.add_argument(
        "--location",
        action="append",
        default=[],
        metavar="AXIS=VALUE",
        help="variable-font coordinate for outline mode",
    )
    gauntlet.add_argument("--report", help="also write the JSON report to this path")
    gauntlet.set_defaults(func=_cmd_gauntlet)
    return parser


def _normalize_shorthand(argv: Sequence[str]) -> list[str]:
    """Treat ``fontblind INPUT OUTPUT`` as the preservation-first clone mode."""
    args = list(argv)
    commands = {
        "clone",
        "surgical",
        "recreate",
        "outline",
        "rebuild",
        "both",
        "audit",
        "verify",
        "gauntlet",
    }
    if args and args[0] not in commands and args[0] not in {"-h", "--help", "--version"}:
        positional = [item for item in args if not item.startswith("-")]
        if len(positional) >= 2:
            return ["clone", *args]
    return args


def main(argv: Sequence[str] | None = None) -> int:
    parser = make_parser()
    raw_args = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_normalize_shorthand(raw_args))
    try:
        return int(args.func(args))
    except (FontBlindError, OSError, ValueError, TTLibError) as exc:
        print(f"fontblind: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
