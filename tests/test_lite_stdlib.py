"""The lite modules must import with the standard library only, under `python3 -I`, and the
package __init__ must stay import-free (the launcher runs them with no venv)."""
import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LITE = ["lite", "paths", "pickfile", "doctor", "setup", "migrate", "config", "models"]

PROBE = """
import sys
sys.path.insert(0, {root!r})
import promptbro.{mod}
bad = sorted({{m.split('.')[0] for m in sys.modules}} - set(sys.stdlib_module_names) - {{'promptbro', '__main__'}})
bad = [m for m in bad if not m.startswith('_')]
print('NONSTDLIB', ','.join(bad))
"""


@pytest.mark.parametrize("mod", LITE)
def test_module_imports_stdlib_only(mod):
    r = subprocess.run(["/usr/bin/python3", "-I", "-c", PROBE.format(root=str(ROOT), mod=mod)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "NONSTDLIB", r.stdout


def test_package_init_has_no_imports():
    tree = ast.parse((ROOT / "promptbro" / "__init__.py").read_text())
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]


def test_lite_commands_run_without_gtk():
    for args in (["doctor", "--json"], ["--help"]):
        r = subprocess.run(["/usr/bin/python3", "-I", str(ROOT / "bin" / "promptbro-lite"), *args],
                           capture_output=True, text=True, timeout=30,
                           env={"HOME": "/nonexistent-home", "PATH": "/usr/bin"})
        assert r.returncode == 0, r.stderr
