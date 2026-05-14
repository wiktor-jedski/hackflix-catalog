"""Save every season from an open IMDb episode-list page in Firefox."""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

import save_firefox_imdb_season as season_saver
import save_firefox_imdb_tabs as tab_saver


SEASON_LINK_RE = re.compile(
    r'href="(?P<href>https://www\.imdb\.com/title/tt\d+/episodes/\?[^"]*season=(?P<season>\d+)[^"]*)"'
)


def main() -> int:
    """Run the IMDb series saver CLI."""
    args = _parse_args()
    try:
        output_dir = tab_saver.catalog_root_path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = save_firefox_imdb_series(
            output_dir=output_dir,
            backend=args.backend,
            window_id=args.window_id,
            overwrite=bool(args.overwrite),
            delay=float(args.delay),
            page_load_delay=float(args.page_load_delay),
            open_tabs_delay=float(args.open_tabs_delay),
            max_tabs=int(args.max_tabs),
            save_delay=float(args.save_delay),
            save_dialog_delay=float(args.save_dialog_delay),
            focus_delay=float(args.focus_delay),
            dry_run=bool(args.dry_run),
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if saved:
        for path in saved:
            print(path)
    else:
        print("no IMDb episode pages saved")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "From an open IMDb episode-list page, save all seasons into "
            "{output_dir}/{season_number}/."
        )
    )
    parser.add_argument(
        "output_dir",
        type=Path,
        help="series directory to create season subdirectories in",
    )
    parser.add_argument(
        "--backend",
        choices=tab_saver.BACKENDS,
        default="auto",
        help="automation backend: auto, xdotool, or ydotool; default: auto",
    )
    parser.add_argument(
        "--window-id",
        help="xdotool-only Firefox window id; defaults to the first visible Firefox window",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="save even when {imdb_id}.html already exists",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="delay after normal GUI actions in seconds, default: 0.25",
    )
    parser.add_argument(
        "--page-load-delay",
        type=float,
        default=season_saver.DEFAULT_PAGE_LOAD_DELAY,
        help=(
            "seconds to wait after loading each season list, "
            f"default: {season_saver.DEFAULT_PAGE_LOAD_DELAY}"
        ),
    )
    parser.add_argument(
        "--open-tabs-delay",
        type=float,
        default=season_saver.DEFAULT_OPEN_TABS_DELAY,
        help=(
            "seconds to wait after opening each season's episode tabs, "
            f"default: {season_saver.DEFAULT_OPEN_TABS_DELAY}"
        ),
    )
    parser.add_argument(
        "--max-tabs",
        type=int,
        default=80,
        help="maximum number of tabs to inspect per season, default: 80",
    )
    parser.add_argument(
        "--save-delay",
        type=float,
        default=tab_saver.DEFAULT_SAVE_DELAY,
        help=(
            "delay after confirming the Save Page dialog, "
            f"default: {tab_saver.DEFAULT_SAVE_DELAY}"
        ),
    )
    parser.add_argument(
        "--save-dialog-delay",
        type=float,
        default=tab_saver.DEFAULT_SAVE_DIALOG_DELAY,
        help=(
            "delay after pressing Ctrl+S before typing the save path, "
            f"default: {tab_saver.DEFAULT_SAVE_DIALOG_DELAY}"
        ),
    )
    parser.add_argument(
        "--focus-delay",
        type=float,
        default=5.0,
        help="ydotool-only seconds to focus Firefox before automation starts, default: 5",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print target paths without opening the Save Page dialog",
    )
    return parser.parse_args()


def save_firefox_imdb_series(
    *,
    output_dir: Path,
    backend: str = "auto",
    window_id: str | None = None,
    overwrite: bool = False,
    delay: float = 0.4,
    page_load_delay: float = season_saver.DEFAULT_PAGE_LOAD_DELAY,
    open_tabs_delay: float = season_saver.DEFAULT_OPEN_TABS_DELAY,
    max_tabs: int = 80,
    save_delay: float = tab_saver.DEFAULT_SAVE_DELAY,
    save_dialog_delay: float = tab_saver.DEFAULT_SAVE_DIALOG_DELAY,
    focus_delay: float = 5.0,
    dry_run: bool = False,
) -> list[Path]:
    """Save every season from an IMDb episode-list page."""
    if max_tabs < 1:
        raise ValueError("--max-tabs must be at least 1")

    backend = tab_saver._resolve_backend(backend)
    clipboard_command = tab_saver._clipboard_command()
    season_saver._focus_firefox(backend, window_id, delay, focus_delay)

    current_url = tab_saver._copy_current_url(backend, clipboard_command, delay)
    season_saver.build_season_url(current_url, 1)
    html_text = _saved_current_page_html(backend, delay, save_delay, save_dialog_delay)
    seasons = extract_seasons(html_text)
    if not seasons:
        raise RuntimeError("No IMDb seasons found on the episode-list page")

    saved_paths: list[Path] = []
    for season in seasons:
        season_dir = output_dir / f"{season:02d}"
        season_dir.mkdir(parents=True, exist_ok=True)
        season_url = season_saver.build_season_url(current_url, season)
        season_saver._navigate_to_url(backend, season_url, delay)
        time.sleep(page_load_delay)

        season_html = _saved_current_page_html(
            backend, delay, save_delay, save_dialog_delay
        )
        episode_urls = season_saver.extract_episode_urls(season_html)
        if not episode_urls:
            raise RuntimeError(f"No IMDb episode links found for season {season}")
        expected_ids = {
            imdb_id
            for url in episode_urls
            if (imdb_id := tab_saver.extract_imdb_id(url)) is not None
        }
        season_saver._open_episode_tabs(episode_urls, open_tabs_delay)
        saved_paths.extend(
            season_saver._save_open_episode_tabs(
                output_dir=season_dir,
                max_tabs=max_tabs,
                backend=backend,
                clipboard_command=clipboard_command,
                overwrite=overwrite,
                delay=delay,
                save_delay=save_delay,
                save_dialog_delay=save_dialog_delay,
                dry_run=dry_run,
                expected_ids=expected_ids,
                close_saved_tabs=True,
            )
        )

    return saved_paths


def extract_seasons(html_text: str) -> list[int]:
    """Extract season numbers from a saved IMDb episode-list page."""
    seasons: list[int] = []
    seen: set[int] = set()
    for match in SEASON_LINK_RE.finditer(html_text):
        url = html.unescape(match.group("href"))
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        season_text = query.get("season") or match.group("season")
        season = int(season_text)
        if season not in seen:
            seen.add(season)
            seasons.append(season)
    return sorted(seasons)


def _saved_current_page_html(
    backend: str, delay: float, save_delay: float, save_dialog_delay: float
) -> str:
    with tempfile.TemporaryDirectory(prefix="hackflix-imdb-series-") as temp_dir:
        html_path = Path(temp_dir) / "episode-list.html"
        tab_saver._save_current_page(
            backend, html_path, delay, save_delay, save_dialog_delay
        )
        return season_saver._read_saved_html(
            html_path, season_saver.DEFAULT_TEMP_SAVE_TIMEOUT
        )


if __name__ == "__main__":
    raise SystemExit(main())
