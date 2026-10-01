#!/usr/bin/env bash
# Remove the symlink that dev-install.sh made. Touches nothing else: not the
# venv, models, config, the bar layout, or Hyprland. Safe to run again.
set -euo pipefail

ID="supercleanse.promptbro"
PLUGINS="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
LINK="$PLUGINS/$ID"

if [[ -L "$LINK" ]]; then
  rm "$LINK"
  echo "removed link $LINK"
elif [[ -e "$LINK" ]]; then
  echo "refusing: $LINK is not a symlink (use: omarchy plugin remove $ID)" >&2
  exit 1
else
  echo "nothing to remove: $LINK does not exist"
fi
echo "If the plugin was enabled in your bar, disable it too: omarchy plugin disable $ID"
