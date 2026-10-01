"""Application shell: single-instance Gtk.Application and the verb dispatcher (DESIGN.md 7.2)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import __version__, paths, recent

# Verbs that need a running prompter. With none running they start nothing: `status` prints
# offline, the rest exit 1. The other verbs (open, paste, show, toggle) may become the instance.
CONTROL_VERBS = ("opacity", "status", "hide", "pause", "resume", "next", "prev", "restart", "goto",
                 "reload-theme", "quit")
VERBS = ("opacity", "toggle", "show", "hide", "pause", "resume", "next", "prev", "restart", "goto",
         "open", "paste", "reload-theme", "status", "quit")
APP_ID = "supercleanse.PromptBro"
BIN = Path(__file__).resolve().parent.parent / "bin" / "promptbro"
TEST_ENV = "PROMPTBRO_TEST"      # set by tests/conftest.py for every test and its subprocesses
SENDER_ENV = "PROMPTBRO_SENDER"  # who ran this command; reaches the primary via SEND_ENVIRONMENT
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3


def log_path() -> Path:
    return paths.log_file()


def notify(message: str) -> None:
    """Desktop notification for a failed first launch (a hotkey has no terminal to print to)."""
    if os.environ.get(TEST_ENV):
        return
    try:
        subprocess.run(["notify-send", "PromptBro", message], timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def first_recent() -> str | None:
    """Most recent script in recent.json that still exists; None when there is none."""
    return recent.first_existing()


def remember(path: str) -> None:
    """Add a successfully opened script to recent.json (never fatal)."""
    try:
        recent.add(path)
    except OSError as e:
        logging.getLogger("promptbro").warning("could not update recent.json: %s", e)


def forget(path: str) -> None:
    """Drop a script that failed to open (and any other missing path) from recent.json."""
    try:
        recent.remove(path)
    except OSError as e:
        logging.getLogger("promptbro").warning("could not update recent.json: %s", e)


def _name_has_owner(Gio, name: str) -> bool:
    """NameHasOwner on the session bus; False when the bus cannot be reached."""
    try:
        from gi.repository import GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        res = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                            "NameHasOwner", GLib.Variant("(s)", (name,)), GLib.VariantType("(b)"),
                            Gio.DBusCallFlags.NONE, 2000, None)
        return bool(res.unpack()[0])
    except Exception:  # noqa: BLE001
        return False


def describe_sender(pid: int | None = None, depth: int = 4) -> str:
    """This process and its ancestors, e.g. ``pid 812: python3 < bash < Hyprland``.

    Logged by the primary with every verb, so a surprise ``hide`` or ``pause`` names its source
    (a hotkey shows Hyprland as an ancestor; an agent's shell shows claude/node)."""
    pid = os.getpid() if pid is None else pid
    names, cur = [], pid
    for _ in range(depth):
        try:
            stat = Path(f"/proc/{cur}/stat").read_text()
        except OSError:
            break
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        names.append(comm)
        cur = int(stat[stat.rindex(")") + 2:].split()[1])
        if cur <= 1:
            break
    return f"pid {pid}: " + " < ".join(names)


def setup_logging(level_name: str) -> Path | None:
    """stderr (journal when launched by Hyprland) plus the rotating file of DESIGN 4.2."""
    import logging.handlers

    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(level=level, format="%(asctime)s.%(msecs)03d %(name)s: %(message)s",
                            datefmt="%H:%M:%S")
    root.setLevel(level)
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS,
                                                  encoding="utf-8")
    except OSError as e:
        logging.getLogger("promptbro").warning("no log file (%s)", e)
        return None
    fh.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
                                      datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(fh)
    return path


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)

    def exit(self, status=0, message=None):
        raise ValueError(message or "")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="promptbro", description="Voice-following promptbro")
    p.add_argument("--version", action="store_true")
    p.add_argument("--config", metavar="PATH")
    p.add_argument("--model", metavar="DIR")
    p.add_argument("--device", default=None)
    p.add_argument("--wav", metavar="FILE")
    p.add_argument("--replay", metavar="FILE", help="replay a recorded event stream (.json[.gz])")
    p.add_argument("--open", dest="open_path", metavar="FILE", help="load this script at startup")
    p.add_argument("--headless-status", action="store_true",
                   help="print status JSON every second on stdout; quit when the WAV/replay ends")
    p.add_argument("--log-level", default=None, choices=("debug", "info", "warning", "error"))
    p.add_argument("--demo", action="store_true", help="use bundled demo script (never real data)")
    p.add_argument("--app-id-suffix", default="")
    p.add_argument("--render-png", metavar="PATH")
    p.add_argument("--size", default="720x240")
    p.add_argument("--line", type=int, default=0)
    p.add_argument("--render-at", type=float, default=None, metavar="SECS",
                   help="with --render-png --replay: render this many seconds into the replay")
    p.add_argument("--check-layer-shell", action="store_true")
    p.add_argument("--print-hypr-snippet", action="store_true")
    p.add_argument("verb", nargs="?", choices=VERBS)
    p.add_argument("rest", nargs="*")
    return p


def hypr_snippet(hide_from_share: bool = True) -> str:
    b = BIN
    share = "" if hide_from_share else "-- "
    return f'''-- PromptBro: paste into ~/.config/hypr/hyprland.lua and bindings.lua yourself.
hl.layer_rule({{ match = {{ namespace = "^promptbro$" }}, animation = "fade" }})
-- hide the prompter from screen shares (and your own screenshots)
{share}hl.layer_rule({{ match = {{ namespace = "^promptbro$" }}, no_screen_share = true }})

o.bind("SUPER + SHIFT + T",            "PromptBro toggle",  "{b} toggle")
o.bind("SUPER + SHIFT + backslash",    "PromptBro pause",   "{b} pause")
o.bind("SUPER + SHIFT + bracketright", "PromptBro next",    "{b} next")
o.bind("SUPER + SHIFT + bracketleft",  "PromptBro prev",    "{b} prev")
o.bind("SUPER + SHIFT + Home",         "PromptBro restart", "{b} restart")
'''


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = build_parser().parse_args(argv)
    except ValueError as e:
        print(f"promptbro: {e}", file=sys.stderr)
        return 2
    if args.version:
        print(f"promptbro {__version__}")
        return 0
    if args.print_hypr_snippet:
        from . import config
        cfg = config.load(Path(args.config) if args.config else None)
        print(hypr_snippet(cfg.window.hide_from_screen_share), end="")
        return 0
    if args.render_png:
        from . import render
        return render.run(args)
    if args.check_layer_shell:
        from . import window
        print(f"LayerShell.is_supported() == {window.layer_shell_supported()}")
        return 0
    return _run(argv, args)


def _run(argv, args) -> int:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    from . import config, controller, theme, window

    suffix = args.app_id_suffix or ("Demo" if args.demo else "")
    app_id = f"{APP_ID}.{suffix}" if suffix else APP_ID
    log = logging.getLogger("promptbro")
    if os.environ.get(TEST_ENV) and app_id == APP_ID:
        # Tests must never reach (or become) the author's real instance: they always pass
        # --app-id-suffix and run on a private bus. Refuse instead of sending the verb.
        print(f"promptbro: {TEST_ENV} is set; refusing to use the real app id {APP_ID} "
              "(pass --app-id-suffix)", file=sys.stderr)
        return 3
    os.environ[SENDER_ENV] = describe_sender()
    launching = (args.verb is None or args.verb in ("open", "paste", "show", "toggle")
                 or args.open_path or args.demo or args.replay or args.wav)
    if not launching and not _name_has_owner(Gio, app_id):
        # Nothing to control. Never construct the application: that would own the name.
        if args.verb == "status":
            print(json.dumps({"running": False, "state": "offline"}))
            return 0
        print("promptbro: not running", file=sys.stderr)
        return 1

    class App(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=app_id,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
                             | Gio.ApplicationFlags.SEND_ENVIRONMENT)
            self.win = None
            self.ctl = None
            self.cfg = None
            self._theme_mon = None
            self._css = None
            self._headless = False

        def _apply_saved_opacity(self) -> None:
            """state.json's opacity (set by the slider or `promptbro opacity`) beats config.toml."""
            try:
                v = json.loads(paths.state_file().read_text()).get("opacity")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    self.cfg.window.opacity = round(min(1.0, max(0.3, float(v))), 3)
            except (OSError, ValueError, AttributeError):
                pass

        # ---- build (primary only) ---------------------------------------
        def _build(self, a):
            self.cfg = config.load(Path(a.config) if a.config else None)
            self._apply_saved_opacity()
            lp = setup_logging(a.log_level or self.cfg.log.level)
            log.info("promptbro %s starting (app id %s, pid %d, log %s)", __version__, app_id,
                     os.getpid(), lp)
            self._css = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            pal = self._apply_css()
            self.win = window.PrompterWindow(self, self.cfg.window, pal)
            source = "replay" if a.replay else "wav" if a.wav else None if a.demo else "mic"
            self.ctl = controller.Controller(self.win.view, self.cfg, source=source, wav=a.wav,
                                             replay=a.replay, model=a.model, device=a.device)
            self.ctl.on_finished = self._input_finished
            self._headless = a.headless_status
            if self._headless:
                GLib.timeout_add(1000, self._print_status)
            self._theme_mon = theme.watch(self._reload_theme)
            self._add_actions()
            self.ctl.start_status()
            self.hold()  # stay alive while the window is hidden

        # ---- GActions (exported on the session bus at /<app/id>) -----------
        ACTIONS = ("next", "prev", "restart", "pause", "resume", "show", "hide", "toggle", "quit",
                   "reload-theme")

        def _add_actions(self):
            for name in self.ACTIONS:
                act = Gio.SimpleAction.new(name, None)
                act.connect("activate", self._on_action, name)
                self.add_action(act)
            op = Gio.SimpleAction.new("opacity", GLib.VariantType("d"))
            op.connect("activate", self._on_action, "opacity")
            self.add_action(op)

        def _on_action(self, _action, param, name):
            log.info("action %s (dbus)", name)
            if self.ctl is None:
                return
            try:
                c = self.ctl
                if name == "next":
                    c.next()
                elif name == "prev":
                    c.prev()
                elif name == "restart":
                    c.restart()
                elif name == "pause":
                    c.toggle_pause()
                elif name == "resume":
                    c.set_paused(False)
                elif name == "show":
                    self._set_visible(True)
                elif name == "hide":
                    self._set_visible(False)
                elif name == "toggle":
                    self._set_visible(not c.visible)
                elif name == "reload-theme":
                    self._reload_theme()
                elif name == "opacity":
                    c.set_opacity(param.get_double())
                elif name == "quit":
                    self._quit()
            except Exception:  # noqa: BLE001
                log.exception("action %s failed", name)

        def _apply_css(self):
            pal = theme.load_palette()
            # The window and card are fully transparent; PrompterView paints the one and only
            # background (theme color at window.opacity alpha), so text is never faded.
            self._css.load_from_string(theme.css_defines(pal) + theme.TRANSPARENT_CSS)
            return pal

        def _reload_theme(self):
            if self.win:
                self.win.view.set_palette(self._apply_css())

        # ---- visibility -------------------------------------------------
        def _set_visible(self, v: bool):
            if v:
                self.win.show()
            else:
                self.win.set_visible(False)
            self.ctl.set_visible(v)

        # ---- headless status (tests) ------------------------------------
        def _print_status(self):
            if self.ctl is None:
                return GLib.SOURCE_REMOVE
            sys.stdout.write(json.dumps(self.ctl.status()) + "\n")
            sys.stdout.flush()
            return GLib.SOURCE_CONTINUE

        def _input_finished(self):
            if self._headless:
                self._print_status()
                self._quit()

        def _quit(self):
            self.ctl.shutdown()
            self.release()
            self.quit()

        # ---- command line -----------------------------------------------
        def do_command_line(self, cmdline):
            try:
                a = build_parser().parse_args(cmdline.get_arguments()[1:])
            except ValueError as e:
                cmdline.printerr_literal(f"promptbro: {e}\n")
                return 2
            first = self.win is None
            if first:
                try:
                    self._build(a)
                except Exception as e:  # noqa: BLE001
                    cmdline.printerr_literal(f"promptbro: startup failed: {e}\n")
                    return 1
                self._log_verb(a, cmdline)  # after _build: logging is set up there
                try:
                    done = self._first_launch(a, cmdline)
                except Exception as e:  # noqa: BLE001
                    return self._first_launch_failed(e, cmdline)
                if done:
                    return 0
            if not first:
                self._log_verb(a, cmdline)
            try:
                return self._dispatch(a, cmdline, first)
            except Exception as e:  # noqa: BLE001
                log.exception("verb failed")
                if first and a.verb in ("open", "paste"):
                    return self._first_launch_failed(e, cmdline)
                cmdline.printerr_literal(f"promptbro: {e}\n")
                return 1

        def _first_launch(self, a, cmdline) -> bool:
            """Initial state for the process that becomes the instance. True when the command
            is fully handled (the caller must not dispatch the verb on top of it)."""
            if a.demo:
                from . import demo
                self.ctl.load_text(demo.SAMPLE.read_text(encoding="utf-8"), None)
                self._set_visible(True)
            elif a.open_path or (a.replay and a.verb is None):
                if a.open_path:
                    p = Path(a.open_path).expanduser()
                    if not p.is_absolute():
                        p = Path(cmdline.get_cwd() or ".") / p
                    self.ctl.load_file(str(p))
                    remember(self.ctl.path)
                    self._set_visible(True)
                elif self.ctl.load_replay_script():
                    self._set_visible(True)
                else:
                    self._set_visible(False)
            elif a.verb in ("toggle", "show"):
                # Hotkey with the app off: reopen the last script, or stay hidden.
                last = first_recent()
                if last:
                    self.ctl.load_file(last)
                    remember(self.ctl.path)
                    self._set_visible(True)
                else:
                    self._set_visible(False)
                return True
            elif a.verb is None:
                self._set_visible(False)  # hidden, ready for `paste`/`open`
            return False

        def _first_launch_failed(self, err, cmdline) -> int:
            """A first-launch open/paste that failed must not leave a blank hidden instance."""
            log.error("first launch failed: %s", err)
            msg = str(err) or err.__class__.__name__
            if isinstance(err, FileNotFoundError):
                if getattr(err, "filename", None):
                    forget(str(err.filename))
                msg = f"Can't open {getattr(err, 'filename', None) or 'that file'}: not found"
            notify(msg)
            cmdline.printerr_literal(f"promptbro: {msg}\n")
            self._quit()
            return 1

        def _log_verb(self, a, cmdline) -> None:
            verb = a.verb or ("open" if a.open_path else "(none: toggle)")
            what = " ".join([verb, *a.rest, *( [a.open_path] if a.open_path and not a.verb else [])])
            sender = cmdline.getenv(SENDER_ENV) or "unknown sender"
            origin = "remote" if cmdline.get_is_remote() else "local"
            (log.debug if verb == "status" else log.info)("verb %s (%s, %s)", what, origin, sender)

        def _dispatch(self, a, cmdline, first) -> int:
            verb, rest, c = a.verb, a.rest, self.ctl
            if verb is None:
                if not first:
                    self._set_visible(not c.visible)
                return 0
            if verb == "toggle":
                self._set_visible(not c.visible)
            elif verb == "show":
                self._set_visible(True)
            elif verb == "hide":
                self._set_visible(False)
            elif verb == "pause":
                c.toggle_pause()
            elif verb == "resume":
                c.set_paused(False)
            elif verb == "next":
                c.next()
            elif verb == "prev":
                c.prev()
            elif verb == "restart":
                c.restart()
            elif verb == "goto":
                c.goto(int(rest[0]))
            elif verb == "open":
                if not rest:
                    raise ValueError("open needs a path")
                p = Path(rest[0]).expanduser()
                if not p.is_absolute():
                    p = Path(cmdline.get_cwd() or ".") / p
                try:
                    c.load_file(str(p))
                except FileNotFoundError:
                    forget(str(p))
                    raise
                remember(c.path)
                self._set_visible(True)
            elif verb == "paste":
                raw = cmdline.getenv("PROMPTBRO_PASTE_CMD") or "wl-paste --no-newline"
                out = subprocess.run(shlex.split(raw), capture_output=True, text=True, timeout=5,
                                     cwd=cmdline.get_cwd() or None)
                if out.returncode != 0:
                    raise RuntimeError(f"clipboard command failed: {out.stderr.strip()}")
                c.load_text(out.stdout, None)
                self._set_visible(True)
            elif verb == "reload-theme":
                self._reload_theme()
            elif verb == "opacity":
                if not rest:
                    raise ValueError("opacity needs a value from 0.3 to 1.0")
                try:
                    val = float(rest[0])
                except ValueError:
                    raise ValueError(f"opacity needs a number, got {rest[0]!r}") from None
                c.set_opacity(val)
            elif verb == "status":
                cmdline.print_literal(json.dumps(c.status()) + "\n")
            elif verb == "quit":
                self._quit()
            return 0

    return App().run([sys.argv[0], *argv])


if __name__ == "__main__":
    sys.exit(main())
