"""App-Icon (Mikrofon auf Farbverlauf) erzeugen: app/static/icon.ico + icon.png."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "static"


def draw(size=512):
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # Farbverlauf blau -> violett
    grad = Image.new("RGBA", (s, s))
    px = grad.load()
    for y in range(s):
        for x in range(s):
            t = (x + y) / (2 * s)
            r = int(63 + (168 - 63) * t)
            g = int(124 + (85 - 124) * t)
            b = int(240 + (247 - 240) * t)
            px[x, y] = (r, g, b, 255)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = s / 2
    # Mikrofon-Kapsel
    w, h = s * 0.24, s * 0.40
    top = s * 0.16
    d.rounded_rectangle([cx - w / 2, top, cx + w / 2, top + h], radius=w / 2, fill=white)
    # Bügel
    lw = int(s * 0.045)
    arc_w = s * 0.40
    d.arc([cx - arc_w / 2, top + h * 0.35, cx + arc_w / 2, top + h + s * 0.10], start=0, end=180, fill=white, width=lw)
    # Stiel + Fuß
    stem_top = top + h + s * 0.10
    d.line([cx, stem_top, cx, s * 0.80], fill=white, width=lw)
    d.rounded_rectangle([cx - s * 0.14, s * 0.78, cx + s * 0.14, s * 0.78 + lw], radius=lw / 2, fill=white)
    # Schallwellen-Akzente
    for i, off in enumerate((0.30, 0.38)):
        rr = s * off
        d.arc([cx - rr, top + h * 0.5 - rr, cx + rr, top + h * 0.5 + rr], start=-35, end=35,
              fill=(255, 255, 255, 170 - i * 60), width=int(lw * 0.7))
        d.arc([cx - rr, top + h * 0.5 - rr, cx + rr, top + h * 0.5 + rr], start=145, end=215,
              fill=(255, 255, 255, 170 - i * 60), width=int(lw * 0.7))
    return img


if __name__ == "__main__":
    big = draw(512)
    big.save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icon gespeichert:", OUT / "icon.ico")
