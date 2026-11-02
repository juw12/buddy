"""Draw Buddy's own icon -> assets/buddy.ico (multi-size) and assets/buddy.png."""
from pathlib import Path

from PIL import Image, ImageDraw

ASSETS_DIR = Path(__file__).parent / "assets"
ORANGE = (255, 138, 61)
ORANGE_DARK = (226, 104, 30)
INK = (43, 33, 52)
CREAM = (255, 246, 236)


def draw_icon(size=512):
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = size / 512
    draw.rounded_rectangle((16 * s, 16 * s, 496 * s, 496 * s), radius=120 * s, fill=ORANGE_DARK)
    draw.rounded_rectangle((16 * s, 16 * s, 496 * s, 460 * s), radius=120 * s, fill=ORANGE)
    draw.ellipse((96 * s, 112 * s, 416 * s, 432 * s), fill=CREAM)                    # face
    draw.ellipse((178 * s, 218 * s, 222 * s, 276 * s), fill=INK)                     # eyes
    draw.ellipse((290 * s, 218 * s, 334 * s, 276 * s), fill=INK)
    draw.ellipse((190 * s, 228 * s, 204 * s, 244 * s), fill=CREAM)
    draw.ellipse((302 * s, 228 * s, 316 * s, 244 * s), fill=CREAM)
    draw.arc((196 * s, 270 * s, 316 * s, 360 * s), start=20, end=160, fill=INK, width=max(2, int(16 * s)))
    draw.line((256 * s, 112 * s, 256 * s, 64 * s), fill=CREAM, width=max(2, int(14 * s)))   # antenna
    draw.ellipse((232 * s, 34 * s, 280 * s, 82 * s), fill=CREAM)
    return image


def main():
    ASSETS_DIR.mkdir(exist_ok=True)
    icon = draw_icon()
    icon.resize((256, 256), Image.LANCZOS).save(ASSETS_DIR / "buddy.png")
    icon.save(ASSETS_DIR / "buddy.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ASSETS_DIR / "buddy.ico")


if __name__ == "__main__":
    main()
