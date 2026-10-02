"""GTK dmabuf-feedback hotplug crash workaround (promptbro/gtkcompat.py). No windows."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from promptbro import gtkcompat
from promptbro.gtkcompat import DMABUF_GLOBAL, ENV, apply_dmabuf_workaround, dmabuf_feedback_bug

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("version,buggy", [
    ((4, 14, 0), True), ((4, 20, 3), True), ((4, 22, 4), True),   # 4.22.4 is the crash build
    ((4, 22, 5), False), ((4, 22, 9), False),
    ((4, 23, 0), True), ((4, 23, 2), True), ((4, 23, 3), False),
    ((4, 24, 0), False), ((4, 24, 1), False), ((5, 0, 0), False),
    (None, True),                                                   # unknown: be safe
])
def test_affected_versions(version, buggy):
    assert dmabuf_feedback_bug(version) is buggy


def test_applies_on_affected_gtk():
    env = {}
    assert apply_dmabuf_workaround(env, (4, 22, 4)) is True
    assert env[ENV] == DMABUF_GLOBAL


def test_skips_on_fixed_gtk():
    env = {}
    assert apply_dmabuf_workaround(env, (4, 22, 5)) is False
    assert ENV not in env


def test_keeps_existing_entries_and_is_idempotent():
    env = {ENV: "wp_color_manager_v1, xdg_activation_v1"}
    assert apply_dmabuf_workaround(env, (4, 22, 4))
    assert env[ENV] == f"wp_color_manager_v1:xdg_activation_v1:{DMABUF_GLOBAL}"
    before = env[ENV]
    assert apply_dmabuf_workaround(env, (4, 22, 4))
    assert env[ENV] == before


def test_user_disable_is_respected_on_fixed_gtk():
    env = {ENV: DMABUF_GLOBAL}
    assert apply_dmabuf_workaround(env, (4, 24, 0)) is True
    assert env[ENV] == DMABUF_GLOBAL


def test_unknown_version_applies(monkeypatch):
    monkeypatch.setattr(gtkcompat, "gtk_runtime_version", lambda: None)
    env = {}
    assert apply_dmabuf_workaround(env)
    assert env[ENV] == DMABUF_GLOBAL


def test_runtime_version_matches_gi():
    v = gtkcompat.gtk_runtime_version()
    if v is None:
        pytest.skip("libgtk-4.so.1 not loadable")
    out = subprocess.run([sys.executable, "-c",
                          "import gi; gi.require_version('Gtk','4.0');"
                          "from gi.repository import Gtk;"
                          "print(Gtk.get_major_version(), Gtk.get_minor_version(),"
                          " Gtk.get_micro_version())"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        pytest.skip("PyGObject/Gtk unavailable: " + out.stderr[-200:])
    assert tuple(int(x) for x in out.stdout.split()) == v


def test_entry_point_sets_env_before_gtk_loads():
    """`python -m promptbro` must disable the global before any Gtk import opens a display."""
    code = ("import os, sys; import promptbro.__main__;"
            "print(os.environ.get('GDK_WAYLAND_DISABLE', ''));"
            "print('gi.repository.Gtk' in sys.modules)")
    env = {k: v for k, v in os.environ.items() if k != ENV}
    env["PYTHONPATH"] = str(ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60, env=env, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    disabled, gtk_loaded = out.stdout.split("\n")[:2]
    assert gtk_loaded == "False"
    expected = dmabuf_feedback_bug(gtkcompat.gtk_runtime_version())
    assert (DMABUF_GLOBAL in disabled.split(":")) is expected
