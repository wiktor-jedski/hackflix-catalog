"""Add all saved IMDb episode pages from one season directory to catalog.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from add_imdb_to_catalog import (
    DEFAULT_CATALOG,
    ImdbMetadata,
    catalog_root_path,
    load_catalog,
    parse_imdb_page,
    resolve_imdb_url,
    upsert_catalog_entry,
    write_catalog,
)


def main() -> int:
    """Run the saved season directory importer CLI."""
    args = _parse_args()
    catalog_path = catalog_root_path(args.catalog)
    directory = catalog_root_path(args.directory)
    try:
        catalog = load_catalog(catalog_path)
        entries = add_season_directory(
            catalog,
            directory,
            create_series=bool(args.create_series),
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(entries, indent=2, ensure_ascii=False))
    if args.write:
        write_catalog(catalog_path, catalog)
        print(f"updated {catalog_path}")
    else:
        print("dry run only; pass --write to update the catalog")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add all saved IMDb episode pages in a season directory."
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="directory containing saved IMDb episode pages named like tt1234567.html",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"catalog path, default: {DEFAULT_CATALOG}",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the added entries back to the catalog",
    )
    parser.add_argument(
        "--create-series",
        action="store_true",
        help="allow an episode page to create its parent series when it does not exist yet",
    )
    return parser.parse_args()


def add_season_directory(
    catalog: dict[str, Any], directory: Path, *, create_series: bool = False
) -> list[dict[str, Any]]:
    """Add all saved IMDb episode pages in a directory to a catalog object."""
    html_paths = _episode_html_paths(directory)
    metadata_items = [_episode_metadata(html_path) for html_path in html_paths]
    metadata_items.sort(key=_episode_sort_key)

    entries: list[dict[str, Any]] = []
    for metadata in metadata_items:
        entries.append(
            upsert_catalog_entry(catalog, metadata, create_series=create_series)
        )
    return entries


def _episode_metadata(html_path: Path) -> ImdbMetadata:
    url = resolve_imdb_url(None, html_path)
    html = html_path.read_text(encoding="utf-8")
    metadata = parse_imdb_page(html, url)
    if metadata.media_type != "episode":
        raise ValueError(
            f"{html_path} is a {metadata.media_type} page, expected episode"
        )
    return metadata


def _episode_sort_key(metadata: ImdbMetadata) -> tuple[int, int, str]:
    return (
        metadata.season_number or 0,
        metadata.episode_number or 0,
        metadata.imdb_id,
    )


def _episode_html_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"{directory} is not a directory")

    html_paths = sorted(path for path in directory.iterdir() if path.suffix == ".html")
    if not html_paths:
        raise ValueError(f"{directory} does not contain any .html files")
    return html_paths


if __name__ == "__main__":
    raise SystemExit(main())
