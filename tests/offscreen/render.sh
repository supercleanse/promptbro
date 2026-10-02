#!/usr/bin/env bash
# Renders PromptBro's bar widget (one PNG per UI state) and dropdown (one PNG
# per scenario) offscreen, then runs the Bridge check. Quickshell runs with
# QT_QPA_PLATFORM=offscreen against canned status files, a fake `doctor`, and a
# scratch runtime/state dir, so no window maps, nothing under ~/.config or the
# live shell is touched, and nothing is written outside the output directory.
#
# Usage: tests/offscreen/render.sh --fake-data <out-dir>
#
# --fake-data is required on purpose: every value on screen is canned (see
# harness.qml). There is no mode that renders against the real app.
set -euo pipefail

usage() { echo "usage: $0 --fake-data <out-dir>" >&2; exit 2; }
[[ $# -eq 2 && "$1" == "--fake-data" ]] || usage
OUT=$(realpath -m "$2")
mkdir -p "$OUT"
trap 'rm -rf "$OUT/.harness" "$OUT/.bridge"' EXIT   # stage dirs hold symlinks; plugin validate rejects them

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
SHELL_KIT=${OMARCHY_PATH:-/usr/share/omarchy}/shell
command -v quickshell >/dev/null || { echo "quickshell not found" >&2; exit 1; }
[[ -d "$SHELL_KIT/Commons" && -d "$SHELL_KIT/Ui" ]] || { echo "Omarchy shell kit not found at $SHELL_KIT" >&2; exit 1; }

export QT_QPA_PLATFORM=offscreen
export PROMPTBRO_SHOT_DIR="$OUT"
ERRORS="ERROR|Failed to load|is not a type|Cannot assign|ReferenceError|TypeError|Unable to assign|is not defined|is not a function"

# $1 = entry file, $2 = scratch name. Builds a stage dir that links the kit and
# the plugin's QML side by side (Quickshell resolves qs.* from the config root),
# with a fake bin/promptbro, and fresh runtime/state dirs.
stage() {
  local dir="$OUT/.$2"
  rm -rf "$dir"
  mkdir -p "$dir/runtime" "$dir/state/promptbro" "$dir/bin"
  ln -s "$SHELL_KIT/Commons" "$dir/Commons"
  # Ui is a copy of symlinks with KeyboardPanel swapped for a stub: the real one
  # is a layer-shell window that cannot load offscreen, and would take Widget.qml
  # down with it.
  mkdir -p "$dir/Ui"
  for f in "$SHELL_KIT"/Ui/*; do ln -s "$f" "$dir/Ui/$(basename "$f")"; done
  rm "$dir/Ui/KeyboardPanel.qml"
  cp "$ROOT/tests/offscreen/KeyboardPanelStub.qml" "$dir/Ui/KeyboardPanel.qml"
  for f in Model.js Bridge.qml MenuContent.qml Widget.qml; do ln -s "$ROOT/$f" "$dir/$f"; done
  cp "$ROOT/tests/offscreen/fake_promptbro" "$dir/bin/promptbro"
  cp "$ROOT/tests/offscreen/$1" "$dir/shell.qml"
  echo "$dir"
}

run() {  # $1 = stage dir, $2 = log
  export PROMPTBRO_RUNTIME_DIR="$1/runtime" XDG_STATE_HOME="$1/state"
  set +e
  timeout 60 quickshell -p "$1/shell.qml" >"$2" 2>&1
  local rc=$?
  set -e
  sed -i 's/\x1b\[[0-9;]*m//g' "$2"
  if grep -qE "$ERRORS" "$2"; then
    echo "render: QML errors (see $2):" >&2
    grep -E "$ERRORS" "$2" >&2
    exit 1
  fi
  return 0
}

H=$(stage harness.qml harness)
run "$H" "$OUT/harness.log"
count=$(ls "$OUT"/widget-*.png "$OUT"/dropdown-*.png 2>/dev/null | wc -l)
echo "render: $count PNG(s) in $OUT"
[[ $count -ge 19 ]] || { echo "render: expected 19 PNGs" >&2; exit 1; }

B=$(stage bridge_check.qml bridge)
run "$B" "$OUT/bridge.log"
grep -q "BRIDGE_CHECK PASS" "$OUT/bridge.log" || { echo "render: bridge check failed:" >&2; grep BRIDGE_CHECK "$OUT/bridge.log" >&2 || tail -5 "$OUT/bridge.log" >&2; exit 1; }
echo "render: bridge check passed"
