#!/usr/bin/env python3
"""Compose preview.png from staged renders. Offscreen only; never opens a window.

Run it with the plugin venv (needs pycairo and PyGObject):

    tests/offscreen/render.sh --fake-data tests/offscreen/out
    ~/.local/share/promptbro/venv/bin/python scripts/make_preview.py

It renders the prompter on the bundled sample script through a private Broadway
display (GDK_BACKEND=broadway), then draws the bar widget and dropdown PNGs from
tests/offscreen/out/ over a theme-colored background. All content is canned.
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cairo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "offscreen" / "out"
SAMPLE = ROOT / "promptbro" / "data" / "sample-script.md"
W, H = 1280, 560
BG_TOP, BG_BOTTOM = (0.10, 0.11, 0.16), (0.07, 0.08, 0.12)
BAR = (0.13, 0.14, 0.20)


def _rt() -> str:
    return os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")


def render_prompter(dest: Path) -> None:
    """Prompter panel on the sample script, rendered on a private Broadway display."""
    while True:
        disp = random.randint(100, 900)
        if not any(os.path.exists(f"{_rt()}/broadway{k}.socket") for k in (disp, disp + 1)):
            break
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
                   PROMPTBRO_TEST="1")
        env.pop("WAYLAND_DISPLAY", None)
        subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro",
                        "--app-id-suffix", "preview", "--open", str(SAMPLE),
                        "--render-png", str(dest), "--size", "760x260", "--line", "3"],
                       cwd=ROOT, env=env, check=True, timeout=90)
    finally:
        srv.terminate()
        try:
            srv.wait(5)
        except subprocess.TimeoutExpired:
            srv.kill()
        for k in (disp, disp + 1):
            try:
                os.unlink(f"{_rt()}/broadway{k}.socket")
            except OSError:
                pass


def draw_png(ctx: cairo.Context, path: Path, x: float, y: float, width: float) -> float:
    img = cairo.ImageSurface.create_from_png(str(path))
    s = width / img.get_width()
    ctx.save()
    ctx.translate(x, y)
    ctx.scale(s, s)
    ctx.set_source_surface(img, 0, 0)
    ctx.paint()
    ctx.restore()
    return img.get_height() * s


def label(ctx: cairo.Context, text: str, x: float, y: float, size: float = 15) -> None:
    ctx.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    ctx.set_font_size(size)
    ctx.set_source_rgba(0.75, 0.78, 0.88, 0.9)
    ctx.move_to(x, y)
    ctx.show_text(text)


def main() -> int:
    for need in ("dropdown-listening.png", "widget-listening.png", "widget-idle.png",
                 "widget-paused.png", "widget-no-audio.png"):
        if not (OUT / need).is_file():
            print(f"missing {OUT / need}; run tests/offscreen/render.sh --fake-data tests/offscreen/out",
                  file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory() as td:
        prompter = Path(td) / "prompter.png"
        render_prompter(prompter)

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        ctx = cairo.Context(surf)
        grad = cairo.LinearGradient(0, 0, 0, H)
        grad.add_color_stop_rgb(0, *BG_TOP)
        grad.add_color_stop_rgb(1, *BG_BOTTOM)
        ctx.set_source(grad)
        ctx.paint()

        # the bar
        ctx.set_source_rgb(*BAR)
        ctx.rectangle(0, 0, W, 44)
        ctx.fill()
        label(ctx, "PromptBro in the bar:", 24, 28, 14)

        # widget states, left to right, with captions
        x = 220
        for name, cap in (("idle", "idle"), ("listening", "listening"),
                          ("paused", "paused"), ("no-audio", "no audio")):
            draw_png(ctx, OUT / f"widget-{name}.png", x, 4, 120)
            label(ctx, cap, x + 124, 28, 12)
            x += 215

        # dropdown, opened from the listening widget
        dd_w = 340
        dd_h = draw_png(ctx, OUT / "dropdown-listening.png", W - dd_w - 40, 70, dd_w)
        label(ctx, "Click the icon for the menu", W - dd_w - 40, 70 + dd_h + 24, 13)

        # the prompter, under the bar
        pw = 800
        ph = draw_png(ctx, prompter, 40, 110, pw)
        label(ctx, "The prompter follows your voice", 40, 110 + ph + 28, 13)

        surf.write_to_png(str(ROOT / "preview.png"))
    print(f"wrote {ROOT / 'preview.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
