#!/usr/bin/env python3
"""LUMINARA — The Poster for a Place That Isn't There.

A self-contained program (Python 3.13 standard library only, no network,
no external assets) that paints a vintage travel poster for an invented
place and writes it to /output/entry.png (1024x1024, under 1 MiB).

The place: LUMINARA, a chain of isles that drift above a sea of cloud,
where the sun never quite sets. Its one landmark is the lantern tower of
the main isle, whose lamp burns gold over the cloud sea while a waterfall
spills off the isle's edge and vanishes into the mist below.

The poster is drawn entirely in code: a posterised sunset sky, a low sun,
floating isles, a cloud sea, and the place name set in a bold stroke
lettering drawn from vector glyphs defined below. Everything is fixed and
deterministic, so two runs produce byte-identical output.
"""

import hashlib
import math
import os
import struct
import sys
import zlib

WIDTH = 1024
HEIGHT = 1024
OUT_PATH = "/output/entry.png"

# ---------------------------------------------------------------- palette
INK_SHADOW = (18, 48, 63)      # deep teal, for the name's shadow
CREAM = (255, 246, 227)        # poster cream, for the name and frame
GOLD = (247, 194, 94)          # gold rule and sunlit edges
SUN_CORE = (255, 217, 142)
SUN_HALO = (246, 178, 94)
ISLE_ROCK = (28, 58, 74)       # silhouette of the floating isles
ISLE_GRASS = (62, 122, 88)     # sunlit turf on the isle tops
PINE = (42, 91, 68)
TOWER = (255, 243, 217)        # lantern tower, cream
TERRA = (217, 108, 79)         # terracotta band on the tower
FALL = (216, 236, 242)         # waterfall ribbon
FOAM = (255, 255, 255)
CLOUD_LIGHT = (255, 243, 217)
CLOUD_MID = (251, 227, 192)
CLOUD_WARM = (247, 201, 143)
CLOUD_DEEP = (239, 167, 106)

# Sunset sky, top to horizon: deep indigo -> teal -> ember -> gold.
SKY_STOPS = [
    (0.00, (23, 38, 79)),
    (0.30, (42, 107, 124)),
    (0.55, (204, 122, 74)),
    (0.75, (247, 194, 94)),
    (1.00, (249, 217, 118)),
]
SKY_BOTTOM = 700               # the cloud sea covers everything below
SKY_BANDS = 48                 # posterise the gradient: small, flat bands


# ---------------------------------------------------------------- canvas
class Canvas:
    """An RGB raster with a tiny vector drawing kit."""

    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.px = bytearray(width * height * 3)

    def _idx(self, x, y):
        return (y * self.w + x) * 3

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = self._idx(x, y)
            self.px[i], self.px[i + 1], self.px[i + 2] = color

    def blend(self, x, y, color, alpha):
        """Paint one pixel with an opacity in [0, 1]."""
        if 0 <= x < self.w and 0 <= y < self.h and alpha > 0.0:
            i = self._idx(x, y)
            a = alpha
            self.px[i] = int(color[0] * a + self.px[i] * (1 - a))
            self.px[i + 1] = int(color[1] * a + self.px[i + 1] * (1 - a))
            self.px[i + 2] = int(color[2] * a + self.px[i + 2] * (1 - a))

    def fill_rect(self, x0, y0, x1, y1, color):
        x0 = max(0, int(x0)); y0 = max(0, int(y0))
        x1 = min(self.w, int(x1)); y1 = min(self.h, int(y1))
        for y in range(y0, y1):
            base = y * self.w * 3
            for x in range(x0, x1):
                i = base + x * 3
                self.px[i], self.px[i + 1], self.px[i + 2] = color

    def disc(self, cx, cy, r, color, alpha=1.0):
        """A filled disc; alpha < 1 blends it over what is beneath."""
        r = int(r)
        for y in range(cy - r, cy + r + 1):
            dy = y - cy
            span = int(math.sqrt(max(0, r * r - dy * dy)))
            for x in range(cx - span, cx + span + 1):
                if alpha >= 1.0:
                    self.set(x, y, color)
                else:
                    self.blend(x, y, color, alpha)

    def ellipse(self, cx, cy, rx, ry, color, alpha=1.0):
        for y in range(cy - ry, cy + ry + 1):
            dy = (y - cy) / float(ry)
            if abs(dy) > 1.0:
                continue
            span = int(rx * math.sqrt(max(0.0, 1.0 - dy * dy)))
            for x in range(cx - span, cx + span + 1):
                if alpha >= 1.0:
                    self.set(x, y, color)
                else:
                    self.blend(x, y, color, alpha)

    def polygon(self, points, color, alpha=1.0):
        """Scanline fill of a simple polygon."""
        edges = []
        n = len(points)
        for k in range(n):
            x0, y0 = points[k]
            x1, y1 = points[(k + 1) % n]
            if y0 == y1:
                continue
            edges.append((x0, y0, x1, y1))
        if not edges:
            return
        ymin = int(min(p[1] for p in points))
        ymax = int(max(p[1] for p in points))
        for y in range(max(0, ymin), min(self.h, ymax + 1)):
            xs = []
            for x0, y0, x1, y1 in edges:
                if (y0 <= y < y1) or (y1 <= y < y0):
                    t = (y - y0) / float(y1 - y0)
                    xs.append(x0 + t * (x1 - x0))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                for x in range(int(math.ceil(xs[k])), int(xs[k + 1]) + 1):
                    if alpha >= 1.0:
                        self.set(x, y, color)
                    else:
                        self.blend(x, y, color, alpha)

    def thick_line(self, x0, y0, x1, y1, width, color):
        """A line with round caps, stamped as discs along its length."""
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(length) + 1)
        r = width / 2.0
        for s in range(steps + 1):
            t = s / float(steps)
            cx = int(round(x0 + (x1 - x0) * t))
            cy = int(round(y0 + (y1 - y0) * t))
            self.disc(cx, cy, int(math.ceil(r)), color)

    def frame(self, inset, width, color, alpha=1.0):
        x0, y0 = inset, inset
        x1, y1 = self.w - inset, self.h - inset
        for y in range(y0, y1):
            for x in (x0, x1 - 1):
                pass
        # four sides, drawn as thin filled rects
        self.fill_rect(x0, y0, x1, y0 + width, color)
        self.fill_rect(x0, y1 - width, x1, y1, color)
        self.fill_rect(x0, y0, x0 + width, y1, color)
        self.fill_rect(x1 - width, y0, x1, y1, color)


def lerp_color(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def sky_color(t):
    """Sky colour at height fraction t (0 top, 1 at the cloud sea)."""
    for k in range(len(SKY_STOPS) - 1):
        t0, c0 = SKY_STOPS[k]
        t1, c1 = SKY_STOPS[k + 1]
        if t0 <= t <= t1:
            return lerp_color(c0, c1, (t - t0) / (t1 - t0))
    return SKY_STOPS[-1][1]


def paint_sky(cv):
    """Posterised sunset gradient, one flat band per row group."""
    band = max(1, SKY_BOTTOM // SKY_BANDS)
    for y0 in range(0, SKY_BOTTOM, band):
        t = (y0 + band / 2.0) / SKY_BOTTOM
        cv.fill_rect(0, y0, WIDTH, min(SKY_BOTTOM, y0 + band), sky_color(t))


# ---------------------------------------------------------------- lettering
# Bold stroke capitals on a 10 x 14 unit grid (y grows downwards).
# Each glyph is a list of strokes; a stroke is a list of (x, y) points.
GLYPHS = {
    "L": [[(1, 0), (1, 14), (9, 14)]],
    "U": [[(1, 0), (1, 11), (2, 13), (4, 14), (6, 14), (8, 13), (9, 11), (9, 0)]],
    "M": [[(1, 14), (1, 0), (5, 7), (9, 0), (9, 14)]],
    "I": [[(2.5, 0), (7.5, 0)], [(5, 0), (5, 14)], [(2.5, 14), (7.5, 14)]],
    "N": [[(1, 14), (1, 0), (9, 14), (9, 0)]],
    "A": [[(1, 14), (5, 0), (9, 14)], [(3.2, 9), (6.8, 9)]],
    "R": [[(1, 14), (1, 0), (7, 0), (8.5, 1), (9, 3), (9, 5), (8, 7), (7, 8), (1, 8)],
          [(5.5, 8), (9, 14)]],
}

LETTER_UNIT = 9.0            # one grid unit, in pixels
LETTER_ADVANCE = 12.0        # grid units from one letter to the next
LETTER_STROKE = 1.7          # stroke width, in grid units


def draw_word(cv, word, top_y, color):
    """Draw a word centred horizontally, top edge at top_y."""
    total_units = (len(word) - 1) * LETTER_ADVANCE + 10.0
    total_px = total_units * LETTER_UNIT
    x = (WIDTH - total_px) / 2.0
    width_px = LETTER_STROKE * LETTER_UNIT
    for ch in word:
        for stroke in GLYPHS[ch]:
            pts = [(x + px * LETTER_UNIT, top_y + py * LETTER_UNIT)
                   for px, py in stroke]
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                cv.thick_line(ax, ay, bx, by, width_px, color)
        x += LETTER_ADVANCE * LETTER_UNIT


# ---------------------------------------------------------------- the scene
def paint_sun(cv):
    cv.disc(712, 340, 158, SUN_HALO, alpha=0.12)
    cv.disc(712, 340, 128, SUN_HALO, alpha=0.25)
    cv.disc(712, 340, 104, SUN_CORE)
    cv.disc(712, 340, 104, SUN_CORE)  # solid core, no blend needed


def paint_stars(cv):
    # Fixed star field in the upper sky; nothing random, nothing to seed.
    stars = [
        (60, 250, 2), (130, 120, 2), (210, 210, 3), (290, 90, 2),
        (420, 150, 2), (480, 70, 3), (600, 110, 2), (660, 60, 2),
        (780, 130, 3), (860, 90, 2), (940, 200, 2), (970, 320, 3),
        (40, 420, 2), (160, 330, 2), (900, 420, 2), (350, 260, 2),
        (540, 230, 2), (830, 250, 2), (70, 60, 3), (960, 60, 2),
    ]
    for x, y, r in stars:
        cv.disc(x, y, r, CREAM, alpha=0.85)


def paint_birds(cv):
    for bx, by, s in [(300, 225, 12), (352, 198, 9), (762, 185, 10)]:
        cv.thick_line(bx - s, by, bx, by - 6, 3, INK_SHADOW)
        cv.thick_line(bx, by - 6, bx + s, by, 3, INK_SHADOW)


def paint_pine(cv, x, base_y, height):
    w = height * 0.42
    cv.polygon([(x - w, base_y), (x, base_y - height), (x + w, base_y)], PINE)
    cv.fill_rect(x - 2, base_y, x + 3, base_y + 8, PINE)


def paint_main_isle(cv):
    # Rocky underside: a wide plateau tapering to one point in the mist.
    cv.polygon([
        (300, 470), (724, 470), (700, 520), (640, 585),
        (560, 640), (512, 668), (464, 640), (384, 585), (324, 520),
    ], ISLE_ROCK)
    # Sunlit turf along the plateau, with a gold rim on its edge.
    cv.polygon([(300, 470), (724, 470), (716, 486), (308, 486)], ISLE_GRASS)
    cv.thick_line(300, 470, 724, 470, 3, GOLD)
    # Pines on the plateau.
    paint_pine(cv, 350, 470, 34)
    paint_pine(cv, 392, 470, 26)
    paint_pine(cv, 660, 470, 30)
    # The lantern tower: cream shaft, terracotta band, glowing lamp.
    cv.fill_rect(498, 332, 526, 470, TOWER)
    cv.fill_rect(498, 386, 526, 402, TERRA)
    cv.fill_rect(494, 322, 530, 332, INK_SHADOW)   # lamp room
    cv.polygon([(494, 322), (530, 322), (526, 306), (498, 306)], INK_SHADOW)
    cv.disc(512, 300, 24, SUN_HALO, alpha=0.4)     # the lamp's glow
    cv.disc(512, 300, 12, SUN_CORE)                # the lamp itself
    cv.disc(512, 288, 3, GOLD)                     # finial
    # Waterfall spilling off the right edge into the cloud sea.
    path = [(700, 484), (706, 540), (698, 600), (702, 650), (694, 700)]
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        cv.thick_line(ax, ay, bx, by, 13, FALL)
    cv.thick_line(700, 484, 706, 540, 5, FOAM)
    for fx, fy, fr in [(694, 700, 12), (706, 706, 9), (684, 708, 8)]:
        cv.disc(fx, fy, fr, FOAM, alpha=0.9)


def paint_small_isle(cv, x0, x1, top_y, point_y, tree=True):
    mid = (x0 + x1) // 2
    cv.polygon([
        (x0, top_y), (x1, top_y), (x1 - 14, top_y + 34),
        (mid + 26, point_y - 60), (mid, point_y),
        (mid - 26, point_y - 60), (x0 + 14, top_y + 34),
    ], ISLE_ROCK)
    cv.polygon([(x0, top_y), (x1, top_y), (x1 - 8, top_y + 12), (x0 + 8, top_y + 12)],
               ISLE_GRASS)
    if tree:
        paint_pine(cv, mid - 12, top_y, 24)


def paint_cloud_sea(cv):
    cv.fill_rect(0, 640, WIDTH, HEIGHT, CLOUD_WARM)
    # Back layer: warm, low clouds.
    for cx, cy, rx, ry in [
        (120, 700, 210, 62), (420, 730, 260, 70), (760, 705, 250, 66),
        (940, 760, 200, 60), (60, 800, 220, 70), (980, 850, 190, 64),
    ]:
        cv.ellipse(cx, cy, rx, ry, CLOUD_DEEP)
    # Mid layer: sunlit cream billows.
    for cx, cy, rx, ry in [
        (200, 780, 230, 78), (560, 800, 280, 84), (880, 790, 230, 76),
        (60, 900, 240, 80), (420, 900, 260, 82), (780, 910, 260, 80),
        (980, 950, 220, 74),
    ]:
        cv.ellipse(cx, cy, rx, ry, CLOUD_MID)
    # Front layer: bright crests catching the sun.
    for cx, cy, rx, ry in [
        (300, 850, 200, 60), (700, 860, 210, 62), (120, 960, 220, 60),
        (520, 960, 240, 62), (900, 970, 200, 58),
    ]:
        cv.ellipse(cx, cy, rx, ry, CLOUD_LIGHT)
    # Low drifters that break the cream at the very bottom.
    for cx, cy, rx, ry in [
        (250, 1005, 240, 58), (700, 1005, 240, 58),
    ]:
        cv.ellipse(cx, cy, rx, ry, CLOUD_MID)


def paint_poster(cv):
    paint_sky(cv)
    paint_stars(cv)
    paint_sun(cv)
    paint_birds(cv)
    # A distant haze band where the isles float.
    cv.ellipse(512, 560, 480, 46, (239, 167, 106), alpha=0.45)
    paint_main_isle(cv)
    paint_small_isle(cv, 92, 272, 578, 700)
    paint_small_isle(cv, 792, 950, 606, 724, tree=False)
    paint_cloud_sea(cv)
    # The place name, set large across the top sky with its shadow.
    draw_word(cv, "LUMINARA", 66 + 5, INK_SHADOW)
    draw_word(cv, "LUMINARA", 66, CREAM)
    # A thin gold rule beneath the name, diamond at its centre.
    cv.fill_rect(312, 214, 712, 217, GOLD)
    cv.polygon([(512, 206), (520, 215), (512, 224), (504, 215)], GOLD)
    # The poster's frame.
    cv.frame(26, 3, CREAM)


# ---------------------------------------------------------------- PNG output
def png_bytes(cv):
    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        out += struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return out

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", cv.w, cv.h, 8, 2, 0, 0, 0)
    raw = bytearray()
    for y in range(cv.h):
        raw.append(0)  # filter: none
        raw.extend(cv.px[y * cv.w * 3:(y + 1) * cv.w * 3])
    idat = zlib.compress(bytes(raw), 9)
    return header + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def main():
    cv = Canvas(WIDTH, HEIGHT)
    paint_poster(cv)
    data = png_bytes(cv)
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "wb") as f:
        f.write(data)
    size = len(data)
    digest = hashlib.sha256(data).hexdigest()
    print(f"wrote {OUT_PATH}: {WIDTH}x{HEIGHT}, {size} bytes, sha256 {digest}")
    if size > 1048576:
        print("FAIL: over the 1 MiB limit", file=sys.stderr)
        return 1
    print("OK: within the 1 MiB and 1024x1024 limits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
