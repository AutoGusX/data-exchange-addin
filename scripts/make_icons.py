"""Build the toolbar icons from a screenshot of the Data Exchange logo (a filled circle on white).

Usage: python scripts/make_icons.py <screenshot.png>
Writes fusion_addin/DataExchange/resources/dx/{16x16,32x32,64x64}.png and @2x variants, with the
white background outside the circle made transparent.
"""
import os
import sys

from PIL import Image, ImageDraw

SIZES = {'16x16.png': 16, '16x16@2x.png': 32, '32x32.png': 32, '32x32@2x.png': 64, '64x64.png': 64}
SUPERSAMPLE = 8


def main(source):
    image = Image.open(source).convert('RGB')
    width, height = image.size
    pixels = image.load()
    # The logo disc is the saturated blue-violet area; everything else is background.
    inside = [(x, y) for x in range(width) for y in range(height)
              if pixels[x, y][2] > 200 and pixels[x, y][0] < 140]
    if not inside:
        raise SystemExit('No logo disc found in the image.')
    left, right = min(p[0] for p in inside), max(p[0] for p in inside)
    top, bottom = min(p[1] for p in inside), max(p[1] for p in inside)
    cx, cy = (left + right + 1) / 2.0, (top + bottom + 1) / 2.0
    # Stay just inside the disc edge so no antialiased white fringe is kept.
    radius = min(right - left + 1, bottom - top + 1) / 2.0 - 1.0

    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    side = int(round(radius * 2 * SUPERSAMPLE))
    disc = image.resize((side, side), Image.LANCZOS, box=box).convert('RGBA')
    mask = Image.new('L', (side, side), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, side - 1, side - 1), fill=255)
    disc.putalpha(mask)

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fusion_addin', 'DataExchange',
                       'resources', 'dx')
    os.makedirs(out, exist_ok=True)
    for name, size in SIZES.items():
        disc.resize((size, size), Image.LANCZOS).save(os.path.join(out, name))
    print('wrote', len(SIZES), 'icons to', os.path.normpath(out))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
