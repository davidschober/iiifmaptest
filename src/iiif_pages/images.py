"""Generate static IIIF Image API 3.0 level 0 derivatives.

A level 0 service cannot compute anything on demand: every URL a client asks
for has to already exist as a file. This module writes exactly the set of
files implied by the ``sizes`` and ``tiles`` declared in ``info.json``, using
the canonical URI syntax from the Image API 3.0 specification (region ``full``
or ``x,y,w,h``; size ``max`` or ``w,h``). That canonical set is also precisely
what OpenSeadragon -- and therefore Mirador, Clover and friends -- requests
from a version 3 service.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

# Source images are local files the repository owner committed, so the
# decompression-bomb guard mainly gets in the way of legitimate large scans.
Image.MAX_IMAGE_PIXELS = 1_000_000_000

IMAGE_CONTEXT = "http://iiif.io/api/image/3/context.json"
LEVEL0_PROFILE = "level0"


@dataclass(frozen=True)
class Derivative:
    """One JPEG file in the static service."""

    region: str
    size: str
    box: tuple[int, int, int, int]  # crop box in the pixels of its own level
    factor: int
    width: int
    height: int

    @property
    def rel_path(self) -> str:
        return f"{self.region}/{self.size}/0/default.jpg"


@dataclass
class ImageDimensions:
    width: int
    height: int


def scale_factors(width: int, height: int, tile_size: int) -> list[int]:
    """Powers of two down to the level that fits inside a single tile."""
    factors = [1]
    while math.ceil(width / factors[-1]) > tile_size or math.ceil(height / factors[-1]) > tile_size:
        factors.append(factors[-1] * 2)
    return factors


def level_size(width: int, height: int, factor: int) -> tuple[int, int]:
    return math.ceil(width / factor), math.ceil(height / factor)


def canonical_region(x: int, y: int, w: int, h: int, width: int, height: int) -> str:
    return "full" if (x, y, w, h) == (0, 0, width, height) else f"{x},{y},{w},{h}"


def canonical_size(w: int, h: int, width: int, height: int) -> str:
    return "max" if (w, h) == (width, height) else f"{w},{h}"


def plan_derivatives(width: int, height: int, tile_size: int, factors: list[int]) -> list[Derivative]:
    """Every file the service must expose, in a stable order.

    For each scale factor this is the grid of tiles plus one whole-level image
    (the entries advertised in ``sizes``). ``full/max`` -- the only size level 0
    is required to serve -- falls out as the whole-level image of factor 1.
    """
    planned: dict[tuple[str, str], Derivative] = {}

    def add(deriv: Derivative) -> None:
        planned.setdefault((deriv.region, deriv.size), deriv)

    for factor in factors:
        level_w, level_h = level_size(width, height, factor)

        for row in range(math.ceil(level_h / tile_size)):
            for col in range(math.ceil(level_w / tile_size)):
                # Offsets within the scaled level ...
                sx, sy = col * tile_size, row * tile_size
                sw, sh = min(tile_size, level_w - sx), min(tile_size, level_h - sy)
                # ... and the same tile expressed as a region of the full image.
                rx, ry = sx * factor, sy * factor
                rw, rh = min(tile_size * factor, width - rx), min(tile_size * factor, height - ry)
                add(
                    Derivative(
                        region=canonical_region(rx, ry, rw, rh, width, height),
                        size=canonical_size(sw, sh, width, height),
                        box=(sx, sy, sx + sw, sy + sh),
                        factor=factor,
                        width=sw,
                        height=sh,
                    )
                )

        add(
            Derivative(
                region="full",
                size=canonical_size(level_w, level_h, width, height),
                box=(0, 0, level_w, level_h),
                factor=factor,
                width=level_w,
                height=level_h,
            )
        )

    return list(planned.values())


def declared_sizes(width: int, height: int, factors: list[int]) -> list[dict[str, int]]:
    """The ``sizes`` array for info.json, smallest first."""
    sizes = [level_size(width, height, factor) for factor in factors]
    return [{"type": "Size", "width": w, "height": h} for w, h in sorted(set(sizes))]


class SourceImageError(Exception):
    """Raised when an original cannot be read or converted."""


def open_source(path: Path) -> Image.Image:
    """Open an original as JPEG-ready pixels.

    Applies EXIF orientation so the dimensions we publish are the ones people
    see, and flattens transparency onto white since JPEG has no alpha channel.
    """
    try:
        with Image.open(path) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
    except Exception as exc:  # unreadable, truncated, or not an image at all
        raise SourceImageError(f"{path}: cannot read this image ({exc})") from exc

    try:
        if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
            image = image.convert("RGBA")
            backdrop = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(backdrop, image).convert("RGB")
        elif image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
    except Exception as exc:
        raise SourceImageError(f"{path}: cannot convert this image for JPEG output ({exc})") from exc
    return image


def write_derivatives(
    source: Path,
    out_dir: Path,
    *,
    tile_size: int,
    jpeg_quality: int,
) -> ImageDimensions:
    """Write every level 0 file for one image into ``out_dir``."""
    image = open_source(source)
    width, height = image.size
    icc_profile = image.info.get("icc_profile")
    factors = scale_factors(width, height, tile_size)
    derivatives = plan_derivatives(width, height, tile_size, factors)

    by_factor: dict[int, list[Derivative]] = {}
    for deriv in derivatives:
        by_factor.setdefault(deriv.factor, []).append(deriv)

    for factor in factors:
        # Image.reduce rounds sizes up, matching ceil(dimension / factor), and
        # box-averages, which is a good downsample for power-of-two steps.
        level = image if factor == 1 else image.reduce(factor)
        expected = level_size(width, height, factor)
        if level.size != expected:
            raise RuntimeError(f"{source}: level for factor {factor} is {level.size}, expected {expected}")

        for deriv in by_factor.get(factor, []):
            target = out_dir / deriv.rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            crop = level if deriv.box == (0, 0, *level.size) else level.crop(deriv.box)
            save_kwargs: dict[str, Any] = {"quality": jpeg_quality, "optimize": True}
            if icc_profile:
                save_kwargs["icc_profile"] = icc_profile
            crop.save(target, "JPEG", **save_kwargs)

    image.close()
    return ImageDimensions(width, height)


def image_info(
    *,
    service_id: str,
    width: int,
    height: int,
    tile_size: int,
    rights: str | None = None,
) -> dict[str, Any]:
    """The ``info.json`` describing this static level 0 service."""
    factors = scale_factors(width, height, tile_size)
    info: dict[str, Any] = {
        "@context": IMAGE_CONTEXT,
        "id": service_id,
        "type": "ImageService3",
        "protocol": "http://iiif.io/api/image",
        "profile": LEVEL0_PROFILE,
        "width": width,
        "height": height,
        "sizes": declared_sizes(width, height, factors),
        "tiles": [{"type": "Tile", "width": tile_size, "height": tile_size, "scaleFactors": factors}],
        "preferredFormats": ["jpg"],
    }
    if rights:
        info["rights"] = rights
    return info


def pick_thumbnail_size(width: int, height: int, tile_size: int, target_width: int) -> tuple[int, int]:
    """Smallest declared size at least ``target_width`` wide, else the largest."""
    sizes = sorted({level_size(width, height, f) for f in scale_factors(width, height, tile_size)})
    for size in sizes:
        if size[0] >= target_width:
            return size
    return sizes[-1]
