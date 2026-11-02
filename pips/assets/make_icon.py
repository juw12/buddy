# make_icon.py - renders the Pips icon (Pip the cat on a Pips-red tile).
#
# Produces, next to this script (so it works from any CWD):
#   assets/widget.ico  (16 / 24 / 32 / 48 / 64 / 128 / 256 px)
#   assets/widget.png  (256 px)
#   assets/pips-icon.svg (same design as a vector, used by the website)
#
# Optional:  python assets\make_icon.py --web <folder>
#   also writes icon-192.png, icon-512.png, apple-touch-icon.png and
#   pips-icon.svg into <folder> (the website's icons/ folder).
#
# Design: Pips-red rounded tile (matches the app's red accents), a round
# peach cat face (the default pet colour) with big shiny eyes, pink cheeks,
# a tiny "w" mouth and a little gold sparkle (the shop coins).
# All geometry is defined once on a 512 x 512 grid and drawn both with
# Pillow (supersampled, then downscaled so it stays crisp at 16 px) and as SVG.
#
# Only dependency: Pillow.

import sys
from pathlib import Path

from PIL import Image, ImageDraw

GRID = 512          # design grid
SS = 1024           # supersample resolution for raster output

# Palette (shared with the app UI / pet).
RED_TOP = (178, 38, 38)       # Pips red, lighter
RED_BOTTOM = (122, 22, 22)    # Pips red, deeper (#7a1616)
CREAM = (250, 248, 243)       # #faf8f3 - card background
PEACH = (255, 199, 143)       # #ffc78f - default pet colour
PEACH_LIGHT = (255, 224, 190)
OUTLINE = (115, 72, 44)       # darkened peach, like the pet outline
EAR_PINK = (255, 150, 170)
CHEEK = (255, 143, 163)       # #ff8fa3 - pet cheeks
EYE = (42, 36, 48)            # #2a2430 - pet eyes
GOLD = (242, 182, 50)         # coin gold
WHITE = (255, 255, 255)

# ---- geometry on the 512 grid ------------------------------------------------
TILE = dict(x0=16, y0=16, x1=496, y1=496, r=108)
EAR_L = [(118, 236), (146, 88), (246, 168)]
EAR_R = [(512 - x, y) for (x, y) in EAR_L]
EAR_IN_L = [(146, 206), (160, 124), (214, 170)]
EAR_IN_R = [(512 - x, y) for (x, y) in EAR_IN_L]
HEAD = dict(cx=256, cy=302, rx=176, ry=146)
MUZZLE = dict(cx=256, cy=346, rx=66, ry=42)
EYE_L = dict(cx=190, cy=288, rx=26, ry=32)
EYE_R = dict(cx=322, cy=288, rx=26, ry=32)
SHINE_OFF = (-8, -12)
SHINE_R = 10
CHEEK_L = dict(cx=150, cy=344, rx=32, ry=19)
CHEEK_R = dict(cx=362, cy=344, rx=32, ry=19)
NOSE = [(244, 322), (268, 322), (256, 336)]
MOUTH_R = 15          # two little arcs make the "w"
MOUTH_Y = 338
SPARKLE = dict(cx=420, cy=100, r=46)
OUTLINE_W = 12


def sparkle_points(cx, cy, r, inner=0.30, n=4):
    """4-point star (a 'twinkle'), returns polygon points."""
    import math
    pts = []
    for i in range(n * 2):
        ang = math.pi / n * i - math.pi / 2
        rad = r if i % 2 == 0 else r * inner
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


# ---- raster -------------------------------------------------------------------
def render(size: int = SS) -> Image.Image:
    k = size / GRID

    def s(v):
        return v * k

    def box(e):
        return [s(e["cx"] - e["rx"]), s(e["cy"] - e["ry"]), s(e["cx"] + e["rx"]), s(e["cy"] + e["ry"])]

    def pts(points):
        return [(s(x), s(y)) for (x, y) in points]

    # Tile: vertical red gradient clipped to a rounded square.
    grad = Image.linear_gradient("L").resize((size, size))
    tile = Image.composite(Image.new("RGB", (size, size), RED_BOTTOM),
                           Image.new("RGB", (size, size), RED_TOP), grad).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [s(TILE["x0"]), s(TILE["y0"]), s(TILE["x1"]), s(TILE["y1"])], radius=s(TILE["r"]), fill=255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img.paste(tile, (0, 0), mask)

    d = ImageDraw.Draw(img)
    ow = max(1, int(s(OUTLINE_W)))

    # Cream rim just inside the tile edge (ties it to the cream UI cards).
    d.rounded_rectangle([s(TILE["x0"] + 14), s(TILE["y0"] + 14), s(TILE["x1"] - 14), s(TILE["y1"] - 14)],
                        radius=s(TILE["r"] - 14), outline=CREAM + (90,), width=max(1, int(s(6))))

    # Ears (behind the head).
    for ear, inner in ((EAR_L, EAR_IN_L), (EAR_R, EAR_IN_R)):
        d.polygon(pts(ear), fill=PEACH, outline=OUTLINE, width=ow)
        d.polygon(pts(inner), fill=EAR_PINK)

    # Head.
    d.ellipse(box(HEAD), fill=PEACH, outline=OUTLINE, width=ow)
    d.ellipse(box(MUZZLE), fill=PEACH_LIGHT)

    # Cheeks (soft, semi-transparent).
    blush = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    for c in (CHEEK_L, CHEEK_R):
        bd.ellipse(box(c), fill=CHEEK + (200,))
    img.alpha_composite(blush)
    d = ImageDraw.Draw(img)

    # Eyes + shine.
    for e in (EYE_L, EYE_R):
        d.ellipse(box(e), fill=EYE)
        sx, sy = e["cx"] + SHINE_OFF[0], e["cy"] + SHINE_OFF[1]
        d.ellipse([s(sx - SHINE_R), s(sy - SHINE_R), s(sx + SHINE_R), s(sy + SHINE_R)], fill=WHITE)

    # Nose + "w" mouth.
    d.polygon(pts(NOSE), fill=CHEEK)
    mw = max(1, int(s(8)))
    for cx in (256 - MOUTH_R, 256 + MOUTH_R):
        d.arc([s(cx - MOUTH_R), s(MOUTH_Y - MOUTH_R), s(cx + MOUTH_R), s(MOUTH_Y + MOUTH_R)],
              start=0, end=180, fill=EYE, width=mw)

    # Gold sparkle.
    d.polygon(pts(sparkle_points(**SPARKLE)), fill=GOLD, outline=CREAM, width=max(1, int(s(5))))
    return img


# ---- vector -------------------------------------------------------------------
def svg() -> str:
    def rgb(c):
        return "#%02x%02x%02x" % c

    def poly(points, fill, stroke=None, sw=0):
        p = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        extra = f' stroke="{rgb(stroke)}" stroke-width="{sw}" stroke-linejoin="round"' if stroke else ""
        return f'  <polygon points="{p}" fill="{rgb(fill)}"{extra}/>'

    def ell(e, fill, stroke=None, sw=0, opacity=None):
        extra = f' stroke="{rgb(stroke)}" stroke-width="{sw}"' if stroke else ""
        if opacity is not None:
            extra += f' opacity="{opacity}"'
        return f'  <ellipse cx="{e["cx"]}" cy="{e["cy"]}" rx="{e["rx"]}" ry="{e["ry"]}" fill="{rgb(fill)}"{extra}/>'

    t = TILE
    out = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">',
        "  <defs>",
        '    <linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">',
        f'      <stop offset="0%" stop-color="{rgb(RED_TOP)}"/>',
        f'      <stop offset="100%" stop-color="{rgb(RED_BOTTOM)}"/>',
        "    </linearGradient>",
        "  </defs>",
        f'  <rect x="{t["x0"]}" y="{t["y0"]}" width="{t["x1"] - t["x0"]}" height="{t["y1"] - t["y0"]}" '
        f'rx="{t["r"]}" fill="url(#tile)"/>',
        f'  <rect x="{t["x0"] + 14}" y="{t["y0"] + 14}" width="{t["x1"] - t["x0"] - 28}" '
        f'height="{t["y1"] - t["y0"] - 28}" rx="{t["r"] - 14}" fill="none" stroke="{rgb(CREAM)}" '
        f'stroke-opacity="0.35" stroke-width="6"/>',
        poly(EAR_L, PEACH, OUTLINE, OUTLINE_W),
        poly(EAR_R, PEACH, OUTLINE, OUTLINE_W),
        poly(EAR_IN_L, EAR_PINK),
        poly(EAR_IN_R, EAR_PINK),
        ell(HEAD, PEACH, OUTLINE, OUTLINE_W),
        ell(MUZZLE, PEACH_LIGHT),
        ell(CHEEK_L, CHEEK, opacity=0.8),
        ell(CHEEK_R, CHEEK, opacity=0.8),
    ]
    for e in (EYE_L, EYE_R):
        out.append(ell(e, EYE))
        out.append(f'  <circle cx="{e["cx"] + SHINE_OFF[0]}" cy="{e["cy"] + SHINE_OFF[1]}" r="{SHINE_R}" fill="#ffffff"/>')
    out.append(poly(NOSE, CHEEK))
    a, b, c = 256 - 2 * MOUTH_R, 256, 256 + 2 * MOUTH_R
    out.append(f'  <path d="M{a} {MOUTH_Y} A{MOUTH_R} {MOUTH_R} 0 0 0 {b} {MOUTH_Y} '
               f'A{MOUTH_R} {MOUTH_R} 0 0 0 {c} {MOUTH_Y}" fill="none" stroke="{rgb(EYE)}" '
               f'stroke-width="8" stroke-linecap="round"/>')
    out.append(poly(sparkle_points(**SPARKLE), GOLD, CREAM, 5))
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main() -> None:
    here = Path(__file__).resolve().parent
    base = render(SS)

    base.resize((256, 256), Image.LANCZOS).save(here / "widget.png", format="PNG")
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    base.save(here / "widget.ico", format="ICO", sizes=[(n, n) for n in ico_sizes])
    (here / "pips-icon.svg").write_text(svg(), encoding="utf-8")
    print(f"wrote {here / 'widget.png'}, {here / 'widget.ico'} {ico_sizes}, {here / 'pips-icon.svg'}")

    if "--web" in sys.argv:
        web = Path(sys.argv[sys.argv.index("--web") + 1])
        web.mkdir(parents=True, exist_ok=True)
        base.resize((512, 512), Image.LANCZOS).save(web / "icon-512.png")
        base.resize((192, 192), Image.LANCZOS).save(web / "icon-192.png")
        # iOS masks its own corners and dislikes transparency: flatten on red.
        apple = Image.new("RGB", (SS, SS), RED_BOTTOM)
        apple.paste(base, (0, 0), base)
        apple.resize((180, 180), Image.LANCZOS).save(web / "apple-touch-icon.png")
        (web / "pips-icon.svg").write_text(svg(), encoding="utf-8")
        print(f"wrote web icons to {web}")


if __name__ == "__main__":
    main()
