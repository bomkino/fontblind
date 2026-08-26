"""Anonymous, functional master locations for Lab result inspection."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

from fontTools.ttLib import TTFont


_WIDTH_PERCENT = {
    1: 50.0,
    2: 62.5,
    3: 75.0,
    4: 87.5,
    5: 100.0,
    6: 112.5,
    7: 125.0,
    8: 150.0,
    9: 200.0,
}


def anonymous_slant_masters(angle: float) -> tuple[dict[str, object], ...]:
    value = float(angle)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("invalid anonymous slant map")
    return (
        {"id": "M01", "location": {"slnt": 0.0}, "default": True},
        {"id": "M02", "location": {"slnt": -value}, "default": False},
    )


def anonymous_variable_masters(
    sources: Iterable[Path],
    axes: tuple[dict[str, object], ...],
) -> tuple[dict[str, object], ...]:
    specs = tuple(dict(axis) for axis in axes)
    tags = tuple(str(axis["tag"]) for axis in specs)
    if not tags or any(tag not in {"wght", "wdth"} for tag in tags) or len(set(tags)) != len(tags):
        raise ValueError("invalid anonymous variable map axes")
    defaults = {str(axis["tag"]): float(axis["default"]) for axis in specs}

    rows: list[tuple[int, float, str, dict[str, float]]] = []
    for source in map(Path, sources):
        font = TTFont(
            str(source),
            lazy=True,
            recalcBBoxes=False,
            recalcTimestamp=False,
            ignoreDecompileErrors=False,
        )
        try:
            weight = int(font["OS/2"].usWeightClass)
            width_class = int(font["OS/2"].usWidthClass)
        finally:
            font.close()
        width = _WIDTH_PERCENT[width_class]
        location: dict[str, float] = {}
        if "wght" in tags:
            location["wght"] = float(weight)
        if "wdth" in tags:
            location["wdth"] = float(width)
        rows.append((weight, width, str(source), location))

    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    masters: list[dict[str, object]] = []
    for index, (_weight, _width, _path, location) in enumerate(rows, start=1):
        is_default = all(math.isclose(location[tag], defaults[tag], abs_tol=1e-7) for tag in tags)
        masters.append(
            {
                "id": f"M{index:02d}",
                "location": location,
                "default": is_default,
            }
        )
    if sum(bool(master["default"]) for master in masters) != 1:
        raise ValueError("anonymous variable map has no unique default")
    return tuple(masters)
