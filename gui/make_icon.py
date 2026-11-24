from pathlib import Path
from PIL import Image, ImageDraw

OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

SCALE = 4
BASE = 256
SIZE = BASE * SCALE

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

# Smooth deep slate-indigo gradient background
bg = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
bg_px = bg.load()
for y in range(SIZE):
    t = y / (SIZE - 1)
    for x in range(SIZE):
        s = x / (SIZE - 1)
        r = int(12 + 28 * (0.35 * s + 0.65 * t))
        g = int(20 + 36 * (0.45 * s + 0.55 * (1 - t)))
        b = int(44 + 74 * (0.55 * s + 0.45 * t))
        bg_px[x, y] = (r, g, b, 255)

mask = Image.new("L", (SIZE, SIZE), 0)
ImageDraw.Draw(mask).rounded_rectangle(
    (12 * SCALE, 12 * SCALE, SIZE - 13 * SCALE, SIZE - 13 * SCALE),
    radius=56 * SCALE,
    fill=255,
)
img.paste(bg, (0, 0), mask)

draw = ImageDraw.Draw(img)
draw.rounded_rectangle(
    (14 * SCALE, 14 * SCALE, SIZE - 15 * SCALE, SIZE - 15 * SCALE),
    radius=54 * SCALE,
    outline=(56, 189, 248, 195),
    width=5 * SCALE,
)

def sc(pts):
    return [(x * SCALE, y * SCALE) for x, y in pts]

# Three layered Strata MoE plates
plates = [
    ([(50, 174), (128, 206), (206, 174), (128, 146)], (99, 102, 241, 225)),
    ([(50, 138), (128, 170), (206, 138), (128, 110)], (56, 189, 248, 238)),
    ([(50, 102), (128, 134), (206, 102), (128, 72)],  (52, 211, 153, 248)),
]
for pts, col in plates:
    spts = sc(pts)
    draw.polygon(spts, fill=col)
    draw.line(spts + [spts[0]], fill=(255, 255, 255, 135), width=3 * SCALE, joint="curve")

# Crisp lightning spark badge
bolt = sc([(156, 40), (122, 94), (146, 94), (130, 138), (178, 80), (152, 80)])
draw.polygon(bolt, fill=(250, 204, 21, 255))
draw.line(bolt + [bolt[0]], fill=(255, 255, 255, 215), width=2 * SCALE, joint="curve")

final_img = img.resize((BASE, BASE), Image.Resampling.LANCZOS)
png_path = OUT_DIR / "inkling.png"
ico_path = OUT_DIR / "inkling.ico"
final_img.save(png_path, format="PNG")
final_img.save(ico_path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("Saved anti-aliased 4x supersampled icons:", png_path, ico_path)
