"""Tests for the command line surface and source-image handling."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from conftest import make_image
from iiif_pages.cli import main
from iiif_pages.config import Config, ConfigError, load_config, resolve_base_url
from iiif_pages.images import SourceImageError, open_source


def cli_args(originals: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--originals",
        str(originals),
        "--out",
        str(out),
        "--config",
        str(out.parent / "missing-config.yml"),
        "--collection",
        str(out.parent / "collection.yml"),
        "--site",
        "site",
        "--cache-dir",
        str(out.parent / ".cache"),
        *extra,
    ]


def test_cli_builds_and_validates(originals: Path, tmp_path: Path, capsys):
    out = tmp_path / "_site"
    assert main(["build", *cli_args(originals, out, "--base-url", "https://x.github.io/y")]) == 0
    captured = capsys.readouterr()
    assert "Built 2 object(s)" in captured.out
    assert "all present" in captured.out

    assert main(["check", *cli_args(originals, out, "--base-url", "https://x.github.io/y")]) == 0


def test_bare_invocation_means_build(originals: Path, tmp_path: Path, monkeypatch):
    """`iiif-build --base-url ...` with no subcommand should build."""
    out = tmp_path / "_site"
    monkeypatch.setenv("IIIF_BASE_URL", "https://x.github.io/y")
    assert main(cli_args(originals, out)) == 0
    assert (out / "iiif" / "collection.json").exists()


def test_missing_base_url_is_a_clean_error(originals: Path, tmp_path: Path, monkeypatch, capsys):
    monkeypatch.delenv("IIIF_BASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert main(["build", *cli_args(originals, tmp_path / "_site")]) == 2
    assert "No base URL available" in capsys.readouterr().err


def test_check_failure_exits_non_zero(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    args = cli_args(originals, out, "--base-url", "https://x.github.io/y")
    assert main(["build", *args]) == 0
    next((out / "iiif" / "images").rglob("default.jpg")).unlink()
    assert main(["check", *args]) == 1


def test_unreadable_source_is_a_clean_error(tmp_path: Path, capsys):
    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / "broken.jpg").write_bytes(b"this is not a JPEG")
    assert main(["build", *cli_args(originals, tmp_path / "_site", "--base-url", "https://x.github.io/y")]) == 2
    assert "cannot read this image" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("repository", "expected"),
    [
        ("Octocat/My-Repo", "https://octocat.github.io/My-Repo"),
        ("Octocat/octocat.github.io", "https://octocat.github.io"),
    ],
)
def test_github_pages_url_fallback(monkeypatch, repository, expected):
    monkeypatch.delenv("IIIF_BASE_URL", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", repository)
    assert resolve_base_url(None, Config()) == expected


def test_base_url_precedence_and_normalisation(monkeypatch):
    monkeypatch.setenv("IIIF_BASE_URL", "https://from-env.example/x/")
    assert resolve_base_url(None, Config(base_url="https://from-config.example")) == "https://from-env.example/x"
    assert resolve_base_url("https://from-flag.example/", Config()) == "https://from-flag.example"
    with pytest.raises(ConfigError, match="http"):
        resolve_base_url("from-flag.example", Config())


def test_config_is_loaded_and_validated(tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("tile_size: 1024\nmystery: 1\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.tile_size == 1024
    assert cfg.warnings == ["config.yml: ignoring unrecognised key: mystery"]

    # title/summary describe the collection now, so config.yml points elsewhere.
    path.write_text("title: Mine\n", encoding="utf-8")
    assert load_config(path).warnings == [
        "config.yml: 'title' has moved to collection.yml, as 'label'; ignoring it here"
    ]

    path.write_text("tile_size: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="tile_size"):
        load_config(path)

    assert load_config(tmp_path / "absent.yml").tile_size == 512


def test_larger_tile_size_changes_the_pyramid(originals: Path, tmp_path: Path):
    out = tmp_path / "_site"
    config = tmp_path / "config.yml"
    config.write_text("tile_size: 1024\n", encoding="utf-8")
    assert main(
        [
            "build",
            "--originals",
            str(originals),
            "--out",
            str(out),
            "--config",
            str(config),
            "--collection",
            str(tmp_path / "collection.yml"),
            "--site",
            "site",
            "--cache-dir",
            str(tmp_path / ".cache"),
            "--base-url",
            "https://x.github.io/y",
        ]
    ) == 0
    info = json.loads((out / "iiif" / "images" / "single-photo" / "info.json").read_text())
    assert info["tiles"][0]["width"] == 1024
    assert info["tiles"][0]["scaleFactors"] == [1]


def test_transparency_is_flattened_onto_white(tmp_path: Path):
    originals = tmp_path / "originals"
    originals.mkdir()
    png = originals / "transparent.png"
    image = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    image.paste((200, 30, 40, 255), (0, 0, 150, 200))
    image.save(png)

    flattened = open_source(png)
    assert flattened.mode == "RGB"
    assert flattened.getpixel((250, 100)) == (255, 255, 255)
    assert flattened.getpixel((50, 100))[0] > 150

    out = tmp_path / "_site"
    assert main(["build", *cli_args(originals, out, "--base-url", "https://x.github.io/y")]) == 0
    with Image.open(out / "iiif" / "images" / "transparent" / "full" / "max" / "0" / "default.jpg") as jpeg:
        assert jpeg.mode == "RGB"
        assert min(jpeg.getpixel((280, 100))) > 240


def test_open_source_reports_the_file_name(tmp_path: Path):
    bad = tmp_path / "nope.jpg"
    bad.write_bytes(b"nope")
    with pytest.raises(SourceImageError, match="nope.jpg"):
        open_source(bad)


def test_grayscale_stays_grayscale(tmp_path: Path):
    path = tmp_path / "gray.jpg"
    make_image(path, 200, 150)
    Image.open(path).convert("L").save(path)
    assert open_source(path).mode == "L"
