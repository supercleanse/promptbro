"""Native file chooser for `promptbro pick-file`. Tries, in order: the xdg-desktop-portal
FileChooser (works on any modern Wayland/X11 desktop), then `zenity`, then `kdialog`; gives up
(exit 2) so the caller (the dropdown) can fall back to a plain text field.

Stdlib only. Derived from Spitball's pickfolder.py; the portal options ask for a single file
filtered to scripts. Tests mock every subprocess: no dialog is ever opened by the suite.
"""
from __future__ import annotations

import argparse
import secrets
import shutil
import subprocess
import sys
import urllib.parse

PORTAL_DEST = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
WAIT_TIMEOUT_S = 600.0   # picking a file can take a while

PATTERNS = ("*.md", "*.txt", "*.markdown")


def _which(name: str) -> str | None:
    return shutil.which(name)


def _path_from_uri(uri: str) -> str:
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path)


def _gi():
    """(GLib, Gio) or None when PyGObject is unavailable."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        return GLib, Gio
    except (ImportError, ValueError):
        return None


def _portal_options(GLib, token: str):
    """OpenFile options. The portal sends Request.Response as a UNICAST signal to the caller's
    unique bus name, so the call and the subscription MUST share one connection."""
    scripts = ("Scripts", [(0, p) for p in PATTERNS])
    return GLib.Variant("a{sv}", {
        "handle_token": GLib.Variant("s", token),
        "multiple": GLib.Variant("b", False),
        "filters": GLib.Variant("a(sa(us))", [scripts, ("All files", [(0, "*")])]),
        "current_filter": GLib.Variant("(sa(us))", scripts),
    })


def _try_portal(title: str, timeout: float = WAIT_TIMEOUT_S, bus_address: str | None = None) -> tuple:
    """(ran, path): ran=False means the portal was unusable (try the next picker); ran=True with
    path="" means the user canceled (stop; do not show a second dialog). Call + Response
    subscription share one D-Bus connection, subscribed BEFORE OpenFile."""
    mods = _gi()
    if mods is None:
        return False, ""
    GLib, Gio = mods
    try:
        if bus_address:
            conn = Gio.DBusConnection.new_for_address_sync(
                bus_address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        else:
            conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        unique = conn.get_unique_name()
    except GLib.Error:
        return False, ""
    token = "promptbro_" + secrets.token_hex(8)
    sender = unique.lstrip(":").replace(".", "_")
    expected = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
    loop = GLib.MainLoop()
    state: dict = {"result": None, "subs": [], "handle": expected}

    def on_response(_c, _s, _p, _i, _m, params, *_a):
        code, results = params.unpack()
        if code != 0:
            state["result"] = ""  # 1 = canceled, 2 = ended some other way
        else:
            uris = results.get("uris") or []
            state["result"] = _path_from_uri(uris[0]) if uris else None
        loop.quit()

    def subscribe(path):
        state["subs"].append(conn.signal_subscribe(
            PORTAL_DEST, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, on_response, None))

    try:
        subscribe(expected)
        reply = conn.call_sync(
            PORTAL_DEST, PORTAL_PATH, "org.freedesktop.portal.FileChooser", "OpenFile",
            GLib.Variant.new_tuple(GLib.Variant("s", ""), GLib.Variant("s", title),
                                   _portal_options(GLib, token)),
            GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 10000, None)
        handle = reply.unpack()[0]
        if handle != expected:
            state["handle"] = handle
            subscribe(handle)
        timed_out = []

        def on_timeout():
            timed_out.append(True)
            loop.quit()
            return False
        tid = GLib.timeout_add(int(timeout * 1000), on_timeout)
        if state["result"] is None:
            loop.run()
        if timed_out:
            try:
                conn.call_sync(PORTAL_DEST, state["handle"], "org.freedesktop.portal.Request",
                               "Close", None, None, Gio.DBusCallFlags.NONE, 5000, None)
            except GLib.Error:
                pass
        else:
            GLib.source_remove(tid)
    except GLib.Error:
        return False, ""
    finally:
        for sid in state["subs"]:
            conn.signal_unsubscribe(sid)
        if bus_address:
            try:
                conn.close_sync(None)
            except GLib.Error:
                pass
    if state["result"] is None:
        return False, ""
    return True, state["result"]


def _try_cli(cmd: list, run=subprocess.run) -> tuple:
    try:
        r = run(cmd, capture_output=True, text=True, timeout=WAIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return False, ""
    if r.returncode == 0 and r.stdout.strip():
        out = r.stdout[:-1] if r.stdout.endswith("\n") else r.stdout
        return True, out
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
    ran, path = _try_portal(title)
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
