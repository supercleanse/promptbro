"""PrompterWindow survives monitor hotplug: debounced re-pick, hidden windows untouched,
only valid monitors handed to layer-shell, compositor close survived, no calls after destroy.

Runs tests/window_hotplug_driver.py under a private Broadway display with a fake layer-shell
and fake monitors. A real output unplug needs a Wayland compositor; that is the manual T10
check documented in docs/BUILD_LOG.md."""
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
pytestmark = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                                or shutil.which("dbus-run-session") is None,
                                reason="needs gtk4-broadwayd and dbus-run-session")


@pytest.fixture(scope="module")
def result():
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
                   PYTHONPATH=str(ROOT))
        env.pop("WAYLAND_DISPLAY", None)
        p = subprocess.run(["dbus-run-session", "--", sys.executable,
                            str(ROOT / "tests" / "window_hotplug_driver.py")],
                           cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    finally:
        stop_display(srv, disp)
    assert p.returncode == 0, p.stderr[-2000:]
    return json.loads(p.stdout)


def test_initial_pick(result):
    assert result["initial"] == [["DP-1", True]]


def test_nothing_happens_inside_items_changed(result):
    assert result["in_signal"] == []
    assert result["hidden_during_burst"] == [] and result["visible_during_burst"] == []


def test_hidden_window_is_left_alone_until_show(result):
    assert result["hidden_pending"] is True
    assert result["hidden_after_settle"] == []
    assert result["show"] == [["DP-1", True]] and result["visible_after_show"] is True


def test_visible_window_remaps_once_after_settle(result):
    assert result["visible_unplugged"] == [[None, None]]       # compositor's choice
    assert result["visible_after_unplug"] is True
    assert result["visible_replug_focus_edp"] == [["eDP-1", True]]
    assert result["keep"] == []


def test_only_valid_monitors_reach_layer_shell(result):
    every = [c for k, v in result.items() if isinstance(v, list) for c in v]
    assert all(valid in (True, None) for _name, valid in every)


def test_compositor_close_is_survived(result):
    assert result["closed_callback"] == 1 and result["visible_after_close"] is False
    assert result["reshow_after_close"] == [["DP-1", True]]
    assert result["visible_after_reshow"] is True


def test_destroyed_window_is_disconnected(result):
    assert result["after_destroy"] == []
    assert result["window_finalized"] is True
    assert result["handlers_after_destroy"] == 0
