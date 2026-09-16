"""Site-wide build configuration (``config.yml``)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """Raised when configuration or command line input cannot be used."""


# Keys accepted in config.yml. Anything else is reported so typos do not fail
# silently after a long build.
KNOWN_KEYS = {
    "base_url",
    "language",
    "tile_size",
    "jpeg_quality",
    "thumbnail_width",
    "rights",
    "required_statement",
    "provider",
    "homepage_link",
}


# Settings that used to live here and now belong in the collection sidecar.
MOVED_KEYS = {
    "title": "collection.yml, as 'label'",
    "summary": "collection.yml, as 'summary'",
}


@dataclass
class Config:
    base_url: str | None = None
    language: str = "en"
    tile_size: int = 512
    jpeg_quality: int = 85
    thumbnail_width: int = 400
    rights: str | None = None
    required_statement: dict[str, Any] | None = None
    provider: list[dict[str, Any]] | None = None
    homepage_link: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def derivative_params(self) -> dict[str, Any]:
        """Settings that change generated JPEG bytes (used as a cache key)."""
        return {"tile_size": self.tile_size, "jpeg_quality": self.jpeg_quality}


def _as_provider(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    entries = value if isinstance(value, list) else [value]
    for entry in entries:
        if not isinstance(entry, dict) or "id" not in entry:
            raise ConfigError("config.yml: each 'provider' entry needs at least an 'id'")
        entry.setdefault("type", "Agent")
    return entries


def load_config(path: Path | None) -> Config:
    """Load ``config.yml``, falling back to defaults when it is absent."""
    data: dict[str, Any] = {}
    if path is not None and path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ConfigError(f"{path}: expected a mapping of settings at the top level")
        data = loaded

    cfg = Config()
    for key in sorted(set(data) - KNOWN_KEYS):
        if key in MOVED_KEYS:
            cfg.warnings.append(f"config.yml: '{key}' has moved to {MOVED_KEYS[key]}; ignoring it here")
        else:
            cfg.warnings.append(f"config.yml: ignoring unrecognised key: {key}")

    for key in ("base_url", "language", "rights"):
        if data.get(key) is not None:
            setattr(cfg, key, str(data[key]))
    for key in ("tile_size", "jpeg_quality", "thumbnail_width"):
        if data.get(key) is not None:
            setattr(cfg, key, int(data[key]))
    if data.get("homepage_link") is not None:
        cfg.homepage_link = bool(data["homepage_link"])
    if data.get("required_statement") is not None:
        statement = data["required_statement"]
        if not isinstance(statement, dict) or "value" not in statement:
            raise ConfigError("config.yml: 'required_statement' needs a 'value' (and usually a 'label')")
        cfg.required_statement = statement
    cfg.provider = _as_provider(data.get("provider"))

    if cfg.tile_size < 64 or cfg.tile_size % 2:
        raise ConfigError("config.yml: 'tile_size' must be an even number of at least 64 (512 is typical)")
    if not 1 <= cfg.jpeg_quality <= 100:
        raise ConfigError("config.yml: 'jpeg_quality' must be between 1 and 100")
    if cfg.thumbnail_width < 1:
        raise ConfigError("config.yml: 'thumbnail_width' must be a positive number of pixels")
    return cfg


def resolve_base_url(cli_value: str | None, cfg: Config) -> str:
    """Work out the absolute URL the published site will live at.

    Precedence: ``--base-url``, then ``$IIIF_BASE_URL``, then ``config.yml``,
    then the conventional GitHub Pages URL for ``$GITHUB_REPOSITORY``. IIIF
    identifiers must be absolute URLs, so a value is required.
    """
    candidates = [cli_value, os.environ.get("IIIF_BASE_URL"), cfg.base_url]
    for candidate in candidates:
        if candidate:
            return normalize_base_url(candidate)

    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository and "/" in repository:
        owner, name = repository.split("/", 1)
        if name.lower() == f"{owner.lower()}.github.io":
            return normalize_base_url(f"https://{owner.lower()}.github.io")
        return normalize_base_url(f"https://{owner.lower()}.github.io/{name}")

    raise ConfigError(
        "No base URL available. Pass --base-url https://<user>.github.io/<repo>, "
        "set IIIF_BASE_URL, or add 'base_url:' to config.yml."
    )


def normalize_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ConfigError(f"Base URL must start with http:// or https:// (got {value!r})")
    return value
