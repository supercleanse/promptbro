"""T11: opacity verb and action, persistence in state.json, and the live panel alpha."""
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
SKIP_MD = ROOT / "tests" / "data" / "skip.md"

needs_app = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                               or shutil.which("dbus-run-session") is None,
                               reason="needs gtk4-broadwayd and dbus-run-session")


@needs_app
def test_verb_persists_and_wins_over_config(tmp_path):
    from test_app_cli import _session
    run = tmp_path / "run"
    cfgdir = tmp_path / "cfg" / "promptbro"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config.toml").write_text("[window]\nopacity = 0.9\n")
    script = (
        "send --demo & P=$!\n"
        "sleep 3\n"
        "echo S0 $(send status)\n"
        "echo F0 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "send opacity 0.5; echo RC_A=$?; sleep 0.6\n"
        "echo F1 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "send opacity 0.1; echo RC_B=$?; sleep 0.6\n"       # clamps to 0.3
        "echo F2 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "send opacity abc 2>&1; echo RC_C=$?\n"
        "send opacity 0.55; sleep 0.6\n"
        "send quit; wait $P\n"
        "send --demo & P=$!\n"                                # restart: state.json beats config.toml
        "sleep 3\n"
        "echo F3 $(cat \"$PROMPTBRO_RUNTIME_DIR/status.json\")\n"
        "send quit; wait $P\n"
    )
    r = _session(tmp_path, script, 0, {"PROMPTBRO_RUNTIME_DIR": str(run)})
    got = {}
    for ln in r.stdout.splitlines():
        tag, _, rest = ln.partition(" ")
        if tag in ("F0", "F1", "F2", "F3"):
            got[tag] = json.loads(rest)
    assert set(got) == {"F0", "F1", "F2", "F3"}, (r.stdout, r.stderr)
    assert got["F0"]["opacity"] == 0.9            # config.toml is the default
    assert got["F1"]["opacity"] == 0.5
    assert got["F2"]["opacity"] == 0.3            # clamped
    assert "RC_A=0" in r.stdout and "RC_B=0" in r.stdout
    assert "RC_C=0" not in r.stdout               # a non-number is an error
    assert got["F3"]["opacity"] == 0.55           # persisted, and wins over config.toml's 0.9
    st = json.loads((tmp_path / "state" / "promptbro" / "state.json").read_text())
    assert st["opacity"] == 0.55


def _render(out, extra_env):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        out.unlink(missing_ok=True)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}", **extra_env)
        env.pop("WAYLAND_DISPLAY", None)
        subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro",
                        "--app-id-suffix", "opact", "--render-png", str(out), "--size", "720x240",
                        "--open", str(SKIP_MD)], cwd=ROOT, env=env, check=True, timeout=60)
    finally:
        stop_display(srv, disp)
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(str(out)).add_alpha(False, 0, 0, 0)
    return pb.get_pixels(), pb.get_rowstride(), pb.get_height()


@needs_app
def test_action_changes_rendered_alpha(tmp_path):
    """Same render before and after the opacity action: background alpha changes, text stays solid."""
    state = {"XDG_STATE_HOME": str(tmp_path / "state")}
    d1, rs, h = _render(tmp_path / "before.png",
                        dict(state, PROMPTBRO_RENDER_OPACITY="1.0"))
    d5, _, _ = _render(tmp_path / "after.png",
                       dict(state, PROMPTBRO_RENDER_OPACITY="1.0", PROMPTBRO_RENDER_SET_OPACITY="0.5"))
    a = lambda d: d[(h // 2) * rs + 6 * 4 + 3]
    assert a(d1) >= 252
    assert abs(a(d5) - 0.5 * 255) <= 3, a(d5)
    solid1 = sum(1 for i in range(3, len(d1), 4) if d1[i] >= 252)
    solid5 = sum(1 for i in range(3, len(d5), 4) if d5[i] >= 252)
    assert 0 < solid5 < solid1                    # only the text stays opaque after the action
    assert json.loads((tmp_path / "state" / "promptbro" / "state.json").read_text())["opacity"] == 0.5
