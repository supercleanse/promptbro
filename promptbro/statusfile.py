"""The widget's status file (CONTRACT 4.2): $XDG_RUNTIME_DIR/promptbro/status.json.

Stdlib only. The writer is atomic (a 0600 temp file in the same directory, then os.replace; the
mode is set at creation, never chmod-ed afterward), coalesces change-triggered writes to at most
one per 100 ms, and rewrites the file on a 5 s heartbeat so the widget can tell a crashed app
from a quiet one. The timers are injected (the controller passes GLib's), so this module can be
tested without a main loop.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from . import paths

log = logging.getLogger("promptbro.statusfile")

SCHEMA = 1
MIN_INTERVAL_S = 0.1
HEARTBEAT_MS = 5000
CLIPBOARD_TITLE = "Clipboard text"


def widget_state(ctl) -> str:
    """The contract's `state` for a controller, first match wins."""
    if ctl.script is None:
        return "idle"
    override = ctl._override
    if override == "error" or ctl.worker_state == "failed":
        return "error"
    if override in ("no-mic", "no-audio"):
        return "no-audio"
    if not ctl.visible:
        return "loaded"
    if ctl.paused:
        return "paused"
    if ctl.worker_state in ("starting", "restarting"):
        return "starting"
    return "listening"


def build(ctl, version: str = "") -> dict:
    """The status payload for a controller (without `updated_at`, which the writer stamps)."""
    state = widget_state(ctl)
    path = ctl.path or ""
    if ctl.script is None:
        title = ""
    else:
        title = Path(path).name if path else CLIPBOARD_TITLE
    if state in ("error", "no-audio") and ctl.worker_error:
        message = ctl.worker_error
    else:
        message = ctl.last_toast
    return {
        "schema": SCHEMA,
        "state": state,
        "detail": ctl._override or ctl.pos.state,
        "path": path,
        "title": title,
        "line": int(ctl.pos.line),
        "lines": len(ctl.script.lines) if ctl.script else 0,
        "visible": bool(ctl.visible),
        "message": message or "",
        "opacity": float(ctl.cfg.window.opacity),
        "pid": os.getpid(),
        "version": version,
    }


def offline_payload(version: str = "") -> dict:
    return {"schema": SCHEMA, "state": "offline", "detail": "", "path": "", "title": "", "line": 0,
            "lines": 0, "visible": False, "message": "", "opacity": 0.75, "pid": os.getpid(),
            "version": version}


def write_atomic(path: Path, payload: dict) -> None:
    """0700 directory, 0600 file created that way, replaced atomically."""
    d = path.parent
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    tmp = d / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class StatusWriter:
    """Writes `build()` output whenever it changes (coalesced) and on a heartbeat."""

    def __init__(self, build_fn: Callable[[], dict], path: Path | None = None,
                 add_timeout: Callable | None = None, remove_timeout: Callable | None = None,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic):
        self._build = build_fn
        self.path = path or paths.status_file()
        self._add, self._remove = add_timeout, remove_timeout
        self._clock, self._mono = clock, mono
        self._last_body: dict | None = None
        self._last_write = float("-inf")
        self._pending = 0
        self._beat = 0
        self.writes = 0
        self.closed = False
        # Structural guard: a test run with no runtime-dir override must never touch the real file.
        self.enabled = not (os.environ.get("PROMPTBRO_TEST") and not os.environ.get("PROMPTBRO_RUNTIME_DIR")
                            and path is None)
        if not self.enabled:
            log.warning("PROMPTBRO_TEST without PROMPTBRO_RUNTIME_DIR: status file disabled")

    def start(self) -> None:
        self.flush(force=True)
        if self._add and not self._beat:
            self._beat = self._add(HEARTBEAT_MS, self._heartbeat)

    def _heartbeat(self) -> bool:
        if self.closed:
            self._beat = 0
            return False
        self.flush(force=True)
        return True

    def poke(self) -> None:
        """Something may have changed: write now, or once shortly if one just happened."""
        if self.closed or not self.enabled:
            return
        if self._pending:
            return
        wait = MIN_INTERVAL_S - (self._mono() - self._last_write)
        if wait <= 0 or not self._add:
            self.flush()
        else:
            self._pending = self._add(max(1, int(wait * 1000) + 1), self._trailing)

    def _trailing(self) -> bool:
        self._pending = 0
        self.flush()
        return False

    def flush(self, force: bool = False) -> bool:
        """Write if the body changed (or force). Never raises."""
        if self.closed or not self.enabled:
            return False
        body = self._build()
        if not force and body == self._last_body:
            return False
        return self._write(body)

    def _write(self, body: dict) -> bool:
        payload = dict(body, updated_at=round(self._clock(), 3))
        try:
            write_atomic(self.path, payload)
        except OSError as e:
            log.warning("could not write status file: %s", e)
            return False
        self._last_body = body
        self._last_write = self._mono()
        self.writes += 1
        return True

    def close(self, version: str = "") -> None:
        """Stop the timers and leave `state: offline` behind."""
        if self.closed:
            return
        for sid in (self._pending, self._beat):
            if sid and self._remove:
                self._remove(sid)
        self._pending = self._beat = 0
        if self.enabled:
            self._write(offline_payload(version))
        self.closed = True
