"""bin/promptbro: routing, the missing-venv exit, symlink resolution. Fake venv in a temp dir."""
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin" / "promptbro"


def run(args, tmp_path, bin_path=BIN, **env_extra):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"),
           "PROMPTBRO_TEST": "1", **env_extra}
    return subprocess.run([str(bin_path), *args], capture_output=True, text=True, env=env, timeout=30)


def fake_venv(tmp_path):
    py = tmp_path / "data" / "promptbro" / "venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.write_text('#!/bin/sh\necho "PRELOAD=$LD_PRELOAD"\necho "PYPATH=$PYTHONPATH"\necho "ARGS=$*"\n')
    py.chmod(0o755)


def test_missing_venv_exits_3_without_notifying(tmp_path):
    r = run(["toggle"], tmp_path)
    assert r.returncode == 3
    assert "not set up yet" in r.stderr and "promptbro setup" in r.stderr


def test_lite_commands_need_no_venv(tmp_path):
    r = run(["doctor", "--json"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "venv-missing" in [p["id"] for p in json.loads(r.stdout)["problems"]]


def test_app_commands_exec_the_venv_python(tmp_path):
    fake_venv(tmp_path)
    r = run(["open", "x.md"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "PRELOAD=/usr/lib/libgtk4-layer-shell.so" in r.stdout
    assert f"PYPATH={ROOT}" in r.stdout
    assert "ARGS=-P -m promptbro open x.md" in r.stdout


def test_symlink_resolves_root(tmp_path):
    fake_venv(tmp_path)
    link = tmp_path / "link" / "promptbro"
    link.parent.mkdir()
    link.symlink_to(BIN)
    r = run(["next"], tmp_path, bin_path=link)
    assert r.returncode == 0 and f"PYPATH={ROOT}" in r.stdout
    assert run(["pick-file", "--help"], tmp_path, bin_path=link).returncode == 0


def test_xdg_data_home_is_honored(tmp_path):
    r = run(["next"], tmp_path, XDG_DATA_HOME=str(tmp_path / "elsewhere"))
    assert r.returncode == 3


def test_cwd_package_cannot_shadow_the_app(tmp_path):
    """-P keeps the working directory off sys.path, so a hostile ./promptbro/ never runs."""
    import sys
    py = tmp_path / "data" / "promptbro" / "venv" / "bin" / "python3"
    py.parent.mkdir(parents=True)
    py.symlink_to(sys.executable)
    evil = tmp_path / "evil"
    (evil / "promptbro").mkdir(parents=True)
    (evil / "promptbro" / "__main__.py").write_text('print("SHADOWED")\n')
    (evil / "promptbro" / "__init__.py").write_text("")
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_DATA_HOME": str(tmp_path / "data"),
           "PROMPTBRO_TEST": "1"}
    r = subprocess.run([str(BIN), "--version"], capture_output=True, text=True, env=env,
                       cwd=evil, timeout=30)
    assert "SHADOWED" not in r.stdout
    assert r.stdout.startswith("promptbro "), (r.stdout, r.stderr)
