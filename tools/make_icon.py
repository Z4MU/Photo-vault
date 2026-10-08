"""
Genera el ícono de PhotoVault (assets/icon.ico con varios tamaños + assets/icon.png).

    py tools/make_icon.py

Una cámara con un ojo de cerradura en el lente (fotos + bóveda), con la
paleta de la app (config.COLORS). Se dibuja a 1024 px y se reduce.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
S = 1024
BG_TOP = (30, 30, 46)  # panel_alt
BG_BOTTOM = (13, 13, 26)  # bg
ACCENT = (74, 158, 255)  # accent
ACCENT_DARK = (40, 96, 190)
WHITE = (236, 240, 255)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # Fondo: cuadrado redondeado con degradé vertical
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        gd.line([(0, y), (S, y)], fill=lerp(BG_TOP, BG_BOTTOM, y / S) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([24, 24, S - 24, S - 24], radius=210, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([24, 24, S - 24, S - 24], radius=210, outline=(58, 58, 90, 255), width=10)

    # Cuerpo de la cámara
    body = [170, 330, S - 170, 790]
    d.rounded_rectangle(body, radius=90, fill=ACCENT + (255,))
    # Visor (arriba a la izquierda) y botón
    d.rounded_rectangle([300, 250, 500, 360], radius=40, fill=ACCENT + (255,))
    d.rounded_rectangle([660, 280, 760, 340], radius=24, fill=ACCENT_DARK + (255,))
    # Lente: anillos
    cx, cy = S // 2, 565
    for r, color in ((205, BG_BOTTOM), (175, ACCENT_DARK), (150, BG_TOP)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color + (255,))
    # Ojo de cerradura en el lente
    d.ellipse([cx - 52, cy - 95, cx + 52, cy + 9], fill=WHITE + (255,))
    d.polygon(
        [(cx - 34, cy - 20), (cx + 34, cy - 20), (cx + 58, cy + 105), (cx - 58, cy + 105)],
        fill=WHITE + (255,),
    )
    # Brillo del lente
    glare = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glare).ellipse([cx - 120, cy - 135, cx - 50, cy - 65], fill=(255, 255, 255, 70))
    img = Image.alpha_composite(img, glare.filter(ImageFilter.GaussianBlur(6)))
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.Resampling.LANCZOS).save(OUT / "icon.png")
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    big.save(OUT / "icon.ico", sizes=sizes)
    print("assets/icon.ico y assets/icon.png generados")


if __name__ == "__main__":
    main()
