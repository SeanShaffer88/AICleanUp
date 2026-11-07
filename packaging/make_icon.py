#!/usr/bin/env python3
"""Regenerate the app icon, the Windows .ico, and the browser favicon.

`python packaging/make_icon.py`

The artwork lives in packaging/aicleanup-icon.png. It arrives with a flat light
backdrop, which would show as pale corners in Explorer, on a dark taskbar, and
in a browser tab, so the backdrop is knocked out to transparency before the
sizes below are written.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "aicleanup-icon.png"
MASTER = HERE / "aicleanup.png"
ICO = HERE / "aicleanup.ico"
FAVICON = HERE.parent / "web" / "favicon.png"

MASTER_SIZE = 512
PADDING = 0.03          # transparent breathing room around the artwork
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
FAVICON_SIZES = [64, 180]


def rounded_mask(size: tuple[int, int], radius: int, supersample: int = 4) -> Image.Image:
    """Anti-aliased rounded-rectangle mask, drawn large and scaled down."""
    scaled = (size[0] * supersample, size[1] * supersample)
    mask = Image.new("L", scaled, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, scaled[0] - 1, scaled[1] - 1], radius=radius * supersample, fill=255
    )
    return mask.resize(size, Image.LANCZOS)


def load_icon() -> Image.Image:
    art = Image.open(SOURCE).convert("RGBA")
    source = art.load()

    # The artwork sits on a flat light backdrop. Find the plate itself, then cut
    # a couple of pixels inside it, because the anti-aliased boundary between
    # plate and backdrop would otherwise read as a pale outline on a dark
    # taskbar, a favicon strip, or a browser tab.
    plate = Image.new("L", art.size, 0)
    plate_pixels = plate.load()
    for y in range(art.height):
        for x in range(art.width):
            red, green, blue, alpha = source[x, y]
            # A strict threshold keeps the anti-aliased boundary out of the box,
            # so the box is the plate itself rather than its halo.
            if alpha > 8 and abs(red - 242) + abs(green - 242) + abs(blue - 242) > 240:
                plate_pixels[x, y] = 255
    box = plate.getbbox()
    if box is None:
        raise SystemExit(f"Could not find the icon plate in {SOURCE}")
    art = art.crop(box)
    plate = plate.crop(box)

    width, height = art.size
    top_row = plate.load()
    radius = next((x for x in range(width) if top_row[x, 0] > 0), width // 8)
    # Diagonal edges blend over more pixels than straight ones, so the inset
    # scales with the artwork instead of being a fixed couple of pixels.
    inset = max(2, round(min(width, height) * 0.012))
    art = art.crop((inset, inset, width - inset, height - inset))
    alpha = rounded_mask(art.size, max(radius - inset, 2))
    masked = Image.new("RGBA", art.size, (0, 0, 0, 0))
    masked.paste(art, (0, 0), alpha)

    # Square the artwork up so every generated size keeps its proportions.
    square = Image.new("RGBA", (max(masked.size), max(masked.size)), (0, 0, 0, 0))
    square.paste(masked, ((square.width - masked.width) // 2, (square.height - masked.height) // 2), masked)
    inner = round(MASTER_SIZE * (1 - PADDING * 2))
    square = square.resize((inner, inner), Image.LANCZOS)
    master = Image.new("RGBA", (MASTER_SIZE, MASTER_SIZE), (0, 0, 0, 0))
    offset = (MASTER_SIZE - inner) // 2
    master.paste(square, (offset, offset), square)
    return master


def main() -> int:
    if not SOURCE.is_file():
        raise SystemExit(f"Missing artwork: {SOURCE}")
    master = load_icon()
    master.save(MASTER)
    master.save(ICO, format="ICO", sizes=ICO_SIZES)
    for size in FAVICON_SIZES:
        name = FAVICON if size == FAVICON_SIZES[0] else FAVICON.with_name(f"favicon-{size}.png")
        master.resize((size, size), Image.LANCZOS).save(name)
    print(f"{MASTER} ({MASTER_SIZE}x{MASTER_SIZE})")
    print(f"{ICO} ({', '.join(f'{w}x{h}' for w, h in ICO_SIZES)})")
    print(f"{FAVICON} ({FAVICON_SIZES[0]}x{FAVICON_SIZES[0]})")
    print(f"{FAVICON.with_name(f'favicon-{FAVICON_SIZES[1]}.png')} ({FAVICON_SIZES[1]}x{FAVICON_SIZES[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
