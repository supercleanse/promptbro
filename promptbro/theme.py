"""Omarchy theme palette (DESIGN.md 7.4).

Reads ~/.local/state/omarchy/current/theme/colors.toml, falls back to the
matte-black values, and exposes CSS @define-color lines plus a watcher.
"""
from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Callable, Dict, Optional

log = logging.getLogger(__name__)

THEME_ROOT = Path(os.path.expanduser("~/.local/state/omarchy/current"))
COLORS_PATH = THEME_ROOT / "theme" / "colors.toml"

FALLBACK: Dict[str, str] = {
    "background": "#121212",
    "foreground": "#bebebe",
    "accent": "#e68e0d",
    "muted": "#333333",
    "light_foreground": "#8a8a8d",
    "red": "#D35F5F",
    "bright_blue": "#f59e0b",
    "mode": "dark",
}

# Window and card stay see-through; the view paints the panel background itself.
TRANSPARENT_CSS = (
    "window.prompter { background: transparent; }\n"
    ".card { background: none; border: none; box-shadow: none; }\n"
)

_warned = False


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning(msg)


def load_palette(path: Optional[os.PathLike] = None) -> Dict[str, str]:
    """Return the palette; missing file or keys fall back to matte-black."""
    p = Path(path) if path is not None else COLORS_PATH
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        _warn_once(f"theme: cannot read {p} ({e}); using matte-black fallbacks")
        return dict(FALLBACK)
    pal = dict(FALLBACK)
    missing = []
    for k in FALLBACK:
        v = data.get(k)
        if isinstance(v, str) and v:
            pal[k] = v
        else:
            missing.append(k)
    if missing:
        _warn_once(f"theme: {p} lacks {', '.join(missing)}; using fallbacks for those")
    return pal


def css_defines(pal: Optional[Dict[str, str]] = None) -> str:
    """@define-color lines named tp_<key> for the GTK CSS provider."""
    pal = pal or load_palette()
    return "".join(f"@define-color tp_{k} {v};\n" for k, v in pal.items() if k != "mode")


def panel_background(pal: Optional[Dict[str, str]], opacity: float) -> str:
    """CSS rgba() of the theme background at the given alpha (alpha only, never text)."""
    pal = pal or load_palette()
    from_hex = pal["background"].lstrip("#")
    r, g, b = (int(from_hex[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {opacity:g})"


def watch(callback: Callable[[], None]):
    """Call callback when the Omarchy theme changes. Returns the Gio.FileMonitor
    (keep a reference), or None if the directory does not exist."""
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    if not THEME_ROOT.is_dir():
        return None
    mon = Gio.File.new_for_path(str(THEME_ROOT)).monitor_directory(Gio.FileMonitorFlags.NONE, None)

    def changed(_m, _f, _o, event):
        if event in (Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.CHANGED,
                     Gio.FileMonitorEvent.MOVED_IN, Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                     Gio.FileMonitorEvent.RENAMED):
            callback()

    mon.connect("changed", changed)
    return mon
