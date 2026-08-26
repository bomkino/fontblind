#!/usr/bin/env python3
"""Repeat Lab builds and frozen instances across a local corpus."""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from fontTools.ttLib import TTFont

from fontblind_instance import build_static_instance_outputs
from fontblind_lab import build_oblique_outputs, build_slant_variable_outputs, build_variable_outputs


FONT_SUFFIXES = {".ttf"}


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _discover(paths: Iterable[Path]) -> list[Path]:
    fonts: set[Path] = set()
    for supplied in paths:
        path = supplied.expanduser()
        if path.is_file() and path.suffix.casefold() in FONT_SUFFIXES:
            fonts.add(path.resolve())
        elif path.is_dir():
            fonts.update(
                candidate.resolve()
                for candidate in path.rglob("*")
                if candidate.is_file() and candidate.suffix.casefold() in FONT_SUFFIXES
            )
    return sorted(fonts, key=str)


def _output_digests(output: Path) -> dict[str, str]:
    return {
        str(item.relative_to(output)): _digest(item)
        for item in sorted(output.rglob("*"), key=lambda path: str(path.relative_to(output)))
        if item.is_file()
    }


def _location_name(location: dict[str, float]) -> str:
    parts = []
    for tag, value in sorted(location.items()):
        rendered = f"{value:.4f}".rstrip("0").rstrip(".").replace("-", "m").replace(".", "p")
        parts.append(f"{tag}-{rendered}")
    return "_".join(parts)


def _sample_locations(path: Path) -> list[dict[str, float]]:
    font = TTFont(str(path), lazy=False, recalcBBoxes=False, recalcTimestamp=False)
    try:
        axes = [
            (str(axis.axisTag), float(axis.minValue), float(axis.defaultValue), float(axis.maxValue))
            for axis in font["fvar"].axes
        ]
    finally:
        font.close()
    if not axes:
        raise RuntimeError("generated variable font exposes no axes")

    rows: dict[tuple[tuple[str, float], ...], dict[str, float]] = {}

    def add(location: dict[str, float]) -> None:
        key = tuple(sorted((tag, float(value)) for tag, value in location.items()))
        rows.setdefault(key, dict(key))

    default = {tag: default_value for tag, _minimum, default_value, _maximum in axes}
    add(default)
    for tag, minimum, _default, maximum in axes:
        for value in (minimum, maximum):
            location = dict(default)
            location[tag] = value
            add(location)

    # All corners and defaults are cheap for the currently supported one- and
    # two-axis models. This is where coupled-axis mistakes usually surface.
    if len(axes) <= 2:
        values = [(minimum, default_value, maximum) for _tag, minimum, default_value, maximum in axes]
        for coordinates in itertools.product(*values):
            add({axis[0]: coordinate for axis, coordinate in zip(axes, coordinates)})

    # Deterministic interior probes catch issues that exact donor reinstancing
    # cannot: midpoint deltas, fractional width mapping, and non-master slants.
    for fraction in (0.25, 0.5, 0.75):
        add(
            {
                tag: minimum + ((maximum - minimum) * fraction)
                for tag, minimum, _default, maximum in axes
            }
        )
    return list(rows.values())


def _freeze_samples(variable_path: Path, output_root: Path) -> dict[str, dict[str, str]]:
    results: dict[str, dict[str, str]] = {}
    for location in _sample_locations(variable_path):
        name = _location_name(location)
        destination = output_root / name
        build_static_instance_outputs(variable_path, destination, location=location)
        results[name] = _output_digests(destination)
    return results


def _oblique_task(task: tuple[int, str, tuple[float, ...], int]) -> dict[str, object]:
    index, source_text, angles, loops = task
    source = Path(source_text)
    started = time.perf_counter()
    result: dict[str, object] = {"index": index, "source": str(source), "ok": False}
    try:
        builds: dict[str, list[dict[str, str]]] = {}
        with tempfile.TemporaryDirectory(prefix="fontblind-lab-gauntlet-") as temp:
            root = Path(temp)
            for angle in angles:
                for lane, builder in (
                    ("oblique", build_oblique_outputs),
                    ("slnt", build_slant_variable_outputs),
                ):
                    key = f"{lane}:{angle:g}"
                    rounds: list[dict[str, str]] = []
                    for loop in range(loops):
                        output = root / f"{lane}-{angle:g}-{loop:02d}"
                        built = builder(source, output, angle=angle)
                        if lane == "slnt":
                            _freeze_samples(output / built.native.filename, output / "frozen")
                        rounds.append(_output_digests(output))
                    if any(round_value != rounds[0] for round_value in rounds[1:]):
                        raise RuntimeError(f"{key} was not deterministic across {loops} rounds")
                    builds[key] = rounds
        result.update(ok=True, seconds=round(time.perf_counter() - started, 4), builds=builds)
    except Exception as exc:
        result.update(
            seconds=round(time.perf_counter() - started, 4),
            error=f"{type(exc).__name__}: {exc}",
        )
    return result


def _run_oblique(args: argparse.Namespace) -> int:
    fonts = _discover(args.paths)
    if args.limit is not None:
        fonts = fonts[: args.limit]
    if not fonts:
        raise SystemExit("no standalone TTF fonts selected")
    tasks = [(index, str(path), tuple(args.angles), args.loops) for index, path in enumerate(fonts, 1)]
    results: list[dict[str, object]] = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(_oblique_task, task) for task in tasks]
        for completed, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            failures = sum(not bool(item["ok"]) for item in results)
            print(f"[{completed}/{len(tasks)}] failures={failures}", flush=True)
    results.sort(key=lambda item: int(item["index"]))
    failed = [item for item in results if not bool(item["ok"])]
    report = {
        "program": "fontblind-lab-gauntlet",
        "lane": "oblique+slnt+frozen-instances",
        "ok": not failed,
        "fonts_tested": len(results),
        "angles": args.angles,
        "loops": args.loops,
        "passed": len(results) - len(failed),
        "failed": len(failed),
        "results": results,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if not failed else 1


def _run_variable(args: argparse.Namespace) -> int:
    donors = [path.expanduser().resolve() for path in args.donors]
    if not 2 <= len(donors) <= 12 or any(not path.is_file() for path in donors):
        raise SystemExit("variable mode requires 2–12 existing donor TTF files")
    rounds: list[dict[str, str]] = []
    locations: list[str] = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="fontblind-variable-gauntlet-") as temp:
        root = Path(temp)
        for loop in range(args.loops):
            output = root / f"round-{loop:02d}"
            built = build_variable_outputs(donors, output)
            frozen = _freeze_samples(output / built.native.filename, output / "frozen")
            if not locations:
                locations = sorted(frozen)
            rounds.append(_output_digests(output))
    deterministic = all(round_value == rounds[0] for round_value in rounds[1:])
    report = {
        "program": "fontblind-lab-gauntlet",
        "lane": "variable+frozen-instances",
        "ok": deterministic,
        "donors": len(donors),
        "frozen_locations": locations,
        "loops": args.loops,
        "seconds": round(time.perf_counter() - started, 4),
        "rounds": rounds,
    }
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")
    return 0 if deterministic else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic FontBlind Lab and static-instance corpus loops")
    subparsers = parser.add_subparsers(dest="command", required=True)

    oblique = subparsers.add_parser("oblique", help="repeat static Oblique, slnt, and frozen-instance builds")
    oblique.add_argument("paths", nargs="+", type=Path)
    oblique.add_argument("--angles", nargs="+", type=float, default=[4.0, 12.0, 20.0])
    oblique.add_argument("--loops", type=int, default=2)
    oblique.add_argument("--workers", type=int, default=2)
    oblique.add_argument("--limit", type=int)
    oblique.add_argument("--json", type=Path)

    variable = subparsers.add_parser("variable", help="repeat one donor set and freeze corner/interior locations")
    variable.add_argument("donors", nargs="+", type=Path)
    variable.add_argument("--loops", type=int, default=2)
    variable.add_argument("--json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 2 <= args.loops <= 10:
        raise SystemExit("--loops must be between 2 and 10")
    if args.command == "oblique":
        if args.workers < 1:
            raise SystemExit("--workers must be at least 1")
        if not args.angles or any(not 4 <= angle <= 20 for angle in args.angles):
            raise SystemExit("--angles must stay between 4 and 20 degrees")
        return _run_oblique(args)
    return _run_variable(args)


if __name__ == "__main__":
    raise SystemExit(main())
