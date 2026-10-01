"""promptbro doctor: file checks against a temp HOME. Never touches the real install."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    from promptbro import doctor
    monkeypatch.setattr(doctor, "missing_packages", lambda: [])
    return tmp_path


def make_venv(home, version=None, stamp=True):
    from promptbro import doctor, paths
    py = paths.venv_python()
    py.parent.mkdir(parents=True)
    py.write_text("#!/bin/sh\n")
    v = version or f"{sys.version_info.major}.{sys.version_info.minor}.1"
    (paths.venv_dir() / "pyvenv.cfg").write_text(f"home = /usr/bin\nversion = {v}\n")
    if stamp:
        paths.deps_stamp().write_text(doctor.requirements_sha() + "\n")


def make_model(home, name=None):
    from promptbro import paths
    d = paths.models_dir() / (name or paths.DEFAULT_MODEL)
    d.mkdir(parents=True)
    for f in ("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx"):
        (d / f).write_text("x")


def ids(report):
    return [p["id"] for p in report["problems"]]


def test_fresh_machine(home):
    from promptbro import doctor
    r = doctor.check()
    assert r["ok"] is False
    assert ids(r) == ["venv-missing", "model-missing"]
    assert r["problems"][0]["fix"] == "setup" and r["model_present"] is False
    assert r["version"]


def test_all_good(home):
    from promptbro import doctor
    make_venv(home)
    make_model(home)
    r = doctor.check()
    assert r == {"ok": True, "problems": [], "missing_packages": [], "model": r["model"],
                 "model_present": True, "version": r["version"]}


def test_python_mismatch(home):
    from promptbro import doctor
    make_venv(home, version="3.9.1")
    make_model(home)
    assert ids(doctor.check()) == ["venv-python-mismatch"]


def test_deps_outdated_when_stamp_differs_or_missing(home):
    from promptbro import doctor, paths
    make_venv(home)
    make_model(home)
    paths.deps_stamp().write_text("0" * 64)
    assert ids(doctor.check()) == ["deps-outdated"]
    paths.deps_stamp().unlink()
    assert ids(doctor.check()) == ["deps-outdated"]


def test_model_comes_from_config(home):
    from promptbro import doctor, paths
    make_venv(home)
    paths.config_dir().mkdir(parents=True)
    paths.config_file().write_text('[asr]\nmodel = "other-model"\nmodels_dir = "~/m"\n')
    d = home / "m" / "other-model"
    d.mkdir(parents=True)
    for f in ("tokens.txt", "encoder.onnx", "decoder.onnx", "joiner.onnx"):
        (d / f).write_text("x")
    r = doctor.check()
    assert r["model"] == "other-model" and r["model_present"] is True and r["ok"] is True


def test_old_install_detected(home):
    from promptbro import doctor, paths
    make_venv(home)
    make_model(home)
    paths.old_config_dir().mkdir(parents=True)
    r = doctor.check()
    assert ids(r) == ["old-install"] and r["problems"][0]["fix"] == "migrate"


def test_old_dir_does_not_flag_when_new_exists(home):
    from promptbro import doctor, paths
    make_venv(home)
    make_model(home)
    paths.old_config_dir().mkdir(parents=True)
    paths.config_dir().mkdir(parents=True)
    assert doctor.check()["ok"] is True


def test_missing_system_packages_map_to_pacman_names(home, monkeypatch):
    from promptbro import doctor
    monkeypatch.undo()  # drop the missing_packages stub from the fixture
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(doctor, "SYSTEM_CHECKS", (("file:/nonexistent/a", "gtk4"),
                                                  ("bin:no-such-tool-xyz", "wl-clipboard"),
                                                  ("glob:/nonexistent/python3.*/gi", "python-gobject"),
                                                  ("file:/nonexistent/b", "gtk4")))
    assert doctor.missing_packages() == ["gtk4", "wl-clipboard", "python-gobject"]


def test_every_pacman_row_of_the_plan_is_present():
    from promptbro import doctor
    pkgs = {p for _, p in doctor.SYSTEM_CHECKS}
    assert pkgs == {"gtk4-layer-shell", "gtk4", "python-gobject", "python-cairo", "gst-plugins-base",
                    "gst-plugin-pipewire", "wl-clipboard", "libnotify", "glib2"}


def test_json_cli_is_valid_and_fast(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": os.environ["PATH"]}
    best = 99.0
    for _ in range(3):
        t = time.perf_counter()
        r = subprocess.run(["/usr/bin/python3", "-I", str(ROOT / "bin" / "promptbro-lite"), "doctor", "--json"],
                           capture_output=True, text=True, env=env, timeout=10)
        best = min(best, time.perf_counter() - t)
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert set(data) == {"ok", "problems", "missing_packages", "model", "model_present", "version"}
    assert "venv-missing" in [p["id"] for p in data["problems"]]
    assert best < 0.25, best  # the plan's target is 100 ms; this leaves room for a loaded machine


def test_plain_output_and_exit_code(home, capsys):
    from promptbro import doctor
    assert doctor.main([]) == 1
    assert "fix: setup" in capsys.readouterr().out
