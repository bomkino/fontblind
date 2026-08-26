#!/usr/bin/env python3
"""Reusable exact-runtime product gauntlet for source and frozen servers."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    if len(arguments) not in {3, 4}:
        return 64
    import hashlib
    import http.client
    import json
    import pathlib
    import re
    import struct
    import time
    import zipfile
    from urllib.parse import urlsplit

    from fontTools.ttLib import TTFont

    from fontblind_pipeline import _decode_woff2, _harfbuzz_shape
    from fontblind_policy import assert_strict_output
    from tests.test_lab import write_fixture_font


    server = urlsplit(arguments[1])
    root = pathlib.Path(arguments[2])
    corpus_root = pathlib.Path(arguments[3]) if len(arguments) == 4 else None
    host = server.hostname or "127.0.0.1"
    port = int(server.port or 80)
    secrets = ("FROZEN_SMOKE_REGULAR_7Q9K", "FROZEN_SMOKE_BOLD_4M2X")
    secret_probes = tuple(
        encoded
        for value in secrets
        for encoded in (
            value.encode("utf-8"),
            value.encode("utf-16-be"),
            value.encode("utf-16-le"),
        )
    )


    def request(
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(host, port, timeout=180)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()


    def require(condition: bool, message: str) -> None:
        if not condition:
            raise SystemExit(message)


    def no_source_identity(payload: bytes, context: str) -> None:
        require(not any(probe in payload for probe in secret_probes), f"{context} leaked source identity")


    status, session_headers, session_payload = request("GET", "/api/session")
    require(status == 200, "frozen server session endpoint failed")
    require(session_headers.get("Cache-Control") == "no-store, max-age=0", "session response can be cached")
    session_value = json.loads(session_payload)
    session = session_value.get("session")
    require(session_value.get("ok") is True and isinstance(session, str), "invalid frozen session contract")
    require(len(session) >= 32 and re.search(r"\s", session) is None, "weak frozen session contract")

    regular = root / "secret-regular.ttf"
    bold = root / "secret-bold.ttf"
    write_fixture_font(regular, weight=400, family=secrets[0])
    write_fixture_font(bold, weight=700, family=secrets[1])
    regular_bytes = regular.read_bytes()
    bold_bytes = bold.read_bytes()


    def post_font(path: str, payload: bytes, extra: dict[str, str] | None = None) -> dict[str, object]:
        headers = {
            "Content-Type": "application/octet-stream",
            "X-FontBlind-Session": session,
            **(extra or {}),
        }
        status, _response_headers, response = request("POST", path, payload, headers)
        require(status == 200, f"frozen workbench failed at {path}: {status} {response[:200]!r}")
        no_source_identity(response, f"public result from {path}")
        value = json.loads(response)
        require(value.get("ok") is True, f"frozen workbench returned no success at {path}")
        require(isinstance(value.get("job"), str) and re.fullmatch(r"[a-f0-9]{32}", value["job"]), "invalid job token")
        return value


    def exact_checks(value: dict[str, object], expected: set[str]) -> None:
        checks = value.get("checks")
        require(isinstance(checks, dict), "public result omitted proof")
        require(checks == {key: True for key in expected}, "public result returned the wrong proof contract")


    def download(value: dict[str, object], label: str) -> dict[str, pathlib.Path]:
        paths: dict[str, pathlib.Path] = {}
        for kind in ("native", "web", "css", "bundle"):
            item = value.get(kind)
            require(isinstance(item, dict), f"{label} omitted {kind}")
            url = item.get("url")
            filename = item.get("filename")
            require(
                isinstance(url, str)
                and re.fullmatch(r"/download/[a-f0-9]{32}/(native|web|css|bundle)", url)
                and isinstance(filename, str),
                f"{label} returned an invalid {kind} descriptor",
            )
            # The runtime deliberately permits only two simultaneous
            # sealed snapshots. A fast local test client can receive EOF before
            # the server thread reaches its semaphore release, so honour the
            # authored 429 back-pressure for a bounded one-second window.
            for _attempt in range(50):
                status, headers, payload = request("GET", url)
                if status != 429:
                    break
                time.sleep(0.02)
            require(
                status == 200,
                f"{label} {kind} download failed: {status} {payload[:200]!r}",
            )
            require(headers.get("Cache-Control") == "no-store, max-age=0", f"{label} {kind} can be cached")
            require(headers.get("Content-Disposition") == f'attachment; filename="{filename}"', f"{label} {kind} filename drifted")
            no_source_identity(payload, f"{label} {kind}")
            path = root / f"{label}-{filename}"
            path.write_bytes(payload)
            paths[kind] = path

        css = paths["css"].read_text(encoding="utf-8")
        require(css.count("@font-face") == 1 and "local(" not in css.casefold(), f"{label} CSS is unsafe")
        require(value["web"]["filename"] in css, f"{label} CSS does not reference its WOFF2")
        with zipfile.ZipFile(paths["bundle"], "r") as archive:
            expected_names = [value[kind]["filename"] for kind in ("native", "web", "css")]
            require(archive.namelist() == expected_names, f"{label} package manifest drifted")
            for kind, name in zip(("native", "web", "css"), expected_names):
                require(archive.read(name) == paths[kind].read_bytes(), f"{label} package changed {kind}")
        return paths


    def delete_job(value: dict[str, object]) -> None:
        token = value["job"]
        status, _headers, payload = request(
            "DELETE",
            f"/api/jobs/{token}",
            headers={"X-FontBlind-Session": session},
        )
        require(status == 200 and json.loads(payload).get("deleted") is True, "frozen job cleanup failed")


    blind = post_font("/api/process", regular_bytes)
    exact_checks(
        blind,
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "outline_flavor_retained",
            "functional_clone_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "source_discarded",
        },
    )
    blind_paths = download(blind, "blind")
    for kind in ("native", "web"):
        font = TTFont(str(blind_paths[kind]), lazy=False)
        try:
            require("glyf" in font and "fvar" not in font, "Blind changed the fixture font model")
        finally:
            font.close()
    delete_job(blind)

    oblique = post_font(
        "/api/lab/oblique",
        regular_bytes,
        {"X-FontBlind-Angle": "12", "X-FontBlind-Output": "static"},
    )
    exact_checks(
        oblique,
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "declared_shear_verified",
            "oblique_not_italic_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "source_discarded",
        },
    )
    oblique_paths = download(oblique, "oblique")
    font = TTFont(str(oblique_paths["native"]), lazy=False)
    try:
        require(bool(int(font["OS/2"].fsSelection) & 0x0200), "static Oblique omitted its Oblique bit")
        require(not bool(int(font["OS/2"].fsSelection) & 0x0001), "static Oblique claimed Italic")
    finally:
        font.close()
    delete_job(oblique)

    slant = post_font(
        "/api/lab/oblique",
        regular_bytes,
        {"X-FontBlind-Angle": "12", "X-FontBlind-Output": "slnt"},
    )
    exact_checks(
        slant,
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "declared_shear_verified",
            "slant_axis_verified",
            "variable_endpoints_verified",
            "oblique_not_italic_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "source_discarded",
        },
    )
    require([axis["tag"] for axis in slant.get("axes", [])] == ["slnt"], "slant workbench exposed the wrong axis")
    require(len(slant.get("masters", [])) == 2, "slant workbench exposed the wrong master map")
    slant_paths = download(slant, "slant")
    font = TTFont(str(slant_paths["native"]), lazy=False)
    try:
        require([str(axis.axisTag) for axis in font["fvar"].axes] == ["slnt"], "frozen slant output lost its axis")
    finally:
        font.close()
    delete_job(slant)

    font_set = bytearray(b"FBLAB1\x00\x00")
    font_set.extend(struct.pack(">B", 2))
    font_set.extend(struct.pack(">I", len(regular_bytes)))
    font_set.extend(struct.pack(">I", len(bold_bytes)))
    font_set.extend(regular_bytes)
    font_set.extend(bold_bytes)
    status, _headers, variable_payload = request(
        "POST",
        "/api/lab/variable",
        bytes(font_set),
        {
            "Content-Type": "application/vnd.fontblind.font-set",
            "X-FontBlind-Session": session,
        },
    )
    require(status == 200, f"frozen Variable Lab failed: {status} {variable_payload[:200]!r}")
    no_source_identity(variable_payload, "Variable Lab public result")
    variable = json.loads(variable_payload)
    exact_checks(
        variable,
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "donor_compatibility_verified",
            "donor_instances_verified",
            "independent_axis_model_verified",
            "axis_metadata_verified",
            "hinting_removed",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "weight_axis_verified",
            "source_discarded",
        },
    )
    require([axis["tag"] for axis in variable.get("axes", [])] == ["wght"], "Variable Lab exposed the wrong axis")
    require(len(variable.get("masters", [])) == 2, "Variable Lab exposed the wrong master map")
    variable_paths = download(variable, "variable")
    font = TTFont(str(variable_paths["native"]), lazy=False)
    try:
        require([str(axis.axisTag) for axis in font["fvar"].axes] == ["wght"], "Variable Lab output lost its weight axis")
    finally:
        font.close()

    status, _headers, instance_payload = request(
        "POST",
        f"/api/jobs/{variable['job']}/instance",
        json.dumps({"location": {"wght": 550}}).encode("utf-8"),
        {"Content-Type": "application/json", "X-FontBlind-Session": session},
    )
    require(status == 200, f"frozen static export failed: {status} {instance_payload[:200]!r}")
    no_source_identity(instance_payload, "static export public result")
    instance = json.loads(instance_payload)
    require(instance.get("location") == {"wght": 550.0}, "static export confirmed the wrong location")
    require("axes" not in instance and "masters" not in instance, "static export remained variable in public data")
    exact_checks(
        instance,
        {
            "source_identity_removed",
            "embedding_flags_cleared",
            "selected_location_verified",
            "static_instance_verified",
            "variation_tables_removed",
            "axis_metadata_verified",
            "harfbuzz_shaping_verified",
            "woff2_roundtrip_verified",
            "source_discarded",
        },
    )
    instance_paths = download(instance, "instance")
    for kind in ("native", "web"):
        font = TTFont(str(instance_paths[kind]), lazy=False)
        try:
            require(not ({"avar", "cvar", "fvar", "gvar", "HVAR", "MVAR", "STAT", "VVAR"} & set(font.keys())), "static export retained variation tables")
            require(int(font["OS/2"].usWeightClass) == 550, "static export emitted the wrong weight")
        finally:
            font.close()

    # The parent is authoritative: deleting it must invalidate the child package.
    delete_job(variable)
    status, _headers, _payload = request("GET", instance["native"]["url"])
    require(status == 404, "a frozen child survived deletion of its variable parent")
    status, _headers, _payload = request("GET", variable["native"]["url"])
    require(status == 404, "a deleted variable parent remained downloadable")


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
            assert_strict_output(native, source)
            _decode_woff2(corpus_paths["web"], decoded)
            assert_strict_output(decoded, source)
            source_shape = _harfbuzz_shape(source, probes[script])
            require(len(source_shape) > 1 and any(glyph_id != 0 for glyph_id, *_rest in source_shape), f"release corpus probe {asset_id} did not shape")
            require(source_shape == _harfbuzz_shape(native, probes[script]), f"release corpus native shaping drifted for {asset_id}")
            require(source_shape == _harfbuzz_shape(decoded, probes[script]), f"release corpus WOFF2 shaping drifted for {asset_id}")
            delete_job(corpus_result)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
