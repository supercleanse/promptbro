"""PrompterWindow: layer-shell overlay window (DESIGN.md 7.1).

Layer-shell is used only on a Wayland display where the protocol is supported;
under Broadway (tests) it is a plain undecorated window.
"""
from __future__ import annotations

import logging
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LayerShell
except (ValueError, ImportError):
    LayerShell = None
from gi.repository import Gdk, Gtk  # noqa: E402

from .config import WindowConfig
from .monitors import (Debouncer, choose_monitor, find_monitor, focused_from_hyprctl,
                       repick_action, valid_connectors)
from .view import PrompterView

log = logging.getLogger("promptbro.window")

NAMESPACE = "promptbro"


def is_wayland() -> bool:
    d = Gdk.Display.get_default()
    return d is not None and d.__gtype__.name == "GdkWaylandDisplay"


def layer_shell_supported() -> bool:
    return bool(LayerShell is not None and is_wayland() and LayerShell.is_supported())


def display_monitors():
    """The display's live Gio.ListModel of Gdk.Monitor (a seam for tests)."""
    return Gdk.Display.get_default().get_monitors()


def monitor_by_connector(connector: str):
    """Return the currently valid Gdk.Monitor with this connector name, or None.

    Use the result immediately; never store it (it goes invalid on unplug).
    """
    return find_monitor(display_monitors(), connector)


class PrompterWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cfg: WindowConfig, palette: dict):
        super().__init__(application=app)
        self.cfg = cfg
        self.add_css_class("prompter")
        self.set_decorated(False)
        self.set_default_size(cfg.width, cfg.panel_height())
        self.layer = False
        self._monitor = None            # connector name only; never a Gdk.Monitor
        self._monitors_model = None
        self._monitors_handler = None
        self._hotplug = None
        self.on_closed = None           # app callback when the surface is closed under us
        if layer_shell_supported():
            LayerShell.init_for_window(self)                      # before present()
            LayerShell.set_namespace(self, NAMESPACE)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, cfg.top_gap)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            if hasattr(LayerShell, "set_respect_close"):              # gtk4-layer-shell >= 1.3
                LayerShell.set_respect_close(self, False)
            self.layer = True
            self.pick_monitor()
            self._hotplug = Debouncer(self._repick_after_hotplug)
            self._watch_monitors()
            self.connect("close-request", self._on_close_request)
        else:
            log.warning("layer-shell unavailable (backend %s); using a plain window",
                        Gdk.Display.get_default().__gtype__.name)
        card = Gtk.Box()
        card.add_css_class("card")
        self.view = PrompterView(cfg, palette)
        card.append(self.view)
        self.set_child(card)
        if cfg.click_through:
            self.connect("map", self._apply_click_through)

    def _gdk_connectors(self) -> list:
        return valid_connectors(display_monitors())

    def _target_monitor(self):
        return choose_monitor(self.cfg.monitor, self._gdk_connectors(), focused_from_hyprctl())

    def pick_monitor(self) -> None:
        """Apply the configured/focused monitor to the layer surface.

        set_monitor on a mapped surface does not move it; callers unmap first when the
        target changed. The Gdk.Monitor is looked up fresh, checked valid, and dropped.
        """
        if not self.layer:
            return
        name, why = self._target_monitor()
        mon = monitor_by_connector(name) if name else None
        if name and mon is None:                # vanished between the two lookups
            name, why = None, why + ":gone"
        LayerShell.set_monitor(self, mon)       # None = compositor's choice
        self._monitor = name
        log.info("monitor: %s (%s)", name or "compositor choice", why)

    def show(self) -> None:
        """Re-pick the monitor, remapping if it changed, then present."""
        if self.layer:
            if self._hotplug is not None:
                self._hotplug.cancel()          # this show re-picks anyway
            if self.get_visible():
                name, _ = self._target_monitor()
                if name != self._monitor or not self._current_monitor_valid():
                    self.set_visible(False)     # unmap so the new monitor takes effect
                    self.pick_monitor()
            else:
                self.pick_monitor()
        self.present()

    def _current_monitor_valid(self) -> bool:
        return self._monitor is None or monitor_by_connector(self._monitor) is not None

    def _monitors_changed(self, _model, position, removed, added) -> None:
        # Outputs are being added/removed right now (a dock re-enumerating fires several of
        # these in a row) and the removed GdkMonitor is mid-invalidation. Touch nothing here;
        # re-pick once the list has been quiet for HOTPLUG_SETTLE_MS.
        log.info("monitors changed (at %d: -%d +%d); re-pick after settle", position, removed,
                 added)
        if self._hotplug is not None:
            self._hotplug.poke()

    def _repick_after_hotplug(self) -> None:
        if not self.layer:
            return
        visible = self.get_visible()
        target, _ = self._target_monitor() if visible else (None, "")
        action = repick_action(visible, self._monitor, target, self._current_monitor_valid())
        log.info("monitors settled: %s (now %s)", action, ", ".join(self._gdk_connectors()) or "none")
        if action == "defer":
            # Hidden: leave the layer surface alone. gtk4-layer-shell already dropped an
            # invalidated monitor; show() picks a valid one before mapping again.
            return
        if action == "remap":
            self.set_visible(False)
            self.pick_monitor()
            self.present()

    def _on_close_request(self, *_a) -> bool:
        # The compositor closed the layer surface (older gtk4-layer-shell forwards .closed as
        # a close request; 1.3+ ignores it because we set respect_close False). Never let GTK
        # destroy the window: unmap it, which drops the dead layer surface; the next show
        # maps a fresh one on a valid monitor.
        log.warning("layer surface closed by the compositor; hiding, will recreate on next show")
        self._monitor = None
        if callable(self.on_closed):
            self.on_closed()
        else:
            self.set_visible(False)
        return True

    def _watch_monitors(self) -> None:
        """Follow the display's monitor list without the list keeping this window alive.

        The handler holds only a weak reference and is disconnected when the window is
        destroyed; a pending re-pick is cancelled on destroy and on unrealize. A hotplug
        can never call into a dead window.
        """
        model = display_monitors()
        ref = weakref.ref(self)

        def on_items_changed(m, position, removed, added):
            w = ref()
            if w is not None:
                w._monitors_changed(m, position, removed, added)

        handler = model.connect("items-changed", on_items_changed)
        hotplug = self._hotplug
        done = []

        def stop(*_a):
            if not done:
                done.append(1)
                hotplug.cancel()
                model.disconnect(handler)

        self._monitors_model, self._monitors_handler = model, handler
        self.connect("destroy", stop)
        self.connect("unrealize", lambda *_a: hotplug.cancel())

    def _apply_click_through(self, *_a) -> None:
        import cairo
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())
