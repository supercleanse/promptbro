"""recent.json (CONTRACT 3.4): the last scripts opened, newest first. Stdlib only.

{"schema": 1, "recent": [{"path": "/abs/x.md", "opened_at": 1790000000}]}. Max 8, deduplicated
by resolved path, written atomically with mode 0600; paths that no longer exist are dropped on
each write. Pasted text is never added.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import paths

MAX_RECENT = 8
SCHEMA = 1


def _resolve(p) -> str:
    try:
        return str(Path(p).expanduser().resolve())
    except (OSError, RuntimeError):
        return str(p)


def load(file: Path | None = None) -> list[dict]:
    """Entries as stored (garbage-tolerant), most recent first, deduplicated, capped."""
    f = file or paths.recent_file()
    try:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        items = data.get("recent", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for it in items:
        p = it.get("path") if isinstance(it, dict) else None
        if not isinstance(p, str) or not p or p in seen:
            continue
        seen.add(p)
        t = it.get("opened_at")
        out.append({"path": p, "opened_at": t if isinstance(t, (int, float)) else 0})
        if len(out) >= MAX_RECENT:
            break
    return out


def _write(entries: list[dict], file: Path) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(f".{file.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"schema": SCHEMA, "recent": entries}, f)
            f.write("\n")
        os.replace(tmp, file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(path, now: float | None = None, file: Path | None = None) -> list[dict]:
    """Record a successful open: newest first, deduplicated, missing files dropped, capped."""
    f = file or paths.recent_file()
    p = _resolve(path)
    keep = [e for e in load(f) if _resolve(e["path"]) != p and Path(e["path"]).exists()]
    entries = ([{"path": p, "opened_at": int(now if now is not None else time.time())}] + keep)[:MAX_RECENT]
    _write(entries, f)
    return entries


def remove(path, file: Path | None = None) -> list[dict]:
    """Drop one path (a failed open), plus anything else that no longer exists."""
    f = file or paths.recent_file()
    p = _resolve(path)
    before = load(f)
    entries = [e for e in before if _resolve(e["path"]) != p and Path(e["path"]).exists()]
    if entries != before:
        _write(entries, f)
    return entries


def first_existing(file: Path | None = None) -> str | None:
    for e in load(file):
        if Path(e["path"]).is_file():
            return e["path"]
    return None
