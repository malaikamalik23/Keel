"""Draw the app icons (frontend/public/icons/*.png) from the same shapes as favicon.svg, with the standard library only.

    python -m scripts.gen_icons

Phones need PNG icons to install the dashboard as an app: 192 and 512 pixels, plus a "maskable" one whose logo sits
inside the safe circle so Android can crop it to any shape. Re-run after changing the logo.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "frontend" / "public" / "icons"
BLUE, WHITE = (0x2F, 0x5F, 0xD0), (0xFF, 0xFF, 0xFF)
# favicon.svg in a 64-unit box: the sail, the smaller sail (70% white) and the hull.
SHAPES = [([(32, 11), (46, 40), (32, 40)], 1.0), ([(29, 18), (18, 40), (29, 40)], 0.7), ([(12, 45), (52, 45), (45, 54), (19, 54)], 1.0)]


def _inside(px: float, py: float, poly: list[tuple[float, float]]) -> bool:
    inside, j = False, len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def render(size: int, *, maskable: bool) -> bytes:
    """RGBA rows. Maskable: full-bleed background and the logo shrunk into the central 80% (the safe zone)."""
    scale = 0.8 if maskable else 1.0
    radius = 0 if maskable else 14
    rows = []
    for y in range(size):
        row = bytearray([0])  # PNG filter type 0
        for x in range(size):
            u, v = (x + 0.5) * 64 / size, (y + 0.5) * 64 / size  # this pixel in favicon units
            # rounded-rect background (transparent outside the corners unless maskable)
            cx, cy = min(max(u, radius), 64 - radius), min(max(v, radius), 64 - radius)
            in_bg = maskable or (u - cx) ** 2 + (v - cy) ** 2 <= radius ** 2
            r, g, b, a = (*BLUE, 255) if in_bg else (0, 0, 0, 0)
            su, sv = 32 + (u - 32) / scale, 32 + (v - 32) / scale  # undo the shrink for the logo shapes
            for poly, alpha in SHAPES:
                if in_bg and _inside(su, sv, poly):
                    r, g, b = (round(c * (1 - alpha) + w * alpha) for c, w in zip((r, g, b), WHITE, strict=True))
            row += bytes((r, g, b, a))
        rows.append(bytes(row))
    return b"".join(rows)


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, size, maskable in (("icon-192.png", 192, False), ("icon-512.png", 512, False), ("maskable-512.png", 512, True),
                                 ("apple-touch-icon.png", 180, True)):
        (OUT / name).write_bytes(png(size, render(size, maskable=maskable)))
        print(f"wrote {OUT / name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
