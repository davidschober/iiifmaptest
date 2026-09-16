# originals/

Drop your images in this folder. Two layouts are recognised:

| What you add | What you get |
| --- | --- |
| `photograph.jpg` | a manifest with one canvas, at `/iiif/manifests/photograph.json` |
| `ledger/` containing images | a manifest with one canvas per image, at `/iiif/manifests/ledger.json` |

Names become URL identifiers, lowercased with punctuation turned into
hyphens: `Ship's Log 1846.jpg` becomes `ships-log-1846`.

Images inside a folder are ordered naturally (`page2.jpg` before `page10.jpg`);
sub-folders are allowed and are included in that order. Accepted file types:
`.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`, `.webp` (derivatives are always JPEG).

Optional metadata sidecars, both in YAML (or JSON):

* `photograph.yml` next to a single image
* `ledger/object.yml` inside a folder

See the two placeholder objects here for a worked example, and the repository
README for every key a sidecar accepts. Delete the placeholders when you add
your own material.

Metadata for the collection as a whole — everything in this folder, published
at `/iiif/collection.json` — goes in `collection.yml` in the repository root,
using the same keys.
