"""Open IMDb episode-list season links in Firefox and save episode pages."""

from __future__ import annotations

import argparse
import html
import re
import shutil
import subprocess
import sys
import time
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import save_firefox_imdb_tabs as tab_saver


DEFAULT_PAGE_LOAD_DELAY = 3.0
DEFAULT_OPEN_TABS_DELAY = 2.0
DEFAULT_TEMP_SAVE_TIMEOUT = 10.0
EPISODE_LINK_RE = re.compile(
    r'href="(?P<href>https://www\.imdb\.com/title/tt\d+/\?ref_=ttep_ep_\d+[^"]*)"'
)


def main() -> int:
    """Run the IMDb season saver CLI."""
    args = _parse_args()
    try:
        output_dir = tab_saver.catalog_root_path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = save_firefox_imdb_season(
            output_dir=output_dir,
            season=args.season,
            max_tabs=args.max_tabs,
            backend=args.backend,
            window_id=args.window_id,
            overwrite=bool(args.overwrite),
            delay=float(args.delay),
            page_load_delay=float(args.page_load_delay),
            open_tabs_delay=float(args.open_tabs_delay),
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
        print("no IMDb episode tabs saved")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "From an open IMDb episode-list page, load a season, open its episode "
            "pages in Firefox tabs, and save them as {imdb_id}.html."
        )
    )
    parser.add_argument(
        "output_dir", type=Path, help="directory to save IMDb episode HTML pages into"
    )
    parser.add_argument(
        "--season",
        type=int,
        required=True,
        help="season number to load from the IMDb episode list",
    )
    parser.add_argument(
        "--max-tabs",
        type=int,
        default=80,
        help="maximum number of tabs to inspect after opening episodes, default: 80",
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
        default=DEFAULT_PAGE_LOAD_DELAY,
        help=f"seconds to wait after loading the season page, default: {DEFAULT_PAGE_LOAD_DELAY}",
    )
    parser.add_argument(
        "--open-tabs-delay",
        type=float,
        default=DEFAULT_OPEN_TABS_DELAY,
        help=f"seconds to wait after opening episode tabs, default: {DEFAULT_OPEN_TABS_DELAY}",
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
        help="open episode tabs and print target paths without opening the Save Page dialog",
    )
    return parser.parse_args()


def save_firefox_imdb_season(
    *,
    output_dir: Path,
    season: int,
    max_tabs: int,
    backend: str = "auto",
    window_id: str | None = None,
    overwrite: bool = False,
    delay: float = 0.4,
    page_load_delay: float = DEFAULT_PAGE_LOAD_DELAY,
    open_tabs_delay: float = DEFAULT_OPEN_TABS_DELAY,
    save_delay: float = tab_saver.DEFAULT_SAVE_DELAY,
    save_dialog_delay: float = tab_saver.DEFAULT_SAVE_DIALOG_DELAY,
    focus_delay: float = 5.0,
    dry_run: bool = False,
    close_saved_tabs: bool = False,
) -> list[Path]:
    """Open all episodes for an IMDb season page and save episode tabs."""
    if season < 1:
        raise ValueError("--season must be at least 1")
    if max_tabs < 1:
        raise ValueError("--max-tabs must be at least 1")

    backend = tab_saver._resolve_backend(backend)
    clipboard_command = tab_saver._clipboard_command()
    _focus_firefox(backend, window_id, delay, focus_delay)

    current_url = tab_saver._copy_current_url(backend, clipboard_command, delay)
    season_url = build_season_url(current_url, season)
    _navigate_to_url(backend, season_url, delay)
    time.sleep(page_load_delay)
    episode_urls = _episode_urls_from_current_page(
        backend, delay, save_delay, save_dialog_delay
    )
    if not episode_urls:
        raise RuntimeError("No IMDb episode links found on the loaded season page")
    _open_episode_tabs(episode_urls, open_tabs_delay)
    expected_ids = {
        imdb_id
        for url in episode_urls
        if (imdb_id := tab_saver.extract_imdb_id(url)) is not None
    }

    return _save_open_episode_tabs(
        output_dir=output_dir,
        max_tabs=max_tabs,
        backend=backend,
        clipboard_command=clipboard_command,
        overwrite=overwrite,
        delay=delay,
        save_delay=save_delay,
        save_dialog_delay=save_dialog_delay,
        dry_run=dry_run,
        expected_ids=expected_ids,
        close_saved_tabs=close_saved_tabs,
    )


def build_season_url(url: str, season: int) -> str:
    """Return the IMDb episode-list URL for the requested season."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc.endswith("imdb.com"):
        raise ValueError("Current Firefox tab must be an IMDb episode-list page")
    if "/title/" not in parsed.path or not parsed.path.rstrip("/").endswith(
        "/episodes"
    ):
        raise ValueError("Current Firefox tab must be an IMDb episode-list page")

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["season"] = str(season)
    query.pop("year", None)
    query.pop("topRated", None)
    return urlunparse(parsed._replace(query=urlencode(query)))


def should_save_episode_url(url: str) -> bool:
    """Return whether a Firefox tab URL is an IMDb episode title page."""
    parsed = urlparse(url)
    return (
        parsed.netloc.endswith("imdb.com")
        and tab_saver.extract_imdb_id(url) is not None
        and not parsed.path.rstrip("/").endswith("/episodes")
    )


def extract_episode_urls(html_text: str) -> list[str]:
    """Extract unique IMDb episode URLs from a saved episode-list page."""
    urls: list[str] = []
    seen_ids: set[str] = set()
    for match in EPISODE_LINK_RE.finditer(html_text):
        url = html.unescape(match.group("href"))
        imdb_id = tab_saver.extract_imdb_id(url)
        if imdb_id is None or imdb_id in seen_ids:
            continue
        seen_ids.add(imdb_id)
        urls.append(url)
    return urls


def _focus_firefox(
    backend: str, window_id: str | None, delay: float, focus_delay: float
) -> None:
    if backend == "xdotool":
        firefox_window = window_id or tab_saver._find_firefox_window()
        tab_saver._run(["xdotool", "windowactivate", "--sync", firefox_window])
        time.sleep(delay)
    elif window_id is not None:
        raise ValueError("--window-id is only supported with --backend xdotool")
    else:
        tab_saver._wait_for_manual_focus(focus_delay)


def _navigate_to_url(backend: str, url: str, delay: float) -> None:
    tab_saver._key(backend, "ctrl+l")
    time.sleep(delay)
    tab_saver._type_text(backend, url)
    time.sleep(delay)
    tab_saver._key(backend, "Return")


def _episode_urls_from_current_page(
    backend: str, delay: float, save_delay: float, save_dialog_delay: float
) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="hackflix-imdb-season-") as temp_dir:
        html_path = Path(temp_dir) / "episode-list.html"
        tab_saver._save_current_page(
            backend, html_path, delay, save_delay, save_dialog_delay
        )
        html_text = _read_saved_html(html_path, DEFAULT_TEMP_SAVE_TIMEOUT)
        return extract_episode_urls(html_text)


def _read_saved_html(html_path: Path, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    candidates = [html_path, tab_saver._firefox_save_dialog_path(html_path)]
    while time.monotonic() < deadline:
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        matches = sorted(html_path.parent.glob(f"{html_path.stem}*.htm*"))
        for match in matches:
            if match.is_file():
                return match.read_text(encoding="utf-8", errors="replace")
        time.sleep(0.25)
    raise FileNotFoundError(html_path)


def _open_episode_tabs(urls: list[str], open_tabs_delay: float) -> None:
    firefox = shutil.which("firefox")
    if firefox is None:
        raise RuntimeError("Missing required command: firefox")
    for url in urls:
        tab_saver._run([firefox, "--new-tab", url])
        time.sleep(0.1)
    time.sleep(open_tabs_delay)


def _save_open_episode_tabs(
    *,
    output_dir: Path,
    max_tabs: int,
    backend: str,
    clipboard_command: list[str],
    overwrite: bool,
    delay: float,
    save_delay: float,
    save_dialog_delay: float,
    dry_run: bool,
    expected_ids: set[str] | None = None,
    close_saved_tabs: bool = False,
) -> list[Path]:
    first_url: str | None = None
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    saved_paths: list[Path] = []

    for index in range(max_tabs):
        url = tab_saver._copy_current_url(backend, clipboard_command, delay)
        if index == 0:
            first_url = url
        elif url == first_url:
            break

        if url in seen_urls:
            tab_saver._next_tab(backend, delay)
            continue
        seen_urls.add(url)

        imdb_id = tab_saver.extract_imdb_id(url)
        if (
            imdb_id is not None
            and imdb_id not in seen_ids
            and should_save_episode_url(url)
        ):
            if expected_ids is not None and imdb_id not in expected_ids:
                tab_saver._next_tab(backend, delay)
                continue
            seen_ids.add(imdb_id)
            destination = output_dir / f"{imdb_id}.html"
            if overwrite or not destination.exists():
                if not dry_run:
                    tab_saver._save_current_page(
                        backend, destination, delay, save_delay, save_dialog_delay
                    )
                saved_paths.append(destination)
            if close_saved_tabs:
                tab_saver._close_tab(backend, delay)
                if expected_ids is not None and expected_ids.issubset(seen_ids):
                    break
                continue

        if expected_ids is not None and expected_ids.issubset(seen_ids):
            break

        tab_saver._next_tab(backend, delay)

    return saved_paths


if __name__ == "__main__":
    raise SystemExit(main())
