"""Verify a built site really is a usable IIIF level 0 service.

Two independent passes:

1. Every absolute URL inside the generated JSON that points at this site must
   resolve to a file that exists.
2. A re-implementation of OpenSeadragon's IIIF tile URL construction (the
   client behind Mirador, Clover and most other viewers) must only produce
   URLs that exist. This is deliberately written from the viewer's formulas
   rather than reusing the tiling code, so a mistake in one shows up here.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

from .images import plan_derivatives, scale_factors


@dataclass
class CheckResult:
    checked_urls: int = 0
    manifests: int = 0
    services: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _url_to_path(url: str, base_url: str, out: Path) -> Path | None:
    if not url.startswith(base_url):
        return None
    remainder = urlsplit(url[len(base_url) :]).path.lstrip("/")
    return out / unquote(remainder)


def _iter_urls(node: Any) -> Iterator[str]:
    """Yield every ``id`` value in a JSON document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "id" and isinstance(value, str):
                yield value
            else:
                yield from _iter_urls(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_urls(item)


def osd_tile_urls(info: dict[str, Any]) -> Iterator[str]:
    """Reproduce OpenSeadragon's requests for a version 3 level 0 service."""
    width, height = info["width"], info["height"]
    tiles = info["tiles"][0]
    tile_w = tiles["width"]
    tile_h = tiles.get("height", tile_w)
    factors = sorted(tiles["scaleFactors"])

    for factor in factors:
        scale = 1 / factor
        level_w, level_h = math.ceil(width * scale), math.ceil(height * scale)

        if level_w < tile_w and level_h < tile_h:
            size = "max" if (level_w, level_h) == (width, height) else f"{level_w},{level_h}"
            yield f"full/{size}/0/default.jpg"
            continue

        for row in range(max(1, math.ceil(level_h / tile_h))):
            for col in range(max(1, math.ceil(level_w / tile_w))):
                region_x = col * round(tile_w / scale)
                region_y = row * round(tile_h / scale)
                region_w = min(round(tile_w / scale), width - region_x)
                region_h = min(round(tile_h / scale), height - region_y)
                if region_w <= 0 or region_h <= 0:
                    continue
                if (col, row) == (0, 0) and (region_w, region_h) == (width, height):
                    region = "full"
                else:
                    region = f"{region_x},{region_y},{region_w},{region_h}"

                # Older OpenSeadragon releases scale the region; newer ones
                # subtract within the level. They must agree.
                size_w_a, size_h_a = math.ceil(region_w * scale), math.ceil(region_h * scale)
                size_w_b = min(tile_w, level_w - col * tile_w)
                size_h_b = min(tile_h, level_h - row * tile_h)
                if (size_w_a, size_h_a) != (size_w_b, size_h_b):
                    raise AssertionError(
                        f"tile size disagreement at factor {factor} tile ({col},{row}): "
                        f"{(size_w_a, size_h_a)} vs {(size_w_b, size_h_b)}"
                    )
                size = "max" if (size_w_a, size_h_a) == (width, height) else f"{size_w_a},{size_h_a}"
                yield f"{region}/{size}/0/default.jpg"


def check(*, out: Path, base_url: str) -> CheckResult:
    result = CheckResult()
    collection_path = out / "iiif" / "collection.json"
    if not collection_path.exists():
        result.errors.append("iiif/collection.json is missing; has the site been built?")
        return result

    documents = [collection_path]
    documents += sorted((out / "iiif" / "manifests").glob("*.json"))
    documents += sorted((out / "iiif" / "images").rglob("info.json"))

    for document in documents:
        if document.name == "index.json":
            continue
        try:
            payload = json.loads(document.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            result.errors.append(f"{document.relative_to(out)}: invalid JSON ({exc})")
            continue

        if payload.get("type") == "Manifest":
            result.manifests += 1
            _check_manifest(payload, document, out, result)
        elif payload.get("type") == "ImageService3":
            result.services += 1
            _check_service(payload, document, out, base_url, result)

        for url in _iter_urls(payload):
            target = _url_to_path(url, base_url, out)
            if target is None:
                continue
            # Canvases and annotations are identifiers, not files; image
            # service ids are directories holding info.json.
            relative = target.relative_to(out).as_posix() if target != out else ""
            if relative.startswith("iiif/canvases/"):
                continue
            result.checked_urls += 1
            if target.is_dir():
                if not (target / "info.json").exists():
                    result.errors.append(f"{document.relative_to(out)}: {url} has no info.json")
            elif not target.exists():
                result.errors.append(f"{document.relative_to(out)}: {url} does not exist in the build")

    return result


def _check_manifest(manifest: dict[str, Any], document: Path, out: Path, result: CheckResult) -> None:
    if not manifest.get("items"):
        result.errors.append(f"{document.relative_to(out)}: manifest has no canvases")
    for canvas in manifest.get("items", []):
        if not canvas.get("width") or not canvas.get("height"):
            result.errors.append(f"{document.relative_to(out)}: canvas {canvas.get('id')} is missing width/height")
        annotations = canvas.get("items", [{}])[0].get("items", [])
        if not annotations:
            result.errors.append(f"{document.relative_to(out)}: canvas {canvas.get('id')} paints nothing")


def _check_service(info: dict[str, Any], document: Path, out: Path, base_url: str, result: CheckResult) -> None:
    service_dir = document.parent
    label = service_dir.relative_to(out).as_posix()

    if info.get("profile") != "level0":
        result.errors.append(f"{label}: profile is {info.get('profile')!r}, expected 'level0'")
    if info.get("@context") != "http://iiif.io/api/image/3/context.json":
        result.errors.append(f"{label}: unexpected @context {info.get('@context')!r}")

    identifier = service_dir.relative_to(out / "iiif" / "images").as_posix()
    expected_id = f"{base_url}/iiif/images/{identifier}"
    if info.get("id") != expected_id:
        result.errors.append(f"{label}: info.json id is {info.get('id')!r}, expected {expected_id!r}")

    # Level 0 must always serve the unscaled full image.
    if not (service_dir / "full" / "max" / "0" / "default.jpg").exists():
        result.errors.append(f"{label}: full/max/0/default.jpg is missing (required at level 0)")

    # Everything advertised in sizes must exist, in canonical w,h form.
    width, height = info["width"], info["height"]
    for size in info.get("sizes", []):
        w, h = size["width"], size["height"]
        name = "max" if (w, h) == (width, height) else f"{w},{h}"
        target = service_dir / "full" / name / "0" / "default.jpg"
        result.checked_urls += 1
        if not target.exists():
            result.errors.append(f"{label}: size {w},{h} is advertised but full/{name}/0/default.jpg is missing")

    # Everything a viewer would ask for must exist.
    try:
        tile_urls = list(osd_tile_urls(info))
    except AssertionError as exc:
        result.errors.append(f"{label}: {exc}")
        tile_urls = []
    for rel in tile_urls:
        result.checked_urls += 1
        if not (service_dir / rel).exists():
            result.errors.append(f"{label}: viewer would request {rel}, which is missing")

    # And nothing else should be taking up space.
    tile_size = info["tiles"][0]["width"]
    planned = {d.rel_path for d in plan_derivatives(width, height, tile_size, scale_factors(width, height, tile_size))}
    actual = {p.relative_to(service_dir).as_posix() for p in service_dir.rglob("default.jpg")}
    for stray in sorted(actual - planned):
        result.warnings.append(f"{label}: unexpected file {stray} (left over from an earlier build?)")
    for missing in sorted(planned - actual):
        result.errors.append(f"{label}: planned file {missing} was not written")
