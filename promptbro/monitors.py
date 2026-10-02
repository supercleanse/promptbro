"""Monitor choice and hotplug logic (pure; GTK objects are duck-typed). See DESIGN.md 7.1."""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Iterable, Optional, Tuple

log = logging.getLogger("promptbro.monitors")

AUTO = "auto"


def focused_from_hyprctl(timeout: float = 1.5) -> Optional[str]:
    """Connector name of the Hyprland-focused monitor, or None if hyprctl fails."""
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True,
                             text=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("hyprctl monitors failed (%s)", e)
        return None
    return parse_focused(out)


def parse_focused(text: str) -> Optional[str]:
    """Extract the focused monitor's name from `hyprctl monitors -j` output."""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, list):
        return None
    for m in data:
        if isinstance(m, dict) and m.get("focused") and m.get("name"):
            return str(m["name"])
    return None


def choose_monitor(setting: str, available: Iterable[str],
                   focused: Optional[str]) -> Tuple[Optional[str], str]:
    """Pick a connector. Returns (connector or None = compositor's choice, reason).

    setting: "auto" / "" (follow focus) or a pinned connector name. A pinned name that
    is not connected falls back to auto. Focus is only honored if GTK also sees it.
    """
    avail = list(available)
    s = (setting or "").strip()
    if s and s.lower() != AUTO:
        if s in avail:
            return s, "pinned"
        log.warning("monitor %s not found (have %s); falling back to auto", s, avail)
        reason = "pinned-missing"
    else:
        reason = "auto"
    if focused and focused in avail:
        return focused, reason + ":focused"
    return None, reason + ":compositor"


# ---- hotplug handling (pure; GTK objects are duck-typed so tests can use fakes) --------

HOTPLUG_SETTLE_MS = 400


def _items(model) -> list:
    """Items of a Gio.ListModel (get_n_items/get_item) or of a plain sequence."""
    if hasattr(model, "get_n_items"):
        return [model.get_item(i) for i in range(model.get_n_items())]
    return list(model)


def _valid(mon) -> bool:
    try:
        return bool(mon is not None and mon.is_valid())
    except Exception:  # noqa: BLE001  (a monitor being torn down)
        return False


def valid_connectors(model) -> list:
    """Connector names of the monitors that are currently valid, in list order."""
    out = []
    for m in _items(model):
        if _valid(m):
            c = m.get_connector()
            if c:
                out.append(c)
    return out


def find_monitor(model, connector: Optional[str]):
    """The valid monitor with this connector, looked up now; None if absent or invalid.

    Callers use the result immediately and never keep it: a GdkMonitor goes invalid when
    its output is unplugged, and a held reference would point at a dead wl_output.
    """
    if not connector:
        return None
    for m in _items(model):
        if _valid(m) and m.get_connector() == connector:
            return m
    return None


def repick_action(visible: bool, current: Optional[str], target: Optional[str],
                  current_valid: bool) -> str:
    """What to do once the monitor list has settled after a hotplug.

    "defer": the window is hidden; leave the layer surface alone (show() re-picks).
    "remap": the surface should move (target changed, or its monitor is gone).
    "keep":  it is already on the right, still-connected monitor.
    """
    if not visible:
        return "defer"
    if target != current or not current_valid:
        return "remap"
    return "keep"


class Debouncer:
    """Run `fire` once, `delay_ms` after the last poke() (trailing-edge debounce).

    timeout_add(ms, fn) -> id and source_remove(id) default to GLib's; tests inject fakes.
    """

    def __init__(self, fire, delay_ms: int = HOTPLUG_SETTLE_MS, timeout_add=None,
                 source_remove=None):
        if timeout_add is None or source_remove is None:
            from gi.repository import GLib
            timeout_add = timeout_add or GLib.timeout_add
            source_remove = source_remove or GLib.source_remove
        self._fire = fire
        self._delay = delay_ms
        self._add = timeout_add
        self._remove = source_remove
        self._id = None

    @property
    def pending(self) -> bool:
        return self._id is not None

    def poke(self) -> None:
        self.cancel()
        self._id = self._add(self._delay, self._run)

    def cancel(self) -> None:
        if self._id is not None:
            self._remove(self._id)
            self._id = None

    def _run(self, *_a) -> bool:
        self._id = None
        try:
            self._fire()
        except Exception:  # noqa: BLE001
            log.exception("monitor re-pick failed")
        return False  # GLib.SOURCE_REMOVE
