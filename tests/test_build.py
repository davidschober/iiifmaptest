"""End-to-end tests: build a site, then verify it the way a viewer would."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from conftest import make_image
from iiif_pages.build import build
from iiif_pages.check import check
from iiif_pages.config import Config
from iiif_pages.discover import DiscoveryError

BASE = "https://example.github.io/demo"


def run_build(originals: Path, out: Path, cfg: Config | None = None, **kwargs) -> object:
    kwargs.setdefault("collection_file", out.parent / "collection.yml")
    return build(
        originals=originals,
        out=out,
        cfg=cfg or Config(),
        base_url=BASE,
        site_dir=Path("site"),
        cache_dir=out.parent / ".cache",
        jobs=1,
        **kwargs,
    )


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_site_layout_and_validation(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    result = run_build(originals, out)

    assert result.objects == 2
    assert result.images == 4
    assert not [w for w in result.warnings if "unrecognised" in w]

    # The endpoints promised in the README.
    assert (out / "iiif" / "collection.json").exists()
    assert (out / "iiif" / "manifests" / "single-photo.json").exists()
    assert (out / "iiif" / "manifests" / "compound-object.json").exists()
    assert (out / "iiif" / "manifests" / "index.html").exists()
    assert (out / "iiif" / "images" / "single-photo" / "info.json").exists()
    assert (out / "iiif" / "images" / "index.html").exists()
    assert (out / ".nojekyll").exists()
    assert (out / "index.html").exists()
    assert (out / "view.html").exists()

    outcome = check(out=out, base_url=BASE)
    assert outcome.errors == []
    assert outcome.warnings == []
    assert outcome.manifests == 2
    assert outcome.services == 4
    assert outcome.checked_urls > 50


def test_single_image_manifest(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    manifest = read_json(out / "iiif" / "manifests" / "single-photo.json")

    assert manifest["type"] == "Manifest"
    assert manifest["id"] == f"{BASE}/iiif/manifests/single-photo.json"
    assert manifest["label"] == {"en": ["Single Photo"]}
    assert len(manifest["items"]) == 1

    canvas = manifest["items"][0]
    assert (canvas["width"], canvas["height"]) == (900, 700)
    body = canvas["items"][0]["items"][0]["body"]
    assert body["id"] == f"{BASE}/iiif/images/single-photo/full/max/0/default.jpg"
    assert body["service"][0] == {
        "id": f"{BASE}/iiif/images/single-photo",
        "type": "ImageService3",
        "profile": "level0",
    }


def test_compound_object_order_and_labels(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    manifest = read_json(out / "iiif" / "manifests" / "compound-object.json")

    assert [canvas["label"]["en"][0] for canvas in manifest["items"]] == ["01 First", "02 Second", "10 Tenth"]
    services = [canvas["items"][0]["items"][0]["body"]["service"][0]["id"] for canvas in manifest["items"]]
    assert services == [f"{BASE}/iiif/images/compound-object/{name}" for name in ("01-first", "02-second", "10-tenth")]


def test_sidecar_metadata_is_used(originals: Path, tmp_path: Path):
    (originals / "single-photo.yml").write_text(
        "label: A Better Title\n"
        "summary: Some words.\n"
        "rights: https://creativecommons.org/publicdomain/zero/1.0/\n"
        "metadata:\n"
        "  - label: Date\n"
        "    value: 1846\n",
        encoding="utf-8",
    )
    (originals / "compound object" / "object.yml").write_text(
        "label: Ordered Object\nbehavior: [paged]\nimages:\n  - file: 10-tenth.jpg\n    label: Put me first\n",
        encoding="utf-8",
    )
    out = tmp_path / "_site"
    result = run_build(originals, out)

    manifest = read_json(out / "iiif" / "manifests" / "single-photo.json")
    assert manifest["label"] == {"en": ["A Better Title"]}
    assert manifest["summary"] == {"en": ["Some words."]}
    assert manifest["rights"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert manifest["metadata"][0]["label"] == {"en": ["Date"]}
    assert read_json(out / "iiif" / "images" / "single-photo" / "info.json")["rights"].startswith("https://")

    compound = read_json(out / "iiif" / "manifests" / "compound-object.json")
    assert compound["label"] == {"en": ["Ordered Object"]}
    assert compound["behavior"] == ["paged"]
    assert compound["items"][0]["label"] == {"en": ["Put me first"]}
    assert any("not listed in 'images'" in w for w in result.warnings)


def test_unknown_sidecar_key_warns_but_builds(originals: Path, tmp_path: Path):
    (originals / "single-photo.yml").write_text("label: Fine\nnonsense: 1\n", encoding="utf-8")
    result = run_build(originals, tmp_path / "_site")
    assert any("nonsense" in w for w in result.warnings)


@pytest.mark.parametrize(
    ("width", "height"),
    [(1, 1), (100, 80), (512, 512), (513, 512), (512, 513), (1025, 769), (1200, 40)],
)
def test_awkward_dimensions_are_servable(tmp_path: Path, width: int, height: int):
    """Whatever the shape, every URL a viewer builds has to exist."""
    originals = tmp_path / "originals"
    make_image(originals / "odd.jpg", width, height)
    out = tmp_path / "_site"
    run_build(originals, out)

    outcome = check(out=out, base_url=BASE)
    assert outcome.errors == []

    info = read_json(out / "iiif" / "images" / "odd" / "info.json")
    assert (info["width"], info["height"]) == (width, height)
    with Image.open(out / "iiif" / "images" / "odd" / "full" / "max" / "0" / "default.jpg") as full:
        assert full.size == (width, height)


def test_tiles_reassemble_into_the_level(tmp_path: Path):
    """Stitched tiles must reproduce the scaled image, i.e. no drift at edges."""
    originals = tmp_path / "originals"
    source = make_image(originals / "grid.jpg", 1300, 1100)
    out = tmp_path / "_site"
    run_build(originals, out)

    service = out / "iiif" / "images" / "grid"
    with Image.open(source) as original:
        expected = original.convert("RGB").reduce(2)

    stitched = Image.new("RGB", expected.size)
    # Level 2 of a 1300x1100 image is 650x550: one 512 column/row plus a remainder.
    for region, size, offset in [
        ("0,0,1024,1024", "512,512", (0, 0)),
        ("1024,0,276,1024", "138,512", (512, 0)),
        ("0,1024,1024,76", "512,38", (0, 512)),
        ("1024,1024,276,76", "138,38", (512, 512)),
    ]:
        with Image.open(service / region / size / "0" / "default.jpg") as tile:
            assert tile.size == tuple(int(n) for n in size.split(","))
            stitched.paste(tile, offset)

    difference = sum(
        abs(a - b) for a, b in zip(expected.tobytes(), stitched.tobytes())
    ) / len(expected.tobytes())
    assert difference < 8, f"stitched tiles differ from the scaled original (mean {difference:.1f}/255)"


def test_exif_orientation_is_applied(tmp_path: Path):
    originals = tmp_path / "originals"
    path = make_image(originals / "rotated.jpg", 800, 400)
    with Image.open(path) as image:
        exif = Image.Exif()
        exif[274] = 6  # rotate 90 CW when displayed
        image.save(path, "JPEG", exif=exif)

    out = tmp_path / "_site"
    run_build(originals, out)
    info = read_json(out / "iiif" / "images" / "rotated" / "info.json")
    assert (info["width"], info["height"]) == (400, 800)
    manifest = read_json(out / "iiif" / "manifests" / "rotated.json")
    assert (manifest["items"][0]["width"], manifest["items"][0]["height"]) == (400, 800)
    assert check(out=out, base_url=BASE).errors == []


def test_rebuild_reuses_then_regenerates(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    first = run_build(originals, out)
    assert first.images_built == 4 and first.images_reused == 0

    second = run_build(originals, out)
    assert second.images_built == 0 and second.images_reused == 4

    make_image(originals / "single-photo.jpg", 700, 500, seed=9)  # different content and size
    third = run_build(originals, out)
    assert third.images_built == 1 and third.images_reused == 3
    assert read_json(out / "iiif" / "images" / "single-photo" / "info.json")["width"] == 700
    assert check(out=out, base_url=BASE).errors == []

    forced = run_build(originals, out, force=True)
    assert forced.images_built == 4


def test_changing_quality_settings_regenerates(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    result = run_build(originals, out, cfg=Config(jpeg_quality=60))
    assert result.images_built == 4


def test_removed_originals_are_pruned(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    shutil.rmtree(originals / "compound object")
    (originals / "single-photo.jpg").unlink()
    make_image(originals / "replacement.jpg", 600, 400)

    result = run_build(originals, out)
    assert result.objects == 1
    assert not (out / "iiif" / "manifests" / "compound-object.json").exists()
    assert not (out / "iiif" / "images" / "compound-object").exists()
    assert not (out / "iiif" / "images" / "single-photo").exists()
    assert (out / "iiif" / "images" / "replacement" / "info.json").exists()
    outcome = check(out=out, base_url=BASE)
    assert outcome.errors == [] and outcome.warnings == []


def test_check_notices_a_missing_tile(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    victim = next((out / "iiif" / "images" / "single-photo").rglob("default.jpg"))
    victim.unlink()

    outcome = check(out=out, base_url=BASE)
    assert not outcome.ok
    assert any("was not written" in e or "missing" in e for e in outcome.errors)


def test_check_notices_a_wrong_base_url(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    run_build(originals, out)
    outcome = check(out=out, base_url="https://somewhere.else/x")
    assert not outcome.ok
    assert any("info.json id is" in e for e in outcome.errors)


def test_identifier_collisions_are_rejected(tmp_path: Path):
    originals = tmp_path / "originals"
    make_image(originals / "My Photo.jpg", 400, 300)
    make_image(originals / "my-photo.jpg", 400, 300)
    with pytest.raises(DiscoveryError, match="same identifier"):
        run_build(originals, tmp_path / "_site")


def test_empty_originals_still_builds_a_collection(tmp_path: Path):
    originals = tmp_path / "originals"
    originals.mkdir()
    out = tmp_path / "_site"
    result = run_build(originals, out)
    assert result.objects == 0
    assert read_json(out / "iiif" / "collection.json")["items"] == []


def test_manifests_validate_against_iiif_prezi3(originals: Path, tmp_path: Path):
    prezi3 = pytest.importorskip("iiif_prezi3", reason="iiif-prezi3 gives an independent Presentation 3.0 model")
    out = tmp_path / "_site"
    run_build(originals, out)
    for path in sorted((out / "iiif" / "manifests").glob("*.json")):
        if path.name == "index.json":
            continue
        parsed = prezi3.Manifest(**read_json(path))
        assert parsed.items
    assert prezi3.Collection(**read_json(out / "iiif" / "collection.json")).items


def test_unsupported_files_are_reported(tmp_path: Path):
    """A warning about a stray file must survive even with nothing to build."""
    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / "notes.pdf").write_bytes(b"%PDF-1.4")
    result = run_build(originals, tmp_path / "_site")
    assert result.objects == 0
    assert any("notes.pdf" in w and "not a supported image type" in w for w in result.warnings)


def test_empty_folder_object_is_reported(originals: Path, tmp_path: Path):
    (originals / "nothing here").mkdir()
    result = run_build(originals, tmp_path / "_site")
    assert result.objects == 2
    assert any("contains no images" in w for w in result.warnings)
