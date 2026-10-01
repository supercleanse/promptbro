"""`promptbro doctor [--json]`: file checks only. No GTK, no network, no heavy imports, so it
finishes well under 100 ms (the dropdown runs it each time it opens)."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tomllib
from pathlib import Path

from . import __version__, migrate, paths

# (what to check, pacman package). "file:" is an absolute path, "glob:" a pattern, "bin:" a PATH name.
SYSTEM_CHECKS = (
    ("file:/usr/lib/libgtk4-layer-shell.so.0", "gtk4-layer-shell"),
    ("file:/usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib", "gtk4-layer-shell"),
    ("file:/usr/lib/girepository-1.0/Gtk-4.0.typelib", "gtk4"),
    ("glob:/usr/lib/python3.*/site-packages/gi", "python-gobject"),
    ("glob:/usr/lib/python3.*/site-packages/cairo", "python-cairo"),
    ("file:/usr/lib/girepository-1.0/Gst-1.0.typelib", "gst-plugins-base"),
    ("file:/usr/lib/gstreamer-1.0/libgstaudioconvert.so", "gst-plugins-base"),
    ("file:/usr/lib/gstreamer-1.0/libgstpipewire.so", "gst-plugin-pipewire"),
    ("bin:wl-paste", "wl-clipboard"),
    ("bin:notify-send", "libnotify"),
    ("bin:gdbus", "glib2"),
)


def requirements_sha() -> str:
    return hashlib.sha256((paths.repo_root() / "requirements.txt").read_bytes()).hexdigest()


def _on_path(name: str) -> bool:
    return any(os.access(os.path.join(d, name), os.X_OK) for d in os.environ.get("PATH", "").split(os.pathsep) if d)


def _site_package(pattern: str) -> bool:
    """`/usr/lib/python3.*/site-packages/<pkg>` without importing glob (it costs 10 ms)."""
    base, _, rest = pattern.partition("python3.*/")
    try:
        names = os.listdir(base)
    except OSError:
        return False
    return any(n.startswith("python3.") and os.path.exists(os.path.join(base, n, rest)) for n in names)


def missing_packages() -> list:
    missing = []
    for check, pkg in SYSTEM_CHECKS:
        kind, _, target = check.partition(":")
        ok = (os.path.exists(target) if kind == "file"
              else _site_package(target) if kind == "glob"
              else _on_path(target))
        if not ok and pkg not in missing:
            missing.append(pkg)
    return missing


def _asr_settings() -> tuple:
    """(model name, models dir) from config.toml, else the defaults."""
    model, mdir = paths.DEFAULT_MODEL, str(paths.models_dir())
    try:
        with open(paths.config_file(), "rb") as f:
            asr = tomllib.load(f).get("asr", {})
        model = asr.get("model") or model
        mdir = asr.get("models_dir") or mdir
    except (OSError, ValueError):
        pass
    return model, paths.expand(mdir)


def model_present(name: str, models_dir: str) -> bool:
    """Cheap presence check (the full sha256 verify happens in setup, via promptbro.models)."""
    d = Path(models_dir) / name

    def has(pattern: str) -> bool:
        return any(p.stat().st_size > 0 for p in d.glob(pattern))

    if name.startswith("vosk"):
        return (d / "am" / "final.mdl").is_file() and (d / "conf" / "model.conf").is_file()
    return all(has(p) for p in ("tokens.txt", "encoder*.onnx", "decoder*.onnx", "joiner*.onnx"))


def _venv_python_mismatch() -> bool:
    cfg = paths.venv_dir() / "pyvenv.cfg"
    try:
        for line in cfg.read_text().splitlines():
            key, _, val = line.partition("=")
            if key.strip() in ("version", "version_info"):
                parts = val.strip().split(".")
                return tuple(int(x) for x in parts[:2]) != tuple(sys.version_info[:2])
    except (OSError, ValueError):
        pass
    return False


def check() -> dict:
    problems = []
    venv_ok = paths.venv_python().exists()
    if not venv_ok:
        problems.append({"id": "venv-missing", "message": "PromptBro isn't set up yet", "fix": "setup"})
    else:
        if _venv_python_mismatch():
            problems.append({"id": "venv-python-mismatch",
                             "message": "The system Python changed; PromptBro needs a fresh setup",
                             "fix": "setup"})
        try:
            stamp = paths.deps_stamp().read_text().strip()
        except OSError:
            stamp = ""
        if stamp != requirements_sha():
            problems.append({"id": "deps-outdated", "message": "PromptBro's packages are out of date",
                             "fix": "setup"})
    model, mdir = _asr_settings()
    present = model_present(model, mdir)
    if not present:
        problems.append({"id": "model-missing", "message": "The speech model isn't downloaded",
                         "fix": "setup"})
    if migrate.pending():
        problems.append({"id": "old-install", "message": "Found an old Teleprompter install to move over",
                         "fix": "migrate"})
    missing = missing_packages()
    if missing:
        problems.append({"id": "system-packages", "message": "Missing system packages: " + " ".join(missing),
                         "fix": "pacman"})
    return {"ok": not problems, "problems": problems, "missing_packages": missing,
            "model": model, "model_present": present, "version": __version__}


def main(argv=None) -> int:
    argv = list(argv or [])
    report = check()
    if "--json" in argv:
        print(json.dumps(report))
        return 0
    else:
        if report["ok"]:
            print("PromptBro is ready.")
        for p in report["problems"]:
            print(f"- {p['message']} (fix: {p['fix']})")
    return 0 if report["ok"] else 1
