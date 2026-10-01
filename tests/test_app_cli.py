import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bwutil import free_display, stop_display

ROOT = Path(__file__).resolve().parent.parent
SKIP = ROOT / "tests" / "data" / "skip.md"
pytestmark = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                                or shutil.which("dbus-run-session") is None,
                                reason="needs gtk4-broadwayd and dbus-run-session")


def test_splitter():
    from promptbro.script import load_text
    assert len(load_text(SKIP.read_text()).lines) == 7
    assert [ln.text for ln in load_text("# Hi\n\n- **bold** [a](http://x)").lines] == ["Hi", "bold a"]


def test_hypr_snippet():
    from promptbro.app import hypr_snippet
    assert "no_screen_share = true" in hypr_snippet(True)
    snip = hypr_snippet(True)
    # xkb keysym spelling, as Omarchy's bindings use it; the all-caps names never fired
    for key in ('"SUPER + SHIFT + backslash"', '"SUPER + SHIFT + bracketleft"',
                '"SUPER + SHIFT + bracketright"', '"SUPER + SHIFT + Home"'):
        assert key in snip, key
    for bad in ("BACKSLASH", "BRACKETLEFT", "BRACKETRIGHT", "HOME"):
        assert bad not in snip, bad
    packaged = (ROOT / "promptbro" / "data" / "hyprland-snippet.lua").read_text()
    assert all(k in packaged for k in ("backslash", "bracketleft", "bracketright", "+ Home"))
    assert not any(b in packaged for b in ("BACKSLASH", "BRACKETLEFT", "BRACKETRIGHT", "HOME"))


def test_verbs(tmp_path):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
               XDG_STATE_HOME=str(tmp_path / "state"), XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HOME=str(tmp_path), PYTHONPATH=str(ROOT),
               PROMPTBRO_PASTE_CMD=f"cat {SKIP}")
    env.pop("WAYLAND_DISPLAY", None)
    base = [sys.executable, "-m", "promptbro", "--app-id-suffix", "test"]
    script = (
        "set -e\n"
        "send() { " + " ".join(base) + " \"$@\"; }\n"
        "send --demo & PRIMARY=$!\n"
        "sleep 2\n"
        "send next; send next; send status; send prev; send status; send restart; send status\n"
        "send goto 5; send status; send hide; send status\n"
        f"send open {SKIP}; send status\n"
        "send paste; send status\n"
        "send next; send next\n"
        f"send open {SKIP}; send status\n"
        "T0=$(date +%s.%N); send quit; wait $PRIMARY; T1=$(date +%s.%N)\n"
        "echo QUIT_SECONDS $(python3 -c \"print($T1 - $T0)\")\n"
    )
    try:
        time.sleep(1)
        r = subprocess.run(["dbus-run-session", "--", "bash", "-c", script], cwd=ROOT, env=env,
                           capture_output=True, text=True, timeout=90)
    finally:
        stop_display(srv, disp)
    assert r.returncode == 0, r.stderr
    st = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    assert [s["line"] for s in st[:4]] == [2, 1, 0, 5]
    assert st[4]["visible"] is False
    assert st[5]["path"] == str(SKIP) and st[5]["lines"] == 7
    assert st[6]["path"] is None and st[6]["lines"] == 7
    assert st[7]["path"] == str(SKIP) and st[7]["line"] == 0  # no state saved yet within 1 s
    quit_s = float(r.stdout.split("QUIT_SECONDS")[1])
    assert quit_s < 2
    sf = json.loads((tmp_path / "state" / "promptbro" / "state.json").read_text())
    assert sf["path"] == str(SKIP)
    # the rotating log records every verb with its sender, and the hide -> pause it implies
    logtxt = (tmp_path / "state" / "promptbro" / "promptbro.log").read_text()
    assert "verb hide (remote, pid " in logtxt and " < bash" in logtxt
    assert "window hidden" in logtxt and "paused (window hidden)" in logtxt
    assert "verb quit (remote" in logtxt
    assert "(local, pid " in logtxt  # the first command (the one that started the app) too


# ---------------------------------------------------------------- launch guard (T3)

def _session(tmp_path, script, disp, extra_env=None):
    """Run a bash script inside a private dbus session with a private Broadway display."""
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
               XDG_STATE_HOME=str(tmp_path / "state"), XDG_CONFIG_HOME=str(tmp_path / "cfg"),
               HOME=str(tmp_path), PYTHONPATH=str(ROOT), PROMPTBRO_PASTE_CMD=f"cat {SKIP}")
    env.update(extra_env or {})
    env.pop("WAYLAND_DISPLAY", None)
    pre = ("send() { " + sys.executable + " -m promptbro --app-id-suffix test \"$@\"; }\n"
           "owned() { gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus "
           "--method org.freedesktop.DBus.NameHasOwner supercleanse.PromptBro.test; }\n")
    try:
        time.sleep(1)
        return subprocess.run(["dbus-run-session", "--", "bash", "-c", pre + script], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=90)
    finally:
        stop_display(srv, disp)


def test_control_verbs_without_instance_start_nothing(tmp_path):
    script = (
        "send status; echo RC_STATUS=$?\n"
        "owned\n"
        "for v in next prev pause resume restart hide reload-theme quit; do\n"
        "  send $v 2>&1; echo RC_$v=$?\n"
        "done\n"
        "send goto 3 2>&1; echo RC_goto=$?\n"
        "owned\n"
    )
    r = _session(tmp_path, script, 41)
    out = r.stdout
    assert '{"running": false, "state": "offline"}' in out
    assert "RC_STATUS=0" in out
    for v in ("next", "prev", "pause", "resume", "restart", "hide", "reload-theme", "quit", "goto"):
        assert f"RC_{v}=1" in out, (v, out, r.stderr)
    assert out.count("promptbro: not running") == 9
    assert out.count("(false,)") == 2 and "(true,)" not in out  # the name is never owned


def test_open_missing_file_without_instance_fails_and_leaves_none(tmp_path):
    script = (
        "send open " + str(tmp_path / "missing.md") + " 2>&1; echo RC=$?\n"
        "owned\n"
        "send paste 2>&1 < /dev/null; echo RC2=$?\n"
        "owned\n"
    )
    r = _session(tmp_path, script, 42, {"PROMPTBRO_PASTE_CMD": "true"})  # empty clipboard
    out = r.stdout
    assert "RC=1" in out and "RC2=1" in out, (out, r.stderr)
    assert "missing.md" in out
    assert out.count("(false,)") == 2 and "(true,)" not in out


def _recent_json(tmp_path, paths_):
    d = tmp_path / "state" / "promptbro"
    d.mkdir(parents=True, exist_ok=True)
    (d / "recent.json").write_text(json.dumps(
        {"schema": 1, "recent": [{"path": str(p), "opened_at": 1790000000 - i}
                                 for i, p in enumerate(paths_)]}))


def test_toggle_first_launch_reopens_last_script(tmp_path):
    _recent_json(tmp_path, [SKIP])
    script = (
        "send toggle & PRIMARY=$!\n"
        "sleep 2\n"
        "send status\n"
        "send quit; wait $PRIMARY\n"
    )
    r = _session(tmp_path, script, 43)
    st = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    assert st and st[0]["path"] == str(SKIP) and st[0]["visible"] is True, (r.stdout, r.stderr)


def test_show_first_launch_without_recent_stays_hidden(tmp_path):
    script = (
        "send show & PRIMARY=$!\n"
        "sleep 2\n"
        "send status\n"
        "send quit; wait $PRIMARY\n"
    )
    r = _session(tmp_path, script, 44)
    st = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    assert st and st[0]["visible"] is False and st[0]["path"] is None, (r.stdout, r.stderr)


def test_second_toggle_still_toggles(tmp_path):
    _recent_json(tmp_path, [SKIP])
    script = (
        "send toggle & PRIMARY=$!\n"
        "sleep 2\n"
        "send toggle; send status\n"
        "send quit; wait $PRIMARY\n"
    )
    r = _session(tmp_path, script, 45)
    st = [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")]
    assert st and st[0]["visible"] is False, (r.stdout, r.stderr)
