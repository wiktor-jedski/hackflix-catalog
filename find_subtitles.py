"""Find and optionally write OpenSubtitles file IDs into catalog.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from time import sleep
from typing import Any


CATALOG_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = CATALOG_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.opensubtitles_client import OpenSubtitlesClient  # noqa: E402
from src.utils.subtitle_catalog_matcher import (  # noqa: E402
    DEFAULT_LANGUAGES,
    CatalogTarget,
    SubtitleCandidate,
    apply_candidate,
    build_search_params,
    iter_catalog_targets,
    rank_subtitles,
    target_label,
)


DEFAULT_CATALOG = CATALOG_ROOT / "catalog.json"


def main() -> int:
    """Run the subtitle finder CLI."""
    args = _parse_args()
    catalog_path = catalog_root_path(args.catalog)
    catalog = _load_catalog(catalog_path)
    client = OpenSubtitlesClient()

    changed = False
    targets = _filter_targets(iter_catalog_targets(catalog), bool(args.force))
    for target in targets:
        sleep(0.2)
        print(f"\n{target_label(target)}")
        candidate = _find_best_candidate(
            client,
            target,
            args.languages,
            args.limit,
            include_fallback_query=bool(args.allow_query_fallback),
        )
        if candidate is None:
            print("  no subtitle found")
            continue

        _print_candidate(candidate)
        if args.write:
            apply_candidate(target, candidate)
            changed = True

    if args.write and changed:
        _write_catalog(catalog_path, catalog)
        print(f"\nupdated {catalog_path}")
    elif args.write:
        print("\nno catalog changes needed")
    else:
        print("\ndry run only; pass --write to update catalog")

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Polish subtitles first, then English fallback, for catalog entries."
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"catalog path, default: {DEFAULT_CATALOG}",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=list(DEFAULT_LANGUAGES),
        help="language priority, default: pl en",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="maximum search results per language, default: 10",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write selected subtitle_id values back to the catalog",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="search entries even when subtitle_id is already set",
    )
    parser.add_argument(
        "--allow-query-fallback",
        action="store_true",
        help="allow broad title searches when no IMDb/TMDB id is available",
    )
    return parser.parse_args()


def catalog_root_path(path: Path) -> Path:
    """Resolve a CLI path relative to hackflix-catalog."""
    if path.is_absolute():
        return path
    return CATALOG_ROOT / path


def _load_catalog(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as catalog_file:
        data = json.load(catalog_file)
    if not isinstance(data, dict):
        raise ValueError("Catalog root must be a JSON object")
    return data


def _write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _filter_targets(targets: list[CatalogTarget], force: bool) -> list[CatalogTarget]:
    targets = [target for target in targets if _subtitles_needed(target)]
    if force:
        return targets
    return [
        target
        for target in targets
        if target.subtitle_holder.get("subtitle_id") is None
    ]


def _subtitles_needed(target: CatalogTarget) -> bool:
    return (
        target.item.get("subtitles_needed") is not False
        and target.subtitle_holder.get("subtitles_needed") is not False
    )


def _find_best_candidate(
    client: OpenSubtitlesClient,
    target: CatalogTarget,
    languages: list[str],
    limit: int,
    *,
    include_fallback_query: bool,
) -> SubtitleCandidate | None:
    for language in languages:
        params = build_search_params(
            target,
            language,
            include_fallback_query=include_fallback_query,
        )
        if "query" not in params and not _has_identifier(params):
            print(f"  {language}: skipped, no usable id or query")
            continue

        results = client.search_subtitles_with_params(params=params, limit=limit)
        candidates = rank_subtitles(results, target)
        print(f"  {language}: {len(candidates)} candidate(s)")
        if candidates:
            return candidates[0]

    return None


def _has_identifier(params: dict[str, Any]) -> bool:
    return any(
        key in params
        for key in (
            "imdb_id",
            "tmdb_id",
            "parent_imdb_id",
            "parent_tmdb_id",
            "moviehash",
        )
    )


def _print_candidate(candidate: SubtitleCandidate) -> None:
    print(
        "  selected "
        f"{candidate.language} file_id={candidate.file_id} "
        f"score={candidate.score} downloads={candidate.download_count} "
        f"trusted={candidate.from_trusted} release={candidate.release}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
