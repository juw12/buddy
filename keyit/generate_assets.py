"""Draw the KeyIt keycap icon -> assets/keyit.ico (multi-size) and assets/keyit.png."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).parent / "assets"
SIZE = 256


def load_font(size):
    for name in ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_icon():
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Keycap skirt (side walls)
    draw.rounded_rectangle((12, 28, 244, 244), radius=44, fill=(38, 40, 52))
    # Keycap top surface with a subtle highlight
    draw.rounded_rectangle((40, 32, 216, 196), radius=34, fill=(255, 132, 36))
    draw.rounded_rectangle((52, 40, 204, 120), radius=28, fill=(255, 158, 74))
    draw.rounded_rectangle((52, 84, 204, 186), radius=26, fill=(255, 132, 36))
    # Legend
    font = load_font(118)
    box = draw.textbbox((0, 0), "K", font=font)
    text_x = 128 - (box[0] + box[2]) / 2
    text_y = 114 - (box[1] + box[3]) / 2
    draw.text((text_x, text_y), "K", font=font, fill=(255, 255, 255))
    return image


if __name__ == "__main__":
    ASSETS_DIR.mkdir(exist_ok=True)
    icon = draw_icon()
    icon.save(ASSETS_DIR / "keyit.png")
    icon.save(ASSETS_DIR / "keyit.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", ASSETS_DIR / "keyit.ico")
