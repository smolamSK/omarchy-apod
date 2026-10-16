"""Offline regression tests; all state is temporary and desktop calls are mocked."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "bin/omarchy-apod"
SCRIPT = SCRIPT_PATH.read_text()
# Load the production helpers without running the CLI dispatch.
HELPERS = SCRIPT.split("SWITCH=0\ncmd=${1:-update}; shift || true", 1)[0]
HELPERS = HELPERS.replace('HERE=$(dirname "$(readlink -f "$0")")', f'HERE="{ROOT}/bin"')
ENTRY = {"date": "2026-10-01", "title": "Test picture", "media_type": "image",
         "hdurl": "https://apod.nasa.gov/x.jpg"}
# Never touch the real desktop.
DESKTOP_MOCKS = """
SWITCH=0
omarchy-theme-set() { echo "theme-set $1" >>"$DATA/events"; }
notify-send() { echo notified >>"$DATA/events"; }
hyprctl() { echo '[{"id":0,"width":320,"height":200,"transform":0}]'; }
"""

sys.path.insert(0, str(ROOT / "bin"))
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("apod_palette", ROOT / "bin/apod-palette.py")
palette = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(palette)


def make_image(path, *spec):
    subprocess.run(["magick", *spec, str(path)], check=True, capture_output=True)


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="apod-test-")
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.data = base / "data/omarchy-apod"
        self.data.mkdir(parents=True)
        self.theme = base / "config/omarchy/themes/apod"
        self.state = base / "home/.local/state/omarchy/current"
        self.state.mkdir(parents=True)
        self.env = {
            **os.environ,
            "HOME": str(base / "home"),
            "XDG_CONFIG_HOME": str(base / "config"),
            "XDG_DATA_HOME": str(base / "data"),
            "ENTRY": json.dumps(ENTRY),
        }
        self.env.pop("APOD_API_KEY", None)
        self.orig = self.data / "orig-2026-10-01.jpg"
        make_image(self.orig, "-size", "64x40", "gradient:#401020-#e08040")

    def run_bash(self, code, expect=0):
        result = subprocess.run(
            ["bash", "-c", HELPERS + DESKTOP_MOCKS + "\n" + code],
            env=self.env, text=True, capture_output=True, timeout=60,
        )
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def run_cli(self, *args, stdin=None):
        return subprocess.run([str(SCRIPT_PATH), *args], env=self.env, text=True,
                              input=stdin, capture_output=True, timeout=60)

    def events(self):
        path = self.data / "events"
        return path.read_text().splitlines() if path.exists() else []

    # ------------------------------------------------------------ set_entry

    def test_build_failure_keeps_state_and_suppresses_notification(self):
        previous = {"current": "old.jpg", "meta.json": '{"title":"Old"}',
                    "changed": "yesterday", "mode": "random"}
        for name, content in previous.items():
            (self.data / name).write_text(content)
        self.run_bash('''
build_theme() { return 1; }
prune() { echo unexpected >"$DATA/pruned"; }
if set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"; then exit 9; fi
''')
        for name, content in previous.items():
            self.assertEqual((self.data / name).read_text(), content)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.data / "pruned").exists())

    def test_new_picture_builds_theme_and_notifies_once(self):
        self.run_bash('''
prune() { :; }
set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"
set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"
''')
        self.assertEqual(json.loads((self.data / "meta.json").read_text())["title"], "Test picture")
        self.assertEqual((self.data / "current").read_text().strip(), str(self.orig))
        # Theme not in use and not asked for: built, not applied.
        self.assertEqual(self.events(), ["notified"])
        self.assertTrue((self.theme / "colors.toml").exists())

    def test_theme_in_use_is_reapplied_with_a_new_picture(self):
        (self.state / "theme.name").write_text("apod\n")
        self.run_bash('prune() { :; }; set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"')
        self.assertEqual(self.events(), ["theme-set apod", "notified"])

    def test_switch_applies_even_when_picture_is_unchanged(self):
        (self.state / "theme.name").write_text("tokyo-night\n")
        self.run_bash('''
prune() { :; }
set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"
SWITCH=1
set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"
''')
        self.assertEqual(self.events(), ["notified", "theme-set apod"])

    def test_hd_upgrade_does_not_notify_again(self):
        (self.data / "current").write_text(str(self.data / "orig-2026-10-01-sd.jpg"))
        self.run_bash('prune() { :; }; set_entry "$ENTRY" "$DATA/orig-2026-10-01.jpg"')
        self.assertNotIn("notified", self.events())

    def test_show_propagates_failure_without_changing_mode(self):
        self.run_bash('''
candidates() { echo "$ENTRY"; }
resolve() { echo "$1"; }
download() { echo "$DATA/orig-2026-10-01.jpg"; }
set_entry() { return 1; }
if show latest && echo latest >"$DATA/mode"; then exit 9; fi
''')
        self.assertFalse((self.data / "mode").exists())

    # ------------------------------------------------------------ the theme

    def test_build_theme_keeps_exactly_one_background(self):
        (self.data / "meta.json").write_text(json.dumps(ENTRY))
        older = self.data / "orig-2026-09-30.jpg"
        make_image(older, "-size", "40x40", "xc:#203060")
        self.run_bash(f'''
build_theme "{older}" "$DATA/meta.json"
build_theme "$DATA/orig-2026-10-01.jpg" "$DATA/meta.json"
''')
        backgrounds = sorted(p.name for p in (self.theme / "backgrounds").iterdir())
        self.assertEqual(backgrounds, ["apod-2026-10-01.jpg"])
        colors = (self.theme / "colors.toml").read_text()
        self.assertIn("# Test picture (2026-10-01)", colors)
        self.assertRegex(colors, r'hyprland_active_border = "rgba\([0-9a-f]{6}ee\) rgba\([0-9a-f]{6}ee\) 45deg"')
        self.assertTrue((self.theme / "preview.jpg").exists())
        self.assertEqual(json.loads((self.data / "palette.json").read_text())["mode"], "dark")
        self.assertEqual([p.name for p in self.data.glob("*.part")], [])

    def test_fit_change_rerenders_and_status_finds_new_cache_name(self):
        (self.data / "meta.json").write_text(json.dumps(ENTRY))
        result = self.run_bash('''
build_theme "$DATA/orig-2026-10-01.jpg" "$DATA/meta.json"
FIT_THRESHOLD=1.5
build_theme "$DATA/orig-2026-10-01.jpg" "$DATA/meta.json"
echo "$DATA/orig-2026-10-01.jpg" >"$DATA/current"
cmd_status
''')
        self.assertEqual(len(list(self.data.glob("render-*.jpg"))), 2)
        status = json.loads(result.stdout)
        self.assertTrue(status["image"].endswith("-fit1.5.jpg"))
        self.assertIn("accent", status["palette"]["colors"])
        self.assertFalse(status["themeActive"])

    # ------------------------------------------------------------ update

    def test_update_asks_nasa_only_when_something_may_be_new(self):
        cases = [
            # (mode, current picture date, changed, checked seconds ago, expect a fetch)
            ("latest", "today", "", None, False),
            ("latest", "old", "", 60, False),
            ("latest", "old", "", 4 * 3600, True),
            ("random", "old", "today", None, False),
            ("random", "old", "yesterday", None, True),
        ]
        for mode, current, changed, checked, fetch in cases:
            with self.subTest(mode=mode, current=current, changed=changed, checked=checked):
                for f in self.data.glob("*"):
                    if f.name not in ("orig-2026-10-01.jpg",):
                        f.unlink()
                (self.data / "mode").write_text(mode)
                name = "orig-$(apod_today).jpg" if current == "today" else "orig-2000-01-01.jpg"
                if changed:
                    (self.data / "changed").write_text(changed)
                self.run_bash(f'''
echo "$DATA/{name}" >"$DATA/current"
[[ "{changed}" != today ]] || date +%F >"$DATA/changed"
[[ -z "{checked or ''}" ]] || echo $(( $(date +%s) - {checked or 0} )) >"$DATA/checked"
show() {{ echo "show $1" >>"$DATA/events"; }}
cmd_update ""
''')
                self.assertEqual(bool(self.events()), fetch, self.events())

    # ------------------------------------------------------------ fetching

    def test_malformed_api_responses_use_date_fallback(self):
        for response in ["broken", "", "null", "{}", "[null]", "{} {}",
                         json.dumps([ENTRY, None])]:
            with self.subTest(response=response):
                self.env["API_RESPONSE"] = response
                result = self.run_bash('''
api() { printf '%s' "$API_RESPONSE"; }
entries date=2026-10-01 2026-10-01
''')
                self.assertEqual(json.loads(result.stdout), {"date": "2026-10-01"})
                self.assertIn("trying the APOD website instead", result.stderr)

    def test_valid_video_and_empty_results_do_not_trigger_fallback(self):
        for response in ["[]", json.dumps({**ENTRY, "media_type": "video"})]:
            with self.subTest(response=response):
                self.env["API_RESPONSE"] = response
                result = self.run_bash('''
api() { printf '%s' "$API_RESPONSE"; }
entries date=2026-10-01 2026-10-01
''')
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_page_entry_reads_the_website(self):
        self.env["PAGE"] = (
            '<meta property="og:title" content="APOD: 2026 October 1 - Test - NASA Science">\n'
            '<img class="x" src="https://assets.science.nasa.gov/dynamicimage/assets/apod/a.jpg?w=400">\n'
            '<h1 class="display-48 x">Test &amp; <i>picture</i></h1>\n'
            '<p class="media-detail-hero__description">Explanation: Seen by <a href="#">Webb</a> .\n'
            'Near <a>NGC 3132</a> , far away.</p>\n'
            '<th>Image Credit &amp; Copyright</th> <td><a>NASA</a> , <a>ESA</a></td>\n')
        result = self.run_bash('curl() { printf "%s" "$PAGE"; }; page_entry 2026-10-01')
        entry = json.loads(result.stdout)
        self.assertEqual(entry["title"], "Test & picture")
        self.assertEqual(entry["explanation"], "Seen by Webb. Near NGC 3132, far away.")
        self.assertEqual(entry["copyright"], "NASA, ESA")
        self.assertEqual(entry["hdurl"], "https://assets.science.nasa.gov/content/dam/apod/a.jpg")
        self.assertEqual(entry["media_type"], "image")
        # Another day's page (or the landing page) is not this day's entry.
        self.run_bash('curl() { printf "%s" "$PAGE"; }; page_entry 2026-10-02', expect=1)

    def test_only_nasa_https_images_are_accepted(self):
        for url, ok in [("https://apod.nasa.gov/a.jpg", True),
                        ("https://assets.science.nasa.gov/a.jpg", True),
                        ("http://apod.nasa.gov/a.jpg", False),
                        ("https://nasa.gov.evil.example/a.jpg", False),
                        ("https://evil.example/nasa.gov/a.jpg", False)]:
            with self.subTest(url=url):
                self.env["ENTRY"] = json.dumps({**ENTRY, "hdurl": url})
                result = self.run_bash('resolve "$ENTRY" >/dev/null && echo yes || echo no')
                self.assertEqual(result.stdout.strip(), "yes" if ok else "no")

    def test_request_limits_reach_all_network_paths(self):
        self.run_bash('''
CONNECT_MAX=5 REQUEST_MAX=15 DOWNLOAD_MAX=30
curl() { printf '%s\n' "$*" >>"$DATA/requests"; return 1; }
api date=2026-10-01 || true
page_entry 2026-10-01 || true
fetch https://apod.nasa.gov/image.jpg "$DATA/download.jpg" || true
''')
        requests = (self.data / "requests").read_text().splitlines()
        self.assertEqual(len(requests), 3)
        for request, limit in zip(requests, [15, 15, 30]):
            self.assertIn("--connect-timeout 5", request)
            self.assertIn(f"--max-time {limit} ", request)
        self.assertIn("--proto =https", requests[2])

    # ------------------------------------------------------------ CLI

    def test_configure_validates_and_keeps_settings_private(self):
        result = self.run_cli("configure", stdin=json.dumps(
            {"apiKey": " abc\tDEF ", "colorMode": "Light", "vividness": "loud", "notify": False}) + "\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.data / "settings.json"
        self.assertEqual(json.loads(path.read_text()),
                         {"apiKey": "abcDEF", "colorMode": "light", "vividness": "balanced", "notify": False})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        bad = self.run_cli("configure", stdin='{"apiKey": "$(touch pwned)"}\n')
        self.assertEqual(bad.returncode, 0, bad.stderr)
        self.assertEqual(json.loads(path.read_text())["apiKey"], "")
        self.assertEqual(self.run_cli("configure", stdin="not json\n").returncode, 2)

    def test_status_and_help_do_not_create_state(self):
        for f in self.data.iterdir():
            f.unlink()
        self.data.rmdir()
        for args in (["status"], ["help"]):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.data.exists())
        self.assertEqual(json.loads(self.run_cli("status").stdout)["mode"], "latest")


class PaletteTests(unittest.TestCase):
    KEYS = {"accent", "selection", "muted", "background", "dark_background", "darker_background",
            "lighter_background", "foreground", "dark_foreground", "light_foreground",
            "bright_foreground", "red", "yellow", "orange", "green", "cyan", "blue", "magenta",
            "brown", "bright_red", "bright_yellow", "bright_green", "bright_cyan", "bright_blue",
            "bright_magenta"}

    def palette_for(self, *spec, mode="auto", vividness="balanced"):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "p.png"
            make_image(path, *spec)
            return palette.build(palette.analyse(palette.read_pixels(str(path))), mode, vividness)

    def test_text_stays_readable_on_every_kind_of_picture(self):
        pictures = {
            "red nebula": ("-size", "80x80", "radial-gradient:#ff5030-#100000"),
            "blue sky": ("-size", "80x80", "gradient:#a0c8ff-#3070d0"),
            "white": ("-size", "80x80", "xc:white"),
            "black": ("-size", "80x80", "xc:black"),
            "grey moon": ("-size", "80x80", "radial-gradient:#d0d0d0-#000000"),
            "green aurora": ("-size", "80x80", "gradient:#00ff80-#001020"),
        }
        for name, spec in pictures.items():
            for mode in ("auto", "dark", "light"):
                for vividness in palette.VIVIDNESS:
                    with self.subTest(picture=name, mode=mode, vividness=vividness):
                        _, c, border = self.palette_for(*spec, mode=mode, vividness=vividness)
                        self.assertEqual(set(c), self.KEYS)
                        bg = c["background"]
                        self.assertGreaterEqual(palette.contrast(c["foreground"], bg), 10)
                        for key in ["accent", *palette.ANSI, *("bright_" + n for n in palette.BRIGHT)]:
                            self.assertGreaterEqual(palette.contrast(c[key], bg), 4.5, key)
                        self.assertRegex(border, r"^rgba\([0-9a-f]{6}ee\) rgba\([0-9a-f]{6}ee\) 45deg$")

    def test_bright_pictures_get_a_light_theme_in_auto(self):
        self.assertEqual(self.palette_for("-size", "40x40", "xc:#fff4e0")[0], "light")
        self.assertEqual(self.palette_for("-size", "40x40", "xc:#050510")[0], "dark")

    def test_accent_follows_the_pictures_strongest_hue(self):
        _, c, _ = self.palette_for("-size", "80x80", "xc:#060608", "-fill", "#20c040",
                                   "-draw", "circle 40,40 40,60")
        self.assertLess(palette.hue_distance(c["accent"].h, 145), 25)
        # Green in the picture pulls the terminal green towards it, but red stays red.
        self.assertLess(palette.hue_distance(c["red"].h, 25), 1)

    def test_colours_stay_in_srgb(self):
        for L, C, h in [(0.9, 0.4, 145), (0.2, 0.4, 260), (0.7, 0.3, 25)]:
            col = palette.Colour(L, C, h)
            self.assertTrue(re.fullmatch(r"#[0-9a-f]{6}", col.hex))
            self.assertLessEqual(col.C, C)


if __name__ == "__main__":
    unittest.main()
