# PromptBro contract

The interfaces the bar widget (QML) and the app (Python) share, and the ones scripts can rely on.
Everything here is versioned by a `schema` field where it is a file. Paths follow the XDG
variables when they are set (`$XDG_CONFIG_HOME`, `$XDG_DATA_HOME`, `$XDG_STATE_HOME`,
`$XDG_RUNTIME_DIR`).

## Files

| Path | Writer | Reader |
|:--|:--|:--|
| `$XDG_RUNTIME_DIR/promptbro/status.json` | the running app | the bar widget |
| `$XDG_STATE_HOME/promptbro/recent.json` | the app | the bar widget |
| `$XDG_CONFIG_HOME/promptbro/config.toml` | you, `promptbro setup` | the app |
| `$XDG_STATE_HOME/promptbro/promptbro.log` | the app | you |

### status.json

Written atomically (a temporary file created with mode 0600 in the same 0700 directory, then
renamed). Written when any field except `updated_at` changes (at most one write per 100 ms),
every 5 seconds as a heartbeat, and once on quit with `"state": "offline"`.

```json
{
  "schema": 1,
  "state": "listening",
  "detail": "tracking",
  "path": "~/Documents/talk.md",
  "title": "talk.md",
  "line": 11,
  "lines": 48,
  "visible": true,
  "message": "",
  "opacity": 0.75,
  "pid": 41237,
  "version": "0.2.0",
  "updated_at": 1790000000.4
}
```

| Field | Meaning |
|:--|:--|
| `state` | `idle` (running, no script), `loaded` (script loaded, panel hidden), `starting` (recognizer loading), `listening`, `paused` (visible but paused), `no-audio` (no microphone or no audio arriving), `error` (recognizer failed), `offline` (written on quit) |
| `detail` | the tracker state (`tracking`, `holding`, `adlib`, `paused`) or the override name |
| `path` | absolute path of the script, or `""` |
| `title` | basename of `path`, or `Clipboard text` for a pasted script |
| `line` | 0-based current display line; show `line + 1` |
| `lines` | number of display lines in the script |
| `visible` | whether the panel is showing |
| `message` | short human text (the error or the last notice), else `""` |
| `opacity` | panel background opacity, 0.3 to 1.0 |
| `pid` | the app's process id |
| `version` | PromptBro version |
| `updated_at` | Unix time of the write |

A reader should treat the file as `offline` when it is missing, unparsable, has
`"state": "offline"`, or its `updated_at` is more than 15 seconds old (a crashed app).

### recent.json

```json
{"schema": 1, "recent": [{"path": "/abs/talk.md", "opened_at": 1790000000}]}
```

Most recent first, at most 8, deduplicated by resolved path, written atomically with mode 0600
on every successful open. Paths that no longer exist are dropped on each write. Pasted text is
not recorded.

## Commands

`promptbro <verb>`. The control verbs never start the app; the launching verbs may.

| Verb | Starts the app? | Notes |
|:--|:--|:--|
| `open <file>`, `paste` | yes | a failed first launch (missing file, empty clipboard) notifies, prints the reason, and quits without leaving an instance |
| `show`, `toggle` | yes | with no instance, loads the most recent existing script, or starts hidden if there is none |
| `status` | no | prints the status JSON; with no instance prints `{"running": false, "state": "offline"}` |
| `hide`, `pause`, `resume`, `next`, `prev`, `restart`, `goto <n>`, `reload-theme`, `quit`, `opacity <0.3-1.0>` | no | with no instance prints `promptbro: not running` |

The stdlib-only commands run without the virtual environment, on the system Python:
`doctor [--json]`, `setup [--dev] [--no-desktop] [--print-hypr-snippet]`, `pick-file`,
`migrate`, `uninstall-data [--all] [--yes]`.

### Exit codes

| Code | Meaning |
|:--|:--|
| 0 | success (`status` and `doctor --json` always exit 0 when they print) |
| 1 | control verb with no running instance; `doctor` without `--json` when something is wrong; setup failed |
| 2 | bad arguments or an unknown command |
| 3 | not set up yet (no virtual environment); run `promptbro setup` |

`pick-file` prints the chosen path and exits 0, exits 1 if you canceled, and exits 2 if no file
chooser (portal, `zenity`, `kdialog`) is available.

## doctor --json

File checks only, no network, no GTK import, about 50 ms.

```json
{"ok": false,
 "problems": [{"id": "venv-missing", "message": "PromptBro isn't set up yet", "fix": "setup"}],
 "missing_packages": [],
 "model": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
 "model_present": true, "version": "0.2.0"}
```

Problem ids: `venv-missing`, `venv-python-mismatch` (system Python upgraded; setup rebuilds the
environment), `deps-outdated` (`requirements.txt` changed), `model-missing`, `old-install`
(an earlier "Teleprompter" install to migrate), `system-packages` (pacman packages missing;
`missing_packages` lists them). `fix` is `setup`, `migrate` or `pacman`.

## GActions

The running app exports these on the session bus at `/supercleanse/PromptBro` (application id
`supercleanse.PromptBro`): `next`, `prev`, `restart`, `pause` (toggles), `resume`, `show`,
`hide`, `toggle`, `quit`, `reload-theme`, and `opacity` (parameter type `d`). They exist only
while the app runs, so activating one never starts an instance. Each activation is logged as
`action <name> (dbus)`.

```bash
gdbus call --session --dest supercleanse.PromptBro --object-path /supercleanse/PromptBro \
  --method org.gtk.Actions.Activate next "[]" "{}"
```

## Layer shell

The prompter's layer-shell namespace is `promptbro`, which is what Hyprland layer rules match
(`^promptbro$`).
