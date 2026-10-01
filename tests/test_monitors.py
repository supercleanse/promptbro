"""Monitor-choice logic with mocked hyprctl output (no windows)."""
import json
import subprocess

from promptbro import monitors
from promptbro.monitors import choose_monitor, parse_focused

HYPR = json.dumps([{"name": "eDP-1", "focused": False}, {"name": "DP-1", "focused": True}])


def test_parse_focused():
    assert parse_focused(HYPR) == "DP-1"
    assert parse_focused("[]") is None
    assert parse_focused("garbage") is None
    assert parse_focused('{"a": 1}') is None


def test_auto_follows_focus():
    assert choose_monitor("auto", ["eDP-1", "DP-1"], "DP-1")[0] == "DP-1"
    assert choose_monitor("", ["eDP-1", "DP-1"], "eDP-1")[0] == "eDP-1"


def test_auto_without_focus_defers_to_compositor():
    assert choose_monitor("auto", ["eDP-1"], None)[0] is None
    assert choose_monitor("auto", ["eDP-1"], "HDMI-A-1")[0] is None


def test_pinned_wins_over_focus():
    assert choose_monitor("eDP-1", ["eDP-1", "DP-1"], "DP-1") == ("eDP-1", "pinned")


def test_pinned_missing_falls_back_to_auto(caplog):
    name, why = choose_monitor("DP-1", ["eDP-1"], "eDP-1")
    assert name == "eDP-1" and why.startswith("pinned-missing")
    assert "not found" in caplog.text


def test_hyprctl_failure(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("hyprctl")
    monkeypatch.setattr(subprocess, "run", boom)
    assert monitors.focused_from_hyprctl() is None


def test_hyprctl_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, HYPR, ""))
    assert monitors.focused_from_hyprctl() == "DP-1"


def test_default_config_is_auto():
    from promptbro.config import WindowConfig
    assert WindowConfig().monitor == "auto"
