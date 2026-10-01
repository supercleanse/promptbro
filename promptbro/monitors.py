"""Monitor choice logic (pure; no GTK). See DESIGN.md section 7.1."""
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
