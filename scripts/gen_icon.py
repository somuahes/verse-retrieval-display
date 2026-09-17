"""Generates the app icon (open book + star, gold-on-navy) at high
resolution, then packs it down into assets/icon.ico and assets/icon.png.
Run manually when the design changes; output is committed, this script
isn't a runtime dependency."""
import math
import os
from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets")
os.makedirs(OUT_DIR, exist_ok=True)

S = 1024  # master canvas, supersampled 4x then downscaled for AA
SS = 4
CANVAS = S * SS

NAVY_TOP = (35, 42, 74, 255)
NAVY_BOT = (13, 16, 34, 255)
GOLD_L = (233, 201, 122, 255)
GOLD = (201, 168, 76, 255)
GOLD_D = (122, 99, 48, 255)
CREAM = (247, 240, 222, 255)


def quad_bezier(p0, p1, p2, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t ** 2 * p2[0]
        y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t ** 2 * p2[1]
        pts.append((x, y))
    return pts


def rounded_rect_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def vertical_gradient(size, top, bot):
    grad = Image.new("RGBA", (1, size), 0)
    for y in range(size):
        t = y / (size - 1)
        px = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(4))
        grad.putpixel((0, y), px)
    return grad.resize((size, size))


def build_background():
    grad = vertical_gradient(CANVAS, NAVY_TOP, NAVY_BOT)
    radius = int(CANVAS * 0.22)
    mask = rounded_rect_mask(CANVAS, radius)
    bg = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    bg.paste(grad, (0, 0), mask)

    # Faint inner highlight near the top for a bit of depth/sheen.
    sheen = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.ellipse(
        [CANVAS * 0.05, -CANVAS * 0.35, CANVAS * 0.95, CANVAS * 0.55],
        fill=(255, 255, 255, 18),
    )
    sheen.putalpha(Image.composite(sheen.split()[3], Image.new("L", (CANVAS, CANVAS), 0), mask))
    bg = Image.alpha_composite(bg, sheen)
    return bg, mask


def build_book(cx, cy, half_w, half_h, dip):
    """One symmetric open-book leaf pair, drawn as two smooth quads
    meeting at a central vertical spine. dip = how much the spine
    pinches in vertically relative to the outer corners (the classic
    open-book curvature)."""
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    top_y = cy - half_h
    bot_y = cy + half_h
    spine_top = (cx, top_y + dip)
    spine_bot = (cx, bot_y - dip)

    for side in (-1, 1):
        outer_x = cx + side * half_w
        outer_top = (outer_x, top_y)
        outer_bot = (outer_x, bot_y)
        # Outer edge bulges slightly outward (page fanned open).
        outer_mid_ctrl = (outer_x + side * half_w * 0.18, cy)
        outer_edge = quad_bezier(outer_top, outer_mid_ctrl, outer_bot, 40)
        # Top edge curves from the outer top corner into the spine's
        # top point; bottom edge mirrors it.
        top_ctrl = (cx + side * half_w * 0.55, top_y - dip * 0.15)
        top_edge = quad_bezier(outer_top, top_ctrl, spine_top, 24)
        bot_ctrl = (cx + side * half_w * 0.55, bot_y + dip * 0.15)
        bot_edge = quad_bezier(spine_bot, bot_ctrl, outer_bot, 24)

        poly = list(reversed(top_edge)) + outer_edge + list(reversed(bot_edge))
        draw.polygon(poly, fill=GOLD)

        # A couple of short "text line" cut-outs on each page.
        for k in range(3):
            ly = cy - half_h * 0.32 + k * half_h * 0.32
            lx0 = cx + side * half_w * 0.22
            lx1 = cx + side * half_w * 0.72
            draw.line([(lx0, ly), (lx1, ly)], fill=NAVY_BOT, width=int(CANVAS * 0.012))

    # Spine line.
    draw.line([spine_top, spine_bot], fill=GOLD_D, width=int(CANVAS * 0.018))
    return layer


def build_star(cx, cy, r_outer, r_inner, points=4):
    layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    pts = []
    for i in range(points * 2):
        r = r_outer if i % 2 == 0 else r_inner
        angle = math.pi / points * i - math.pi / 2
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=CREAM)
    return layer


def main():
    bg, mask = build_background()

    cx, cy = CANVAS / 2, CANVAS * 0.56
    book = build_book(cx, cy, half_w=CANVAS * 0.30, half_h=CANVAS * 0.20, dip=CANVAS * 0.075)

    star = build_star(cx, CANVAS * 0.215, r_outer=CANVAS * 0.055, r_inner=CANVAS * 0.022)

    img = Image.alpha_composite(bg, book)
    img = Image.alpha_composite(img, star)

    # Soft drop shadow of the whole glyph group for a touch of polish.
    glyph_alpha = Image.alpha_composite(book, star).split()[3]
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (0, int(CANVAS * 0.01)), glyph_alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(CANVAS * 0.01))
    composed = Image.alpha_composite(bg, shadow)
    composed = Image.alpha_composite(composed, book)
    composed = Image.alpha_composite(composed, star)
    composed.putalpha(Image.composite(composed.split()[3], Image.new("L", (CANVAS, CANVAS), 0), mask))

    final = composed.resize((S, S), Image.LANCZOS)
    final.save(os.path.join(OUT_DIR, "icon.png"))

    sizes = [16, 24, 32, 48, 64, 128, 256]
    final.save(
        os.path.join(OUT_DIR, "icon.ico"),
        sizes=[(s, s) for s in sizes],
    )
    print("wrote", os.path.join(OUT_DIR, "icon.png"), "and icon.ico")


if __name__ == "__main__":
    main()
