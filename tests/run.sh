#!/usr/bin/env bash
# PromptBro test runner: pytest (offscreen, isolated) + node --test for Model.js.
# Usage: tests/run.sh [pytest args]
# Uses the plugin venv when it exists (it has pytest and the app's dependencies),
# otherwise $PYTHON or python3. Never opens a window on your desktop.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

VENV_PY="${XDG_DATA_HOME:-$HOME/.local/share}/promptbro/venv/bin/python"
PY=${PYTHON:-$([[ -x "$VENV_PY" ]] && echo "$VENV_PY" || echo python3)}

echo "== Python tests (pytest) =="
"$PY" -m pytest -q "$@"
py_status=$?

echo
echo "== Model.js tests (node --test) =="
if command -v node >/dev/null 2>&1; then
  node --test tests/js
  js_status=$?
else
  echo "node not found on PATH; skipping Model.js tests" >&2
  js_status=1
fi

echo
status=0
if [[ $py_status -ne 0 ]]; then echo "Python suite: FAILED"; status=1; else echo "Python suite: passed"; fi
if [[ $js_status -ne 0 ]]; then echo "Model.js suite: FAILED"; status=1; else echo "Model.js suite: passed"; fi
exit $status
