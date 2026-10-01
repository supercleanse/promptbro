"""`promptbro migrate`: move a Teleprompter install into the PromptBro locations.

Stdlib only, idempotent, never merges or overwrites. Run it with the old app quit.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import paths


def name_has_owner(name: str, run=None) -> bool:
    """True when `name` is owned on the session bus (gdbus; no D-Bus bindings needed)."""
    import subprocess
    run = run or subprocess.run
    try:
        r = run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                 "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.NameHasOwner", name],
                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "true" in r.stdout


def moves() -> list:
    """(old, new) directory pairs, in order."""
    return [
        (paths.old_config_dir(), paths.config_dir()),
        (paths.old_data_dir() / "models", paths.models_dir()),
        (paths.old_data_dir() / "tts", paths.tts_dir()),
        (paths.old_state_dir(), paths.state_dir()),
    ]


def pending() -> list:
    """The moves that would happen now: old exists and new does not."""
    return [(o, n) for o, n in moves() if o.exists() and not n.exists()]


def _move(old: Path, new: Path) -> None:
    new.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(old, new)
    except OSError:
        import shutil
        shutil.move(str(old), str(new))




def rewrite_models_dir(cfg: Path, now: float | None = None) -> str | None:
    """Back up `cfg`, then repoint models_dir when it is under the old data dir. Returns the new
    value, or None when nothing needed changing. Touches nothing else in the file."""
    if not cfg.is_file():
        return None
    import re
    import shutil
    text = cfg.read_text(encoding="utf-8")
    m = re.search(r'^(\s*models_dir\s*=\s*)(["\'])(.*?)\2', text, re.M)
    if not m:
        return None
    value = m.group(3)
    old_root = paths.old_data_dir()
    expanded = paths.expand(value)
    if expanded != str(old_root) and not expanded.startswith(str(old_root) + os.sep):
        return None
    rest = expanded[len(str(old_root)):]
    new_expanded = str(paths.data_dir()) + rest
    new_value = new_expanded
    if value.startswith("~"):
        new_value = paths.tilde(new_expanded)
    stamp = int(now if now is not None else time.time())
    shutil.copy2(cfg, cfg.with_name(f"{cfg.name}.bak.{stamp}"))
    cfg.write_text(text[:m.start(3)] + new_value + text[m.end(3):], encoding="utf-8")
    return new_value


def _seed_recent() -> bool:
    rf = paths.recent_file()
    sf = paths.state_file()
    if rf.exists() or not sf.is_file():
        return False
    try:
        p = json.loads(sf.read_text(encoding="utf-8")).get("path")
    except (OSError, ValueError, AttributeError):
        return False
    if not p or not Path(p).exists():
        return False
    data = {"schema": 1, "recent": [{"path": str(Path(p).resolve()), "opened_at": int(time.time())}]}
    tmp = rf.with_name(rf.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, rf)
    return True


def run(out=print, owner_check=name_has_owner) -> int:
    """Do the migration. Returns 0 on success (including nothing to do), 1 when refused."""
    if owner_check(paths.OLD_APP_ID):
        out(f"promptbro: the old Teleprompter is running; quit it first: {paths.old_bin_link()} quit")
        return 1
    moved, skipped = [], []
    for old, new in moves():
        if not old.exists():
            continue
        if new.exists():
            skipped.append((old, new))
            continue
        _move(old, new)
        moved.append((old, new))
    # the moved state dir still carries the old log names
    for f in sorted(paths.state_dir().glob(f"{paths.OLD_NAME}.log*")) if paths.state_dir().is_dir() else []:
        target = f.with_name(f.name.replace(f"{paths.OLD_NAME}.log", "promptbro.log", 1))
        if not target.exists():
            f.rename(target)
    new_cfg_value = rewrite_models_dir(paths.config_file())
    seeded = _seed_recent()
    # old launcher pieces
    removed = []
    for f in (paths.old_desktop_file(), paths.old_icon_file()):
        if f.is_file() or f.is_symlink():
            f.unlink()
            removed.append(f)
    link = paths.old_bin_link()
    if link.is_symlink():
        target = Path(os.readlink(link))
        if target.name == paths.OLD_NAME and target.parent.name == "bin":
            link.unlink()
            removed.append(link)
    for old, new in moved:
        out(f"moved   {old} -> {new}")
    for old, new in skipped:
        out(f"skipped {old}: {new} already exists (not merged)")
    if new_cfg_value:
        out(f"config  models_dir now {new_cfg_value} (backup kept beside config.toml)")
    if seeded:
        out(f"seeded  {paths.recent_file()} from the last script")
    for f in removed:
        out(f"removed {f}")
    if not (moved or skipped or new_cfg_value or seeded or removed):
        out("nothing to migrate")
    old_venv = paths.old_data_dir() / "venv"
    if old_venv.exists():
        out(f"left    {old_venv} in place (setup builds a new one; delete it later)")
    return 0


def main(argv=None) -> int:
    if os.environ.get("PROMPTBRO_TEST"):
        print("promptbro: migrate never runs under PROMPTBRO_TEST", file=sys.stderr)
        return 3
    return run()
