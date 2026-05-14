"""Save open Firefox IMDb title tabs as tt-id HTML files."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


CATALOG_ROOT = Path(__file__).resolve().parent
IMDB_ID_RE = re.compile(r"/title/(?P<id>tt\d+)/|(?P<plain>tt\d+)")
BACKENDS = ("auto", "xdotool", "ydotool")
YDO_KEY_CHORDS = {
    "ctrl+l": ["29:1", "38:1", "38:0", "29:0"],
    "ctrl+c": ["29:1", "46:1", "46:0", "29:0"],
    "ctrl+s": ["29:1", "31:1", "31:0", "29:0"],
    "ctrl+w": ["29:1", "17:1", "17:0", "29:0"],
    "ctrl+Tab": ["29:1", "15:1", "15:0", "29:0"],
    "Escape": ["1:1", "1:0"],
    "Return": ["28:1", "28:0"],
}
DEFAULT_SAVE_DELAY = 1.5
DEFAULT_SAVE_DIALOG_DELAY = 1.0


def main() -> int:
    """Run the Firefox tab saver CLI."""
    args = _parse_args()
    try:
        output_dir = catalog_root_path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = save_firefox_imdb_tabs(
            output_dir=output_dir,
            max_tabs=args.max_tabs,
            backend=args.backend,
            window_id=args.window_id,
            overwrite=bool(args.overwrite),
            delay=float(args.delay),
            save_dialog_delay=float(args.save_dialog_delay),
            save_delay=float(args.save_delay),
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
        print("no IMDb title tabs saved")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cycle Firefox tabs and save IMDb title pages as {imdb_id}.html."
    )
    parser.add_argument(
        "output_dir", type=Path, help="directory to save IMDb HTML pages into"
    )
    parser.add_argument(
        "--max-tabs",
        type=int,
        default=80,
        help="maximum number of tabs to inspect, default: 80",
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
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
        "--save-delay",
        type=float,
        default=DEFAULT_SAVE_DELAY,
        help=f"delay after confirming the Save Page dialog, default: {DEFAULT_SAVE_DELAY}",
    )
    parser.add_argument(
        "--save-dialog-delay",
        type=float,
        default=DEFAULT_SAVE_DIALOG_DELAY,
        help=(
            "delay after pressing Ctrl+S before typing the save path, "
            f"default: {DEFAULT_SAVE_DIALOG_DELAY}"
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
        help="cycle tabs and print target paths without opening the Save Page dialog",
    )
    return parser.parse_args()


def catalog_root_path(path: Path) -> Path:
    """Resolve a CLI path relative to hackflix-catalog."""
    if path.is_absolute():
        return path
    return CATALOG_ROOT / path


def save_firefox_imdb_tabs(
    *,
    output_dir: Path,
    max_tabs: int,
    backend: str = "auto",
    window_id: str | None = None,
    overwrite: bool = False,
    delay: float = 0.4,
    save_dialog_delay: float = DEFAULT_SAVE_DIALOG_DELAY,
    save_delay: float = DEFAULT_SAVE_DELAY,
    focus_delay: float = 5.0,
    dry_run: bool = False,
) -> list[Path]:
    """Save IMDb title tabs from a running Firefox window."""
    if max_tabs < 1:
        raise ValueError("--max-tabs must be at least 1")
    backend = _resolve_backend(backend)
    clipboard_command = _clipboard_command()

    if backend == "xdotool":
        firefox_window = window_id or _find_firefox_window()
        _run(["xdotool", "windowactivate", "--sync", firefox_window])
        time.sleep(delay)
    elif window_id is not None:
        raise ValueError("--window-id is only supported with --backend xdotool")
    else:
        _wait_for_manual_focus(focus_delay)

    first_url: str | None = None
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    saved_paths: list[Path] = []

    for index in range(max_tabs):
        url = _copy_current_url(backend, clipboard_command, delay)
        if index == 0:
            first_url = url
        elif url == first_url:
            break

        if url in seen_urls:
            _next_tab(backend, delay)
            continue
        seen_urls.add(url)

        imdb_id = extract_imdb_id(url)
        if imdb_id is not None and imdb_id not in seen_ids:
            seen_ids.add(imdb_id)
            destination = output_dir / f"{imdb_id}.html"
            if overwrite or not destination.exists():
                if not dry_run:
                    _save_current_page(
                        backend, destination, delay, save_delay, save_dialog_delay
                    )
                saved_paths.append(destination)

        _next_tab(backend, delay)

    return saved_paths


def extract_imdb_id(value: str) -> str | None:
    """Extract an IMDb title ID from a URL or filename-like value."""
    match = IMDB_ID_RE.search(value)
    if match is None:
        return None
    return match.group("id") or match.group("plain")


def _resolve_backend(backend: str) -> str:
    if backend not in BACKENDS:
        raise ValueError(f"Unsupported backend: {backend}")
    if backend == "auto":
        if os.environ.get("WAYLAND_DISPLAY") and shutil.which("ydotool"):
            return "ydotool"
        if shutil.which("xdotool"):
            return "xdotool"
        if shutil.which("ydotool"):
            return "ydotool"
        raise RuntimeError("Missing automation backend: install ydotool or xdotool")

    _require_command(backend)
    return backend


def _find_firefox_window() -> str:
    result = _run(["xdotool", "search", "--onlyvisible", "--class", "firefox"])
    windows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not windows:
        raise RuntimeError("Could not find a visible Firefox window")
    return windows[0]


def _wait_for_manual_focus(seconds: float) -> None:
    if seconds <= 0:
        return
    print(
        f"Focus the Firefox window now. Starting in {seconds:g} seconds...",
        file=sys.stderr,
    )
    time.sleep(seconds)


def _copy_current_url(backend: str, clipboard_command: list[str], delay: float) -> str:
    _key(backend, "ctrl+l")
    time.sleep(delay)
    _key(backend, "ctrl+c")
    time.sleep(delay)
    return _run(clipboard_command).stdout.strip()


def _save_current_page(
    backend: str,
    destination: Path,
    delay: float,
    save_delay: float,
    save_dialog_delay: float = DEFAULT_SAVE_DIALOG_DELAY,
) -> None:
    _key(backend, "ctrl+s")
    time.sleep(save_dialog_delay)
    _key(backend, "ctrl+l")
    time.sleep(delay)
    _type_text(backend, str(_firefox_save_dialog_path(destination)))
    time.sleep(delay)
    _key(backend, "Return")
    time.sleep(save_delay)
    _key(backend, "Escape")
    time.sleep(delay)


def _next_tab(backend: str, delay: float) -> None:
    _key(backend, "ctrl+Tab")
    time.sleep(delay)


def _close_tab(backend: str, delay: float) -> None:
    _key(backend, "ctrl+w")
    time.sleep(delay)


def _firefox_save_dialog_path(destination: Path) -> Path:
    if destination.suffix == ".html":
        return destination.with_suffix("")
    return destination


def _key(backend: str, key: str) -> None:
    if backend == "xdotool":
        _run(["xdotool", "key", key])
        return
    if backend == "ydotool":
        chord = YDO_KEY_CHORDS.get(key)
        if chord is None:
            raise ValueError(f"Unsupported ydotool key: {key}")
        _run(["ydotool", "key", *chord])
        return
    raise ValueError(f"Unsupported backend: {backend}")


def _type_text(backend: str, text: str) -> None:
    if backend == "xdotool":
        _run(["xdotool", "type", "--delay", "1", text])
        return
    if backend == "ydotool":
        _run(["ydotool", "type", text])
        return
    raise ValueError(f"Unsupported backend: {backend}")


def _clipboard_command() -> list[str]:
    if shutil.which("wl-paste"):
        return ["wl-paste", "--no-newline"]
    if shutil.which("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--output"]
    raise RuntimeError("Missing clipboard reader: install wl-clipboard, xclip, or xsel")


def _require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"Missing required command: {command}")


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    raise SystemExit(main())
