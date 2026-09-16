"""Copy the static browse/viewer pages and write directory listings."""

from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

from .presentation import collection_url

TEMPLATE_SUFFIXES = {".html", ".css", ".js", ".json", ".txt", ".webmanifest"}


def _render(text: str, title: str, summary: str, base_url: str) -> str:
    replacements = {
        "{{TITLE}}": title,
        "{{TITLE_HTML}}": html.escape(title),
        "{{SUMMARY}}": summary,
        "{{SUMMARY_HTML}}": html.escape(summary),
        "{{BASE_URL}}": base_url,
        "{{COLLECTION_URL}}": collection_url(base_url),
    }
    for token, value in replacements.items():
        text = text.replace(token, value)
    return text


def _listing_page(heading: str, title: str, base_url: str, links: list[tuple[str, str]]) -> str:
    items = "\n".join(
        f'      <li><a href="{html.escape(url)}">{html.escape(label)}</a></li>' for label, url in links
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(heading)} &middot; {html.escape(title)}</title>
<link rel="stylesheet" href="{base_url}/assets/style.css">
</head>
<body>
  <header class="site-header">
    <p class="eyebrow"><a href="{base_url}/">{html.escape(title)}</a></p>
    <h1>{html.escape(heading)}</h1>
  </header>
  <main>
    <ul class="link-list">
{items}
    </ul>
  </main>
</body>
</html>
"""


def write_site_files(
    *,
    out: Path,
    site_dir: Path | None,
    base_url: str,
    title: str,
    summary: str = "",
    manifest_urls: list[str],
    image_service_urls: list[str],
) -> None:
    out.mkdir(parents=True, exist_ok=True)

    # Tell GitHub Pages to publish the tree verbatim: tile directories contain
    # commas and would otherwise be at the mercy of Jekyll's filters.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    if site_dir and site_dir.exists():
        for source in sorted(site_dir.rglob("*")):
            if source.is_dir() or source.name.startswith("."):
                continue
            target = out / source.relative_to(site_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix.lower() in TEMPLATE_SUFFIXES:
                target.write_text(
                    _render(source.read_text(encoding="utf-8"), title, summary, base_url), encoding="utf-8"
                )
            else:
                shutil.copy2(source, target)

    # Machine-readable listings, so /iiif/manifests and /iiif/images are useful
    # URLs even though a static host cannot list a directory.
    manifests_dir = out / "iiif" / "manifests"
    images_dir = out / "iiif" / "images"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    (manifests_dir / "index.json").write_text(
        json.dumps({"collection": collection_url(base_url), "manifests": manifest_urls}, indent=2) + "\n",
        encoding="utf-8",
    )
    (images_dir / "index.json").write_text(
        json.dumps(
            {
                "profile": "level0",
                "images": [{"id": url, "info": f"{url}/info.json"} for url in image_service_urls],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (manifests_dir / "index.html").write_text(
        _listing_page(
            "Manifests",
            title,
            base_url,
            [(url.rsplit("/", 1)[-1], url) for url in manifest_urls],
        ),
        encoding="utf-8",
    )
    (images_dir / "index.html").write_text(
        _listing_page(
            "Image services",
            title,
            base_url,
            [(url.split("/iiif/images/", 1)[-1], f"{url}/info.json") for url in image_service_urls],
        ),
        encoding="utf-8",
    )
