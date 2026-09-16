"""Unit tests for the level 0 tiling maths."""

from __future__ import annotations

import math

import pytest

from iiif_pages.images import (
    canonical_region,
    canonical_size,
    declared_sizes,
    level_size,
    pick_thumbnail_size,
    plan_derivatives,
    scale_factors,
)

# Deliberately awkward shapes: below/at/just above the tile edge, extreme
# aspect ratios, and sizes that are not multiples of the tile.
DIMENSIONS = [
    (1, 1),
    (100, 80),
    (511, 511),
    (512, 512),
    (513, 512),
    (512, 513),
    (1024, 1024),
    (1025, 769),
    (1800, 1200),
    (4000, 137),
    (137, 4000),
    (5000, 4000),
]


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_smallest_level_fits_in_one_tile(width, height):
    factors = scale_factors(width, height, 512)
    assert factors[0] == 1
    assert all(factors[i] * 2 == factors[i + 1] for i in range(len(factors) - 1))
    last_w, last_h = level_size(width, height, factors[-1])
    assert last_w <= 512 and last_h <= 512
    if len(factors) > 1:
        previous_w, previous_h = level_size(width, height, factors[-2])
        assert previous_w > 512 or previous_h > 512


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_full_max_is_always_planned(width, height):
    """Region full + size max is the one request level 0 must always answer."""
    factors = scale_factors(width, height, 512)
    paths = {d.rel_path for d in plan_derivatives(width, height, 512, factors)}
    assert "full/max/0/default.jpg" in paths


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_every_declared_size_is_planned(width, height):
    factors = scale_factors(width, height, 512)
    planned = {d.rel_path for d in plan_derivatives(width, height, 512, factors)}
    for size in declared_sizes(width, height, factors):
        name = canonical_size(size["width"], size["height"], width, height)
        assert f"full/{name}/0/default.jpg" in planned


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_tiles_cover_each_level_exactly_once(width, height):
    """Tiles must tile: aligned to the grid, inside the level, no gaps."""
    factors = scale_factors(width, height, 512)
    derivatives = plan_derivatives(width, height, 512, factors)
    for factor in factors:
        level_w, level_h = level_size(width, height, factor)
        tiles = [
            d
            for d in derivatives
            if d.factor == factor and not (d.region == "full" and (d.width, d.height) == (level_w, level_h))
        ]
        if not tiles:  # a level that fits in one tile is stored as the whole level
            assert level_w <= 512 and level_h <= 512
            continue
        origins = set()
        area = 0
        for deriv in tiles:
            x0, y0, x1, y1 = deriv.box
            assert x0 % 512 == 0 and y0 % 512 == 0, "tile origins must sit on the grid"
            assert (x0, y0) not in origins, "two tiles share an origin"
            origins.add((x0, y0))
            assert 0 <= x0 < x1 <= level_w and 0 <= y0 < y1 <= level_h, "tile falls outside the level"
            assert (x1 - x0, y1 - y0) == (deriv.width, deriv.height)
            area += (x1 - x0) * (y1 - y0)
        assert area == level_w * level_h, "tiles do not cover the level exactly"
        assert len(origins) == math.ceil(level_w / 512) * math.ceil(level_h / 512)


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_planned_paths_are_unique_and_canonical(width, height):
    factors = scale_factors(width, height, 512)
    derivatives = plan_derivatives(width, height, 512, factors)
    paths = [d.rel_path for d in derivatives]
    assert len(paths) == len(set(paths))
    for deriv in derivatives:
        assert deriv.region == "full" or len(deriv.region.split(",")) == 4
        assert deriv.size == "max" or len(deriv.size.split(",")) == 2
        assert deriv.rel_path.endswith("/0/default.jpg")


def test_canonical_forms():
    assert canonical_region(0, 0, 100, 80, 100, 80) == "full"
    assert canonical_region(0, 0, 50, 80, 100, 80) == "0,0,50,80"
    assert canonical_size(100, 80, 100, 80) == "max"
    assert canonical_size(50, 40, 100, 80) == "50,40"


def test_thumbnail_prefers_an_existing_size():
    width, height = 5000, 4000
    thumb = pick_thumbnail_size(width, height, 512, 400)
    sizes = {(s["width"], s["height"]) for s in declared_sizes(width, height, scale_factors(width, height, 512))}
    assert thumb in sizes
    assert thumb[0] >= 400
    # A small image has nothing bigger than the target, so it uses its largest.
    assert pick_thumbnail_size(100, 80, 512, 400) == (100, 80)


@pytest.mark.parametrize(("width", "height"), DIMENSIONS)
def test_level_size_matches_ceiling_division(width, height):
    for factor in scale_factors(width, height, 512):
        assert level_size(width, height, factor) == (
            math.ceil(width / factor),
            math.ceil(height / factor),
        )
