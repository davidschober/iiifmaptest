"""Discover objects and images in the ``originals/`` directory.

Conventions:

* ``originals/photo.jpg``  -> a single-image object, optional sidecar ``photo.yml``
* ``originals/ledger/``    -> a compound object of every image in the folder
                              (recursively), optional sidecar ``ledger/object.yml``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
SIDECAR_EXTENSIONS = (".yml", ".yaml", ".json")
OBJECT_SIDECAR_STEMS = ("object", "metadata")

# Metadata keys copied from a sidecar onto the manifest, in IIIF Presentation
# 3.0 spelling. Everything else in a sidecar is reported as unknown.
MANIFEST_KEYS = {
    "label",
    "summary",
    "metadata",
    "required_statement",
    "rights",
    "provider",
    "behavior",
    "viewing_direction",
    "nav_date",
    "see_also",
    "homepage",
    "images",
}

# The repository's single collection is described the same way, except that
# "images" makes no sense for it and it may nominate a cover object.
COLLECTION_KEYS = (MANIFEST_KEYS - {"images"}) | {"thumbnail"}


class DiscoveryError(Exception):
    """Raised when the originals directory cannot be turned into IIIF objects."""


@dataclass
class SourceImage:
    path: Path
    image_id: str
    label: str | None = None


@dataclass
class SourceObject:
    object_id: str
    label: str
    images: list[SourceImage]
    compound: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def slugify(value: str) -> str:
    """Make a filename safe and predictable inside a URL path segment."""
    slug = value.strip().lower().replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    return slug or "untitled"


def prettify(stem: str) -> str:
    """Turn ``ships-log_1846`` into ``Ships Log 1846`` for a default label."""
    words = [w for w in re.split(r"[-_\s]+", stem.strip()) if w]
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in words) or stem


def natural_key(value: str) -> list[Any]:
    """Sort ``page2`` before ``page10``."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and not path.name.startswith(".")


def _load_sidecar(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)  # YAML is a superset of JSON, so .json works too
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise DiscoveryError(f"{path}: expected a mapping of metadata keys at the top level")
    return data


def _find_sidecar(candidates: list[Path]) -> tuple[dict[str, Any], Path | None]:
    for candidate in candidates:
        if candidate.exists():
            return _load_sidecar(candidate), candidate
    return {}, None


def _sidecar_for_file(image: Path) -> tuple[dict[str, Any], Path | None]:
    return _find_sidecar([image.with_suffix(ext) for ext in SIDECAR_EXTENSIONS])


def _sidecar_for_folder(folder: Path) -> tuple[dict[str, Any], Path | None]:
    inside = [folder / f"{stem}{ext}" for stem in OBJECT_SIDECAR_STEMS for ext in SIDECAR_EXTENSIONS]
    sibling = [folder.with_suffix(ext) for ext in SIDECAR_EXTENSIONS]
    return _find_sidecar(inside + sibling)


def _ordered_images(folder: Path, spec: Any, warnings: list[str]) -> list[tuple[Path, str | None]]:
    """Order a compound object's images, honouring an optional ``images:`` list."""
    found = sorted((p for p in folder.rglob("*") if is_image(p)), key=lambda p: natural_key(str(p.relative_to(folder))))
    by_relpath = {str(p.relative_to(folder)): p for p in found}
    ordered: list[tuple[Path, str | None]] = []
    used: set[str] = set()

    for entry in spec or []:
        if isinstance(entry, str):
            entry = {"file": entry}
        if not isinstance(entry, dict) or "file" not in entry:
            raise DiscoveryError(f"{folder}: each 'images' entry must be a filename or a mapping with a 'file' key")
        relpath = str(entry["file"]).lstrip("./")
        match = by_relpath.get(relpath)
        if match is None:
            warnings.append(f"{folder.name}: 'images' lists {relpath!r}, which is not an image in this folder")
            continue
        if relpath in used:
            warnings.append(f"{folder.name}: 'images' lists {relpath!r} more than once; ignoring the repeat")
            continue
        used.add(relpath)
        label = entry.get("label")
        ordered.append((match, str(label) if label is not None else None))

    leftovers = [(p, None) for rel, p in by_relpath.items() if rel not in used]
    if used and leftovers:
        warnings.append(
            f"{folder.name}: {len(leftovers)} image(s) are not listed in 'images' and were added after the listed ones"
        )
    ordered.extend(sorted(leftovers, key=lambda pair: natural_key(str(pair[0].relative_to(folder)))))
    return ordered


def _split_metadata(
    raw: dict[str, Any],
    source: Path | None,
    warnings: list[str],
    allowed: set[str] = MANIFEST_KEYS,
) -> dict[str, Any]:
    if source is not None:
        unknown = sorted(set(raw) - allowed)
        if unknown:
            warnings.append(f"{source.name}: ignoring unrecognised key(s): {', '.join(unknown)}")
    return {k: v for k, v in raw.items() if k in allowed}


def discover_collection(path: Path) -> tuple[dict[str, Any], list[str]]:
    """Load the sidecar describing this repository's collection.

    Uses the same keys and the same YAML/JSON handling as an object sidecar, so
    ``collection.yml`` reads like ``ledger/object.yml``. Returns empty metadata
    when there is no sidecar, in which case the collection keeps its defaults.
    """
    warnings: list[str] = []
    candidates = [path, *(path.with_suffix(ext) for ext in SIDECAR_EXTENSIONS)]
    seen: list[Path] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    raw, source = _find_sidecar(seen)
    return _split_metadata(raw, source, warnings, COLLECTION_KEYS), warnings


def discover(originals: Path) -> tuple[list[SourceObject], list[str]]:
    """Build the list of IIIF objects represented by ``originals/``.

    Returns the objects plus any warnings that are not about one object in
    particular (files that are neither images nor metadata, say).
    """
    if not originals.exists():
        raise DiscoveryError(f"{originals} does not exist; create it and add images")

    objects: list[SourceObject] = []
    notes: list[str] = []
    entries = sorted(originals.iterdir(), key=lambda p: natural_key(p.name))

    for entry in entries:
        if entry.name.startswith("."):
            continue

        if entry.is_dir():
            warnings: list[str] = []
            raw, sidecar = _sidecar_for_folder(entry)
            meta = _split_metadata(raw, sidecar, warnings)
            pairs = _ordered_images(entry, meta.get("images"), warnings)
            if not pairs:
                warnings.append(f"{entry.name}: folder contains no images; skipping")
                objects.append(SourceObject(slugify(entry.name), prettify(entry.name), [], True, meta, warnings))
                continue
            object_id = slugify(entry.name)
            images = []
            for path, label in pairs:
                relpath = path.relative_to(entry)
                parts = [slugify(part) for part in relpath.parent.parts if part != "."]
                image_id = "/".join([object_id, *parts, slugify(relpath.stem)])
                images.append(SourceImage(path, image_id, label or prettify(relpath.stem)))
            objects.append(
                SourceObject(object_id, str(meta.get("label") or prettify(entry.name)), images, True, meta, warnings)
            )

        elif is_image(entry):
            warnings = []
            raw, sidecar = _sidecar_for_file(entry)
            meta = _split_metadata(raw, sidecar, warnings)
            if "images" in meta:
                warnings.append(f"{entry.name}: 'images' only applies to folder objects; ignoring it")
                meta.pop("images")
            object_id = slugify(entry.stem)
            label = str(meta.get("label") or prettify(entry.stem))
            images = [SourceImage(entry, object_id, label)]
            objects.append(SourceObject(object_id, label, images, False, meta, warnings))

        elif entry.is_file() and entry.suffix.lower() not in SIDECAR_EXTENSIONS and entry.suffix.lower() != ".md":
            # Not an image, not metadata: worth mentioning but not fatal.
            notes.append(f"{entry.name}: not a supported image type; skipping")

    _check_collisions(objects)
    return objects, notes


def _check_collisions(objects: list[SourceObject]) -> None:
    seen_objects: dict[str, str] = {}
    for obj in objects:
        if obj.object_id in seen_objects:
            raise DiscoveryError(
                f"Two originals produce the same identifier {obj.object_id!r} "
                f"({seen_objects[obj.object_id]} and {obj.label}). Rename one of them."
            )
        seen_objects[obj.object_id] = obj.label

        seen_images: dict[str, Path] = {}
        for image in obj.images:
            if image.image_id in seen_images:
                raise DiscoveryError(
                    f"Two images produce the same identifier {image.image_id!r} "
                    f"({seen_images[image.image_id]} and {image.path}). Rename one of them."
                )
            seen_images[image.image_id] = image.path
