#!/usr/bin/env python3
"""apod-palette — an Omarchy colors.toml whose colours come from a picture.

    apod-palette.py IMAGE [--mode auto|dark|light] [--vividness subtle|balanced|vivid]
                          [--title TEXT] [--json FILE]

Prints colors.toml on stdout. With --json, also writes the palette (plus how it
was derived) as JSON for the bar widget.

The picture decides the character, never the legibility. Its most prominent
hues become the accent and the window-border gradient, its dark areas tint the
background, and the terminal colours keep their usual meaning (red still reads
as red) but lean towards nearby hues of the picture. Every text colour is then
checked against the background and moved in lightness until it is readable.

Colours are worked out in OKLab/OKLCH, where equal steps in lightness look
equal, so the same recipe gives an even theme for a red nebula and a blue one.
Standard library only; ImageMagick reads the picture.
"""
import argparse
import json
import math
import subprocess
import sys

SAMPLE = 160            # the picture is shrunk to at most SAMPLE x SAMPLE pixels
CHROMATIC = 0.03        # OKLCH chroma below which a pixel counts as grey
BINS = 72               # 5-degree hue bins
PEAK_GAP = 35           # degrees between two hues that count as different colours
VIVIDNESS = {"subtle": 0.7, "balanced": 1.0, "vivid": 1.35}

# Terminal colours: the usual OKLCH hue of each, and the lightness it gets on a
# dark and on a light background. Yellow and green need to be lighter than red
# and blue to look equally bright.
ANSI = {
    #          hue  dark  light
    "red":     (25, 0.68, 0.52),
    "orange":  (55, 0.74, 0.56),
    "yellow":  (95, 0.84, 0.60),
    "green":   (145, 0.77, 0.54),
    "cyan":    (200, 0.78, 0.54),
    "blue":    (258, 0.70, 0.50),
    "magenta": (330, 0.72, 0.52),
}
BRIGHT = ["red", "yellow", "green", "cyan", "blue", "magenta"]


# ---------------------------------------------------------------- colour maths

def srgb_to_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def oklab(r, g, b):
    r, g, b = srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b)
    l = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklab_to_linear(L, a, b):
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def in_gamut(rgb):
    return all(-1e-6 <= c <= 1 + 1e-6 for c in rgb)


class Colour:
    """An OKLCH colour, kept inside sRGB by lowering chroma, never by shifting hue."""

    def __init__(self, L, C, h):
        self.L = min(max(L, 0.0), 1.0)
        self.h = h % 360
        rad = math.radians(self.h)
        if not in_gamut(oklab_to_linear(self.L, C * math.cos(rad), C * math.sin(rad))):
            lo, hi = 0.0, C
            for _ in range(24):
                mid = (lo + hi) / 2
                if in_gamut(oklab_to_linear(self.L, mid * math.cos(rad), mid * math.sin(rad))):
                    lo = mid
                else:
                    hi = mid
            C = lo
        self.C = C
        lin = oklab_to_linear(self.L, C * math.cos(rad), C * math.sin(rad))
        self.rgb = tuple(min(max(round(linear_to_srgb(min(max(c, 0.0), 1.0)) * 255), 0), 255) for c in lin)

    def lighter(self, dL):
        return Colour(self.L + dL, self.C, self.h)

    @property
    def hex(self):
        return "#%02x%02x%02x" % self.rgb

    @property
    def luminance(self):
        return sum(w * srgb_to_linear(c) for w, c in zip((0.2126, 0.7152, 0.0722), self.rgb))


def contrast(c1, c2):
    a, b = sorted((c1.luminance, c2.luminance), reverse=True)
    return (a + 0.05) / (b + 0.05)


def readable(colour, bg, ratio):
    """colour, moved away from bg in lightness until the WCAG contrast reaches ratio."""
    step = 0.01 if bg.L < 0.5 else -0.01
    for _ in range(100):
        if contrast(colour, bg) >= ratio or not 0 < colour.L < 1:
            break
        colour = colour.lighter(step)
    return colour


def hue_distance(h1, h2):
    d = abs(h1 - h2) % 360
    return min(d, 360 - d)


# ---------------------------------------------------------------- the picture

def read_pixels(path):
    """The picture as a list of OKLab (L, a, b), shrunk to at most SAMPLE pixels a side."""
    out = subprocess.run(
        ["magick", path + "[0]", "-auto-orient", "-resize", f"{SAMPLE}x{SAMPLE}>",
         "-colorspace", "sRGB", "-alpha", "off", "-depth", "8", "rgb:-"],
        check=True, capture_output=True).stdout
    if len(out) < 3:
        raise ValueError("the picture has no pixels")
    cache = {}
    pixels = []
    for i in range(0, len(out) - 2, 3):
        key = out[i:i + 3]
        lab = cache.get(key)
        if lab is None:
            lab = cache[key] = oklab(*key)
        pixels.append(lab)
    return pixels


def analyse(pixels):
    """What the palette needs to know about the picture."""
    n = len(pixels)
    mean_L = sum(p[0] for p in pixels) / n

    # Hue histogram of the coloured pixels, weighted by chroma so that a small
    # vivid nebula outweighs a large dull haze.
    hist = [0.0] * BINS
    members = [[] for _ in range(BINS)]
    total_chroma = 0.0
    for L, a, b in pixels:
        C = math.hypot(a, b)
        total_chroma += C
        if C < CHROMATIC or not 0.12 < L < 0.98:
            continue
        h = math.degrees(math.atan2(b, a)) % 360
        k = int(h / (360 / BINS)) % BINS
        hist[k] += C ** 1.5
        members[k].append((L, a, b, C))
    kernel = [1, 2, 3, 2, 1]
    smooth = [sum(w * hist[(i + j - 2) % BINS] for j, w in enumerate(kernel)) for i in range(BINS)]
    chromatic_weight = sum(hist)

    peaks = []
    order = sorted(range(BINS), key=lambda i: smooth[i], reverse=True)
    for i in order:
        if smooth[i] <= 0 or len(peaks) == 3:
            break
        if smooth[i] < smooth[(i - 1) % BINS] or smooth[i] < smooth[(i + 1) % BINS]:
            continue
        centre = (i + 0.5) * 360 / BINS
        if any(hue_distance(centre, p["hue"]) < PEAK_GAP for p in peaks):
            continue
        near = [m for k in range(i - 3, i + 4) for m in members[k % BINS]]
        if not near:
            continue
        a = sum(m[1] * m[3] for m in near)
        b = sum(m[2] * m[3] for m in near)
        chromas = sorted(m[3] for m in near)
        share = sum(hist[k % BINS] for k in range(i - 3, i + 4)) / chromatic_weight
        if peaks and share < 0.04:
            continue
        peaks.append({
            "hue": math.degrees(math.atan2(b, a)) % 360,
            "chroma": chromas[int(len(chromas) * 0.75)],
            "share": share,
        })

    # The background takes the hue of the picture's dark areas: the sky
    # around the object, which is rarely pure black.
    dark = [p for p in pixels if p[0] < 0.4] or pixels
    da = sum(p[1] for p in dark) / len(dark)
    db = sum(p[2] for p in dark) / len(dark)

    return {
        "mean_lightness": mean_L,
        "colourfulness": total_chroma / n,
        "peaks": peaks,
        "dark_hue": math.degrees(math.atan2(db, da)) % 360,
        "dark_chroma": math.hypot(da, db),
    }


# ---------------------------------------------------------------- the palette

def build(info, mode="auto", vividness="balanced"):
    vivid = VIVIDNESS[vividness]
    if mode == "auto":
        mode = "light" if info["mean_lightness"] > 0.68 else "dark"
    light = mode == "light"
    peaks = info["peaks"]

    if peaks:
        accent_hue = peaks[0]["hue"]
        accent_chroma = min(max(peaks[0]["chroma"] * 1.1, 0.09), 0.19) * vivid
    else:
        # A black-and-white picture gets a nearly grey theme.
        accent_hue, accent_chroma = 250, 0.03

    # Background: the dark areas' hue, or the accent's when they are neutral.
    if info["dark_chroma"] >= 0.006:
        bg_hue, bg_chroma = info["dark_hue"], min(max(info["dark_chroma"] * 1.3, 0.010), 0.045)
    else:
        bg_hue, bg_chroma = accent_hue, 0.012 if peaks else 0.0
    bg_chroma *= vivid
    text_chroma = min(bg_chroma * 0.6, 0.02)

    def surface(L):
        return Colour(L, bg_chroma, bg_hue)

    def text(L, chroma=None):
        return Colour(L, text_chroma if chroma is None else chroma, bg_hue)

    if light:
        background = surface(0.975)
        c = {
            "background": background,
            "dark_background": surface(0.95),
            "darker_background": surface(0.92),
            "lighter_background": surface(0.88),
            "foreground": text(0.27),
            "light_foreground": text(0.22),
            "bright_foreground": text(0.17),
            "dark_foreground": text(0.68, bg_chroma),
            "muted": text(0.60, bg_chroma),
            "selection": Colour(0.86, min(0.07, accent_chroma * 0.5), accent_hue),
            "accent": Colour(0.52, accent_chroma, accent_hue),
        }
    else:
        background = surface(0.19)
        c = {
            "background": background,
            "dark_background": surface(0.165),
            "darker_background": surface(0.14),
            "lighter_background": surface(0.26),
            "foreground": text(0.91),
            "light_foreground": text(0.86),
            "bright_foreground": text(0.97),
            "dark_foreground": text(0.62, bg_chroma),
            "muted": text(0.50, bg_chroma),
            "selection": Colour(0.33, min(0.07, accent_chroma * 0.5), accent_hue),
            "accent": Colour(0.78, accent_chroma, accent_hue),
        }

    # Terminal colours lean towards a prominent hue of the picture that is
    # close to them, so a teal nebula gets a teal cyan, but never so far that
    # green stops looking green.
    ansi_chroma = min(max(0.06 + info["colourfulness"] * 0.8, 0.09), 0.15) * vivid
    for name, (hue, dark_L, light_L) in ANSI.items():
        chroma = ansi_chroma
        for p in peaks:
            if p["share"] >= 0.08 and hue_distance(hue, p["hue"]) <= 30:
                delta = (p["hue"] - hue + 180) % 360 - 180
                hue += delta * 0.6
                # Capped: a clear blue sky would otherwise make cyan glare.
                chroma = max(chroma, min(p["chroma"], 0.15) * vivid)
                break
        c[name] = Colour(light_L if light else dark_L, chroma, hue)
        if name in BRIGHT:
            c["bright_" + name] = Colour((light_L - 0.06) if light else (dark_L + 0.07),
                                         chroma * 1.1, hue)
    c["brown"] = Colour(0.40 if light else 0.50, 0.07 * vivid, 60)

    # Legibility wins over fidelity.
    for name in ("foreground", "light_foreground", "bright_foreground"):
        c[name] = readable(c[name], background, 10)
    for name in ["accent", *ANSI, *("bright_" + n for n in BRIGHT)]:
        c[name] = readable(c[name], background, 4.5)
    c["dark_foreground"] = readable(c["dark_foreground"], background, 3)
    c["muted"] = readable(c["muted"], background, 2.2)

    # The window border runs between the picture's two strongest colours.
    second = (Colour(c["accent"].L, max(peaks[1]["chroma"], 0.08) * vivid, peaks[1]["hue"])
              if len(peaks) > 1 else Colour(c["accent"].L, accent_chroma, accent_hue + 50))
    border = f"rgba({c['accent'].hex[1:]}ee) rgba({second.hex[1:]}ee) 45deg"

    return mode, c, border


ORDER = [
    ["accent", "selection", "muted"],
    ["background", "dark_background", "darker_background", "lighter_background"],
    ["foreground", "dark_foreground", "light_foreground", "bright_foreground"],
    ["red", "yellow", "orange", "green", "cyan", "blue", "magenta", "brown"],
    ["bright_" + n for n in BRIGHT],
]


def toml(mode, colours, border, title=""):
    title = " ".join(title.split())
    lines = ["# Generated by omarchy-apod from NASA's Astronomy Picture of the Day."]
    if title:
        lines.append("# " + title)
    lines += ["# Rewritten with every new picture; edits here do not last.", f'mode = "{mode}"']
    for group in ORDER:
        lines.append("")
        lines += [f'{name} = "{colours[name].hex}"' for name in group]
    lines += ["", f'hyprland_active_border = "{border}"', ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Print an Omarchy colors.toml made from a picture.")
    ap.add_argument("image")
    ap.add_argument("--mode", choices=["auto", "dark", "light"], default="auto")
    ap.add_argument("--vividness", choices=list(VIVIDNESS), default="balanced")
    ap.add_argument("--title", default="")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args()

    try:
        info = analyse(read_pixels(args.image))
    except (subprocess.CalledProcessError, ValueError, OSError):
        print(f"apod-palette: can't read {args.image} as a picture", file=sys.stderr)
        return 1
    mode, colours, border = build(info, args.mode, args.vividness)
    sys.stdout.write(toml(mode, colours, border, args.title))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({
                "mode": mode,
                "colors": {k: v.hex for k, v in colours.items()},
                "border": border,
                "picture": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in info.items()
                            if k != "peaks"} | {"peaks": [{k: round(v, 4) for k, v in p.items()}
                                                          for p in info["peaks"]]},
            }, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
