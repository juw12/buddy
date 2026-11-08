"""Draw Buddy's iPhone app icon (1024 x 1024, opaque, full-bleed: iOS rounds the corners itself)."""
import json
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent
ICONSET = HERE / "Buddy" / "Assets.xcassets" / "AppIcon.appiconset"
ORANGE = (255, 132, 36)
ORANGE_DARK = (226, 104, 30)
INK = (43, 33, 52)
CREAM = (255, 246, 236)


def draw(size=1024):
    image = Image.new("RGB", (size, size), ORANGE)
    draw = ImageDraw.Draw(image)
    s = size / 1024
    draw.rectangle((0, 900 * s, size, size), fill=ORANGE_DARK)                     # soft base shade
    draw.ellipse((192 * s, 232 * s, 832 * s, 872 * s), fill=CREAM)                 # face
    draw.ellipse((356 * s, 440 * s, 444 * s, 556 * s), fill=INK)                   # eyes
    draw.ellipse((580 * s, 440 * s, 668 * s, 556 * s), fill=INK)
    draw.ellipse((380 * s, 460 * s, 408 * s, 492 * s), fill=CREAM)
    draw.ellipse((604 * s, 460 * s, 632 * s, 492 * s), fill=CREAM)
    draw.arc((392 * s, 540 * s, 632 * s, 720 * s), start=20, end=160, fill=INK, width=int(32 * s))
    draw.line((512 * s, 232 * s, 512 * s, 136 * s), fill=CREAM, width=int(28 * s))  # antenna
    draw.ellipse((464 * s, 76 * s, 560 * s, 172 * s), fill=CREAM)
    return image


def main():
    ICONSET.mkdir(parents=True, exist_ok=True)
    draw().save(ICONSET / "icon-1024.png")
    contents = {
        "images": [{"filename": "icon-1024.png", "idiom": "universal", "platform": "ios", "size": "1024x1024"}],
        "info": {"author": "xcode", "version": 1},
    }
    (ICONSET / "Contents.json").write_text(json.dumps(contents, indent=2) + "\n", encoding="utf-8")
    (ICONSET.parent / "Contents.json").write_text(json.dumps({"info": {"author": "xcode", "version": 1}}, indent=2) + "\n",
                                                  encoding="utf-8")
    print("wrote", ICONSET / "icon-1024.png")


if __name__ == "__main__":
    main()
