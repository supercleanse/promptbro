"""manifest.json against the QML, the Python package and pyproject; `omarchy plugin
validate` and `qmllint` when installed (skipped cleanly otherwise); and repo hygiene for a
public tree (no symlinks, no personal paths in shipped files)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())


def test_required_top_level_fields():
    for key in ("schemaVersion", "id", "name", "version", "author", "license", "description",
                "homepage", "kinds", "entryPoints"):
        assert key in MANIFEST, key
    assert MANIFEST["id"] == "supercleanse.promptbro"
    assert MANIFEST["license"] == "MIT"
    assert MANIFEST["kinds"] == ["bar-widget"]


def test_bar_widget_block():
    bw = MANIFEST["barWidget"]
    for key in ("displayName", "description", "category", "aliases", "allowMultiple",
                "defaultSection"):
        assert key in bw, key
    assert bw["allowMultiple"] is False
    assert (ROOT / MANIFEST["entryPoints"]["barWidget"]).is_file()


def test_id_matches_widget_module_name():
    m = re.search(r'moduleName:\s*"([^"]+)"', (ROOT / "Widget.qml").read_text())
    assert m, "Widget.qml has no moduleName"
    assert m.group(1) == MANIFEST["id"]


def test_widget_setting_keys_are_declared():
    """Every setting the widget reads is in the manifest schema, with a matching default."""
    declared = {s["key"]: s.get("defaultValue") for s in MANIFEST["barWidget"]["schema"]}
    used = set(re.findall(r'setting\("([A-Za-z]+)"', (ROOT / "Widget.qml").read_text()))
    assert used <= set(declared), used - set(declared)
    assert declared["showProgress"] is True


def test_version_matches_package_and_pyproject():
    from promptbro import __version__
    pyproject = (ROOT / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    assert m
    assert MANIFEST["version"] == __version__ == m.group(1)


def test_homepage_and_repo_names_agree():
    assert MANIFEST["homepage"] == "https://github.com/supercleanse/promptbro"
    assert "supercleanse/promptbro" in (ROOT / "README.md").read_text()


def test_license_is_mit():
    text = (ROOT / "LICENSE").read_text()
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Blair Williams" in text


def test_omarchy_plugin_validate_passes():
    if not shutil.which("omarchy"):
        pytest.skip("omarchy CLI not installed")
    r = subprocess.run(["omarchy", "plugin", "validate", str(ROOT)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"


def test_no_symlinks_in_the_tree():
    """`omarchy plugin validate` rejects symlinks. (render.sh removes its staging links on exit.)"""
    links = [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
             if p.is_symlink() and ".git" not in p.relative_to(ROOT).parts]
    assert links == []


def _qmllint():
    return shutil.which("qmllint") or next(
        (p for p in ("/usr/lib/qt6/bin/qmllint", "/usr/lib/qt6/bin/qmllint6") if Path(p).exists()),
        None)


def test_qmllint_has_no_syntax_errors():
    """Report-only for unresolved qs.* imports (they resolve only inside Quickshell)."""
    lint = _qmllint()
    if not lint:
        pytest.skip("qmllint not installed")
    noise = ("Warnings occurred while importing", "Failed to import", "was not found. Did you add",
             "Are your import paths set up properly")
    for qml in ("Widget.qml", "MenuContent.qml", "Bridge.qml"):
        r = subprocess.run([lint, str(ROOT / qml)], capture_output=True, text=True, timeout=60)
        text = r.stdout + r.stderr
        errors = [ln for ln in text.splitlines()
                  if ("Error" in ln or "error:" in ln) and not any(n in ln for n in noise)]
        assert errors == [], f"{qml}: {errors}"


def _tracked():
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    return [ROOT / n for n in r.stdout.splitlines() if (ROOT / n).is_file()]


def test_shipped_files_have_no_personal_paths():
    """Files that ship (everything outside docs/ and tests/) never hard-code a home directory."""
    bad = []
    for p in _tracked():
        rel = p.relative_to(ROOT)
        if rel.parts[0] in ("docs", "tests") or p.suffix in (".png", ".gz", ".wav"):
            continue
        text = p.read_text(errors="ignore")
        if re.search(r"/home/[a-z]|/Users/[A-Za-z]", text):
            bad.append(str(rel))
    assert bad == []


def test_no_build_artifacts_are_tracked():
    names = [str(p.relative_to(ROOT)) for p in _tracked()]
    assert not [n for n in names if ".egg-info" in n or n.startswith(("build/", "proto/"))]
