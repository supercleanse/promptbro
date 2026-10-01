"""promptbro migrate against a temp HOME tree with fake old dirs. Never touches the real ones."""
import json
import os
from pathlib import Path

import pytest

from promptbro import migrate, paths


@pytest.fixture
def home(tmp_path, monkeypatch):
    for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def old_tree(home):
    """What the Teleprompter left behind."""
    (home / ".config/teleprompter").mkdir(parents=True)
    (home / ".config/teleprompter/config.toml").write_text(
        '[window]\nwidth = 700\n\n[asr]\nmodel = "m"\nmodels_dir = "~/.local/share/teleprompter/models"\n'
        'num_threads = 2\n')
    (home / ".local/share/teleprompter/models/m").mkdir(parents=True)
    (home / ".local/share/teleprompter/models/m/tokens.txt").write_text("t")
    (home / ".local/share/teleprompter/tts/voice").mkdir(parents=True)
    (home / ".local/share/teleprompter/venv/bin").mkdir(parents=True)
    st = home / ".local/state/teleprompter"
    st.mkdir(parents=True)
    script = home / "talk.md"
    script.write_text("hello world\n")
    (st / "state.json").write_text(json.dumps({"path": str(script), "line": 3}))
    (st / "teleprompter.log").write_text("a")
    (st / "teleprompter.log.1").write_text("b")
    apps = home / ".local/share/applications"
    apps.mkdir(parents=True)
    (apps / "com.aldus.Teleprompter.desktop").write_text("[Desktop Entry]\n")
    icons = home / ".local/share/icons/hicolor/scalable/apps"
    icons.mkdir(parents=True)
    (icons / "com.aldus.Teleprompter.svg").write_text("<svg/>")
    (home / ".local/bin").mkdir(parents=True)
    (home / ".local/bin/teleprompter").symlink_to("/old/repo/bin/teleprompter")
    return script


def go(**kw):
    lines = []
    rc = migrate.run(out=lines.append, owner_check=kw.pop("owner_check", lambda n: False))
    return rc, lines


def test_full_migration(home):
    script = old_tree(home)
    rc, lines = go()
    assert rc == 0
    assert (home / ".config/promptbro/config.toml").is_file() and not (home / ".config/teleprompter").exists()
    assert (home / ".local/share/promptbro/models/m/tokens.txt").read_text() == "t"
    assert (home / ".local/share/promptbro/tts/voice").is_dir()
    assert not (home / ".local/share/teleprompter/models").exists()
    assert (home / ".local/share/teleprompter/venv").is_dir()  # left for T15
    sd = home / ".local/state/promptbro"
    assert (sd / "state.json").is_file() and not (home / ".local/state/teleprompter").exists()
    assert sorted(p.name for p in sd.glob("*.log*")) == ["promptbro.log", "promptbro.log.1"]
    assert not (home / ".local/share/applications/com.aldus.Teleprompter.desktop").exists()
    assert not (home / ".local/share/icons/hicolor/scalable/apps/com.aldus.Teleprompter.svg").exists()
    assert not (home / ".local/bin/teleprompter").is_symlink()
    rec = json.loads((sd / "recent.json").read_text())
    assert rec["schema"] == 1 and rec["recent"][0]["path"] == str(script)
    assert (sd / "recent.json").stat().st_mode & 0o777 == 0o600
    assert any("moved" in x for x in lines)


def test_config_rewrite_touches_only_models_dir(home):
    old_tree(home)
    go()
    cfg = home / ".config/promptbro"
    new = (cfg / "config.toml").read_text()
    assert 'models_dir = "~/.local/share/promptbro/models"' in new
    assert "width = 700" in new and "num_threads = 2" in new and 'model = "m"' in new
    baks = list(cfg.glob("config.toml.bak.*"))
    assert len(baks) == 1 and "teleprompter/models" in baks[0].read_text()


def test_config_rewrite_expanded_form(home):
    old_tree(home)
    cfg = home / ".config/teleprompter/config.toml"
    cfg.write_text(f'[asr]\nmodels_dir = "{home}/.local/share/teleprompter/models"\n')
    go()
    assert f'models_dir = "{home}/.local/share/promptbro/models"' in \
        (home / ".config/promptbro/config.toml").read_text()


def test_config_pointing_elsewhere_is_left_alone(home):
    old_tree(home)
    cfg = home / ".config/teleprompter/config.toml"
    cfg.write_text('[asr]\nmodels_dir = "/data/models"\n')
    go()
    assert (home / ".config/promptbro/config.toml").read_text() == '[asr]\nmodels_dir = "/data/models"\n'
    assert not list((home / ".config/promptbro").glob("*.bak.*"))


def test_both_exist_is_skipped_never_merged(home):
    old_tree(home)
    (home / ".config/promptbro").mkdir(parents=True)
    (home / ".config/promptbro/config.toml").write_text("mine = 1\n")
    rc, lines = go()
    assert rc == 0
    assert (home / ".config/promptbro/config.toml").read_text() == "mine = 1\n"
    assert (home / ".config/teleprompter/config.toml").is_file()  # untouched
    assert any(x.startswith("skipped") and ".config/teleprompter" in x for x in lines)
    assert (home / ".local/share/promptbro/models/m").is_dir()  # the other pairs still moved


def test_idempotent(home):
    old_tree(home)
    go()
    snap = sorted(str(p.relative_to(home)) for p in home.rglob("*"))
    rc, lines = go()
    assert rc == 0 and sorted(str(p.relative_to(home)) for p in home.rglob("*")) == snap
    assert not migrate.pending()


def test_nothing_to_migrate(home):
    rc, lines = go()
    assert rc == 0 and lines == ["nothing to migrate"]


def test_refuses_while_old_app_runs(home):
    old_tree(home)
    rc, lines = go(owner_check=lambda n: n == "com.aldus.Teleprompter")
    assert rc == 1 and "quit it first" in lines[0]
    assert (home / ".config/teleprompter").is_dir() and not (home / ".config/promptbro").exists()


def test_foreign_bin_link_is_kept(home):
    old_tree(home)
    link = home / ".local/bin/teleprompter"
    link.unlink()
    link.symlink_to("/somewhere/else/tool")
    go()
    assert link.is_symlink()


def test_recent_not_seeded_when_script_is_gone(home):
    script = old_tree(home)
    script.unlink()
    go()
    assert not (home / ".local/state/promptbro/recent.json").exists()


def test_cli_refuses_under_test_env(home):
    assert os.environ["PROMPTBRO_TEST"] == "1"
    old_tree(home)
    assert migrate.main([]) == 3
    assert (home / ".config/teleprompter").is_dir()


def test_name_has_owner_parses_gdbus():
    class R:
        def __init__(self, rc, out): self.returncode, self.stdout = rc, out
    assert migrate.name_has_owner("x", run=lambda *a, **k: R(0, "(true,)\n")) is True
    assert migrate.name_has_owner("x", run=lambda *a, **k: R(0, "(false,)\n")) is False
    assert migrate.name_has_owner("x", run=lambda *a, **k: R(1, "")) is False
