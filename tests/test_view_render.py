import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bwutil import free_display, stop_display

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                                or shutil.which("dbus-run-session") is None,
                                reason="needs gtk4-broadwayd and dbus-run-session")

ACCENT = (0xE6, 0x8E, 0x0D)


def _near(px, c, tol=12):
    return all(abs(a - b) <= tol for a, b in zip(px[:3], c))


def test_render_png(tmp_path):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        out = ROOT / "tests" / "out" / "view.png"
        out.unlink(missing_ok=True)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}")
        env.pop("WAYLAND_DISPLAY", None)
        subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro", "--demo",
                        "--app-id-suffix", "test", "--render-png", str(out),
                        "--size", "720x240", "--line", "3"],
                       cwd=ROOT, env=env, check=True, timeout=60)
    finally:
        stop_display(srv, disp)
    assert out.exists()
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(str(out))
    data, rs, nc = pb.get_pixels(), pb.get_rowstride(), pb.get_n_channels()

    class Img:
        width, height = pb.get_width(), pb.get_height()

        @staticmethod
        def getpixel(xy):
            o = xy[1] * rs + xy[0] * nc
            return tuple(data[o:o + 3])

    img = Img
    assert img.width >= 720 and img.height >= 240
    assert len({img.getpixel((x, y)) for y in range(0, img.height, 3)
                for x in range(0, img.width, 3)}) > 1
    mid = img.height // 2
    assert any(_near(img.getpixel((x, y)), ACCENT) for y in range(mid - 4, mid + 5)
               for x in range(img.width)), "no accent pixels near the center row"
    assert not any(_near(img.getpixel((x, y)), ACCENT) for y in range(12)
                   for x in range(img.width)), "accent pixels in top 12 rows"


def _render(disp, out, opacity):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        out.unlink(missing_ok=True)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
                   PROMPTBRO_RENDER_OPACITY=str(opacity))
        env.pop("WAYLAND_DISPLAY", None)
        subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro", "--demo",
                        "--app-id-suffix", "optest", "--render-png", str(out),
                        "--size", "720x240", "--line", "3"],
                       cwd=ROOT, env=env, check=True, timeout=60)
    finally:
        stop_display(srv, disp)
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(str(out)).add_alpha(False, 0, 0, 0)
    return pb.get_pixels(), pb.get_rowstride(), pb.get_width(), pb.get_height()


def test_background_alpha_only():
    """Panel background alpha follows window.opacity; text pixels stay opaque and unchanged."""
    art = ROOT / "artifacts"
    art.mkdir(exist_ok=True)
    d75, rs, w, h = _render(38, art / "opacity-0.75.png", 0.75)
    d100, _, _, _ = _render(39, art / "opacity-1.0.png", 1.0)

    def px(d, x, y):
        o = y * rs + x * 4
        return tuple(d[o:o + 4])

    # Background sample well inside the card, left of the text, on the center row.
    a75 = px(d75, 6, h // 2)[3]
    a100 = px(d100, 6, h // 2)[3]
    assert abs(a75 - 0.75 * 255) <= 3, a75
    assert a100 >= 252, a100
    # Straight-alpha color of the background is the theme color either way (no darkening by fade).
    # Text: every accent-colored pixel in the center band is fully opaque and identical in both.
    n = 0
    for y in range(h // 2 - 20, h // 2 + 20):
        for x in range(w):
            p75, p100 = px(d75, x, y), px(d100, x, y)
            if _near(p100, ACCENT, 6) and p100[3] >= 252:
                assert p75[3] >= 252 and _near(p75, ACCENT, 6), (x, y, p75, p100)
                n += 1
    assert n > 50, "no solid accent text pixels found"
    # Composite over a known magenta backdrop for a visual check.
    bg = (200, 40, 160)
    out = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px(d75, x, y)
            k = a / 255
            out += bytes(int(c * k + bgc * (1 - k)) for c, bgc in zip((r, g, b), bg))
    from gi.repository import GdkPixbuf, GLib
    GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(out)), GdkPixbuf.Colorspace.RGB,
                                    False, 8, w, h, w * 3).savev(
        str(art / "opacity-0.75-over-magenta.png"), "png", [], [])


def _render_legend(disp, out, on, size):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        out.unlink(missing_ok=True)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
                   PROMPTBRO_RENDER_LEGEND="1" if on else "0")
        env.pop("WAYLAND_DISPLAY", None)
        subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro", "--demo",
                        "--app-id-suffix", "legtest", "--render-png", str(out),
                        "--size", size, "--line", "3"],
                       cwd=ROOT, env=env, check=True, timeout=60)
    finally:
        stop_display(srv, disp)
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(str(out)).add_alpha(False, 0, 0, 0)
    return pb.get_pixels(), pb.get_rowstride(), pb.get_width(), pb.get_height()


def test_hotkey_legend():
    from promptbro.legend import LEGEND_STRIP_PX
    art = ROOT / "artifacts"
    art.mkdir(exist_ok=True)
    # Window height = script height (240) + legend strip; legend off uses the plain 240.
    on, rs, w, h_on = _render_legend(40, art / "legend-on.png", True, "720x264")
    off, rs_off, _, h_off = _render_legend(41, art / "legend-off.png", False, "720x240")
    assert h_on - h_off == LEGEND_STRIP_PX

    def px(d, r, x, y):
        o = y * r + x * 4
        return tuple(d[o:o + 4])

    bg = px(off, rs_off, 6, h_off // 2)

    def lit(d, r, y0, y1):
        return sum(1 for y in range(y0, y1) for x in range(20, w - 20)
                   if any(abs(a - b) > 6 for a, b in zip(px(d, r, x, y), bg)))

    # Legend row: the strip, just above the bottom corner rounding.
    n_on = lit(on, rs, h_on - LEGEND_STRIP_PX + 2, h_on - 4)
    assert n_on > 100, n_on
    # Legend off: the window has no strip at all (height is the plain script height), and the
    # strip band of the legend-on render holds only legend pixels (the script area ends above it).
    assert h_off == 240
    # Current line (accent pixels) is identical with and without the legend.
    for y in range(h_off // 2 - 20, h_off // 2 + 20):
        for x in range(w):
            a, b = px(on, rs, x, y), px(off, rs_off, x, y)
            if _near(b, ACCENT, 6) or _near(a, ACCENT, 6):
                assert a == b, (x, y, a, b)
