"""Makes `stage\\icon.ico`: made-up artwork (a page with a check badge on a teal tile), standard library only.

    python -I -B make_icon.py OUT.ico

Sizes 16, 24, 32 and 48 are 32-bit DIB entries; 64 and 256 are PNG entries (Windows Vista and newer). The output is
the same bytes on every run (no time stamps), so the stage is reproducible.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

SIZES = (16, 24, 32, 48, 64, 256)
PNG_FROM = 64                       # entries this size and larger are stored as PNG

TEAL = (31, 111, 139)
WHITE = (255, 255, 255)
GREY = (150, 170, 182)
GREEN = (46, 158, 91)

RGBA = Tuple[int, int, int, int]


def _in_round_rect(x: float, y: float, x0: float, y0: float, x1: float, y1: float, r: float) -> bool:
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


PAGE = (0.24, 0.14, 0.70, 0.84)    # x0, y0, x1, y1 in the unit square
FOLD = 0.13                        # the folded top-right corner


def _in_corner(x: float, y: float) -> bool:
    _, y0, x1, _ = PAGE
    return x1 - FOLD <= x <= x1 and y0 <= y <= y0 + FOLD


def _in_page(x: float, y: float) -> bool:
    """The page: a rectangle whose top-right corner triangle is cut away (the fold)."""
    x0, y0, x1, y1 = PAGE
    if not (x0 <= x <= x1 and y0 <= y <= y1):
        return False
    return not (_in_corner(x, y) and (x - (x1 - FOLD)) > (y - y0))


def _in_fold(x: float, y: float) -> bool:
    """The folded flap: the lower-left half of the corner square."""
    _, y0, x1, _ = PAGE
    return _in_corner(x, y) and (x - (x1 - FOLD)) <= (y - y0)


def _in_lines(x: float, y: float) -> bool:
    for top, right in ((0.30, 0.52), (0.40, 0.60), (0.50, 0.60), (0.60, 0.46)):
        if 0.32 <= x <= right and top <= y <= top + 0.045:
            return True
    return False


def _seg_dist2(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)))
    dx, dy = px - (ax + t * vx), py - (ay + t * vy)
    return dx * dx + dy * dy


def _colour_at(x: float, y: float) -> Optional[Tuple[int, int, int]]:
    """The colour of the artwork at (x, y) in the unit square, or None (transparent)."""
    cx, cy, r = 0.70, 0.71, 0.20
    if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
        w2 = 0.045 ** 2
        if (_seg_dist2(x, y, 0.61, 0.72, 0.68, 0.79) <= w2 or _seg_dist2(x, y, 0.68, 0.79, 0.80, 0.63) <= w2):
            return WHITE
        return GREEN
    if _in_fold(x, y):
        return GREY
    if _in_page(x, y):
        return GREY if _in_lines(x, y) else WHITE
    if _in_round_rect(x, y, 0.03, 0.03, 0.97, 0.97, 0.18):
        return TEAL
    return None


def render(size: int, ss: Optional[int] = None) -> List[List[RGBA]]:
    """size x size RGBA pixels, top row first, with ss x ss supersampling (straight alpha)."""
    ss = ss or (3 if size >= 128 else 4)
    rows: List[List[RGBA]] = []
    n = ss * ss
    for py in range(size):
        row: List[RGBA] = []
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                y = (py + (sy + 0.5) / ss) / size
                for sx in range(ss):
                    c = _colour_at((px + (sx + 0.5) / ss) / size, y)
                    if c is not None:
                        r += c[0]
                        g += c[1]
                        b += c[2]
                        a += 1
            if a:
                row.append((round(r / a), round(g / a), round(b / a), round(255 * a / n)))
            else:
                row.append((0, 0, 0, 0))
        rows.append(row)
    return rows


def png_bytes(pixels: Sequence[Sequence[RGBA]]) -> bytes:
    h, w = len(pixels), len(pixels[0])
    raw = b"".join(b"\x00" + bytes(v for px in row for v in px) for row in pixels)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def dib_bytes(pixels: Sequence[Sequence[RGBA]]) -> bytes:
    """A 32-bit BGRA DIB as an ICO entry: BITMAPINFOHEADER (height doubled), bottom-up rows, then the AND mask."""
    h, w = len(pixels), len(pixels[0])
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    xor = b"".join(bytes((px[2], px[1], px[0], px[3])) for row in reversed(pixels) for px in row)
    row_bytes = ((w + 31) // 32) * 4
    mask = bytearray()
    for row in reversed(pixels):
        bits = bytearray(row_bytes)
        for x, px in enumerate(row):
            if px[3] == 0:
                bits[x // 8] |= 0x80 >> (x % 8)
        mask += bits
    return header + xor + bytes(mask)


def ico_bytes(sizes: Sequence[int] = SIZES) -> bytes:
    images = []
    for s in sizes:
        if not 1 <= s <= 256:
            raise ValueError(f"icon size {s} out of range")
        px = render(s)
        images.append((s, png_bytes(px) if s >= PNG_FROM else dib_bytes(px)))
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in images:
        dim = 0 if s == 256 else s
        out += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(d for _, d in images)


def write_icon(path: Path, sizes: Sequence[int] = SIZES) -> int:
    data = ico_bytes(sizes)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return len(data)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or not argv[0].lower().endswith(".ico"):
        print("usage: make_icon.py OUT.ico", file=sys.stderr)
        return 2
    n = write_icon(Path(argv[0]))
    print(f"icon written: {n} bytes, sizes {', '.join(str(s) for s in SIZES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
