"""Microphone selection (stdlib only): never open a Bluetooth headset mic by default.

Opening a Bluetooth source flips the headset from A2DP (music) to HFP/HSP (call mode), which
cuts whatever is playing. ``audio.device = "auto"`` therefore uses the system default source
unless that is Bluetooth, and otherwise the best non-Bluetooth one (USB/external before the
built-in mic). Matching uses stable properties (device.bus, the ``bluez_`` node prefix), never
a full serial-bearing node name. A configured string may be an exact node name, a glob, or a
substring (e.g. ``Studio_Display``); a Bluetooth source is opened only when explicitly pinned.
"""
from __future__ import annotations

import fnmatch
import json
import logging
import subprocess

log = logging.getLogger(__name__)

AUTO = "auto"


def _pactl(*args: str) -> str:
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5, check=True).stdout


def read_sources(runner=_pactl) -> tuple[list[dict], str]:
    """(sources, default source name) from pactl; ([], "") when pactl is unavailable."""
    try:
        sources = json.loads(runner("-f", "json", "list", "sources"))
        default = runner("get-default-source").strip()
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("pactl unavailable (%s): using the system default source", e)
        return [], ""
    return [s for s in sources if isinstance(s, dict) and s.get("name")], default


def _props(s: dict) -> dict:
    p = s.get("properties")
    return p if isinstance(p, dict) else {}


def is_bluetooth(s: dict) -> bool:
    return str(s["name"]).startswith("bluez_") or str(_props(s).get("device.bus", "")) == "bluetooth"


def is_monitor(s: dict) -> bool:
    return str(s["name"]).endswith(".monitor") or str(_props(s).get("device.class", "")) == "monitor"


def _is_usb(s: dict) -> bool:
    return str(_props(s).get("device.bus", "")) == "usb" or str(s["name"]).startswith("alsa_input.usb-")


def _match_pinned(pin: str, sources: list[dict]) -> dict | None:
    real = [s for s in sources if not is_monitor(s)]
    for s in real:
        if s["name"] == pin:
            return s
    if any(c in pin for c in "*?["):
        hits = [s for s in real if fnmatch.fnmatchcase(s["name"], pin)]
    else:
        hits = [s for s in real if pin in s["name"]]
    hits.sort(key=lambda s: (is_bluetooth(s), s["name"]))
    return hits[0] if hits else None


def choose(configured: str, sources: list[dict], default: str) -> tuple[str | None, str]:
    """Pick the capture node. Returns (node name or None, reason). None with sources present
    means no acceptable (non-Bluetooth) microphone exists; None with no sources means pactl
    gave nothing and the caller falls back to the system default."""
    pin = (configured or "").strip()
    note = ""
    if pin and pin.lower() != AUTO:
        hit = _match_pinned(pin, sources)
        if hit:
            return hit["name"], f"pinned {pin!r}"
        note = f"pinned device {pin!r} not present; "
    cands = [s for s in sources if not is_monitor(s) and not is_bluetooth(s)]
    by_name = {s["name"]: s for s in cands}
    if default in by_name:
        return default, note + "default source"
    if default and not any(s["name"] == default for s in sources):
        pass  # unknown default: rank everything below
    cands.sort(key=lambda s: (not _is_usb(s), s["name"]))
    if cands:
        why = "default source is Bluetooth" if default.startswith("bluez_") else "default source unusable"
        return cands[0]["name"], f"{note}{why}; using {'USB/external' if _is_usb(cands[0]) else 'built-in'} mic"
    return None, note + "no non-Bluetooth microphone available"


def select(configured: str, runner=_pactl) -> tuple[str | None, str]:
    sources, default = read_sources(runner)
    if not sources:
        return None, "pactl unavailable"
    return choose(configured, sources, default)
