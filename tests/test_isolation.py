"""The test harness can never reach the author's running prompter, his mic, or his desktop."""
import os
import subprocess
import sys
from pathlib import Path

from promptbro.app import APP_ID, describe_sender

ROOT = Path(__file__).resolve().parent.parent


def test_environment_is_isolated():
    assert os.environ["PROMPTBRO_TEST"] == "1"
    assert "WAYLAND_DISPLAY" not in os.environ and "DISPLAY" not in os.environ
    assert os.environ["GDK_BACKEND"] == "broadway"
    bus = os.environ["DBUS_SESSION_BUS_ADDRESS"]
    assert bus and f"/run/user/{os.getuid()}/bus" not in bus
    assert not os.environ["XDG_STATE_HOME"].startswith(str(Path.home() / ".local" / "state"))


def test_real_app_id_is_refused():
    for verb in ("hide", "pause", "status", "quit"):
        r = subprocess.run([sys.executable, "-m", "promptbro", verb], cwd=ROOT,
                           capture_output=True, text=True, timeout=20)
        assert r.returncode == 3, (verb, r.stderr)
        assert APP_ID in r.stderr and "refusing" in r.stderr


def test_worker_refuses_microphone():
    r = subprocess.run([sys.executable, "-m", "promptbro.asr_worker"], cwd=ROOT,
                       capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    assert r.returncode == 3 and "never open the microphone" in r.stderr


def test_describe_sender_names_ancestors():
    s = describe_sender()
    assert s.startswith(f"pid {os.getpid()}: ")
    assert " < " in s  # at least this process and its parent
