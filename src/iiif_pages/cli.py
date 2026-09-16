"""Command line entry point: ``iiif-build [build|check|serve]``."""

from __future__ import annotations

import argparse
import os
import sys
from functools import partial
from pathlib import Path

from . import __version__
from .build import build
from .check import check
from .config import ConfigError, load_config, resolve_base_url
from .discover import DiscoveryError
from .images import SourceImageError
from .presentation import PresentationError

DEFAULT_ORIGINALS = "originals"
DEFAULT_OUT = "_site"
DEFAULT_CONFIG = "config.yml"
DEFAULT_SITE = "site"
DEFAULT_COLLECTION = "collection.yml"
DEFAULT_CACHE = ".iiif-cache"


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        help="Absolute URL the site is published at, e.g. https://user.github.io/repo. "
        "Defaults to $IIIF_BASE_URL, then config.yml, then the GitHub Pages URL for $GITHUB_REPOSITORY.",
    )
    parser.add_argument("--originals", default=DEFAULT_ORIGINALS, type=Path, help="Directory of source images")
    parser.add_argument("--out", default=DEFAULT_OUT, type=Path, help="Directory to write the site into")
    parser.add_argument("--config", default=DEFAULT_CONFIG, type=Path, help="Path to config.yml")
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        type=Path,
        help="Path to the collection metadata sidecar (collection.yml)",
    )
    parser.add_argument("--site", default=DEFAULT_SITE, type=Path, help="Directory of browse/viewer page templates")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE, type=Path, help="Where to keep the build state file")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iiif-build",
        description="Build a static IIIF Image API 3.0 level 0 service and Presentation 3.0 manifests.",
    )
    parser.add_argument("--version", action="version", version=f"iiif-pages {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    build_parser = subparsers.add_parser("build", help="Generate the site (default)")
    _common_arguments(build_parser)
    build_parser.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=min(8, os.cpu_count() or 1),
        help="Number of images to process in parallel",
    )
    build_parser.add_argument("--force", action="store_true", help="Rebuild derivatives even if unchanged")
    build_parser.add_argument("--skip-check", action="store_true", help="Do not validate the output afterwards")
    build_parser.add_argument("--quiet", "-q", action="store_true", help="Only print warnings and errors")

    check_parser = subparsers.add_parser("check", help="Validate an already built site")
    _common_arguments(check_parser)

    serve_parser = subparsers.add_parser("serve", help="Build for localhost and serve the result")
    _common_arguments(serve_parser)
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--jobs", "-j", type=int, default=min(8, os.cpu_count() or 1))
    serve_parser.add_argument("--no-build", action="store_true", help="Serve the existing output without rebuilding")
    return parser


def _report(result, quiet: bool) -> None:
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if quiet:
        return
    print(
        f"Built {result.objects} object(s), {result.images} image(s) "
        f"({result.images_built} generated, {result.images_reused} reused from cache)"
    )
    print(f"Wrote {result.files_written} files, {result.total_bytes / 1e6:.1f} MB")


def _run_check(out: Path, base_url: str) -> int:
    result = check(out=out, base_url=base_url)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.ok:
        print(
            f"Checked {result.manifests} manifest(s), {result.services} image service(s), "
            f"{result.checked_urls} URLs: all present"
        )
        return 0
    for error in result.errors[:50]:
        print(f"error: {error}", file=sys.stderr)
    if len(result.errors) > 50:
        print(f"error: ... and {len(result.errors) - 50} more", file=sys.stderr)
    return 1


def _serve(args, base_url: str) -> int:
    import http.server
    import socketserver

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            # GitHub Pages sends this, so local testing should too.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, fmt: str, *fmt_args) -> None:  # quieter output
            sys.stderr.write("  %s\n" % (fmt % fmt_args))

    handler = partial(Handler, directory=str(args.out))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((args.host, args.port), handler) as httpd:
        print(f"Serving {args.out} at {base_url}  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0].startswith("-") and argv[0] not in ("--version", "-h", "--help")):
        argv.insert(0, "build")  # bare `iiif-build` and `iiif-build --flags` mean build
    args = parser.parse_args(argv)
    if args.command is None:
        args.command = "build"

    try:
        cfg = load_config(args.config)
        if args.command == "serve":
            base_url = f"http://{args.host}:{args.port}"
        else:
            base_url = resolve_base_url(args.base_url, cfg)

        if args.command == "check":
            return _run_check(args.out, base_url)

        if args.command == "serve" and args.no_build:
            return _serve(args, base_url)

        result = build(
            originals=args.originals,
            out=args.out,
            cfg=cfg,
            base_url=base_url,
            site_dir=args.site,
            collection_file=args.collection,
            cache_dir=args.cache_dir,
            jobs=args.jobs,
            force=getattr(args, "force", False),
        )
        _report(result, quiet=getattr(args, "quiet", False))

        if args.command == "serve":
            return _serve(args, base_url)
        if getattr(args, "skip_check", False):
            return 0
        return _run_check(args.out, base_url)
    except (ConfigError, DiscoveryError, SourceImageError, PresentationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
