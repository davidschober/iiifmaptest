"""The repository's single collection and its metadata sidecar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iiif_pages.check import check
from iiif_pages.config import Config
from test_build import BASE, read_json, run_build

FULL_SIDECAR = """\
label: Voyages of the Mary Anne
summary: Everything digitised from the Mary Anne collection.
metadata:
  - label: Repository
    value: Example Library
  - label: Extent
    value: 2 objects
required_statement:
  label: Attribution
  value: Collection of the Example Library
rights: https://creativecommons.org/publicdomain/zero/1.0/
provider:
  - id: https://example.org
    label: Example Library
behavior:
  - unordered
nav_date: "1846-06-01T00:00:00Z"
see_also:
  - id: https://example.org/records.xml
    type: Dataset
    format: text/xml
homepage:
  - id: https://example.org/collections/mary-anne
    label: Collection page
"""


def build_with_sidecar(originals: Path, tmp_path: Path, text: str | None, **kwargs):
    sidecar = tmp_path / "collection.yml"
    if text is not None:
        sidecar.write_text(text, encoding="utf-8")
    out = tmp_path / "_site"
    result = run_build(originals, out, collection_file=sidecar, **kwargs)
    return out, result


def test_collection_defaults_without_a_sidecar(originals: Path, tmp_path: Path):
    out, result = build_with_sidecar(originals, tmp_path, None)
    collection = read_json(out / "iiif" / "collection.json")

    assert collection["type"] == "Collection"
    assert collection["id"] == f"{BASE}/iiif/collection.json"
    assert collection["label"] == {"en": ["IIIF Collection"]}
    assert "summary" not in collection
    assert [item["type"] for item in collection["items"]] == ["Manifest", "Manifest"]
    assert result.warnings == []


def test_sidecar_metadata_uses_the_same_keys_as_objects(originals: Path, tmp_path: Path):
    out, result = build_with_sidecar(originals, tmp_path, FULL_SIDECAR)
    collection = read_json(out / "iiif" / "collection.json")

    assert collection["label"] == {"en": ["Voyages of the Mary Anne"]}
    assert collection["summary"] == {"en": ["Everything digitised from the Mary Anne collection."]}
    assert collection["metadata"][0] == {
        "label": {"en": ["Repository"]},
        "value": {"en": ["Example Library"]},
    }
    assert collection["requiredStatement"]["value"] == {"en": ["Collection of the Example Library"]}
    assert collection["rights"] == "https://creativecommons.org/publicdomain/zero/1.0/"
    assert collection["provider"][0] == {
        "type": "Agent",
        "id": "https://example.org",
        "label": {"en": ["Example Library"]},
    }
    assert collection["behavior"] == ["unordered"]
    assert collection["navDate"] == "1846-06-01T00:00:00Z"
    assert collection["seeAlso"][0]["id"] == "https://example.org/records.xml"
    assert collection["homepage"][0] == {
        "id": "https://example.org/collections/mary-anne",
        "type": "Text",
        "format": "text/html",
        "label": {"en": ["Collection page"]},
    }
    assert result.warnings == []
    assert check(out=out, base_url=BASE).errors == []


def test_json_sidecar_is_accepted(originals: Path, tmp_path: Path):
    (tmp_path / "collection.json").write_text(
        json.dumps({"label": "From JSON", "summary": "Also fine."}), encoding="utf-8"
    )
    out = tmp_path / "_site"
    run_build(originals, out, collection_file=tmp_path / "collection.yml")
    assert read_json(out / "iiif" / "collection.json")["label"] == {"en": ["From JSON"]}


def test_collection_label_reaches_manifests_and_pages(originals: Path, tmp_path: Path):
    out, _ = build_with_sidecar(originals, tmp_path, FULL_SIDECAR)

    manifest = read_json(out / "iiif" / "manifests" / "single-photo.json")
    assert manifest["partOf"][0] == {
        "id": f"{BASE}/iiif/collection.json",
        "type": "Collection",
        "label": {"en": ["Voyages of the Mary Anne"]},
    }

    index = (out / "index.html").read_text(encoding="utf-8")
    assert "Voyages of the Mary Anne" in index
    assert "Everything digitised from the Mary Anne collection." in index
    assert "{{TITLE_HTML}}" not in index
    assert "Voyages of the Mary Anne" in (out / "iiif" / "manifests" / "index.html").read_text(encoding="utf-8")


def test_thumbnail_can_nominate_an_object(originals: Path, tmp_path: Path):
    out, result = build_with_sidecar(originals, tmp_path, "label: Covered\nthumbnail: compound object\n")
    collection = read_json(out / "iiif" / "collection.json")
    expected = read_json(out / "iiif" / "manifests" / "compound-object.json")["thumbnail"]

    assert collection["thumbnail"] == expected
    assert collection["thumbnail"][0]["id"].startswith(f"{BASE}/iiif/images/compound-object/01-first/full/")
    assert result.warnings == []
    assert check(out=out, base_url=BASE).errors == []


def test_unknown_thumbnail_warns_and_is_dropped(originals: Path, tmp_path: Path):
    out, result = build_with_sidecar(originals, tmp_path, "thumbnail: no-such-object\n")
    assert "thumbnail" not in read_json(out / "iiif" / "collection.json")
    assert any("not one of this collection's objects" in w for w in result.warnings)
    assert check(out=out, base_url=BASE).errors == []


def test_explicit_thumbnail_body_is_passed_through(originals: Path, tmp_path: Path):
    out, _ = build_with_sidecar(
        originals,
        tmp_path,
        "thumbnail:\n  - id: https://example.org/cover.jpg\n    type: Image\n    format: image/jpeg\n",
    )
    assert read_json(out / "iiif" / "collection.json")["thumbnail"][0]["id"] == "https://example.org/cover.jpg"


def test_unknown_and_object_only_keys_warn(originals: Path, tmp_path: Path):
    out, result = build_with_sidecar(
        originals,
        tmp_path,
        "label: Fine\nmystery: 1\nimages:\n  - file: nope.jpg\n",
    )
    assert read_json(out / "iiif" / "collection.json")["label"] == {"en": ["Fine"]}
    warning = next(w for w in result.warnings if "collection.yml" in w)
    assert "images" in warning and "mystery" in warning


def test_config_defaults_apply_to_the_collection(originals: Path, tmp_path: Path):
    cfg = Config(
        rights="http://rightsstatements.org/vocab/InC/1.0/",
        required_statement={"label": "Attribution", "value": "From config"},
        provider=[{"id": "https://config.example", "type": "Agent"}],
    )
    out, _ = build_with_sidecar(originals, tmp_path, "label: Defaults\n", cfg=cfg)
    collection = read_json(out / "iiif" / "collection.json")

    assert collection["rights"] == "http://rightsstatements.org/vocab/InC/1.0/"
    assert collection["requiredStatement"]["value"] == {"en": ["From config"]}
    assert collection["provider"][0]["id"] == "https://config.example"
    # ... and the same defaults reach each manifest.
    assert read_json(out / "iiif" / "manifests" / "single-photo.json")["rights"].endswith("InC/1.0/")


def test_sidecar_beats_config_defaults(originals: Path, tmp_path: Path):
    cfg = Config(rights="http://rightsstatements.org/vocab/InC/1.0/")
    out, _ = build_with_sidecar(originals, tmp_path, "label: Wins\nrights: https://example.org/rights\n", cfg=cfg)
    assert read_json(out / "iiif" / "collection.json")["rights"] == "https://example.org/rights"


def test_collection_validates_against_iiif_prezi3(originals: Path, tmp_path: Path):
    prezi3 = pytest.importorskip("iiif_prezi3", reason="iiif-prezi3 gives an independent Presentation 3.0 model")
    out, _ = build_with_sidecar(originals, tmp_path, FULL_SIDECAR)
    parsed = prezi3.Collection(**read_json(out / "iiif" / "collection.json"))
    assert len(parsed.items) == 2
