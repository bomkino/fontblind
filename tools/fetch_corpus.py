#!/usr/bin/env python3
"""Fetch and verify FontBlind's pinned, open-licensed release corpus."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePath
from typing import Iterable
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "tests" / "corpus" / "manifest.json"
DEFAULT_OUTPUT = ROOT / "tests" / "corpus" / "cache"
ALLOWED_HOSTS = frozenset({"raw.githubusercontent.com"})
CHUNK_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class CorpusError(RuntimeError):
    """The pinned corpus could not be fetched or verified safely."""


def _load_manifest(path: Path) -> tuple[dict[str, object], ...]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusError("The corpus manifest is unreadable.") from exc
    if not isinstance(value, dict) or value.get("schema") != 1 or not isinstance(value.get("assets"), list):
        raise CorpusError("The corpus manifest has an unsupported schema.")

    assets: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for raw in value["assets"]:
        if not isinstance(raw, dict):
            raise CorpusError("The corpus manifest contains a malformed asset.")
        required = {
            "id",
            "filename",
            "role",
            "script",
            "source_repository",
            "source_commit",
            "source_path",
            "url",
            "size",
            "sha256",
            "license",
            "license_url",
        }
        if set(raw) != required:
            raise CorpusError("The corpus manifest contains an unexpected field set.")

        asset_id = raw["id"]
        filename = raw["filename"]
        url = raw["url"]
        license_url = raw["license_url"]
        digest = raw["sha256"]
        size = raw["size"]
        if (
            not isinstance(asset_id, str)
            or not asset_id
            or asset_id in seen_ids
            or not isinstance(filename, str)
            or PurePath(filename).name != filename
            or filename in seen_names
            or not isinstance(url, str)
            or not isinstance(license_url, str)
            or not isinstance(digest, str)
            or SHA256_PATTERN.fullmatch(digest) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 < size <= MAX_ASSET_BYTES
        ):
            raise CorpusError("The corpus manifest contains an unsafe asset descriptor.")
        for remote in (url, license_url):
            parsed = urlsplit(remote)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
                raise CorpusError("The corpus manifest points outside the reviewed upstream host.")
        for text_field in required - {"size"}:
            if not isinstance(raw[text_field], str) or not raw[text_field]:
                raise CorpusError("The corpus manifest contains an empty text field.")
        seen_ids.add(asset_id)
        seen_names.add(filename)
        assets.append(dict(raw))
    if not assets:
        raise CorpusError("The corpus manifest contains no assets.")
    return tuple(assets)


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
                size += len(block)
                digest.update(block)
    except OSError as exc:
        raise CorpusError("A corpus asset could not be read.") from exc
    return size, digest.hexdigest()


def _verify(path: Path, asset: dict[str, object]) -> None:
    if path.is_symlink() or not path.is_file():
        raise CorpusError(f"Corpus asset {asset['id']} is missing or not a regular file.")
    size, digest = _digest_file(path)
    if size != asset["size"] or digest != asset["sha256"]:
        raise CorpusError(f"Corpus asset {asset['id']} failed its pinned size or SHA-256 check.")


def _download(asset: dict[str, object], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(
        str(asset["url"]),
        headers={"User-Agent": "FontBlind-release-corpus/1"},
        method="GET",
    )
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".download",
            dir=str(destination.parent),
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            digest = hashlib.sha256()
            written = 0
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None:
                        try:
                            declared = int(content_length)
                        except ValueError as exc:
                            raise CorpusError(f"Corpus asset {asset['id']} returned an invalid length.") from exc
                        if declared != asset["size"]:
                            raise CorpusError(f"Corpus asset {asset['id']} returned an unexpected length.")
                    while True:
                        block = response.read(CHUNK_BYTES)
                        if not block:
                            break
                        written += len(block)
                        if written > asset["size"] or written > MAX_ASSET_BYTES:
                            raise CorpusError(f"Corpus asset {asset['id']} exceeded its pinned size.")
                        temporary.write(block)
                        digest.update(block)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise CorpusError(f"Corpus asset {asset['id']} could not be downloaded.") from exc
            temporary.flush()
            os.fsync(temporary.fileno())
        if written != asset["size"] or digest.hexdigest() != asset["sha256"]:
            raise CorpusError(f"Corpus asset {asset['id']} failed its pinned size or SHA-256 check.")
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def fetch_corpus(
    manifest: Path = DEFAULT_MANIFEST,
    output: Path = DEFAULT_OUTPUT,
    *,
    verify_only: bool = False,
    selected_ids: Iterable[str] = (),
) -> tuple[Path, ...]:
    assets = _load_manifest(Path(manifest))
    requested = set(selected_ids)
    known = {str(asset["id"]) for asset in assets}
    unknown = requested - known
    if unknown:
        raise CorpusError("Unknown corpus asset id: " + ", ".join(sorted(unknown)))

    output = Path(output)
    completed: list[Path] = []
    for asset in assets:
        asset_id = str(asset["id"])
        if requested and asset_id not in requested:
            continue
        destination = output / str(asset["filename"])
        if verify_only:
            _verify(destination, asset)
        else:
            try:
                _verify(destination, asset)
            except CorpusError:
                destination.unlink(missing_ok=True)
                _download(asset, destination)
                _verify(destination, asset)
        completed.append(destination)
    return tuple(completed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--only", action="append", default=[], metavar="ASSET_ID")
    arguments = parser.parse_args(argv)
    try:
        completed = fetch_corpus(
            arguments.manifest,
            arguments.output,
            verify_only=arguments.verify_only,
            selected_ids=arguments.only,
        )
    except CorpusError as exc:
        parser.exit(1, f"fontblind corpus: {exc}\n")
    for path in completed:
        print(f"verified corpus asset: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
