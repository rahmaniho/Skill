#!/usr/bin/env python3
"""ساخت آیکون‌های PWA رابط «نُوا» — بدون هیچ وابستگی بیرونی (حتی Pillow).

چرا خودمان؟ چون مخزن صفر وابستگی پایتون دارد و آیکون‌ها باید در CI قابل بازتولید و
بررسی باشند. این اسکریپت پیکسل‌ها را با ریاضی می‌سازد (میدان فاصله برای گوشهٔ گرد و
آنتی‌الیاس) و با `zlib`+`struct` فایل PNG می‌نویسد.

طرح: مربع گوشه‌گرد با گرادیان مورب (نیلی → فیروزه‌ای → سبز) و نماد «رادار/هدف» سفید.
خروجی‌ها:
    nova/icons/icon-192.png      nova/icons/icon-512.png
    nova/icons/maskable-512.png  nova/icons/apple-touch-icon.png
    nova/icons/icon.svg          (برای favicon و مقیاس بی‌نهایت)

اجرا:
    python3 scripts/make_icons.py            # ساخت همه
    python3 scripts/make_icons.py --check    # فقط بررسی همگام‌بودن (برای CI)
"""
from __future__ import annotations

import argparse
import hashlib
import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "nova" / "icons"

# گرادیان برند: نیلی → فیروزه‌ای → سبز
STOPS = ((79, 70, 229), (6, 182, 212), (16, 185, 129))
GLYPH = (255, 255, 255)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ramp(t: float) -> tuple[float, float, float]:
    """رنگ گرادیان در جایگاه t (۰ تا ۱) — دو تکهٔ هم‌اندازه."""
    t = min(1.0, max(0.0, t))
    if t < 0.5:
        a, b, k = STOPS[0], STOPS[1], t / 0.5
    else:
        a, b, k = STOPS[1], STOPS[2], (t - 0.5) / 0.5
    return tuple(lerp(a[i], b[i], k) for i in range(3))


def sd_rounded_box(x: float, y: float, half: float, radius: float) -> float:
    """فاصلهٔ علامت‌دار تا مربع گوشه‌گرد (مرکز در مبدأ). منفی = داخل."""
    dx = abs(x) - (half - radius)
    dy = abs(y) - (half - radius)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(ax, ay) + min(max(dx, dy), 0.0) - radius


def glyph_coverage(nx: float, ny: float, maskable: bool) -> float:
    """پوشش نماد سفید در نقطهٔ نرمال (−۱..۱) — رادار: دو کمان + نقطهٔ مرکز + خط جاروب.

    maskable=True → نماد کوچک‌تر (منطقهٔ امن ۸۰٪) تا در برش دایره/مربع حذف نشود.
    """
    scale = 0.78 if maskable else 1.0
    x, y = nx / scale, ny / scale
    r = math.hypot(x, y)
    if r > 1.35:
        return 0.0
    cov = 0.0

    # حلقهٔ بیرونی (کمان ۲۷۰ درجه با شکاف در بالا-چپ)
    angle = math.atan2(-y, x)
    ring = abs(r - 0.82)
    gap = (angle > 2.35) and (angle < 3.05)
    if not gap:
        cov = max(cov, 1.0 - min(1.0, max(0.0, (ring - 0.055) / 0.055)))

    # کمان میانی
    ring2 = abs(r - 0.52)
    gap2 = (angle < -0.35) and (angle > -1.35)
    if not gap2:
        cov = max(cov, 1.0 - min(1.0, max(0.0, (ring2 - 0.045) / 0.05)))

    # نقطهٔ مرکز (لیدِ پیدا شده)
    cov = max(cov, 1.0 - min(1.0, max(0.0, (r - 0.15) / 0.06)))

    # خط جاروب رادار (از مرکز به سمت بالا-راست)
    if 0.55 < r < 0.95 and abs(angle - 0.78) < 0.05:
        cov = max(cov, 0.85)
    return min(1.0, cov)


def render(size: int, *, maskable: bool = False, radius_ratio: float = 0.22) -> bytes:
    """بافر RGBA اندازهٔ size×size."""
    half = size / 2.0
    radius = size * radius_ratio
    pad = size * (0.10 if maskable else 0.0)     # maskable: پس‌زمینه تمام‌صفحه
    rows = bytearray()
    for py in range(size):
        rows.append(0)                              # فیلتر None برای هر اسکن‌لاین
        y = (py + 0.5) - half
        for px in range(size):
            x = (px + 0.5) - half
            # پوشش گوشهٔ گرد (آنتی‌الیاس تقریبی با یک پیکسل نرمی)
            d = sd_rounded_box(x, y, half - pad, radius)
            alpha = 1.0 - min(1.0, max(0.0, (d + 0.5) / 1.0))
            if alpha <= 0.003:
                rows.extend(b"\x00\x00\x00\x00")
                continue
            # گرادیان مورب با کمی درخشش مرکزی
            t = ((x + y) / (2.0 * (half - pad)) + 1.0) / 2.0
            glow = 0.16 * max(0.0, 1.0 - math.hypot(x, y) / (half * 1.1))
            r0, g0, b0 = ramp(t)
            r0 = min(255.0, r0 + 255.0 * glow)
            g0 = min(255.0, g0 + 255.0 * glow)
            b0 = min(255.0, b0 + 255.0 * glow)
            # نماد سفید
            nx, ny = x / (half - pad), y / (half - pad)
            g = glyph_coverage(nx, ny, maskable)
            if g > 0:
                r0 = lerp(r0, GLYPH[0], g)
                g0 = lerp(g0, GLYPH[1], g)
                b0 = lerp(b0, GLYPH[2], g)
            # پوسترانه‌سازی ملایم: هم نوی گرادیان را کم می‌کند هم فشرده‌سازی بهتر می‌شود
            rows.extend(bytes((int(round(r0 / 6) * 6), int(round(g0 / 6) * 6),
                               int(round(b0 / 6) * 6), int(round(alpha * 255)))))
    return bytes(rows)


def png_bytes(size: int, *, maskable: bool = False, radius_ratio: float = 0.22) -> bytes:
    """بافر کامل PNG (بدون نیاز به کتابخانهٔ تصویر)."""
    raw = render(size, maskable=maskable)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)   # ۸-بیت RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#4f46e5"/>
      <stop offset=".5" stop-color="#06b6d4"/>
      <stop offset="1" stop-color="#10b981"/>
    </linearGradient>
  </defs>
  <rect width="512" height="512" rx="112" fill="url(#g)"/>
  <g fill="none" stroke="#fff" stroke-linecap="round">
    <path d="M256 92a164 164 0 1 1-116 48" stroke-width="34"/>
    <path d="M256 168a88 88 0 1 0 62 26" stroke-width="28"/>
    <path d="M256 256 340 190" stroke-width="26"/>
  </g>
  <circle cx="256" cy="256" r="34" fill="#fff"/>
</svg>
"""

TARGETS = (
    ("icon-192.png", 192, False),
    ("icon-512.png", 512, False),
    ("maskable-512.png", 512, True),
    ("apple-touch-icon.png", 180, False),
)


def build() -> dict[str, bytes]:
    """همهٔ فایل‌های آیکون به‌صورت {نام: بایت} — برای ساخت و برای --check."""
    out: dict[str, bytes] = {"icon.svg": SVG.encode("utf-8")}
    for name, size, maskable in TARGETS:
        out[name] = png_bytes(size, maskable=maskable)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ساخت آیکون‌های PWA رابط نُوا")
    parser.add_argument("--check", action="store_true", help="فقط بررسی همگام‌بودن با فایل‌ها")
    args = parser.parse_args(argv)

    wanted = build()
    if args.check:
        stale, missing = [], []
        for name, data in wanted.items():
            path = ICON_DIR / name
            if not path.is_file():
                missing.append(name)
            elif hashlib.sha256(path.read_bytes()).hexdigest() != hashlib.sha256(data).hexdigest():
                stale.append(name)
        if missing or stale:
            print(f"❌ آیکون‌ها به‌روز نیستند — python3 scripts/make_icons.py  "
                  f"(نبود: {missing or '—'} · کهنه: {stale or '—'})")
            return 1
        print(f"✔ {len(wanted)} آیکون PWA به‌روز است")
        return 0

    for name, data in wanted.items():
        path = ICON_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"  ✔ {path.relative_to(ROOT)}  ({len(data):,} بایت)")
    print(f"\n✔ {len(wanted)} آیکون در nova/icons/ ساخته شد")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
