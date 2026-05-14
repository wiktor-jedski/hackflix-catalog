"""Add a movie, series, or episode from an IMDb page to catalog.json."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


CATALOG_ROOT = Path(__file__).resolve().parent
DEFAULT_CATALOG = CATALOG_ROOT / "catalog.json"
IMDB_ID_RE = re.compile(r"tt\d+")
YEAR_RE = re.compile(r"^\d{4}")
SERIES_LINK_RE = re.compile(
    r'data-testid="hero-title-block__series-link"[^>]+href="[^"]*/title/(?P<id>tt\d+)/[^"]*"[^>]*>'
    r"(?P<title>.*?)</a>",
    re.DOTALL,
)
SEASON_EPISODE_RE = re.compile(
    r"S(?P<season>\d+)\s*<!--\s*-->\.<!--\s*-->\s*E(?P<episode>\d+)"
)


@dataclass(frozen=True)
class ImdbMetadata:
    """Metadata extracted from an IMDb title page."""

    imdb_id: str
    media_type: str
    title: str
    genres: list[str]
    poster_url: str | None
    year: int | None
    series_imdb_id: str | None = None
    series_title: str | None = None
    season_number: int | None = None
    episode_number: int | None = None


class JsonLdScriptParser(HTMLParser):
    """Extract JSON-LD script contents from an HTML document."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self._capturing = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "script":
            return
        attributes = {name.lower(): value for name, value in attrs}
        if attributes.get("type") == "application/ld+json":
            self._capturing = True
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self.scripts.append("".join(self._buffer).strip())
            self._capturing = False
            self._buffer = []


def main() -> int:
    """Run the IMDb catalog updater CLI."""
    args = _parse_args()
    catalog_path = catalog_root_path(args.catalog)
    html_path = catalog_root_path(args.html) if args.html is not None else None
    try:
        url = resolve_imdb_url(args.url, html_path)
        html = _load_html(url, html_path)
        metadata = parse_imdb_page(html, url)
        catalog = load_catalog(catalog_path)
        entry = upsert_catalog_entry(
            catalog,
            metadata,
            update=args.update,
            create_series=args.create_series,
        )
    except (OSError, ValueError, HTTPError, URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(entry, indent=2, ensure_ascii=False))
    if args.write:
        write_catalog(catalog_path, catalog)
        print(f"updated {catalog_path}")
    else:
        print("dry run only; pass --write to update the catalog")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape an IMDb title page and add it to catalog.json."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help=(
            "IMDb title URL, for example https://www.imdb.com/title/tt0111161/. "
            "Optional when --html filename contains a tt id."
        ),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"catalog path, default: {DEFAULT_CATALOG}",
    )
    parser.add_argument(
        "--html",
        type=Path,
        help="read IMDb HTML from a saved file instead of fetching the URL",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the new entry back to the catalog",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="update existing movie or series metadata instead of failing on duplicates",
    )
    parser.add_argument(
        "--create-series",
        action="store_true",
        help="allow an episode page to create its parent series when it does not exist yet",
    )
    return parser.parse_args()


def catalog_root_path(path: Path) -> Path:
    """Resolve a CLI path relative to hackflix-catalog."""
    if path.is_absolute():
        return path
    return CATALOG_ROOT / path


def resolve_imdb_url(url: str | None, html_path: Path | None) -> str:
    """Return an IMDb URL from an explicit URL or an HTML filename containing a tt id."""
    if url:
        return url
    if html_path is None:
        raise ValueError(
            "URL is required unless --html filename contains an IMDb tt id"
        )

    match = IMDB_ID_RE.search(html_path.name)
    if match is None:
        raise ValueError(
            "URL is required because --html filename does not contain an IMDb tt id"
        )
    return f"https://www.imdb.com/title/{match.group(0)}/"


def _load_html(url: str, html_path: Path | None) -> str:
    if html_path is not None:
        return html_path.read_text(encoding="utf-8")
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
            ),
        },
    )
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def load_catalog(path: Path) -> dict[str, Any]:
    """Load a catalog JSON object."""
    with path.open(encoding="utf-8") as catalog_file:
        catalog = json.load(catalog_file)
    if not isinstance(catalog, dict):
        raise ValueError("Catalog root must be a JSON object")
    if not isinstance(catalog.get("items"), list):
        raise ValueError("Catalog must contain an items list")
    return catalog


def write_catalog(path: Path, catalog: dict[str, Any]) -> None:
    """Write a catalog JSON object."""
    catalog["timestamp"] = (
        datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )
    path.write_text(
        json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def parse_imdb_page(html: str, url: str) -> ImdbMetadata:
    """Parse IMDb JSON-LD metadata from a title page."""
    imdb_id = _extract_imdb_id(url)
    ld_object = _find_title_json_ld(html)
    media_type = _media_type(ld_object)
    title = _string_value(ld_object.get("name"))
    if not title:
        raise ValueError("IMDb page is missing a title in JSON-LD")

    if media_type == "movie":
        return ImdbMetadata(
            imdb_id=imdb_id,
            media_type="movie",
            title=title,
            genres=_genres(ld_object.get("genre")),
            poster_url=_image_url(ld_object.get("image")),
            year=_year(ld_object.get("datePublished")),
        )

    if media_type == "series":
        return ImdbMetadata(
            imdb_id=imdb_id,
            media_type="series",
            title=title,
            genres=_genres(ld_object.get("genre")),
            poster_url=_image_url(ld_object.get("image")),
            year=_year(ld_object.get("datePublished")),
        )

    if media_type == "episode":
        return ImdbMetadata(
            imdb_id=imdb_id,
            media_type="episode",
            title=title,
            genres=_genres(ld_object.get("genre")),
            poster_url=_image_url(ld_object.get("image")),
            year=_year(ld_object.get("datePublished")),
            series_imdb_id=_series_imdb_id(ld_object) or _series_from_html(html)[0],
            series_title=_series_title(ld_object) or _series_from_html(html)[1],
            season_number=_nested_int(ld_object, "partOfSeason", "seasonNumber")
            or _season_episode_from_html(html)[0],
            episode_number=_int_value(ld_object.get("episodeNumber"))
            or _season_episode_from_html(html)[1],
        )

    raise ValueError(f"Unsupported IMDb JSON-LD type: {ld_object.get('@type')}")


def upsert_catalog_entry(
    catalog: dict[str, Any],
    metadata: ImdbMetadata,
    *,
    update: bool = False,
    create_series: bool = False,
) -> dict[str, Any]:
    """Add IMDb metadata to the catalog and return the affected entry."""
    if metadata.media_type == "movie":
        return _upsert_movie(catalog, metadata, update=update)
    if metadata.media_type == "series":
        return _upsert_series(catalog, metadata, update=update)
    if metadata.media_type == "episode":
        return _upsert_episode(catalog, metadata, create_series=create_series)
    raise ValueError(f"Unsupported metadata media type: {metadata.media_type}")


def _find_title_json_ld(html: str) -> dict[str, Any]:
    parser = JsonLdScriptParser()
    parser.feed(html)
    for script in parser.scripts:
        try:
            value = json.loads(script)
        except json.JSONDecodeError:
            continue
        for candidate in _walk_json(value):
            if not isinstance(candidate, dict):
                continue
            candidate_type = candidate.get("@type")
            if candidate_type in {"Movie", "TVSeries", "TVEpisode"} and candidate.get(
                "name"
            ):
                return candidate
    raise ValueError("Could not find IMDb Movie, TVSeries, or TVEpisode JSON-LD data")


def _walk_json(value: Any) -> list[Any]:
    if isinstance(value, list):
        children: list[Any] = []
        for item in value:
            children.extend(_walk_json(item))
        return children
    if isinstance(value, dict):
        children = [value]
        graph = value.get("@graph")
        if graph is not None:
            children.extend(_walk_json(graph))
        return children
    return []


def _upsert_movie(
    catalog: dict[str, Any], metadata: ImdbMetadata, *, update: bool
) -> dict[str, Any]:
    existing = _find_item(catalog, metadata.imdb_id)
    if existing is not None:
        if not update:
            raise ValueError(
                f"Movie {metadata.imdb_id} already exists; pass --update to refresh it"
            )
        _update_movie_metadata(existing, metadata)
        return existing

    entry = _movie_entry(metadata)
    catalog["items"].append(entry)
    return entry


def _upsert_series(
    catalog: dict[str, Any], metadata: ImdbMetadata, *, update: bool
) -> dict[str, Any]:
    existing = _find_item(catalog, metadata.imdb_id)
    if existing is not None:
        if existing.get("type") != "series":
            raise ValueError(
                f"Catalog item {metadata.imdb_id} exists but is not a series"
            )
        if not update:
            raise ValueError(
                f"Series {metadata.imdb_id} already exists; pass --update to refresh it"
            )
        _update_series_metadata(existing, metadata)
        return existing

    entry = _series_entry(metadata)
    catalog["items"].append(entry)
    return entry


def _upsert_episode(
    catalog: dict[str, Any], metadata: ImdbMetadata, *, create_series: bool
) -> dict[str, Any]:
    if metadata.series_imdb_id is None or metadata.series_title is None:
        raise ValueError("Episode page is missing parent series metadata")
    if metadata.season_number is None or metadata.episode_number is None:
        raise ValueError("Episode page is missing season or episode number")

    series = _find_item(catalog, metadata.series_imdb_id)
    if series is None:
        if not create_series:
            raise ValueError(
                f"Series {metadata.series_imdb_id} is not in the catalog; add the series page first "
                "or pass --create-series"
            )
        series = _series_entry(
            ImdbMetadata(
                imdb_id=metadata.series_imdb_id,
                media_type="series",
                title=metadata.series_title,
                genres=metadata.genres,
                poster_url=metadata.poster_url,
                year=None,
            )
        )
        catalog["items"].append(series)
    elif series.get("type") != "series":
        raise ValueError(
            f"Catalog item {metadata.series_imdb_id} exists but is not a series"
        )

    season = _find_season(series, metadata.season_number)
    if season is None:
        season = {"season_number": metadata.season_number, "magnet": "", "episodes": []}
        series.setdefault("seasons", []).append(season)
        series["seasons"].sort(key=lambda item: int(item.get("season_number") or 0))

    episode = _find_episode(season, metadata.episode_number)
    episode_entry = _episode_entry(metadata)
    if episode is not None:
        _update_episode_metadata(episode, metadata)
        return episode

    episodes = _episode_list(season)
    episodes.append(episode_entry)
    episodes.sort(key=lambda item: int(item.get("number") or 0))
    return episode_entry


def _movie_entry(metadata: ImdbMetadata) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": metadata.imdb_id,
        "type": "movie",
        "title": metadata.title,
        "genres": _join_genres(metadata.genres),
        "imdb_id": metadata.imdb_id,
        "magnet": "",
        "poster_url": metadata.poster_url or "",
        "subtitle_id": None,
        "translation_needed": False,
    }
    if metadata.year is not None:
        entry["year"] = metadata.year
    return entry


def _series_entry(metadata: ImdbMetadata) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": metadata.imdb_id,
        "type": "series",
        "title": metadata.title,
        "genres": _join_genres(metadata.genres),
        "imdb_id": metadata.imdb_id,
        "poster_url": metadata.poster_url or "",
        "seasons": [],
    }
    if metadata.year is not None:
        entry["year"] = metadata.year
    return entry


def _episode_entry(metadata: ImdbMetadata) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "number": metadata.episode_number,
        "title": metadata.title,
        "imdb_id": metadata.imdb_id,
        "subtitle_id": None,
        "translation_needed": False,
    }
    if metadata.year is not None:
        entry["year"] = metadata.year
    return entry


def _update_movie_metadata(entry: dict[str, Any], metadata: ImdbMetadata) -> None:
    entry["title"] = metadata.title
    entry["genres"] = _join_genres(metadata.genres)
    entry["imdb_id"] = metadata.imdb_id
    if metadata.poster_url:
        entry["poster_url"] = metadata.poster_url
    if metadata.year is not None:
        entry["year"] = metadata.year


def _update_series_metadata(entry: dict[str, Any], metadata: ImdbMetadata) -> None:
    entry["title"] = metadata.title
    entry["genres"] = _join_genres(metadata.genres)
    entry["imdb_id"] = metadata.imdb_id
    if metadata.poster_url:
        entry["poster_url"] = metadata.poster_url
    if metadata.year is not None:
        entry["year"] = metadata.year
    entry.setdefault("seasons", [])


def _update_episode_metadata(entry: dict[str, Any], metadata: ImdbMetadata) -> None:
    entry["number"] = metadata.episode_number
    entry["title"] = metadata.title
    entry["imdb_id"] = metadata.imdb_id
    if metadata.year is not None:
        entry["year"] = metadata.year


def _find_item(catalog: dict[str, Any], imdb_id: str) -> dict[str, Any] | None:
    for item in catalog.get("items", []):
        if item.get("id") == imdb_id or item.get("imdb_id") == imdb_id:
            return item
    return None


def _find_season(series: dict[str, Any], season_number: int) -> dict[str, Any] | None:
    for season in series.get("seasons", []):
        if _int_value(season.get("season_number")) == season_number:
            return season
    return None


def _find_episode(season: dict[str, Any], episode_number: int) -> dict[str, Any] | None:
    for episode in _episode_list(season):
        if _int_value(episode.get("number")) == episode_number:
            return episode
    return None


def _episode_list(season: dict[str, Any]) -> list[dict[str, Any]]:
    episodes = season.setdefault("episodes", [])
    if not isinstance(episodes, list):
        raise ValueError("Season episodes must be a list")
    return cast(list[dict[str, Any]], episodes)


def _extract_imdb_id(url: str) -> str:
    match = IMDB_ID_RE.search(url)
    if match is None:
        raise ValueError("URL does not contain an IMDb tt id")
    return match.group(0)


def _media_type(data: dict[str, Any]) -> str:
    imdb_type = data.get("@type")
    if imdb_type == "Movie":
        return "movie"
    if imdb_type == "TVSeries":
        return "series"
    if imdb_type == "TVEpisode":
        return "episode"
    raise ValueError(f"Unsupported IMDb JSON-LD type: {imdb_type}")


def _genres(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _join_genres(genres: list[str]) -> str:
    return ", ".join(genres)


def _image_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _string_value(value.get("url"))
    return None


def _year(value: Any) -> int | None:
    string = _string_value(value)
    match = YEAR_RE.match(string)
    if match is None:
        return None
    return _int_value(match.group(0))


def _series_imdb_id(data: dict[str, Any]) -> str | None:
    series = data.get("partOfSeries")
    if not isinstance(series, dict):
        return None
    url = _string_value(series.get("url") or series.get("@id"))
    match = IMDB_ID_RE.search(url)
    if match is None:
        return None
    return match.group(0)


def _series_title(data: dict[str, Any]) -> str | None:
    series = data.get("partOfSeries")
    if not isinstance(series, dict):
        return None
    return _string_value(series.get("name")) or None


def _series_from_html(html: str) -> tuple[str | None, str | None]:
    match = SERIES_LINK_RE.search(html)
    if match is None:
        return None, None
    title = re.sub(r"<[^>]+>", "", match.group("title"))
    return match.group("id"), html_lib.unescape(title).strip() or None


def _season_episode_from_html(html: str) -> tuple[int | None, int | None]:
    match = SEASON_EPISODE_RE.search(html)
    if match is None:
        return None, None
    return _int_value(match.group("season")), _int_value(match.group("episode"))


def _nested_int(data: dict[str, Any], parent_key: str, child_key: str) -> int | None:
    parent = data.get(parent_key)
    if not isinstance(parent, dict):
        return None
    return _int_value(parent.get(child_key))


def _int_value(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


if __name__ == "__main__":
    raise SystemExit(main())
