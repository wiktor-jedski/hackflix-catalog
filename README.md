# Catalog Scripts

Small helpers for maintaining `catalog.json`.

All relative paths passed to these scripts are resolved from this
`hackflix-catalog` directory.

## Add IMDb Entries

Use `add_imdb_to_catalog.py` to add movies, series, or episodes from IMDb metadata.

```bash
# Dry run
uv run python hackflix-catalog/add_imdb_to_catalog.py "https://www.imdb.com/title/tt29420686/"

# Write to catalog
uv run python hackflix-catalog/add_imdb_to_catalog.py "https://www.imdb.com/title/tt29420686/" --write
```

IMDb often blocks direct command-line scraping. If that happens, save the IMDb page from a browser and pass it with `--html`:

```bash
uv run python hackflix-catalog/add_imdb_to_catalog.py \
  --html pages/1670-01-01.html \
  --write
```

When `--html` is used, the URL is optional if the saved file name contains an IMDb ID such as `tt29439399.html`.

Recommended series flow:

1. Add the IMDb series page first. This creates the series shell.
2. Add each IMDb episode page. Episodes are upserted under the existing series.
3. Add the season `magnet` manually later.

To add a whole saved season directory, name each episode file with its IMDb ID:

```text
pages/1670-01/tt29439399.html
pages/1670-01/tt30423767.html
```

Then run:

```bash
# Dry run
uv run python hackflix-catalog/add_imdb_season_to_catalog.py pages/1670-01

# Write all episodes
uv run python hackflix-catalog/add_imdb_season_to_catalog.py pages/1670-01 --write
```

Useful options:

```bash
--update          Refresh existing movie or series metadata.
--create-series   Allow an episode page to create its parent series.
--catalog PATH    Use a catalog other than catalog.json.
```

## Save Firefox IMDb Tabs

Use `save_firefox_imdb_tabs.py` when the IMDb episode pages are already open in Firefox and direct command-line fetching is blocked.

```bash
# Preview which files would be saved
uv run python hackflix-catalog/save_firefox_imdb_tabs.py pages/1670-01 --dry-run

# Save all open IMDb title tabs in the active Firefox window
uv run python hackflix-catalog/save_firefox_imdb_tabs.py pages/1670-01
```

The script cycles Firefox tabs with `Ctrl+Tab`, copies each current URL, extracts the `tt...` ID, and saves matching IMDb title pages as:

```text
pages/1670-01/tt29439399.html
```

Firefox appends `.html` in the save dialog, so the script types the path without that suffix to avoid files like `tt29439399.html.html`.

After each save, the script waits for the browser to settle and sends `Escape` before switching tabs. If only the first tab saves, increase `--save-delay`:

```bash
uv run python hackflix-catalog/save_firefox_imdb_tabs.py pages/wire-01 --backend ydotool --save-delay 2
```

Requirements:

- Firefox must already be running with the IMDb pages open.
- On Wayland, use `ydotool`. It sends keys to the focused window, so after starting the command you must focus Firefox during the countdown.
- On X11, `xdotool` can activate Firefox automatically.
- One clipboard reader is required: `wl-paste`, `xclip`, or `xsel`.

Useful options:

```bash
--backend auto     Use ydotool on Wayland when available, otherwise xdotool.
--max-tabs 80      Stop after this many tabs if the first tab is not reached.
--window-id ID     Use a specific xdotool window id. X11 only.
--overwrite        Save even when {imdb_id}.html already exists.
--delay 0.25       Delay after normal GUI actions.
--save-delay 1.5   Delay after confirming the Save Page dialog.
--focus-delay 5    Seconds to focus Firefox before ydotool starts.
--dry-run          Print target paths without saving pages.
```

## Save Firefox IMDb Season

Use `save_firefox_imdb_season.py` when Firefox is open on an IMDb episode-list page and you want the script to open every episode for one season before saving.

```bash
uv run python hackflix-catalog/save_firefox_imdb_season.py pages/dexter-05 --season 5
```

The script focuses Firefox, reads the current episode-list URL, loads the requested season, temporarily saves that list page under `/tmp`, extracts links matching IMDb's episode-card URLs, opens those episode URLs as tabs, then saves only IMDb title tabs and skips the episode-list page. The temporary episode-list save is removed automatically.

If IMDb or Firefox needs more time after changing seasons or opening tabs, increase these:

```bash
uv run python hackflix-catalog/save_firefox_imdb_season.py pages/dexter-05 \
  --season 5 \
  --page-load-delay 5 \
  --open-tabs-delay 3
```

Useful options:

```bash
--backend auto         Use ydotool on Wayland when available, otherwise xdotool.
--max-tabs 80          Stop after this many tabs if the first tab is not reached.
--window-id ID         Use a specific xdotool window id. X11 only.
--overwrite            Save even when {imdb_id}.html already exists.
--delay 0.25           Delay after normal GUI actions.
--page-load-delay 3    Seconds to wait after selecting the season.
--open-tabs-delay 2    Seconds to wait after opening episode tabs.
--save-delay 1.5       Delay after confirming the Save Page dialog.
--focus-delay 5        Seconds to focus Firefox before ydotool starts.
--dry-run              Open tabs and print target paths without saving pages.
```

## Find Subtitles

Use `find_subtitles.py` to find OpenSubtitles file IDs and optionally write them into the catalog.

```bash
# Dry run
uv run python hackflix-catalog/find_subtitles.py

# Write selected subtitle metadata
uv run python hackflix-catalog/find_subtitles.py --write
```

The finder requires IMDb/TMDB identifiers by default. This avoids bad broad title matches. Use this only for manual investigation:

```bash
uv run python hackflix-catalog/find_subtitles.py --allow-query-fallback
```

Useful options:

```bash
--languages pl en   Language priority.
--limit 10          Search result limit per language.
--force             Re-check entries that already have subtitle_id.
--catalog PATH      Use a catalog other than catalog.json.
```

## Catalog Flags

For reliable subtitle matching, add IMDb IDs:

```json
{
  "imdb_id": "tt0099685"
}
```

For Polish/native-language content that does not need subtitles, set this at the movie, series, or episode level:

```json
{
  "subtitles_needed": false
}
```

`translation_needed` has a different meaning: it is set after subtitle selection and means the selected subtitle is not Polish.
