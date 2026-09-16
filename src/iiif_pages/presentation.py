"""Build IIIF Presentation 3.0 manifests and the repository's collection."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from .config import Config
from .images import canonical_size, pick_thumbnail_size

PRESENTATION_CONTEXT = "http://iiif.io/api/presentation/3/context.json"
DEFAULT_COLLECTION_LABEL = "IIIF Collection"


class PresentationError(Exception):
    """Raised when sidecar metadata cannot be turned into valid IIIF."""


def language_map(value: Any, language: str) -> dict[str, list[str]]:
    """Wrap a plain string (or list) in a IIIF language map."""
    if isinstance(value, dict):  # already a language map
        return {k: v if isinstance(v, list) else [str(v)] for k, v in value.items()}
    values = value if isinstance(value, list) else [value]
    return {language: [str(v) for v in values]}


def plain_text(value: Any, default: str = "") -> str:
    """Pull a plain string out of a label that may be a language map."""
    if value is None:
        return default
    if isinstance(value, dict):
        first = next(iter(value.values()), None)
        if isinstance(first, list):
            return str(first[0]) if first else default
        return str(first) if first is not None else default
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value)


def _metadata_pairs(entries: Any, language: str, source: str) -> list[dict[str, Any]]:
    pairs = []
    if isinstance(entries, dict):
        entries = [{"label": k, "value": v} for k, v in entries.items()]
    for entry in entries or []:
        if not isinstance(entry, dict) or "label" not in entry or "value" not in entry:
            raise PresentationError(f"{source}: each 'metadata' entry needs a 'label' and a 'value'")
        pairs.append(
            {
                "label": language_map(entry["label"], language),
                "value": language_map(entry["value"], language),
            }
        )
    return pairs


def service_url(base_url: str, image_id: str) -> str:
    return f"{base_url}/iiif/images/{image_id}"


def manifest_url(base_url: str, object_id: str) -> str:
    return f"{base_url}/iiif/manifests/{object_id}.json"


def collection_url(base_url: str) -> str:
    return f"{base_url}/iiif/collection.json"


def viewer_url(base_url: str, object_id: str) -> str:
    return f"{base_url}/view.html?manifest={quote(manifest_url(base_url, object_id), safe='')}"


def thumbnail(base_url: str, image: dict[str, Any], cfg: Config) -> dict[str, Any]:
    """A thumbnail body pointing at one of the sizes declared in info.json."""
    width, height = image["width"], image["height"]
    thumb_w, thumb_h = pick_thumbnail_size(width, height, cfg.tile_size, cfg.thumbnail_width)
    size = canonical_size(thumb_w, thumb_h, width, height)
    service = service_url(base_url, image["image_id"])
    return {
        "id": f"{service}/full/{size}/0/default.jpg",
        "type": "Image",
        "format": "image/jpeg",
        "width": thumb_w,
        "height": thumb_h,
        "service": [
            {
                "id": service,
                "type": "ImageService3",
                "profile": "level0",
            }
        ],
    }


def apply_descriptive_properties(
    target: dict[str, Any],
    metadata: dict[str, Any],
    cfg: Config,
    *,
    default_label: str,
    source: str,
) -> None:
    """Write the descriptive properties shared by manifests and collections.

    Sidecars use the same keys whether they describe an object or the whole
    collection, so both are filled in here. ``rights``, ``required_statement``
    and ``provider`` fall back to the defaults in ``config.yml``.
    """
    language = cfg.language
    target["label"] = language_map(metadata.get("label") or default_label, language)

    if metadata.get("summary"):
        target["summary"] = language_map(metadata["summary"], language)
    if metadata.get("metadata"):
        target["metadata"] = _metadata_pairs(metadata["metadata"], language, source)

    statement = metadata.get("required_statement") or cfg.required_statement
    if statement:
        if not isinstance(statement, dict) or "value" not in statement:
            raise PresentationError(f"{source}: 'required_statement' needs a 'value' (and usually a 'label')")
        target["requiredStatement"] = {
            "label": language_map(statement.get("label", "Attribution"), language),
            "value": language_map(statement["value"], language),
        }

    rights = metadata.get("rights") or cfg.rights
    if rights:
        target["rights"] = str(rights)

    provider = metadata.get("provider") or cfg.provider
    if provider:
        entries = provider if isinstance(provider, list) else [provider]
        for entry in entries:
            if not isinstance(entry, dict) or "id" not in entry:
                raise PresentationError(f"{source}: each 'provider' entry needs at least an 'id'")
        target["provider"] = [
            {
                "type": "Agent",
                **entry,
                **({"label": language_map(entry["label"], language)} if entry.get("label") else {}),
            }
            for entry in entries
        ]

    if metadata.get("thumbnail"):
        thumbnails = metadata["thumbnail"]
        target["thumbnail"] = thumbnails if isinstance(thumbnails, list) else [thumbnails]

    if metadata.get("behavior"):
        behavior = metadata["behavior"]
        target["behavior"] = [str(b) for b in (behavior if isinstance(behavior, list) else [behavior])]
    if metadata.get("viewing_direction"):
        target["viewingDirection"] = str(metadata["viewing_direction"])
    if metadata.get("nav_date"):
        target["navDate"] = str(metadata["nav_date"])

    if metadata.get("homepage"):
        entries = metadata["homepage"]
        target["homepage"] = [
            {
                "id": entry["id"] if isinstance(entry, dict) else str(entry),
                "type": "Text",
                "format": "text/html",
                **(
                    {"label": language_map(entry["label"], language)}
                    if isinstance(entry, dict) and entry.get("label")
                    else {}
                ),
            }
            for entry in (entries if isinstance(entries, list) else [entries])
        ]

    if metadata.get("see_also"):
        entries = metadata["see_also"]
        target["seeAlso"] = [
            entry if isinstance(entry, dict) else {"id": str(entry), "type": "Dataset"}
            for entry in (entries if isinstance(entries, list) else [entries])
        ]


def canvas(base_url: str, object_id: str, index: int, image: dict[str, Any], cfg: Config) -> dict[str, Any]:
    canvas_id = f"{base_url}/iiif/canvases/{object_id}/{index}"
    service = service_url(base_url, image["image_id"])
    width, height = image["width"], image["height"]
    body = {
        "id": f"{service}/full/max/0/default.jpg",
        "type": "Image",
        "format": "image/jpeg",
        "width": width,
        "height": height,
        "service": [
            {
                "id": service,
                "type": "ImageService3",
                "profile": "level0",
            }
        ],
    }
    result: dict[str, Any] = {
        "id": canvas_id,
        "type": "Canvas",
        "width": width,
        "height": height,
        "thumbnail": [thumbnail(base_url, image, cfg)],
        "items": [
            {
                "id": f"{canvas_id}/page",
                "type": "AnnotationPage",
                "items": [
                    {
                        "id": f"{canvas_id}/annotation",
                        "type": "Annotation",
                        "motivation": "painting",
                        "target": canvas_id,
                        "body": body,
                    }
                ],
            }
        ],
    }
    if image.get("label"):
        result["label"] = language_map(image["label"], cfg.language)
    return result


def build_manifest(
    *,
    base_url: str,
    object_id: str,
    label: str,
    images: list[dict[str, Any]],
    metadata: dict[str, Any],
    cfg: Config,
    collection_label: str = DEFAULT_COLLECTION_LABEL,
) -> dict[str, Any]:
    """Assemble a manifest for one object (one canvas per image)."""
    manifest: dict[str, Any] = {
        "@context": PRESENTATION_CONTEXT,
        "id": manifest_url(base_url, object_id),
        "type": "Manifest",
    }
    apply_descriptive_properties(
        manifest,
        metadata,
        cfg,
        default_label=label,
        source=f"metadata for {object_id!r}",
    )

    if images and "thumbnail" not in manifest:
        manifest["thumbnail"] = [thumbnail(base_url, images[0], cfg)]
    if "homepage" not in manifest and cfg.homepage_link:
        manifest["homepage"] = [
            {
                "id": viewer_url(base_url, object_id),
                "type": "Text",
                "label": language_map("View in this collection", cfg.language),
                "format": "text/html",
            }
        ]

    manifest["items"] = [canvas(base_url, object_id, i + 1, image, cfg) for i, image in enumerate(images)]
    manifest["partOf"] = [
        {
            "id": collection_url(base_url),
            "type": "Collection",
            "label": language_map(collection_label, cfg.language),
        }
    ]
    return manifest


def build_collection(
    *,
    base_url: str,
    entries: list[dict[str, Any]],
    cfg: Config,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one collection for this repository, listing every manifest."""
    collection: dict[str, Any] = {
        "@context": PRESENTATION_CONTEXT,
        "id": collection_url(base_url),
        "type": "Collection",
    }
    apply_descriptive_properties(
        collection,
        metadata or {},
        cfg,
        default_label=DEFAULT_COLLECTION_LABEL,
        source="collection.yml",
    )

    collection["items"] = [
        {
            "id": entry["manifest"]["id"],
            "type": "Manifest",
            "label": entry["manifest"]["label"],
            **({"thumbnail": entry["manifest"]["thumbnail"]} if entry["manifest"].get("thumbnail") else {}),
            **({"summary": entry["manifest"]["summary"]} if entry["manifest"].get("summary") else {}),
        }
        for entry in entries
    ]
    return collection
