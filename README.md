# IIIF Pages

A GitHub repository template that turns a folder of images into a **static IIIF
Image API 3.0 level 0 service** with **IIIF Presentation 3.0 manifests**,
published by GitHub Pages.

Drop a JPEG in `originals/`, push, and you get a zoomable, standards-compliant
image service with no server to run:

```
https://<user>.github.io/<repo>/iiif/manifests/<object>.json   # manifests
https://<user>.github.io/<repo>/iiif/images/<image>/info.json  # image services
https://<user>.github.io/<repo>/iiif/collection.json           # everything, as a IIIF Collection
https://<user>.github.io/<repo>/                               # browse + viewer
```

Single images become one-canvas manifests. A folder of images becomes one
manifest with a canvas per image, so complex objects — books, folders, boxes,
multi-view objects — work the same way.

## Quick start

1. Click **Use this template → Create a new repository**.
2. In the new repository, open **Settings → Pages** and set
   **Build and deployment → Source** to **GitHub Actions**.
3. Put your images in `originals/` (delete the two placeholder objects), commit,
   and push to `main`.
4. Watch the **Publish IIIF site** action. When it finishes, your service is
   live at the URL shown in the workflow summary.

Nothing needs configuring for this to work: the workflow reads your Pages URL
from the repository's own settings, so custom domains and
`<user>.github.io` repositories get correct identifiers automatically.

## How `originals/` maps to IIIF

| You add | You get |
| --- | --- |
| `originals/photograph.jpg` | `iiif/manifests/photograph.json`, one canvas |
| `originals/ledger/` with images inside | `iiif/manifests/ledger.json`, one canvas per image |

* Identifiers come from file and folder names, lowercased with punctuation
  turned into hyphens: `Ship's Log 1846.jpg` → `ships-log-1846`.
* Images inside a folder are ordered naturally, so `page2.jpg` sorts before
  `page10.jpg`. Sub-folders are included, in the same order.
* Accepted sources: `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp`.
  Derivatives are always JPEG, as level 0 requires.
* EXIF orientation is applied, so canvas dimensions match what people see.
* Two files that would produce the same identifier stop the build with a
  message naming both, rather than silently overwriting one another.

## Metadata sidecars

Metadata is optional. Add a YAML (or JSON) file next to the thing it
describes:

* `photograph.yml` — for the single image `photograph.jpg`
* `ledger/object.yml` — for the folder object `ledger/`
* `collection.yml` — for the repository's collection (see below)

```yaml
label: Ship's Log, 1846
summary: Daily entries from the voyage of the Mary Anne.
metadata:
  - label: Date
    value: 1846
  - label: Shelfmark
    value: MS 1846/3
required_statement:
  label: Attribution
  value: Collection of the Example Library
rights: https://creativecommons.org/publicdomain/zero/1.0/
behavior: [paged]          # e.g. paged, individuals, continuous
viewing_direction: left-to-right
nav_date: "1846-06-01T00:00:00Z"
see_also:
  - id: https://example.org/records/1846-3.xml
    type: Dataset
    format: text/xml
images:                    # folder objects only: order and per-canvas labels
  - file: 001.jpg
    label: Front cover
  - file: 002.jpg
    label: Page 1
```

Every key is optional. `label` defaults to a tidied-up file name; images you
leave out of `images:` are appended in natural order. Unrecognised keys are
reported as warnings in the build log instead of being ignored silently.

## The collection

Each repository publishes exactly one IIIF Collection, at
`/iiif/collection.json`, listing every manifest it contains. Describe it in
`collection.yml` in the root of the repository, using the same keys as an object
sidecar:

```yaml
label: Voyages of the Mary Anne
summary: Everything digitised from the Mary Anne collection.
metadata:
  - label: Repository
    value: Example Library
required_statement:
  label: Attribution
  value: Collection of the Example Library
rights: https://creativecommons.org/publicdomain/zero/1.0/
provider:
  - id: https://example.org
    label: Example Library
thumbnail: ships-log            # any object in originals/, used as the cover
behavior: [unordered]
see_also:
  - id: https://example.org/records.xml
    type: Dataset
    format: text/xml
```

The whole file is optional; without it the collection is labelled
"IIIF Collection". Two things are worth knowing:

* `label` and `summary` also title the browse page, the viewer and the
  directory listings, and every manifest points back with `partOf`.
* `thumbnail` takes the name of one of your objects and reuses that object's
  thumbnail, so no extra derivative is generated. A full IIIF thumbnail body
  works too, if you would rather point somewhere else.

`rights`, `required_statement` and `provider` fall back to the defaults in
`config.yml`, so setting them once there covers the collection and every object.

## `config.yml`

Site-wide settings, all optional — see the commented `config.yml` in this
repository.

`config.yml` covers how the site is built; what the collection *is* lives in
`collection.yml`.

| Key | Default | Purpose |
| --- | --- | --- |
| `base_url` | from Pages settings | Override the published URL |
| `language` | `en` | Language tag for plain-string labels |
| `tile_size` | `512` | Tile edge in pixels |
| `jpeg_quality` | `85` | Derivative JPEG quality |
| `thumbnail_width` | `400` | Preferred thumbnail width |
| `rights` | — | Default rights statement URL |
| `required_statement` | — | Default attribution shown by viewers |
| `provider` | — | Default provider block shown by viewers |
| `homepage_link` | `true` | Add a `homepage` link to the built-in viewer |

## What gets published

For every image, a level 0 service in canonical Image API 3.0 URL form:

```
iiif/images/ledger/001/info.json
iiif/images/ledger/001/full/max/0/default.jpg        # full resolution
iiif/images/ledger/001/full/625,500/0/default.jpg    # each size in "sizes"
iiif/images/ledger/001/0,0,1024,1024/512,512/0/default.jpg   # tiles
```

`info.json` declares `"profile": "level0"`, the tile grid (`tiles`) and the
scaled whole-image sizes (`sizes`). Because level 0 means "no image processing
at request time", the build writes **exactly** the files those declarations
promise — the tile pyramid, every declared size, and `full/max` — and the build
then verifies that nothing is missing.

The `iiif/manifests` and `iiif/images` URLs are browsable in a browser and also
serve an `index.json` listing everything, which is handy for scripting.

### Level 0 in practice

* Supported, per the specification's level 0 requirements: region `full` and the
  declared tile regions, size `max` and the declared sizes, rotation `0`,
  quality `default`, format `jpg`, and CORS (GitHub Pages sends
  `Access-Control-Allow-Origin: *`, so other people's viewers can load your
  images).
* Not supported, because a static host cannot compute them: arbitrary regions
  and sizes, rotation, `gray`/`bitonal`, other formats, and a redirect from the
  service id to `info.json` — request `<service-id>/info.json` directly, which
  is what viewers do anyway.
* URLs are generated in the canonical form the specification defines
  (`full` / `x,y,w,h` for region, `max` / `w,h` for size), which is exactly what
  OpenSeadragon — and therefore Mirador, Clover and most other viewers — asks a
  version 3 service for.

## Using what you publish

Drag a manifest URL into any IIIF viewer, or point one at it:

```html
<div id="viewer" style="height: 600px"></div>
<script src="https://cdn.jsdelivr.net/npm/mirador@3.3.0/dist/mirador.min.js"></script>
<script>
  Mirador.viewer({
    id: 'viewer',
    windows: [{ manifestId: 'https://user.github.io/repo/iiif/manifests/ledger.json' }],
  });
</script>
```

OpenSeadragon can use an image service on its own:

```js
OpenSeadragon({
  id: 'osd',
  prefixUrl: 'https://cdn.jsdelivr.net/npm/openseadragon@5/build/openseadragon/images/',
  tileSources: 'https://user.github.io/repo/iiif/images/ledger/001/info.json',
});
```

The generated site also includes a browse page and a Mirador viewer at the root
of your Pages URL, so the repository is usable as a small image collection out
of the box.

## Working locally

[uv](https://docs.astral.sh/uv/) handles Python and dependencies.

```sh
uv run iiif-build serve          # build for localhost and serve at :8000
uv run iiif-build                # build into _site/ (needs a base URL, see below)
uv run iiif-build check          # re-validate an existing build
uv run pytest                    # the builder's own tests
```

`build` and `check` need to know the site's absolute URL, because IIIF
identifiers are absolute. They take it from `--base-url`, then `$IIIF_BASE_URL`,
then `config.yml`, then `$GITHUB_REPOSITORY`:

```sh
uv run iiif-build --base-url https://user.github.io/repo
```

Useful flags: `--jobs N` (images processed in parallel), `--force` (rebuild
every derivative), `--originals`/`--out`/`--config`/`--collection`/`--site` to
move files around, `--skip-check` to skip validation.

Rebuilds are incremental: an image whose bytes and settings have not changed
keeps its existing tiles, and derivatives for deleted originals are removed.
The GitHub Actions workflow caches derivatives the same way, so a push that adds
one image only tiles that image.

## Size and limits

Publishing an image costs roughly **three times the size of a full-resolution
JPEG of it** — the tile pyramid, the scaled preview sizes, and the
full-resolution JPEG itself. Measured on ordinary photographs at the default
settings:

| Source image | Files published | Bytes published |
| --- | --- | --- |
| 12 MP (4000×3000), 0.6 MB | 71 | 1.8 MB |
| 48 MP (8000×6000), 2.1 MB | 264 | 5.5 MB |

The multiplier against *your* originals depends on how compressed they already
are: a heavily compressed JPEG grows more, a TIFF or a `jpeg_quality: 95` scan
grows less.

GitHub's relevant limits are 100 MB per file, a recommended 1 GB per repository,
and a recommended 1 GB published Pages site with 100 GB/month of bandwidth. The
build warns as you approach them.

For larger collections: set `tile_size: 1024`, which cuts the number of
published files by about three quarters (a 12 MP image drops from 71 files to
22) for a small saving in bytes; lower `jpeg_quality`; or keep originals in
[Git LFS](https://docs.github.com/repositories/working-with-files/managing-large-files)
— the workflow already checks LFS files out.

## Repository layout

```
originals/            your images (and optional metadata sidecars)
collection.yml        metadata for this repository's IIIF collection
config.yml            build settings
site/                 browse page, viewer page, stylesheet (edit freely)
src/iiif_pages/       the builder
tests/                its tests
.github/workflows/    publish to Pages; run tests on branches and PRs
_site/                build output (git-ignored, produced by CI)
```

The builder is plain Python with Pillow and PyYAML. `src/iiif_pages/images.py`
holds the tiling and `info.json` logic, `presentation.py` builds the manifests
and the collection, and
`check.py` validates a finished build — including by re-deriving every URL
OpenSeadragon would request and confirming the file exists.

## Licence

No licence is included. Add one that suits your images and your code before
publishing.
