"""Native file chooser for `promptbro pick-file`. Tries, in order: the xdg-desktop-portal
FileChooser (works on any modern Wayland/X11 desktop), then `zenity`, then `kdialog`; gives up
(exit 2) so the caller (the dropdown) can fall back to a plain text field.

Stdlib only. Derived from Spitball's pickfolder.py; the portal options ask for a single file
filtered to scripts. Tests mock every subprocess: no dialog is ever opened by the suite.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

PORTAL_DEST = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
WAIT_TIMEOUT_S = 600.0   # picking a file can take a while

PATTERNS = ("*.md", "*.txt", "*.markdown")
_SCRIPTS = "('Scripts', [" + ", ".join(f"(uint32 0, '{p}')" for p in PATTERNS) + "])"
PORTAL_OPTIONS = ("{'multiple': <false>, 'filters': <[" + _SCRIPTS + ", ('All files', [(uint32 0, '*')])]>, "
                  "'current_filter': <" + _SCRIPTS + ">}")

_HANDLE_RE = re.compile(r"objectpath\s+'([^']+)'")
_RESPONSE_CODE_RE = re.compile(r"uint32\s+(\d+)")
_URI_RE = re.compile(r"'uris':\s*<\[\s*'([^']*)'")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _path_from_uri(uri: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path)


def _portal_call_handle(title: str, run=subprocess.run) -> str | None:
    """FileChooser.OpenFile; returns the Request object path (the choice arrives later as a
    signal on that path, see _portal_wait_response)."""
    cmd = ["gdbus", "call", "--session", "--dest", PORTAL_DEST, "--object-path", PORTAL_PATH,
           "--method", "org.freedesktop.portal.FileChooser.OpenFile", "", title, PORTAL_OPTIONS]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    m = _HANDLE_RE.search(r.stdout)
    return m.group(1) if m else None


def _portal_wait_response(handle: str, popen=subprocess.Popen, timeout: float = WAIT_TIMEOUT_S) -> str | None:
    """Watches `gdbus monitor` for Request.Response on `handle`. Returns the chosen absolute
    path, "" if the user canceled, or None if no clean response was seen (caller falls back)."""
    try:
        proc = popen(["gdbus", "monitor", "--session", "--dest", PORTAL_DEST],
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except OSError:
        return None
    deadline = time.monotonic() + timeout
    try:
        for line in proc.stdout:
            if handle not in line or "Response" not in line:
                if time.monotonic() > deadline:
                    return None
                continue
            code_m = _RESPONSE_CODE_RE.search(line)
            code = int(code_m.group(1)) if code_m else 1
            if code != 0:
                return ""  # 1 = canceled, 2 = ended some other way: both read as "no file chosen"
            uri_m = _URI_RE.search(line)
            return _path_from_uri(uri_m.group(1)) if uri_m else None
        return None
    finally:
        if proc.poll() is None:
            proc.terminate()


def _try_portal(title: str, run=subprocess.run, popen=subprocess.Popen) -> tuple:
    """(ran, path): ran=False means the portal was unusable (try the next picker); ran=True with
    path="" means the user canceled (stop; do not show a second dialog)."""
    handle = _portal_call_handle(title, run)
    if not handle:
        return False, ""
    result = _portal_wait_response(handle, popen)
    if result is None:
        return False, ""
    return True, result


def _try_cli(cmd: list, run=subprocess.run) -> tuple:
    try:
        r = run(cmd, capture_output=True, text=True, timeout=WAIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if r.returncode == 0 and r.stdout.strip():
        return True, r.stdout.strip()
    if r.returncode == 1:  # the dialog's own cancel/close exit code
        return True, ""
    return False, ""


def _try_zenity(title: str, run=subprocess.run) -> tuple:
    return _try_cli(["zenity", "--file-selection", f"--title={title}",
                     "--file-filter=Scripts | " + " ".join(PATTERNS)], run)


def _try_kdialog(title: str, run=subprocess.run) -> tuple:
    return _try_cli(["kdialog", "--getopenfilename", "~", " ".join(PATTERNS), f"--title={title}"], run)


def pick_file(title: str = "Choose a script", run=subprocess.run, popen=subprocess.Popen) -> tuple:
    """(exit_code, path): (0, path) chosen, (1, "") canceled, (2, "") no picker available."""
    if _which("gdbus"):
        ran, path = _try_portal(title, run, popen)
        if ran:
            return (0, path) if path else (1, "")
    for tool, fn in (("zenity", _try_zenity), ("kdialog", _try_kdialog)):
        if _which(tool):
            ran, path = fn(title, run)
            if ran:
                return (0, path) if path else (1, "")
    return 2, ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptbro pick-file")
    ap.add_argument("--title", default="Choose a script")
    a = ap.parse_args(argv)
    code, path = pick_file(a.title)
    if code == 0:
        print(path)
    elif code == 2:
        print("promptbro: no file picker available (need xdg-desktop-portal, zenity or kdialog)",
              file=sys.stderr)
    return code
