"""PrompterWindow: layer-shell overlay window (DESIGN.md 7.1).

Layer-shell is used only on a Wayland display where the protocol is supported;
under Broadway (tests) it is a plain undecorated window.
"""
from __future__ import annotations

import logging

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
from .monitors import choose_monitor, focused_from_hyprctl
from .view import PrompterView

log = logging.getLogger("promptbro.window")

NAMESPACE = "promptbro"


def is_wayland() -> bool:
    d = Gdk.Display.get_default()
    return d is not None and d.__gtype__.name == "GdkWaylandDisplay"


def layer_shell_supported() -> bool:
    return bool(LayerShell is not None and is_wayland() and LayerShell.is_supported())


def monitor_by_connector(connector: str):
    """Return the Gdk.Monitor with this connector name, or None."""
    if not connector:
        return None
    mons = Gdk.Display.get_default().get_monitors()
    for i in range(mons.get_n_items()):
        m = mons.get_item(i)
        if m.get_connector() == connector:
            return m
    return None


class PrompterWindow(Gtk.ApplicationWindow):
    def __init__(self, app, cfg: WindowConfig, palette: dict):
        super().__init__(application=app)
        self.cfg = cfg
        self.add_css_class("prompter")
        self.set_decorated(False)
        self.set_default_size(cfg.width, cfg.panel_height())
        self.layer = False
        if layer_shell_supported():
            LayerShell.init_for_window(self)                      # before present()
            LayerShell.set_namespace(self, NAMESPACE)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_margin(self, LayerShell.Edge.TOP, cfg.top_gap)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            self.layer = True
            self.pick_monitor()
            Gdk.Display.get_default().get_monitors().connect("items-changed", self._monitors_changed)
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
        mons = Gdk.Display.get_default().get_monitors()
        return [mons.get_item(i).get_connector() for i in range(mons.get_n_items())]

    def pick_monitor(self) -> None:
        """Apply the configured/focused monitor to the layer surface.

        set_monitor on a mapped surface does not move it; the caller (show) unmaps
        first when the target changed. Safe to call while hidden.
        """
        if not self.layer:
            return
        name, why = choose_monitor(self.cfg.monitor, self._gdk_connectors(),
                                   focused_from_hyprctl())
        mon = monitor_by_connector(name) if name else None
        LayerShell.set_monitor(self, mon)       # None = compositor's choice
        self._monitor = name
        log.info("monitor: %s (%s)", name or "compositor choice", why)

    def show(self) -> None:
        """Re-pick the monitor, remapping if it changed, then present."""
        if self.layer:
            prev = getattr(self, "_monitor", None)
            if self.get_visible():
                name, _ = choose_monitor(self.cfg.monitor, self._gdk_connectors(),
                                         focused_from_hyprctl())
                if name != prev:
                    self.set_visible(False)     # unmap so the new monitor takes effect
                    self.pick_monitor()
            else:
                self.pick_monitor()
        self.present()

    def _monitors_changed(self, *_a) -> None:
        was_visible = self.get_visible()
        if was_visible:
            self.set_visible(False)
        self.pick_monitor()
        if was_visible:
            self.present()

    def _apply_click_through(self, *_a) -> None:
        import cairo
        surface = self.get_surface()
        if surface is not None:
            surface.set_input_region(cairo.Region())
