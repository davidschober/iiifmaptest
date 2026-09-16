"""Turn ``originals/`` into a static IIIF site."""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config
from .discover import discover, discover_collection, slugify
from .images import SourceImageError, image_info, write_derivatives
from .presentation import (
    DEFAULT_COLLECTION_LABEL,
    build_collection,
    build_manifest,
    manifest_url,
    plain_text,
    service_url,
)
from .site import write_site_files

STATE_VERSION = 2
# GitHub's published limits for Pages sites and individual files in a repo.
FILE_WARN_BYTES = 90 * 1024 * 1024
SITE_WARN_BYTES = 900 * 1024 * 1024


@dataclass
class BuildResult:
    objects: int = 0
    images: int = 0
    images_built: int = 0
    images_reused: int = 0
    files_written: int = 0
    total_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _params_key(cfg: Config) -> str:
    payload = json.dumps(cfg.derivative_params, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": STATE_VERSION, "images": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "images": {}}
    if state.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "images": {}}
    state.setdefault("images", {})
    return state


def _generate_one(task: tuple[str, str, str, int, int]) -> tuple[str, int, int]:
    """Worker entry point: build every derivative for a single image."""
    image_id, source, out_dir, tile_size, jpeg_quality = task
    dims = write_derivatives(
        Path(source),
        Path(out_dir),
        tile_size=tile_size,
        jpeg_quality=jpeg_quality,
    )
    return image_id, dims.width, dims.height


def _prune_images(images_root: Path, expected: set[Path]) -> None:
    """Delete derivative directories for images that no longer exist."""
    if not images_root.exists():
        return
    for info in sorted(images_root.rglob("info.json"), key=lambda p: len(p.parts), reverse=True):
        if info.parent not in expected:
            shutil.rmtree(info.parent, ignore_errors=True)
    for directory in sorted((p for p in images_root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()


def _prune_manifests(manifests_root: Path, expected: set[Path]) -> None:
    if not manifests_root.exists():
        return
    for existing in manifests_root.glob("*.json"):
        if existing not in expected and existing.name != "index.json":
            existing.unlink()


def build(
    *,
    originals: Path,
    out: Path,
    cfg: Config,
    base_url: str,
    site_dir: Path | None = None,
    collection_file: Path | None = None,
    cache_dir: Path | None = None,
    jobs: int = 1,
    force: bool = False,
) -> BuildResult:
    result = BuildResult()
    result.warnings.extend(cfg.warnings)

    collection_metadata: dict[str, Any] = {}
    if collection_file is not None:
        collection_metadata, collection_notes = discover_collection(collection_file)
        result.warnings.extend(collection_notes)
    collection_label = plain_text(collection_metadata.get("label"), DEFAULT_COLLECTION_LABEL)

    objects, notes = discover(originals)
    result.warnings.extend(notes)
    for obj in objects:
        result.warnings.extend(obj.warnings)

    objects = [obj for obj in objects if obj.images]
    images_root = out / "iiif" / "images"
    manifests_root = out / "iiif" / "manifests"
    images_root.mkdir(parents=True, exist_ok=True)
    manifests_root.mkdir(parents=True, exist_ok=True)

    cache_dir = cache_dir or (out.parent / ".iiif-cache")
    state_path = cache_dir / "state.json"
    state = _load_state(state_path)
    params_key = _params_key(cfg)

    # Decide which images need (re)generating.
    tasks: list[tuple[str, str, str, int, int]] = []
    dimensions: dict[str, tuple[int, int]] = {}
    digests: dict[str, str] = {}
    for obj in objects:
        for image in obj.images:
            digest = file_digest(image.path)
            digests[image.image_id] = digest
            cached = state["images"].get(image.image_id)
            out_dir = images_root / image.image_id
            reusable = (
                not force
                and cached is not None
                and cached.get("sha256") == digest
                and cached.get("params") == params_key
                and cached.get("width")
                and cached.get("height")
                and (out_dir / "full" / "max" / "0" / "default.jpg").exists()
            )
            if reusable:
                dimensions[image.image_id] = (cached["width"], cached["height"])
                result.images_reused += 1
            else:
                shutil.rmtree(out_dir, ignore_errors=True)
                tasks.append(
                    (image.image_id, str(image.path), str(out_dir), cfg.tile_size, cfg.jpeg_quality)
                )

    if tasks:
        if jobs > 1 and len(tasks) > 1:
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                for image_id, width, height in pool.map(_generate_one, tasks):
                    dimensions[image_id] = (width, height)
                    result.images_built += 1
        else:
            for task in tasks:
                image_id, width, height = _generate_one(task)
                dimensions[image_id] = (width, height)
                result.images_built += 1

    # info.json is cheap and embeds the base URL, so it is always rewritten.
    expected_image_dirs: set[Path] = set()
    for obj in objects:
        for image in obj.images:
            width, height = dimensions[image.image_id]
            out_dir = images_root / image.image_id
            expected_image_dirs.add(out_dir)
            info = image_info(
                service_id=service_url(base_url, image.image_id),
                width=width,
                height=height,
                tile_size=cfg.tile_size,
                rights=obj.metadata.get("rights") or cfg.rights,
            )
            _write_json(out_dir / "info.json", info)

    entries: list[dict[str, Any]] = []
    expected_manifests: set[Path] = set()
    for obj in objects:
        images = [
            {
                "image_id": image.image_id,
                "label": image.label if obj.compound else None,
                "width": dimensions[image.image_id][0],
                "height": dimensions[image.image_id][1],
            }
            for image in obj.images
        ]
        manifest = build_manifest(
            base_url=base_url,
            object_id=obj.object_id,
            label=obj.label,
            images=images,
            metadata=obj.metadata,
            cfg=cfg,
            collection_label=collection_label,
        )
        target = manifests_root / f"{obj.object_id}.json"
        expected_manifests.add(target)
        _write_json(target, manifest)
        entries.append({"object": obj, "manifest": manifest})
        result.images += len(images)

    collection_metadata = _resolve_collection_thumbnail(collection_metadata, entries, result.warnings)
    collection = build_collection(base_url=base_url, entries=entries, cfg=cfg, metadata=collection_metadata)
    _write_json(out / "iiif" / "collection.json", collection)

    _prune_images(images_root, expected_image_dirs)
    _prune_manifests(manifests_root, expected_manifests)

    write_site_files(
        out=out,
        site_dir=site_dir,
        base_url=base_url,
        title=collection_label,
        summary=plain_text(collection_metadata.get("summary")),
        manifest_urls=[manifest_url(base_url, obj.object_id) for obj in objects],
        image_service_urls=[service_url(base_url, i.image_id) for obj in objects for i in obj.images],
    )

    state["images"] = {
        image.image_id: {
            "sha256": digests[image.image_id],
            "params": params_key,
            "width": dimensions[image.image_id][0],
            "height": dimensions[image.image_id][1],
        }
        for obj in objects
        for image in obj.images
    }
    state["builder_version"] = __version__
    cache_dir.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    result.objects = len(objects)
    for path in out.rglob("*"):
        if path.is_file():
            size = path.stat().st_size
            result.files_written += 1
            result.total_bytes += size
            if size > FILE_WARN_BYTES:
                result.warnings.append(
                    f"{path.relative_to(out)} is {size / 1e6:.0f} MB; GitHub blocks files over 100 MB"
                )
    if result.total_bytes > SITE_WARN_BYTES:
        result.warnings.append(
            f"the built site is {result.total_bytes / 1e9:.2f} GB; GitHub Pages recommends staying under 1 GB"
        )
    return result


def _resolve_collection_thumbnail(
    metadata: dict[str, Any],
    entries: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    """Let ``thumbnail:`` in collection.yml name an object to use as the cover."""
    nominated = metadata.get("thumbnail")
    if not isinstance(nominated, str):
        return metadata  # absent, or already a IIIF thumbnail body

    wanted = slugify(nominated.rsplit(".", 1)[0] if "." in nominated else nominated)
    for entry in entries:
        if entry["object"].object_id == wanted:
            cover = entry["manifest"].get("thumbnail")
            if cover:
                return {**metadata, "thumbnail": cover}
            break
    available = ", ".join(entry["object"].object_id for entry in entries) or "none"
    warnings.append(
        f"collection.yml: 'thumbnail' names {nominated!r}, which is not one of this "
        f"collection's objects ({available}); leaving the collection without one"
    )
    return {k: v for k, v in metadata.items() if k != "thumbnail"}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
