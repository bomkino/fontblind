#!/usr/bin/env python3
"""Isolated one-job worker. It never prints source or output details."""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
from dataclasses import replace
from pathlib import Path

from fontblind_contract import validate_build_result
from fontblind_mastermap import anonymous_slant_masters, anonymous_variable_masters
from fontblind_pipeline import build_browser_outputs
from fontblind_policy import BrowserCompatibilityError, ZeroIdPolicyError
from fontblind_surgical import FontBlindError
from fontblind_web import WebBuildError


def _lab_failure(message: str) -> str:
    folded = message.casefold()
    if "independent weight and width" in folded or "real base" in folded:
        return "axis_model"
    if "coordinate" in folded or "weight from" in folded or "width class" in folded:
        return "coordinates"
    if any(term in folded for term in ("topology", "glyph order", "glyph count", "character map", "units-per-em")):
        return "structure"
    if "upright source" in folded:
        return "upright"
    if "standalone truetype" in folded or "static donor" in folded:
        return "unsupported"
    return "compile"


def _terminate(signum: int, _frame: object) -> None:
    raise SystemExit(128 + signum)


def _watch_parent(parent_fd: int, done: threading.Event) -> None:
    """Exit immediately when the browser parent closes or dies."""
    try:
        while os.read(parent_fd, 1):
            pass
    except OSError:
        pass
    if not done.is_set():
        os._exit(70)


def main(argv: list[str]) -> int:
    if len(argv) < 7:
        return 64
    mode = argv[1]
    output_dir = Path(argv[2])
    result_path = Path(argv[3])
    try:
        parent_fd = int(argv[4])
        options = json.loads(argv[5])
    except (ValueError, TypeError, json.JSONDecodeError):
        return 64
    if not isinstance(options, dict):
        return 64
    sources = [Path(value) for value in argv[6:]]
    if mode in {"blind", "oblique", "instance"} and len(sources) != 1:
        return 64
    if mode == "variable" and not 2 <= len(sources) <= 12:
        return 64
    if mode not in {"blind", "oblique", "variable", "instance"}:
        return 64
    done = threading.Event()
    threading.Thread(target=_watch_parent, args=(parent_fd, done), name="fontblind-parent-watch", daemon=True).start()
    temporary = result_path.with_suffix(".tmp")
    try:
        try:
            if mode == "blind":
                result = build_browser_outputs(sources[0], output_dir)
            elif mode == "oblique":
                from fontblind_lab import build_oblique_outputs, build_slant_variable_outputs

                angle = float(options.get("angle", 12))
                if options.get("output") == "slnt":
                    result = build_slant_variable_outputs(sources[0], output_dir, angle=angle)
                    result = replace(result, masters=anonymous_slant_masters(angle))
                else:
                    result = build_oblique_outputs(sources[0], output_dir, angle=angle)
            elif mode == "variable":
                from fontblind_lab import build_variable_outputs

                result = build_variable_outputs(sources, output_dir)
                result = replace(result, masters=anonymous_variable_masters(sources, result.axes))
            else:
                from fontblind_instance import StaticInstanceError, build_static_instance_outputs

                try:
                    result = build_static_instance_outputs(
                        sources[0],
                        output_dir,
                        location=options.get("location"),
                    )
                except StaticInstanceError:
                    return 7
        except BrowserCompatibilityError:
            return 6
        except ZeroIdPolicyError:
            return 3
        except FontBlindError as exc:
            if mode in {"oblique", "variable"}:
                temporary.write_text(
                    json.dumps({"failure": _lab_failure(str(exc))}, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(temporary, result_path)
            return 4
        except (WebBuildError, OSError, ValueError):
            return 4
        except Exception:
            return 5

        result.require_verified()
        validate_build_result(result)
        temporary.write_text(
            json.dumps(result.to_internal_dict(), separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, result_path)
        return 0
    finally:
        done.set()
        try:
            os.close(parent_fd)
        except OSError:
            pass
        for source in sources:
            if source.parent != Path("/dev/fd"):
                source.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _terminate)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _terminate)
    raise SystemExit(main(sys.argv))
