# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

An Omarchy shell plugin (`io.github.smolamsk.apod`) that sets NASA's Astronomy Picture of the Day as the wallpaper and builds an Omarchy theme (`~/.config/omarchy/themes/apod`) from the picture's colours. It is a port of [kde-apod](https://github.com/smolamSK/kde-apod), whose fetch pipeline it shares almost line for line.

- `bin/omarchy-apod` (Bash) does everything: fetching, rendering, building and applying the theme, status. `Service.qml` and `Widget.qml` only call its subcommands.
- `bin/apod-palette.py` (Python standard library, run with `python3 -I`) turns a picture into `colors.toml`. It works in OKLCH and enforces WCAG contrast against the background.

## Development

- Checks: `bash -n bin/omarchy-apod`, `python3 -B -m unittest discover -s tests -v`, `omarchy plugin validate .`.
- The installed copy is a git clone in `~/.config/omarchy/plugins/io.github.smolamsk.apod`; commit here, then `git pull` there. QML changes need `omarchy restart shell` (a rescan keeps the cached component). `omarchy-shell shell summon io.github.smolamsk.apod` opens the popup.
- `update`, `latest`, `random`, `use` and `apply` change the real theme (and restart terminals through `omarchy-theme-set`). `status`, `help` and `preview IMAGE` are read-only. The tests set `HOME`/`XDG_*` to a temporary directory and mock `omarchy-theme-set`, `notify-send` and `hyprctl`.

## Contracts

- **Fetch pipeline, `set -e` behaviour, page fallback, .part files**: as in kde-apod's CLAUDE.md. The page-scraping jq regexes depend on science.nasa.gov's markup.
- **When the theme is applied.** `apply_theme` runs `omarchy-theme-set apod` only when `SWITCH=1` (latest, random, use, and the very first update) or when the APOD theme is already in use. A background update must never take the user away from a theme they chose.
- **When anything happens at all.** The service runs `update` 20 s after load and hourly. `cmd_update` must stay cheap when there is nothing new: no network if today's (US Eastern) picture is current or NASA was asked less than `CHECK_HOURS` ago; random mode reshuffles once per day. `set_entry` returns early for an unchanged picture, because re-applying a theme restarts terminals.
- **One background.** `build_theme` keeps exactly one file in the theme's `backgrounds/`; `omarchy-theme-set` cycles through backgrounds and would otherwise pick an older picture.
- **Settings** are extra keys on the widget's entry in `~/.config/omarchy/shell.json` (this Omarchy has no settings UI that renders the manifest schema). They arrive from the widget as one JSON line on stdin to `configure` (never argv: it holds the API key) and are stored in `~/.local/share/omarchy-apod/settings.json`. `load_settings` checks each value against a pattern before use. `shell.json` is world-readable, so the API key is normally saved with `key` instead; an empty `apiKey` from the widget keeps the saved one. Changing colour mode or vividness rebuilds the theme.
- **Widget ↔ service.** The widget gets the service with `shell.serviceFor(id)` and reads `status`, `busy`, `error`, `stage`, `downloaded`, `downloadTotal`. The service runs the script with `APOD_PROGRESS=1` and reads stderr line by line: `progress DONE TOTAL` lines (from `fetch`, every 0.5 s) drive the progress bar, messages matching the `progress` regex in `Service.qml` become the stage shown in the widget, and the first message that doesn't match is the failure reason. Add every new progress message to that regex, or it shows up as an error. In a terminal, `progress` redraws one line instead.
- **Picking a picture (`show`).** Videos never count as attempts; in latest mode the newest one is saved to `video.json` and `status` reports it while an older picture is showing. Random mode reads each picture's size from its first `PROBE_BYTES` (`probe_size`) before downloading, tries at most `MAX_TRIES`, then settles for the largest it saw. `render` never enlarges more than `MAX_UPSCALE`.
- **Privacy.** The script runs with `umask 077`. Downloads are https-only and limited to `*.nasa.gov` (`resolve`).
- **Help text comes from the header comment.** Adding a subcommand means editing the header, the `case` dispatch and the README's "Command line" section.
- Glyphs in QML are `\u` surrogate escapes; the Write tool turns them into literal characters, so convert afterwards.
