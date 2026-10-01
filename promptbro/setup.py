"""`promptbro setup` and `promptbro uninstall-data`. Stdlib only, idempotent.

setup never runs sudo and never edits Hyprland files: it prints what to do instead.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

from . import doctor, migrate, paths

PYTHON = "/usr/bin/python3"
DEV_REQUIREMENTS = ("pytest==9.1.1",)

DEFAULT_CONFIG = """[window]
monitor = "auto"
width = 720
height = 240
top_gap = 8
opacity = 0.75
click_through = true
hide_from_screen_share = true
font = "Adwaita Sans"
current_pt = 26
other_pt = 17
history_lines = 3
upcoming_lines = 3
animation_ms = 180
max_line_chars = 90
show_legend = true

[asr]
engine = "sherpa-onnx"
model = "{model}"
models_dir = "{models_dir}"
num_threads = 1
device = ""
endpoint_rule2_s = 1.0
hotwords = false
vad_rms = 0.01

[aligner]
tail_tokens = 6
ahead_units = 40
display_coasting = false
line_complete_on_final = false

[log]
level = "info"
"""


def _install_model(name: str, models_dir: str, log=print) -> None:
    from . import models
    if models.verify(name, models_dir):
        log(f"model {name}: already present")
        return
    models.download(name, models_dir, log=log)


def write_default_config(out=print) -> bool:
    cfg = paths.config_file()
    if cfg.exists():
        out(f"config already present at {cfg}")
        return False
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(DEFAULT_CONFIG.format(model=paths.DEFAULT_MODEL,
                                         models_dir=paths.tilde(paths.models_dir())), encoding="utf-8")
    out(f"wrote default config to {cfg}")
    return True


def install_desktop(out=print) -> None:
    root = paths.repo_root()
    data = root / "promptbro" / "data"
    paths.icon_file().parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(data / f"{paths.APP_ID}.svg", paths.icon_file())
    paths.applications_dir().mkdir(parents=True, exist_ok=True)
    tpl = (data / f"{paths.APP_ID}.desktop.in").read_text(encoding="utf-8")
    paths.desktop_file().write_text(tpl.replace("@BINPATH@", str(root / "bin" / "promptbro")),
                                    encoding="utf-8")
    out(f"installed {paths.desktop_file()}")


def link_bin(out=print) -> None:
    link = paths.bin_link()
    target = paths.repo_root() / "bin" / "promptbro"
    if link.is_symlink() or link.exists():
        out(f"{link} already present")
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    out(f"linked {link} -> {target}")


def run_setup(dev: bool = False, desktop: bool = True, print_snippet: bool = True, out=print,
              run=subprocess.run, install_model=_install_model) -> int:
    report = doctor.check()
    ids = {p["id"] for p in report["problems"]}
    if "old-install" in ids:
        out("moving the old Teleprompter install over first")
        if migrate.run(out) != 0:
            return 1
    missing = doctor.missing_packages()
    if missing:
        out("System packages are missing. Run this, then run setup again:")
        out("  sudo pacman -S --needed " + " ".join(missing))
        return 1
    venv = paths.venv_dir()
    if paths.venv_python().exists() and "venv-python-mismatch" in ids:
        out("the system Python changed; rebuilding the venv")
        shutil.rmtree(venv)
    if not paths.venv_python().exists():
        venv.parent.mkdir(parents=True, exist_ok=True)
        out(f"creating the venv at {venv}")
        run([PYTHON, "-m", "venv", "--system-site-packages", str(venv)], check=True)
    py = str(paths.venv_python())
    sha = doctor.requirements_sha()
    try:
        stamp = paths.deps_stamp().read_text().strip()
    except OSError:
        stamp = ""
    if stamp != sha:
        out("installing packages")
        run([py, "-m", "pip", "install", "--quiet", "-r", str(paths.repo_root() / "requirements.txt")],
            check=True)
        paths.deps_stamp().write_text(sha + "\n")
    if dev:
        out("installing dev packages")
        run([py, "-m", "pip", "install", "--quiet", *DEV_REQUIREMENTS], check=True)
    model, mdir = doctor._asr_settings()
    install_model(model, mdir, out)
    write_default_config(out)
    if desktop:
        install_desktop(out)
        link_bin(out)
    if print_snippet:
        from .app import hypr_snippet
        out("")
        out("Add these lines to ~/.config/hypr/hyprland.lua and bindings.lua yourself:")
        out(hypr_snippet(True).rstrip("\n"))
        out("")
    out("Done.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptbro setup")
    ap.add_argument("--dev", action="store_true", help="also install the test dependencies")
    ap.add_argument("--no-desktop", action="store_true", help="skip the desktop entry and PATH link")
    ap.add_argument("--print-hypr-snippet", action="store_true", help="only print the Hyprland snippet")
    a = ap.parse_args(argv)
    if a.print_hypr_snippet:
        from .app import hypr_snippet
        print(hypr_snippet(True), end="")
        return 0
    return run_setup(dev=a.dev, desktop=not a.no_desktop)


# ---- uninstall-data ----------------------------------------------------------------------
def _quit_running(run=subprocess.run) -> None:
    if migrate.name_has_owner(paths.APP_ID):
        run([str(paths.repo_root() / "bin" / "promptbro"), "quit"], capture_output=True, timeout=15)


def uninstall_data(everything: bool = False, yes: bool = False, out=print, ask=input,
                   run=subprocess.run) -> int:
    targets = [paths.data_dir()]
    if everything:
        targets += [paths.config_dir(), paths.state_dir()]
    targets = [t for t in targets if t.exists()]
    if not yes:
        out("This removes:")
        for t in targets + [paths.desktop_file(), paths.icon_file()]:
            out(f"  {t}")
        if ask("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
            out("Nothing removed.")
            return 1
    _quit_running(run)
    for t in targets:
        shutil.rmtree(t)
        out(f"removed {t}")
    for f in (paths.desktop_file(), paths.icon_file()):
        if f.exists() or f.is_symlink():
            f.unlink()
            out(f"removed {f}")
    link = paths.bin_link()
    if link.is_symlink():
        into = os.path.realpath(link).startswith(str(paths.repo_root()) + os.sep)
        if into:
            link.unlink()
            out(f"removed {link}")
    out("Next: omarchy plugin remove supercleanse.promptbro, then delete the PromptBro lines from "
        "~/.config/hypr/bindings.lua and hyprland.lua by hand.")
    return 0


def uninstall_main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptbro uninstall-data")
    ap.add_argument("--all", action="store_true", help="also remove config and state")
    ap.add_argument("--yes", action="store_true", help="do not ask first")
    a = ap.parse_args(argv)
    return uninstall_data(everything=a.all, yes=a.yes)
