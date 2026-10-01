#!/usr/bin/env bash
# Link this checkout into Omarchy's plugin folder so the shell can load it.
# Safe to run again: if the link already points here, nothing changes. It refuses
# to replace anything else at that path. It does not rescan or restart the shell,
# and it does not touch your bar; it prints the commands to do that yourself.
set -euo pipefail

ID="supercleanse.promptbro"
ROOT=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
PLUGINS="${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins"
LINK="$PLUGINS/$ID"

mkdir -p "$PLUGINS"
if [[ -L "$LINK" ]]; then
  if [[ "$(readlink -f "$LINK")" == "$ROOT" ]]; then
    echo "already linked: $LINK -> $ROOT"
  else
    echo "refusing: $LINK is a link to $(readlink "$LINK"), not to $ROOT" >&2
    echo "remove it yourself if you want this checkout instead." >&2
    exit 1
  fi
elif [[ -e "$LINK" ]]; then
  echo "refusing: $LINK exists and is not a link (an installed copy?). Remove it first:" >&2
  echo "  omarchy plugin remove $ID" >&2
  exit 1
else
  ln -s "$ROOT" "$LINK"
  echo "linked: $LINK -> $ROOT"
fi

cat <<MSG

Next, by hand (nothing here was restarted; the shell finds the plugin the next time it starts):
  promptbro setup --dev        # or Set up PromptBro... from the menu: venv, model, launcher
  omarchy plugin enable $ID    # adds it to your bar
MSG
