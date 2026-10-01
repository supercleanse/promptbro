"""Offscreen PNG rendering (Broadway offscreen render).

Run only against a private gtk4-broadwayd (GDK_BACKEND=broadway): nothing is
shown on the live desktop. Loads either the bundled demo data (--demo) or a script
named with --open, optionally driven by a recorded --replay through the real
Controller + Aligner, and renders a settled frame (--line N, or --render-at S
seconds into the replay).
"""
from __future__ import annotations

import logging
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

from .config import WindowConfig
from .theme import TRANSPARENT_CSS, css_defines, load_palette
from .view import PrompterView

log = logging.getLogger("promptbro.render")


def parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x")
    return int(w), int(h)


def render_to_png(window: Gtk.Widget, path: str, scale: float = 1.0, fallback=None) -> None:
    w, h = window.get_width(), window.get_height()
    paintable = Gtk.WidgetPaintable.new(window)
    snap = Gtk.Snapshot()
    snap.scale(scale, scale)
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    if node is None and fallback is not None:
        # The widget's cached render node is dropped by queue_draw() and only rebuilt on the
        # next frame, which Broadway without a browser may not deliver in time. Draw directly.
        snap = Gtk.Snapshot()
        snap.scale(scale, scale)
        fallback.do_snapshot(snap)
        node = snap.to_node()
    if node is None:
        raise RuntimeError("nothing to render (window not laid out)")
    tex = window.get_native().get_renderer().render_texture(
        node, Graphene.Rect().init(0, 0, w * scale, h * scale))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tex.save_to_png(path)
    log.info("rendered %s (%dx%d)", path, w, h)


def run(args) -> int:
    """args: demo, open_path, replay, render_at, app_id_suffix, render_png, size, line."""
    if os.environ.get("GDK_BACKEND") != "broadway":
        print("render: refusing to run outside GDK_BACKEND=broadway", file=sys.stderr)
        return 2
    open_path = getattr(args, "open_path", None)
    replay = getattr(args, "replay", None)
    if not (args.demo or open_path or replay):
        print("render: --render-png needs --demo, --open FILE or --replay FILE", file=sys.stderr)
        return 2
    from . import demo

    width, height = parse_size(args.size)
    suffix = args.app_id_suffix or "render"
    app = Gtk.Application(application_id=f"supercleanse.PromptBro.{suffix}",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    rc = {"v": 1}

    def on_activate(app):
        pal = load_palette()
        prov = Gtk.CssProvider()
        prov.load_from_string(css_defines(pal) + TRANSPARENT_CSS)
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win = Gtk.ApplicationWindow(application=app)
        win.add_css_class("prompter")
        win.set_decorated(False)
        win.set_default_size(width, height)
        wcfg = WindowConfig(width=width, height=height)
        if os.environ.get("PROMPTBRO_RENDER_OPACITY"):      # test hook
            wcfg.opacity = float(os.environ["PROMPTBRO_RENDER_OPACITY"])
        if os.environ.get("PROMPTBRO_RENDER_LEGEND"):       # test hook
            wcfg.show_legend = os.environ["PROMPTBRO_RENDER_LEGEND"] == "1"
        view = PrompterView(wcfg, pal)
        delay_ms = 1000
        if open_path or replay:
            from . import config as config_mod
            from .controller import Controller

            cfg = config_mod.load(args.config) if args.config else config_mod.Config()
            cfg.window = wcfg
            ctl = Controller(view, cfg, source="replay" if replay else None, replay=replay)
            if open_path:
                ctl.load_file(open_path, restore=False)
            elif not ctl.load_replay_script():
                raise SystemExit("render: the replay file has no script; pass --open")
            if replay:
                ctl.set_visible(True)          # starts the replay clock now
                delay_ms = int(1000 * (args.render_at if args.render_at is not None else 1.0))
            elif args.line:
                ctl.goto(args.line)
            if os.environ.get("PROMPTBRO_RENDER_SET_OPACITY"):   # test hook: the opacity action path
                ctl.set_opacity(float(os.environ["PROMPTBRO_RENDER_SET_OPACITY"]))
        else:
            script = demo.load_demo_script()
            view.set_script(script)
            line = min(args.line, len(script.lines) - 1)
            view.jump_to(line)
            view.set_position(demo.DemoPosition(line, script.unit_at(line, 0.5)))
        win.set_child(view)
        win.present()

        def prepare():
            view.settle()          # finish any scroll animation and drop the transient toast,
            view.clear_toast()     # then give GTK a frame to redraw before rendering
            GLib.timeout_add(300, shoot)
            return GLib.SOURCE_REMOVE

        def shoot():
            try:
                render_to_png(win, args.render_png, fallback=view)
                rc["v"] = 0
            except Exception as e:  # noqa: BLE001
                print(f"render failed: {e}", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(max(delay_ms, 300), prepare)

    app.connect("activate", on_activate)
    app.run([])
    return rc["v"]
