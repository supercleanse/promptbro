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


# ---- hotplug: settle-then-re-pick with fake monitors (no GTK) -------------------------

from promptbro.monitors import (Debouncer, find_monitor, repick_action,  # noqa: E402
                                valid_connectors)


class FakeMonitor:
    def __init__(self, connector, valid=True):
        self.connector, self.valid = connector, valid

    def get_connector(self):
        return self.connector

    def is_valid(self):
        return self.valid


class FakeListModel:
    """Gio.ListModel shape (get_n_items/get_item)."""

    def __init__(self, items):
        self.items = list(items)

    def get_n_items(self):
        return len(self.items)

    def get_item(self, i):
        return self.items[i]


class Clock:
    """Fake GLib.timeout_add/source_remove driven by advance()."""

    def __init__(self):
        self.now, self.next_id, self.timers = 0, 1, {}

    def timeout_add(self, ms, fn):
        i = self.next_id
        self.next_id += 1
        self.timers[i] = (self.now + ms, fn)
        return i

    def source_remove(self, i):
        del self.timers[i]

    def advance(self, ms):
        self.now += ms
        for i, (due, fn) in sorted(self.timers.items(), key=lambda kv: kv[1][0]):
            if due <= self.now and i in self.timers:
                del self.timers[i]
                fn()


def test_valid_connectors_skips_invalid_and_unnamed():
    model = FakeListModel([FakeMonitor("eDP-1"), FakeMonitor("DP-1", valid=False),
                           FakeMonitor(None), FakeMonitor("DP-2")])
    assert valid_connectors(model) == ["eDP-1", "DP-2"]
    assert valid_connectors([FakeMonitor("eDP-1")]) == ["eDP-1"]


def test_find_monitor_never_returns_an_invalidated_monitor():
    dying = FakeMonitor("DP-1", valid=False)
    model = FakeListModel([FakeMonitor("eDP-1"), dying])
    assert find_monitor(model, "DP-1") is None
    assert find_monitor(model, "eDP-1").get_connector() == "eDP-1"
    assert find_monitor(model, None) is None and find_monitor(model, "") is None


def test_find_monitor_tolerates_a_monitor_that_raises():
    class Broken(FakeMonitor):
        def is_valid(self):
            raise RuntimeError("finalized")
    assert find_monitor([Broken("DP-1")], "DP-1") is None
    assert valid_connectors([Broken("DP-1"), FakeMonitor("eDP-1")]) == ["eDP-1"]


def test_repick_action():
    assert repick_action(False, "DP-1", "eDP-1", False) == "defer"   # hidden: never touch
    assert repick_action(False, "DP-1", "DP-1", True) == "defer"
    assert repick_action(True, "DP-1", "eDP-1", True) == "remap"     # focus moved
    assert repick_action(True, "DP-1", "DP-1", False) == "remap"     # same name, re-created
    assert repick_action(True, "DP-1", "DP-1", True) == "keep"
    assert repick_action(True, None, None, True) == "keep"


def test_debounce_coalesces_a_hotplug_burst_into_one_repick():
    clock, fired = Clock(), []
    d = Debouncer(lambda: fired.append(clock.now), delay_ms=400,
                  timeout_add=clock.timeout_add, source_remove=clock.source_remove)
    # Dock re-enumerates: DP-1 removed, then re-added, then a second wobble.
    for step in (0, 50, 120, 300):
        clock.advance(step - clock.now)
        d.poke()
        assert d.pending
    clock.advance(399)                  # 699 ms: 399 after the last poke
    assert fired == []
    clock.advance(1)
    assert fired == [700] and not d.pending
    assert clock.timers == {}


def test_debounce_cancel_and_errors():
    clock, calls = Clock(), []

    def boom():
        calls.append(1)
        raise RuntimeError("monitor vanished")

    d = Debouncer(boom, delay_ms=400, timeout_add=clock.timeout_add,
                  source_remove=clock.source_remove)
    d.poke()
    d.cancel()
    clock.advance(1000)
    assert calls == [] and not d.pending
    d.poke()
    clock.advance(400)                  # exception is logged, not raised into the main loop
    assert calls == [1] and not d.pending


def test_unplug_replug_scenario_hidden_window_is_left_alone():
    """The 2026-10-01 crash timeline: hidden window, DP-1 drops and returns."""
    edp, dp = FakeMonitor("eDP-1"), FakeMonitor("DP-1")
    model = FakeListModel([edp, dp])
    dp.valid = False                    # unplug: GDK invalidates, then removes
    model.items.remove(dp)
    assert valid_connectors(model) == ["eDP-1"]
    assert repick_action(False, "DP-1", None, find_monitor(model, "DP-1") is not None) == "defer"
    dp2 = FakeMonitor("DP-1")           # replug: a new GdkMonitor object
    model.items.append(dp2)
    target, _ = choose_monitor("auto", valid_connectors(model), "DP-1")
    assert target == "DP-1" and find_monitor(model, target) is dp2


def test_debouncer_uses_glib_by_default():
    from gi.repository import GLib
    loop, fired = GLib.MainLoop(), []
    d = Debouncer(lambda: (fired.append(1), loop.quit()), delay_ms=20)
    d.poke()
    d.poke()
    GLib.timeout_add(2000, loop.quit)
    loop.run()
    assert fired == [1]
