from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw


def make_image(path: Path, width: int, height: int, seed: int = 0) -> Path:
    """Write a JPEG with enough structure that misaligned tiles are visible."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (width, height), (250, 248, 240))
    draw = ImageDraw.Draw(image)
    for i in range(0, max(width, height), 37):
        shade = (i * 7 + seed * 53) % 200 + 30
        draw.line((i - height, 0, i, height), fill=(shade, (shade * 3) % 255, 200 - shade // 2), width=9)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(10, 10, 10), width=5)
    image.save(path, "JPEG", quality=92)
    return path


@pytest.fixture
def originals(tmp_path: Path) -> Path:
    root = tmp_path / "originals"
    make_image(root / "single-photo.jpg", 900, 700, seed=1)
    for index, name in enumerate(["01-first.jpg", "02-second.jpg", "10-tenth.jpg"]):
        make_image(root / "compound object" / name, 640, 800, seed=index + 2)
    return root
