"""promptbro setup / uninstall-data with pip, venv creation and the model download mocked."""
from pathlib import Path

import pytest

from promptbro import doctor, paths, setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(doctor, "missing_packages", lambda: [])
    return tmp_path


class FakeRun:
    """Records commands; creating a venv drops a python3 stub the way `python -m venv` would."""
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append([str(c) for c in cmd])
        if "venv" in cmd:
            venv = Path(cmd[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python3").write_text("#!/bin/sh\n")
            (venv / "pyvenv.cfg").write_text(f"version = {doctor.sys.version_info.major}."
                                             f"{doctor.sys.version_info.minor}.0\n")
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    def pips(self):
        return [c for c in self.calls if "pip" in c]


def run_setup(**kw):
    fr = FakeRun()
    models = []
    lines = []
    rc = setup.run_setup(out=lines.append, run=fr,
                         install_model=lambda n, d, log: models.append((n, d)), **kw)
    return rc, fr, models, lines


def test_first_setup_builds_everything(home):
    rc, fr, models, lines = run_setup()
    assert rc == 0 and lines[-1] == "Done."
    assert fr.calls[0][:5] == ["/usr/bin/python3", "-m", "venv", "--system-site-packages", str(paths.venv_dir())]
    assert fr.pips()[0][-2:] == ["-r", str(paths.repo_root() / "requirements.txt")]
    assert paths.deps_stamp().read_text().strip() == doctor.requirements_sha()
    assert models == [(paths.DEFAULT_MODEL, str(paths.models_dir()))]
    cfg = paths.config_file().read_text()
    assert "show_legend = true" in cfg and f'models_dir = "~/.local/share/promptbro/models"' in cfg
    assert 'model = "' + paths.DEFAULT_MODEL + '"' in cfg
    desk = paths.desktop_file().read_text()
    assert "Name=PromptBro" in desk and f"Exec={paths.repo_root()}/bin/promptbro show" in desk
    assert paths.icon_file().is_file()
    assert paths.bin_link().is_symlink() and paths.bin_link().resolve() == paths.repo_root() / "bin" / "promptbro"
    assert any("bindings" in x or "SUPER + SHIFT + T" in x for x in lines)  # snippet printed, not applied
    assert not any("sudo" in " ".join(c) for c in fr.calls)


def test_second_run_is_idempotent(home):
    run_setup()
    cfg_before = paths.config_file().read_text()
    rc, fr, models, lines = run_setup()
    assert rc == 0
    assert fr.calls == []  # venv exists, stamp matches: no venv, no pip
    assert paths.config_file().read_text() == cfg_before
    assert any("already present" in x for x in lines)


def test_dev_installs_pytest(home):
    rc, fr, _, _ = run_setup(dev=True)
    assert any("pytest==9.1.1" in c for c in fr.pips())


def test_no_desktop_skips_launcher(home):
    run_setup(desktop=False)
    assert not paths.desktop_file().exists() and not paths.bin_link().exists()


def test_existing_bin_path_is_not_replaced(home):
    paths.bin_link().parent.mkdir(parents=True)
    paths.bin_link().write_text("mine")
    run_setup()
    assert paths.bin_link().read_text() == "mine"


def test_missing_system_packages_print_pacman_and_stop(home, monkeypatch):
    monkeypatch.setattr(doctor, "missing_packages", lambda: ["gtk4-layer-shell", "wl-clipboard"])
    rc, fr, models, lines = run_setup()
    assert rc == 1
    assert "  sudo pacman -S --needed gtk4-layer-shell wl-clipboard" in lines
    assert fr.calls == [] and models == []  # printed, never run


def test_python_mismatch_rebuilds_venv(home):
    run_setup()
    (paths.venv_dir() / "pyvenv.cfg").write_text("version = 3.9.0\n")
    marker = paths.venv_dir() / "stale"
    marker.write_text("x")
    rc, fr, _, _ = run_setup()
    assert rc == 0 and not marker.exists()
    assert any("venv" in c for c in fr.calls)


def test_old_install_is_migrated_first(home, monkeypatch):
    (home / ".config/teleprompter").mkdir(parents=True)
    called = []
    monkeypatch.setattr(setup.migrate, "run", lambda out=print: called.append(1) or 0)
    run_setup()
    assert called == [1]


def test_existing_config_is_kept(home):
    paths.config_dir().mkdir(parents=True)
    paths.config_file().write_text("[window]\nwidth = 1\n")
    run_setup()
    assert paths.config_file().read_text() == "[window]\nwidth = 1\n"


def test_default_config_is_valid_for_the_loader(home):
    from promptbro import config
    setup.write_default_config(lambda *_: None)
    cfg = config.load()
    assert cfg.asr.model == paths.DEFAULT_MODEL and cfg.window.show_legend is True


def test_model_download_uses_models_module(home, monkeypatch):
    from promptbro import models
    got = []
    monkeypatch.setattr(models, "verify", lambda n, d: False)
    monkeypatch.setattr(models, "download", lambda n, d, log=print: got.append((n, d)))
    setup._install_model("m", "/d", lambda *_: None)
    assert got == [("m", "/d")]
    monkeypatch.setattr(models, "verify", lambda n, d: True)
    setup._install_model("m", "/d", lambda *_: None)
    assert len(got) == 1


# ---- uninstall-data -------------------------------------------------------------------
def populate(home):
    run_setup()
    paths.state_dir().mkdir(parents=True, exist_ok=True)
    (paths.state_dir() / "state.json").write_text("{}")


def uninstall(monkeypatch, **kw):
    monkeypatch.setattr(setup.migrate, "name_has_owner", lambda n, run=None: False)
    lines = []
    rc = setup.uninstall_data(out=lines.append, **kw)
    return rc, lines


def test_uninstall_asks_first_and_declines(home, monkeypatch):
    populate(home)
    rc, _ = uninstall(monkeypatch, ask=lambda p: "n")
    assert rc == 1 and paths.data_dir().exists()


def test_uninstall_data_keeps_config_and_state(home, monkeypatch):
    populate(home)
    rc, _ = uninstall(monkeypatch, yes=True)
    assert rc == 0
    assert not paths.data_dir().exists() and not paths.desktop_file().exists() and not paths.icon_file().exists()
    assert not paths.bin_link().exists()
    assert paths.config_file().exists() and paths.state_dir().exists()


def test_uninstall_all_removes_config_and_state(home, monkeypatch):
    populate(home)
    uninstall(monkeypatch, yes=True, everything=True)
    assert not paths.config_dir().exists() and not paths.state_dir().exists()


def test_uninstall_leaves_foreign_bin_link(home, monkeypatch):
    populate(home)
    paths.bin_link().unlink()
    paths.bin_link().symlink_to("/usr/bin/true")
    uninstall(monkeypatch, yes=True)
    assert paths.bin_link().is_symlink()


def test_uninstall_quits_a_running_instance(home, monkeypatch):
    populate(home)
    monkeypatch.setattr(setup.migrate, "name_has_owner", lambda n, run=None: True)
    fr = FakeRun()
    setup.uninstall_data(yes=True, out=lambda *_: None, run=fr)
    assert fr.calls and fr.calls[0][-1] == "quit"


# ---- Package C: desktop Exec quoting, repair, stale notice -------------------------------
def _parse_exec(line):
    import shlex
    val = line.split("=", 1)[1]
    val = val.replace("\\\\", "\\")  # string-level unescape
    # Exec-level: only quoted args with backslash escapes; %% -> %
    out, cur, q, i, started = [], "", False, 0, False
    while i < len(val):
        c = val[i]
        if q and c == "\\" and i + 1 < len(val):
            cur += val[i + 1]
            i += 2
            continue
        if c == '"':
            q = not q
            started = True
        elif c == " " and not q:
            if started or cur:
                out.append(cur)
            cur, started = "", False
        else:
            cur += c
        i += 1
    if started or cur:
        out.append(cur)
    return [a.replace("%%", "%") for a in out]


def test_desktop_exec_quotes_awkward_paths(home, monkeypatch):
    root = Path("/tmp/My Projects/Pr$o%mpt")
    monkeypatch.setattr(paths, "repo_root", lambda: root)
    real = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(setup.paths, "repo_root", lambda: real)
    # use the real data dir for icon/template but a fake binary path
    tpl = (real / "promptbro" / "data" / f"{paths.APP_ID}.desktop.in").read_text()
    line = [l for l in tpl.replace("@BINPATH@", setup._exec_quote(str(root / "bin" / "promptbro"))).splitlines()
            if l.startswith("Exec=")][0]
    assert _parse_exec(line) == [str(root / "bin" / "promptbro"), "show"]


def test_exec_quote_handles_quote_backtick_backslash(home):
    p = '/tmp/a "b" `c` \\d/promptbro'
    assert _parse_exec("Exec=" + setup._exec_quote(p) + " show") == [p, "show"]
    assert setup._exec_quote("/plain/path") == "/plain/path"


def test_install_model_repair(home, monkeypatch):
    from promptbro import models
    got = []
    monkeypatch.setattr(models, "stale_tmp_dirs", lambda d: [])
    monkeypatch.setattr(models, "download", lambda n, d, log=print, force=False: got.append(force))
    monkeypatch.setattr(models, "has_manifest", lambda n, d: False)
    setup._install_model("m", "/d", lambda *_: None, repair=True)
    assert got == [True]  # no manifest -> downloaded again
    monkeypatch.setattr(models, "has_manifest", lambda n, d: True)
    monkeypatch.setattr(models, "verify", lambda n, d, deep=False: deep)
    setup._install_model("m", "/d", lambda *_: None, repair=True)
    assert got == [True]  # deep check passes -> untouched
    monkeypatch.setattr(models, "verify", lambda n, d, deep=False: False)
    setup._install_model("m", "/d", lambda *_: None, repair=True)
    assert got == [True, True]


def test_stale_tmp_notice(home, monkeypatch):
    from promptbro import models
    monkeypatch.setattr(models, "stale_tmp_dirs", lambda d: ["tmpabc"])
    monkeypatch.setattr(models, "verify", lambda n, d, deep=False: True)
    lines = []
    setup._install_model("m", "/d", lines.append)
    assert any("tmpabc" in l for l in lines)
