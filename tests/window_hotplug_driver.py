"""Drive PrompterWindow's hotplug handling under Broadway with a fake layer-shell and fake
monitors (run by tests/test_window_hotplug.py in a subprocess; prints one JSON object).

Simulates the 2026-10-01 dock re-enumeration: DP-1 is invalidated and removed, then a new
DP-1 monitor object appears, in a burst of items-changed signals."""
import json
import sys
import types
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from promptbro import window  # noqa: E402
from promptbro.config import WindowConfig  # noqa: E402

calls = []          # every LayerShell.set_monitor: (connector, valid-at-call-time)
in_signal = []      # set_monitor calls made while an items-changed emission was running
emitting = [False]


class FakeMonitor:
    def __init__(self, connector):
        self.connector, self.valid = connector, True

    def get_connector(self):
        return self.connector

    def is_valid(self):
        return self.valid


class FakeModel:
    def __init__(self, items):
        self.items, self.handlers, self.next = list(items), {}, 1

    def get_n_items(self):
        return len(self.items)

    def get_item(self, i):
        return self.items[i]

    def connect(self, sig, cb):
        assert sig == "items-changed"
        h, self.next = self.next, self.next + 1
        self.handlers[h] = cb
        return h

    def disconnect(self, h):
        del self.handlers[h]

    def emit(self, pos, removed, added):
        emitting[0] = True
        try:
            for cb in list(self.handlers.values()):
                cb(self, pos, removed, added)
        finally:
            emitting[0] = False


def set_monitor(_w, mon):
    rec = (mon.get_connector(), mon.is_valid()) if mon is not None else (None, None)
    calls.append(rec)
    if emitting[0]:
        in_signal.append(rec)


def _noop(*_a, **_k):
    return None


fake_ls = types.SimpleNamespace(
    Layer=types.SimpleNamespace(OVERLAY=3), Edge=types.SimpleNamespace(TOP=2),
    KeyboardMode=types.SimpleNamespace(NONE=0), is_supported=lambda: True,
    init_for_window=_noop, set_namespace=_noop, set_layer=_noop, set_anchor=_noop,
    set_exclusive_zone=_noop, set_margin=_noop, set_keyboard_mode=_noop,
    set_respect_close=_noop, set_monitor=set_monitor)

edp, dp = FakeMonitor("eDP-1"), FakeMonitor("DP-1")
model = FakeModel([edp, dp])
focus = ["DP-1"]
window.LayerShell = fake_ls
window.layer_shell_supported = lambda: True
window.display_monitors = lambda: model
window.focused_from_hyprctl = lambda: focus[0]


def pump(ms):
    ctx = GLib.MainContext.default()
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)
        GLib.usleep(2000)


def unplug_replug(burst_gap_ms=30, replug=True):
    """Invalidate + remove DP-1, then (optionally) add a fresh DP-1 object."""
    global dp
    dp.valid = False
    i = model.items.index(dp)
    model.items.pop(i)
    model.emit(i, 1, 0)
    pump(burst_gap_ms)
    if replug:
        dp = FakeMonitor("DP-1")
        model.items.append(dp)
        model.emit(len(model.items) - 1, 0, 1)


out = {}


def scenario(app):
    cfg = WindowConfig()
    cfg.monitor = "auto"
    win = window.PrompterWindow(app, cfg, {})
    out["initial"] = list(calls)

    # 1. hidden window, dock re-enumerates: nothing touches the layer surface.
    n = len(calls)
    unplug_replug()
    out["hidden_during_burst"] = calls[n:]
    out["hidden_pending"] = win._hotplug.pending
    pump(700)
    out["hidden_after_settle"] = calls[n:]

    # 2. show: picks the fresh, valid DP-1.
    win.show()
    pump(50)
    out["show"] = calls[n:]
    out["visible_after_show"] = win.get_visible()

    # 3. visible, DP-1 unplugged (focus still says DP-1): falls back to compositor choice.
    n = len(calls)
    unplug_replug(replug=False)
    out["visible_during_burst"] = calls[n:]
    pump(700)
    out["visible_unplugged"] = calls[n:]
    out["visible_after_unplug"] = win.get_visible()

    # 4. DP-1 returns while focus is on eDP-1: remaps to eDP-1.
    n = len(calls)
    focus[0] = "eDP-1"
    dp_new = FakeMonitor("DP-1")
    model.items.append(dp_new)
    model.emit(len(model.items) - 1, 0, 1)
    pump(700)
    out["visible_replug_focus_edp"] = calls[n:]

    # 5. unrelated change, same valid target: keep (no remap).
    n = len(calls)
    model.items.append(FakeMonitor("HDMI-A-1"))
    model.emit(len(model.items) - 1, 0, 1)
    pump(700)
    out["keep"] = calls[n:]

    # 6. compositor closes the surface: window survives, hidden, app callback runs.
    closed = []
    win.on_closed = lambda: (closed.append(1), win.set_visible(False))
    win.close()
    pump(50)
    out["closed_callback"] = len(closed)
    out["visible_after_close"] = win.get_visible()
    n = len(calls)
    focus[0] = "DP-1"
    win.show()
    pump(50)
    out["reshow_after_close"] = calls[n:]
    out["visible_after_reshow"] = win.get_visible()

    # 7. destroyed window: later hotplug must not reach it, and the monitor list must not
    #    keep it alive.
    import gc
    alive = weakref.ref(win)
    win.destroy()
    del win
    gc.collect()
    pump(50)
    out["window_finalized"] = alive() is None
    out["handlers_after_destroy"] = len(model.handlers)
    n = len(calls)
    model.emit(0, 0, 0)
    pump(600)
    out["after_destroy"] = calls[n:]
    out["in_signal"] = in_signal
    app.quit()


app = Gtk.Application(application_id="test.promptbro.hotplug",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", lambda a: (a.hold(), scenario(a)))
app.run([])
json.dump(out, sys.stdout)
