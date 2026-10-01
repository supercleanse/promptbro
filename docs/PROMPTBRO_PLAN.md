# PromptBro: plan to turn the Teleprompter into an Omarchy plugin

Status: plan, not started. Written 2026-09-30. This file moves with the repo in T1 and is
committed as `docs/PROMPTBRO_PLAN.md` in T9.

the author's ask: "Convert this into an official Omarchy plugin, as a standalone project. It should have a
menu item icon that opens a simple dropdown similar to Spitball but allows you to select a file
to load into the teleprompter. There should be buttons that go up/down a line, restart it, etc.
I don't want to have to issue a command-line prompt every time. Model how this works after
Spitball. Call it PromptBro." Keep the hotkey legend that was just added to the panel
(`teleprompter/legend.py`, `window.show_legend`, commit `09c101d`).

---

## 1. Decisions made in this plan (the short version)

| Question | Decision | Why |
|:--|:--|:--|
| Repo | `~/AI/Projects/PromptBro`, made by `mv` of the existing repo (whole `.git` moves, so all history stays), then `git mv` for the package rename | No remote exists yet, so a plain move keeps every commit; `git log --follow` works across `git mv` |
| Plugin id | `supercleanse.promptbro` | Same pattern as `supercleanse.spitball` and `supercleanse.expressvpn` |
| GApplication id | `supercleanse.PromptBro` (D-Bus object path `/supercleanse/PromptBro`) | Drops the old namespace, same namespace as the plugin id |
| CLI | `promptbro` (`bin/promptbro`). No `teleprompter` alias in the repo | the author wants clicks, not commands; hotkeys get repointed in the same pass |
| Layer-shell namespace | `promptbro` | Hyprland layer rules are updated in T4 |
| Bridge, app to widget | The app writes `$XDG_RUNTIME_DIR/promptbro/status.json` atomically on every change that matters to the widget, plus a 5 s heartbeat. The widget watches it with Quickshell `FileView` (`watchChanges: true`). No polling, no process per second | This is exactly Spitball's `state.json` pattern, already proven in the author's bar |
| Bridge, widget to app | Transport buttons (prev, next, restart, pause, show, hide, quit, opacity) call `gdbus call ... org.gtk.Actions.Activate` against the running app. Only open, paste and setup go through `bin/promptbro` | `gdbus` is a C binary (fast), and a D-Bus call to a name nobody owns just fails, so it can never start a blank instance |
| `status` launching a blank app | Fixed in `app.py`: non-launching verbs check `NameHasOwner` first and exit without starting anything | The known pitfall |
| File picker | `promptbro pick-file`, a stdlib-only command run on the system `python3 -I`, that asks the xdg-desktop-portal FileChooser via `gdbus` (then `zenity`, then `kdialog`), prints the path, and the widget then runs `promptbro open <path>`. It's a copy of Spitball's `spitball/pickfolder.py`, changed to pick files | Proven on this machine (Spitball's folder picker uses the same portal, backed by `xdg-desktop-portal-gtk`). A QtQuick.Dialogs `FileDialog` would run inside the `omarchy-shell` process, so any dialog bug could take down the bar, notifications and the lock screen, and without a portal platform theme it draws a non-native Qt dialog anyway. Keep dialogs out of the shell |
| Bar icon | Nerd Font Material "script text" family from JetBrainsMono Nerd Font (the bar font): outline when idle/offline, filled when a script is loaded, "play" variant when listening. Codepoints in section 5 | Readable at 13 px; reads as "script"; distinct states without extra glyph families |
| Plugin kinds | `bar-widget` only. No `service` | The prompter starts on demand; nothing needs to run while it's off |
| ASR venv and models | Outside the plugin folder: `~/.local/share/promptbro/{venv,models}`. Created by `promptbro setup` | `omarchy plugin validate` refuses any symlink inside a plugin folder, and a venv is full of them |
| Opus tasks | None | The two hard questions (file chooser from Quickshell, streaming bridge) are settled above; what's left is ordinary Sonnet/Haiku work |

---

## 2. Paths and names

### 2.1 Runtime paths

| What | Old | New |
|:--|:--|:--|
| Repo | `~/AI/Projects/Teleprompter` | `~/AI/Projects/PromptBro` |
| Installed plugin | n/a | `~/.config/omarchy/plugins/supercleanse.promptbro` (dev: a symlink to the repo, the way Spitball is linked) |
| Config | `~/.config/teleprompter/config.toml` | `~/.config/promptbro/config.toml` |
| Venv | `~/.local/share/teleprompter/venv` | `~/.local/share/promptbro/venv` (rebuilt, not moved: a moved venv has stale absolute paths) |
| Models (4.9 GB today) | `~/.local/share/teleprompter/models` | `~/.local/share/promptbro/models` (moved with `mv`, same filesystem, instant) |
| Test TTS voices | `~/.local/share/teleprompter/tts` | `~/.local/share/promptbro/tts` (moved) |
| Persisted state (last script, line) | `~/.local/state/teleprompter/state.json` | `~/.local/state/promptbro/state.json` |
| Recent scripts (new) | n/a | `~/.local/state/promptbro/recent.json` |
| Log | `~/.local/state/teleprompter/teleprompter.log` (+ `.1`-`.3`) | `~/.local/state/promptbro/promptbro.log` (+ `.1`-`.3`) |
| Live status for the widget (new) | n/a | `$XDG_RUNTIME_DIR/promptbro/status.json` (dir mode 0700, file 0600) |
| Desktop entry | `~/.local/share/applications/com.example.Teleprompter.desktop` | `~/.local/share/applications/supercleanse.PromptBro.desktop` |
| Icon | `.../hicolor/scalable/apps/com.example.Teleprompter.svg` | `.../hicolor/scalable/apps/supercleanse.PromptBro.svg` |
| PATH link | `~/.local/bin/teleprompter` | `~/.local/bin/promptbro` (old link removed) |

All path logic lives in one new stdlib-only module, `promptbro/paths.py`, honoring
`XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_RUNTIME_DIR`, plus the test override
`PROMPTBRO_RUNTIME_DIR`. Every other module imports paths from there.

### 2.2 Renames inside the code

| Old | New |
|:--|:--|
| package `teleprompter/` | `promptbro/` |
| `APP_ID = "com.example.Teleprompter"` | `"supercleanse.PromptBro"` (demo: `supercleanse.PromptBro.Demo`) |
| env `TELEPROMPTER_TEST`, `_SENDER`, `_PASTE_CMD`, `_RENDER_LEGEND`, `_RENDER_OPACITY` | `PROMPTBRO_*` |
| `NAMESPACE = "teleprompter"` (window.py) | `"promptbro"` |
| `client-name=teleprompter` (asr_worker.py pipewiresrc) | `client-name=promptbro` |
| logger names `teleprompter.*` | `promptbro.*` |
| User-Agent `teleprompter/0.1` (models.py) | `promptbro/<version>` |
| `data/com.example.Teleprompter.{svg,desktop.in}` | `data/supercleanse.PromptBro.{svg,desktop.in}` (Name=PromptBro) |
| pyproject `name = "teleprompter"`, version 0.1.0 | `name = "promptbro"`, version 0.2.0 (kept equal to `__version__` and `manifest.json`; a test checks) |
| `prog="teleprompter"`, messages `teleprompter: ...` | `promptbro` |

`DESIGN.md`, `BUILD_PLAN.md`, `BUILD_LOG.md` move to `docs/` unchanged except a one-line header:
"Written when this app was called Teleprompter; names and paths below are the old ones." Do not
search-and-replace inside them (it would garble the history they record).

### 2.3 Repo layout after the work

```
PromptBro/                       # the repository IS the plugin (like Spitball)
├── manifest.json  Widget.qml  MenuContent.qml  Bridge.qml  Model.js
├── preview.png  README.md  CONTRACT.md  LICENSE  .gitignore  pyproject.toml  requirements.txt
├── bin/promptbro                # bash dispatcher (section 4.1)
├── bin/promptbro-lite           # python3 -I entry for the stdlib-only commands
├── promptbro/                   # the GTK app (was teleprompter/), plus:
│   ├── paths.py  lite.py  pickfile.py  doctor.py  setup.py  migrate.py  statusfile.py  recent.py
│   └── legend.py  (kept as is)
├── scripts/  (bench, fixtures, eval, make_preview.py, dev-install.sh, dev-uninstall.sh)
├── tests/    (pytest suite + js/ + offscreen/ + run.sh)
└── docs/     (DESIGN.md, BUILD_PLAN.md, BUILD_LOG.md, PROMPTBRO_PLAN.md)
```

Removed from git (still in history): `proto/`, `build/`, `teleprompter.egg-info/`, `install.sh`
(replaced by `promptbro setup`). `.gitignore` gains `build/`, `*.egg-info/`, `.ruff_cache/`,
`tests/offscreen/out/`.

---

## 3. Behavior

### 3.1 Lifecycle

- Nothing runs until the author uses it. The bar icon is always visible (dim when the app is off).
- "Open script…" or a recent file: the widget runs `promptbro open <path>`. If no instance is
  running, that call becomes the instance (the GApplication primary), loads the file, shows the
  panel and starts listening. Launched with `Quickshell.execDetached`, so restarting the shell
  never kills the prompter.
- "Quit PromptBro" stops the ASR worker and exits. The status file is rewritten as offline.
- Hotkeys keep working. `toggle` and `show` with nothing running start the app and load the most
  recent script (top of `recent.json`) if one exists; otherwise they start it hidden and do
  nothing visible. Every other control verb with nothing running exits 1 with
  `promptbro: not running` and starts nothing.
- A failed first-launch `open`/`paste` (missing file, empty clipboard, unreadable file) must not
  leave a blank hidden instance behind: the app sends `notify-send "PromptBro" "<reason>"`,
  drops a missing path from `recent.json`, and quits.

### 3.2 Verbs (unchanged set plus three)

Launching: `open <path>`, `paste`, `show`, `toggle`, (none).
Non-launching: `status`, `hide`, `pause`, `resume`, `next`, `prev`, `restart`, `goto <n>`,
`reload-theme`, `quit`, and new `opacity <0.3-1.0>` (T11).
Stdlib-only (never touch GTK or D-Bus single-instance): `pick-file`, `doctor`, `setup`,
`migrate`, `uninstall-data`.

`status` keeps its current JSON (tests depend on its `state` field being the aligner state).
With nothing running it prints `{"running": false, "state": "offline"}` and exits 0.

### 3.3 GActions

The app registers `Gio.SimpleAction`s on the `Gtk.Application`, each calling the same code as the
verb: `next`, `prev`, `restart`, `pause` (toggles), `resume`, `show`, `hide`, `toggle`, `quit`,
`reload-theme`, and `opacity` (parameter type `d`). GApplication exports them on the session bus
automatically at `/supercleanse/PromptBro`. Each activation is logged as
`action <name> (dbus)`. Example the widget uses:

```
gdbus call --session --dest supercleanse.PromptBro --object-path /supercleanse/PromptBro \
  --method org.gtk.Actions.Activate next "[]" "{}"
gdbus call ... org.gtk.Actions.Activate opacity "[<0.8>]" "{}"
```

### 3.4 Recent scripts

`recent.json`: `{"schema": 1, "recent": [{"path": "/abs/x.md", "opened_at": 1790000000}]}`, most
recent first, max 8, deduplicated by resolved path, written atomically with mode 0600 by the app
on every successful `open` (CLI, hotkey or widget). Paths that no longer exist are dropped on each
write. Pasted text is not added. On migration (T4), seed it from the old `state.json` `path` if
that file exists. The widget reads it with a second `FileView`, so recents show even when the
app is off.

---

## 4. The bridge

### 4.1 `bin/promptbro` (bash)

```
resolve ROOT with readlink -f (so ~/.local/bin/promptbro symlinks work; the current
  bin/teleprompter gets ROOT wrong through a symlink)
case "$1" in
  pick-file|doctor|setup|migrate|uninstall-data)
     exec /usr/bin/python3 -I "$ROOT/bin/promptbro-lite" "$@" ;;
esac
VENV=$(paths: ${XDG_DATA_HOME:-~/.local/share}/promptbro/venv)
if venv python missing: notify-send "PromptBro" "Run setup from the PromptBro menu"; echo to stderr; exit 3
LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so..., PYTHONPATH=$ROOT, exec venv python -m promptbro "$@"
```

`bin/promptbro-lite` mirrors `Spitball-v2/bin/spitball`: `#!/usr/bin/python3 -I`, inserts the repo
root on `sys.path`, calls `promptbro.lite:main`. The lite modules (`lite`, `paths`, `pickfile`,
`doctor`, `setup`, `migrate`, `recent`, plus the already-stdlib `config` and `models`) must import
only the standard library; `promptbro/__init__.py` must stay import-free. A test enforces this.

### 4.2 `status.json` contract (goes into `CONTRACT.md`)

Written by `promptbro/statusfile.py`, atomically (temp file in the same dir created with mode
0600, then `os.replace`; never write first and chmod later, the exact issue a marketplace
reviewer raised against Spitball). Written when any field below except `updated_at` changes
(coalesced to at most one write per 100 ms), every 5 s as a heartbeat, and once on quit with
`"state": "offline"`.

```json
{
  "schema": 1,
  "state": "listening",
  "detail": "tracking",
  "path": "/home/me/talk.md",
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

| field | meaning |
|:--|:--|
| `state` | one of `idle` (running, no script), `loaded` (script loaded, panel hidden), `starting` (worker loading the model), `listening`, `paused` (visible but paused), `no-audio` (controller override `no-audio` or `no-mic`), `error` (override `error` or worker `failed`), `offline` (written on quit) |
| `detail` | the aligner state (`tracking`, `holding`, `adlib`, `paused`) or the override name |
| `line` | 0-based current display line; the widget shows `line + 1` |
| `title` | basename of `path`, or `Clipboard text` for pasted scripts |
| `message` | short human text: the worker error or the last toast, else `""` |

Mapping in Python (`statusfile.widget_state(ctl)`, unit-tested), first match wins: no script →
`idle`; override `error` or worker `failed` → `error`; override `no-mic`/`no-audio` → `no-audio`;
not visible → `loaded`; paused → `paused`; worker `starting` or `restarting` → `starting`; else
`listening`.

The widget treats the file as `offline` when it is missing, unparsable, has `state: offline`, or
its `updated_at` is more than 15 s old (a crashed app). A QML `Timer` ticks every 5 s only to
re-check that age; it never spawns a process.

Test isolation: `paths.runtime_dir()` returns `$PROMPTBRO_RUNTIME_DIR` if set. `tests/conftest.py`
sets it to a temp dir. If `PROMPTBRO_TEST` is set and `PROMPTBRO_RUNTIME_DIR` is not, the
status writer refuses to write (same structural guard as the app id refusal).

### 4.3 `doctor --json`

Runs in under 100 ms, file checks only, no imports of GTK, no network. The widget runs it each
time the dropdown opens (one short process per open is fine; Spitball's settings do the same).

```json
{"ok": false,
 "problems": [{"id": "venv-missing", "message": "PromptBro isn't set up yet", "fix": "setup"}],
 "missing_packages": [],
 "model": "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25",
 "model_present": true, "version": "0.2.0"}
```

Checks, each with a problem id: `venv-missing` (no venv python), `venv-python-mismatch`
(`pyvenv.cfg` version differs from `/usr/bin/python3`, e.g. after an Arch Python upgrade),
`deps-outdated` (`venv/.promptbro-deps` does not equal the sha256 of `requirements.txt`),
`model-missing` (the configured model fails `models.verify`), `old-install` (old teleprompter
dirs exist and new ones don't; fix `migrate`), and system pieces mapped to pacman names:

| File checked | pacman package |
|:--|:--|
| `/usr/lib/libgtk4-layer-shell.so.0`, `/usr/lib/girepository-1.0/Gtk4LayerShell-1.0.typelib` | `gtk4-layer-shell` |
| `/usr/lib/girepository-1.0/Gtk-4.0.typelib` | `gtk4` |
| `gi` under `/usr/lib/python3.*/site-packages/` | `python-gobject` |
| `cairo` under the same site-packages | `python-cairo` |
| `/usr/lib/girepository-1.0/Gst-1.0.typelib`, `/usr/lib/gstreamer-1.0/libgstaudioconvert.so` | `gst-plugins-base` |
| `/usr/lib/gstreamer-1.0/libgstpipewire.so` | `gst-plugin-pipewire` |
| `wl-paste` on PATH | `wl-clipboard` |
| `notify-send` on PATH | `libnotify` |
| `gdbus` on PATH | `glib2` |

All of these are already installed on the author's laptop, so nothing in this plan needs sudo there.
For other users, `setup` prints `sudo pacman -S --needed <missing>` and stops; it never runs sudo.

### 4.4 `pick-file`

`promptbro/pickfile.py`: copy `~/AI/Projects/Spitball-v2/spitball/pickfolder.py` and its tests
(`tests/test_pickfolder.py`, mocked `gdbus`/`zenity`), then change:

- portal options: `{'multiple': <false>, 'filters': <[('Scripts', [(uint32 0, '*.md'), (uint32 0, '*.txt'), (uint32 0, '*.markdown')]), ('All files', [(uint32 0, '*')])]>, 'current_filter': <('Scripts', [(uint32 0, '*.md'), (uint32 0, '*.txt'), (uint32 0, '*.markdown')])>}`; add `'current_folder': <b'/dir/'>` (the folder of the most recent script) only if a unit test of the exact `gdbus` argv passes and the author's manual check in T12 shows the dialog opening there; drop it otherwise.
- zenity: `--file-selection --file-filter='Scripts | *.md *.txt *.markdown'`; kdialog: `--getopenfilename <dir> '*.md *.txt *.markdown'`.
- wait timeout 600 s (picking a file can take a while).
- `promptbro pick-file [--title T]` prints the absolute path; exit 0 chosen, 1 canceled, 2 no picker available.

Do not open a real dialog in any test or during the build. The real dialog is checked once by
the author in T12.

### 4.5 QML side

Three files plus `Model.js`, modeled on Spitball's `Widget.qml`, `settings/SettingsStore.qml`
and `LivePopup.qml`:

- **`Bridge.qml`** (non-visual `Item`): properties `cliPath` (`Qt.resolvedUrl("bin/promptbro")`
  minus `file://`), `runtimeDir`, `stateDir`, `appId: "supercleanse.PromptBro"`; `st`,
  `recent`, `doctor`, `nowMs`, `running`. Two `FileView`s (status.json, recent.json) with
  `watchChanges: true`, `printErrors: false`, `onFileChanged: reload()`, `onLoadFailed` → empty
  value (copy the Spitball block). A 5 s `Timer` sets `nowMs`. Functions: `act(name, param)`
  (execDetached `gdbus` argv from `Model.activateArgs`), `open(path)`, `paste()`,
  `showOrLaunch()` (act `show` if running, else CLI `show`), `pickFile(cb)` (a `Process` that
  collects stdout and exit code, the `_cliRunner` pattern from SettingsStore), `refreshDoctor()`,
  `runSetup()` (`omarchy-launch-floating-terminal-with-presentation` with
  `Model.shellQuote(cliPath) + " setup"`).
- **`MenuContent.qml`**: the dropdown body only. Inputs: `st`, `uiState`, `recent`, `doctor`,
  `pickerFallback`, `foreground`, `fontFamily`. Signals out: `openRequested()`,
  `pasteRequested()`, `recentRequested(string path)`, `actionRequested(string name, var param)`,
  `setupRequested()`, `pathEntered(string path)`. No process spawning inside it, so the offscreen
  harness can render it with canned data.
- **`Widget.qml`**: `Panel { moduleName: "supercleanse.promptbro"; ipcTarget: "supercleanse.promptbro" }`
  (so `omarchy-shell supercleanse.promptbro toggle` opens the dropdown), a `WidgetButton` with the
  glyph and optional `12/48` label, a `KeyboardPanel` hosting `MenuContent`, and one `Bridge`.
  Left or right click toggles the dropdown (Spitball rule: a bar click only opens the menu).
  Reads the bar setting `showProgress` with `setting("showProgress", true)`.

**Layering rule** (from Spitball's CONTRACT.md): the dropdown is a layer-shell surface with
exclusive keyboard focus and sits above normal windows, so close it before anything opens a
normal window. "Open script…": `root.close()`, then `pickFile`; exit 0 → `open(path)` (don't
reopen; the prompter appears); exit 1 → reopen the dropdown; exit 2 → reopen with
`pickerFallback = true`, which shows a `TextField` "Path to a .md or .txt file" plus an Open
button. "Set up…": close, launch the terminal, don't reopen.

---

## 5. Bar widget and dropdown

### 5.1 Icon states (JetBrainsMono Nerd Font; glyph names checked in the installed font)

| UI state | Glyph | Codepoint | Color | Label |
|:--|:--|:--|:--|:--|
| `offline` | md-script_text_outline | U+F0BC3 | dim (`Qt.darker(foreground, 1.5)`) | none |
| `setup` (offline and doctor not ok) | md-cog | U+F0493 | amber `#d9a441` | none |
| `idle` | md-script_text_outline | U+F0BC3 | foreground | none |
| `loaded` (hidden) | md-script_text | U+F0BC2 | foreground | `12/48` |
| `starting` | md-script_text_play | U+F1727 | dim, gentle pulse (Spitball's opacity animation) | `12/48` |
| `listening` | md-script_text_play | U+F1727 | `Color.accent` | `12/48` |
| `paused` | md-script_text_play_outline | U+F1728 | amber `#d9a441` | `12/48` |
| `no-audio` | md-microphone_off | U+F036D | `bar.urgent` | `12/48` |
| `error` | md-alert_circle_outline | U+F05D6 | `bar.urgent` | none |

Tooltip: `PromptBro: talk.md, line 12 of 48, listening` / `PromptBro: not running` /
`PromptBro: <doctor problem message>`. The `setup` state is computed in QML from the last
doctor result; the widget runs doctor once at load and on each dropdown open.

### 5.2 Dropdown, top to bottom (~280 px wide)

1. Header: script title (or "No script loaded"), then `Line 12 of 48 · Listening` (or
   `Not running`, or the `message`).
2. Only when doctor is not ok: **Set up PromptBro…** (U+F0493) with the problem message under it.
   For `venv-missing` say the size: "downloads about 470 MB".
3. **Open script…** (md-folder_open U+F0770).
4. **Paste from clipboard** (md-content_paste U+F0192).
5. "Recent" header and up to 8 rows (basename; full path as tooltip). Hidden when empty.
6. Separator. Transport row of four icon buttons, enabled only when running with a script:
   previous line (md-chevron_up U+F0143), next line (md-chevron_down U+F0140), restart from top
   (md-format_vertical_align_top U+F0623), pause/resume (md-pause U+F03E4 / md-play U+F040A,
   by state). **These leave the dropdown open** so the author can click repeatedly.
7. **Show prompter** / **Hide prompter** (md-eye U+F0208 / md-eye_off U+F0209). Works when off
   too (launches with the last script).
8. Opacity: `PanelSlider` 0.3-1.0, step 0.05, value from `st.opacity`, sends on `released`
   (T11; leave the row out if T11 is cut).
9. Separator. **Quit PromptBro** (md-power U+F0425), enabled when running.

Esc closes. Every row except the transport row closes the dropdown after acting. No monitor
picker: `monitor = "auto"` already follows the focused monitor.

### 5.3 `Model.js` (pure functions, `.pragma library`, tested with `node --test`)

`offlineStatus()`, `parseStatus(text)` (missing/garbage → offline; unknown `state` → offline;
fills missing fields), `isStale(st, nowMs)` (15 s), `uiState(st, nowMs, doctor)`,
`glyphFor(state)`, `colorRoleFor(state)` (`dim|fg|accent|amber|urgent`), `progressLabel(st)`,
`headerTitle(st)`, `headerSubtitle(st, state)`, `tooltipFor(st, state, doctor)`,
`canNavigate(state)`, `parseRecent(text)` (→ `[{path, name, dir}]`, max 8, dedup),
`parseDoctor(text)`, `objectPathFor(appId)` (`.`→`/`, `-`→`_`, leading `/`),
`activateArgs(appId, name, param)` (full `gdbus` argv; a number param becomes `[<0.8>]`),
`shellQuote(s)`.

---

## 6. Setup, migration, install, uninstall

### 6.1 `promptbro migrate` (stdlib, idempotent, never runs under `PROMPTBRO_TEST`)

1. Refuse if the old app is running (`NameHasOwner com.example.Teleprompter` via `gdbus`); print
   "quit it first: <old bin> quit".
2. For each pair below: if old exists and new doesn't, `mv` (same filesystem: instant). If both
   exist, skip and report; never merge or overwrite.
   - `~/.config/teleprompter` → `~/.config/promptbro`
   - `~/.local/share/teleprompter/models` → `~/.local/share/promptbro/models`
   - `~/.local/share/teleprompter/tts` → `~/.local/share/promptbro/tts`
   - `~/.local/state/teleprompter` → `~/.local/state/promptbro`, then rename `teleprompter.log*`
     to `promptbro.log*`
3. In the moved `config.toml`: copy to `config.toml.bak.<epoch>`, then replace only the value of
   `models_dir` when it points under `~/.local/share/teleprompter` (both `~` and expanded forms).
   Touch nothing else.
4. Seed `recent.json` from `state.json`'s `path` if that file exists and recent.json doesn't.
5. Leave `~/.local/share/teleprompter/venv` in place (it is rebuilt, and deleted only in T15 with
   the author's OK). Remove the old desktop entry and icon and the `~/.local/bin/teleprompter` link
   only if that link points at the old repo's `bin/teleprompter`.
6. Print what moved and what was skipped.

### 6.2 `promptbro setup` (stdlib, idempotent; runs in a visible terminal from the dropdown, or by hand)

1. Run `migrate` if doctor reports `old-install`.
2. System check (section 4.3). If anything is missing, print the `sudo pacman -S --needed ...`
   line and stop.
3. Create the venv: `/usr/bin/python3 -m venv --system-site-packages ~/.local/share/promptbro/venv`
   (recreate it if `venv-python-mismatch`).
4. `pip install -r requirements.txt` (exact pins, from what the working venv has today:
   `sherpa-onnx==1.13.8`, `numpy==2.5.3`, `rapidfuzz==3.14.6`, `metaphone==0.6`,
   `num2words==0.5.14`). Write the sha256 of `requirements.txt` to `venv/.promptbro-deps`.
   `--dev` also installs `pytest==9.1.1`.
5. Download the configured model if `models.verify` fails (T10 adds sha256 checks).
6. Write the default `config.toml` if missing (the block from today's `install.sh`, with the new
   `models_dir`, plus `show_legend = true`).
7. Install the desktop entry and icon (skip with `--no-desktop`) and the `~/.local/bin/promptbro`
   link if that path is free.
8. Print the Hyprland snippet (`--print-hypr-snippet`). Never edit Hyprland files.
9. "Done." Exit 0. The terminal wrapper shows Omarchy's done screen.

### 6.3 Dev install (the author's machine)

`scripts/dev-install.sh`: `ln -s ~/AI/Projects/PromptBro ~/.config/omarchy/plugins/supercleanse.promptbro`
(refuse if that path exists and isn't already this link). It does not rescan or restart the
shell; it prints the command for the author. `scripts/dev-uninstall.sh` removes only that link.

### 6.4 Uninstall

`promptbro uninstall-data [--all] [--yes]`: quits a running instance (the `quit` action), removes
`~/.local/share/promptbro` (venv and models; asks first unless `--yes`), the desktop entry, the
icon, and `~/.local/bin/promptbro` if it points into the plugin. `--all` also removes
`~/.config/promptbro` and `~/.local/state/promptbro`. README "Remove" section: run that, then
`omarchy plugin remove supercleanse.promptbro`, then delete the PromptBro lines from
`~/.config/hypr/bindings.lua` and `hyprland.lua` by hand.

### 6.5 Hyprland (T4, done by the agent with backups, per the omarchy skill)

Back up both files first: `cp FILE FILE.bak.$(date +%s)`. Then replace, in place:

`~/.config/hypr/hyprland.lua` lines 31-33 with
```lua
-- PromptBro: fade in/out, and hide from screen shares (and screenshots).
hl.layer_rule({ match = { namespace = "^promptbro$" }, animation = "fade" })
hl.layer_rule({ match = { namespace = "^promptbro$" }, no_screen_share = true })
```

`~/.config/hypr/bindings.lua` lines 44-49 with
```lua
-- PromptBro (Omarchy plugin supercleanse.promptbro; dev checkout ~/AI/Projects/PromptBro)
o.bind("SUPER + SHIFT + T",            "PromptBro toggle",  "~/.config/omarchy/plugins/supercleanse.promptbro/bin/promptbro toggle")
o.bind("SUPER + SHIFT + backslash",    "PromptBro pause",   "~/.config/omarchy/plugins/supercleanse.promptbro/bin/promptbro pause")
o.bind("SUPER + SHIFT + bracketright", "PromptBro next",    "~/.config/omarchy/plugins/supercleanse.promptbro/bin/promptbro next")
o.bind("SUPER + SHIFT + bracketleft",  "PromptBro prev",    "~/.config/omarchy/plugins/supercleanse.promptbro/bin/promptbro prev")
o.bind("SUPER + SHIFT + Home",         "PromptBro restart", "~/.config/omarchy/plugins/supercleanse.promptbro/bin/promptbro restart")
```

Same keys as before, so no `hl.unbind` is needed. The plugin path works for both the dev link and
a later marketplace install. Hyprland reloads on save; then run `hyprctl configerrors` and expect
no output. Update `hypr_snippet()` in `app.py` to print exactly these lines (with the resolved
plugin path), and keep `legend.py` keys in sync (they don't change).

---

## 7. Testing rules

- Python: the existing pytest suite must pass after the rename with the same passed/skipped
  counts as the baseline captured in T0 (about 134 tests today), plus the new tests. Run with
  `~/.local/share/promptbro/venv/bin/python -m pytest` (before T4 the old venv works with
  `PYTHONPATH=.`).
- `tests/conftest.py` keeps every current guard and adds `PROMPTBRO_RUNTIME_DIR` (temp). No test
  may touch the real bus, the real mic, the real status file, the real state dir, or the live
  desktop. GUI tests stay on Broadway, offscreen.
- Lite modules: a test runs each under `/usr/bin/python3 -I` in a subprocess and asserts it
  imports with no third-party modules on the path.
- QML logic: `node --test tests/js` against `Model.js`, loaded the way
  `Spitball-v2/tests/js/model_loader.js` does it (strip `.pragma library`, run in `vm`).
- QML rendering: `tests/offscreen/render.sh --fake-data <out>` copies Spitball's harness approach
  (`QT_QPA_PLATFORM=offscreen`, a scratch root linking `$OMARCHY_PATH/shell/{Commons,Ui}`,
  `timeout 40 quickshell -p`), renders `MenuContent.qml` in a plain `FloatingWindow` with canned
  states (offline+setup, offline with recents, listening, paused, no-audio, picker fallback) and
  grabs one PNG per state. The script fails on any QML error line (same grep as Spitball's).
  A second harness check instantiates `Bridge.qml` with `runtimeDir` pointing at a scratch dir
  holding a canned `status.json`, rewrites the file once, and asserts (via console output) that
  `st.line` changed. Bridge must not spawn anything in this check (doctor disabled by a property).
- `qmllint` is at `/usr/lib/qt6/bin/qmllint` (not on PATH); `tests/test_manifest.py` runs it when
  present and runs `omarchy plugin validate .` when `omarchy` is present (skip otherwise).
- `tests/run.sh`: pytest, then `node --test tests/js`, then (with `--render <dir>`) the offscreen
  harness.
- Never restart or rescan the Omarchy shell, open a real file dialog, or show the real prompter
  without the author's OK. Those happen only in T12.

---

## 8. Build plan

Each task commits in the repo when its acceptance passes (message names the task, e.g.
"PromptBro T5: status file, GActions, recents"). Agents never push, never publish, and never
commit this plan file before T9.

### T0. Preconditions and baseline
- **Model:** haiku. **Depends on:** nothing. **Parallel with:** nothing.
- **Goal:** make sure nobody else is working in the repo and record the test baseline.
- **Steps:** confirm with the orchestrator that the hotkey-legend agent has finished (its commit
  `09c101d` is in; `git status` shows only `teleprompter.egg-info/SOURCES.txt` modified, which T2
  removes). Run the suite with the old venv; save passed/skipped/failed counts to the scratchpad.
  Check (read-only) whether the old app is running:
  `gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus --method org.freedesktop.DBus.NameHasOwner com.example.Teleprompter`.
  Do **not** run `teleprompter status` (it would start a blank instance).
- **Acceptance:** counts recorded; running yes/no reported.

### T1. Move the repo
- **Model:** haiku. **Depends on:** T0. **Needs the author:** yes, a one-line OK that he isn't using the
  prompter right now, because the running app must be quit (its worker restarts from the old path).
- **Goal:** `~/AI/Projects/PromptBro` with full history.
- **Steps:** if running, `~/AI/Projects/Teleprompter/bin/teleprompter quit`. Then
  `mv ~/AI/Projects/Teleprompter ~/AI/Projects/PromptBro`. Tell the author the hotkeys are offline
  until T4.
- **Acceptance:** `git -C ~/AI/Projects/PromptBro log --oneline | wc -l` equals the old count;
  `PROMPTBRO_PLAN.md` is present and untracked.

### T2. Rename package, ids and env vars
- **Model:** haiku. **Depends on:** T1. **Parallel with:** nothing (touches everything).
- **Goal:** everything in section 2.2, plus the housekeeping in 2.3.
- **Files:** `git mv teleprompter promptbro`; every `.py` under `promptbro/`, `tests/`, `scripts/`;
  `bin/teleprompter` → `git mv` to `bin/promptbro` (rewritten in T3; for now just the new module
  name and `readlink -f`); `pyproject.toml`; `promptbro/data/*` renamed; `README.md` (names only,
  full rewrite in T9); `git rm -r --cached build teleprompter.egg-info proto`, delete those dirs;
  `.gitignore`; move the three docs to `docs/` with the header line.
- **Acceptance:** `grep -rniE 'teleprompter|oldns' --include='*.py' --include='*.sh' --include='*.toml' --include='*.in' --include='*.lua' promptbro tests scripts bin pyproject.toml`
  returns nothing (except a deliberate comment in `migrate.py` later). Test counts equal the T0
  baseline (run with the old venv and `PYTHONPATH=.`). `legend.py` and `show_legend` untouched in
  behavior.

### T3. CLI entry: launch guard, paths module, lite dispatcher
- **Model:** sonnet. **Depends on:** T2.
- **Goal:** sections 3.1, 3.2 (minus `opacity`), 4.1, 4.3, 4.4, 6.1, 6.2, 6.4.
- **Files:** `bin/promptbro`, `bin/promptbro-lite`, `promptbro/{paths,lite,pickfile,doctor,setup,migrate}.py`,
  `promptbro/app.py` (NameHasOwner guard before `App().run`, using
  `Gio.bus_get_sync(SESSION).call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", ...)`;
  `toggle`/`show` load `recent[0]` on first launch; failed first-launch open/paste notifies and
  quits), `promptbro/config.py` and `models.py` (defaults from `paths`), `requirements.txt`,
  `install.sh` deleted, `promptbro/data/supercleanse.PromptBro.desktop.in` (`Exec=... show`).
  Tests: `test_pickfile.py` (ported from Spitball), `test_doctor.py`, `test_migrate.py` (temp
  HOME tree with fake old dirs, including the config rewrite and the both-exist skip),
  `test_setup.py` (pip and download mocked), `test_lite_stdlib.py`, and in `test_app_cli.py`:
  `status`/`next`/`quit` with no instance print offline or exit 1 **and** the name is still unowned
  afterward; `open missing.md` with no instance exits nonzero and leaves no instance.
- **Acceptance:** full suite green; new tests pass; `/usr/bin/python3 -I bin/promptbro-lite doctor --json`
  runs in under 100 ms on the author's machine and prints valid JSON (expected problem before T4:
  `old-install` / `venv-missing`).

### T4. Switch the author's machine over
- **Model:** haiku. **Depends on:** T3. **Needs the author:** OK to move his config/models/state dirs and
  edit his two Hyprland files (backups taken).
- **Steps:** `bin/promptbro migrate`; `bin/promptbro setup --dev` (venv rebuilt; model already
  present so no download; desktop entry swapped); `scripts/dev-install.sh` (plugin link; no shell
  rescan); Hyprland edits per 6.5 with backups and `hyprctl configerrors`. Then run the suite with
  the new venv.
- **Acceptance:** `doctor --json` says `ok: true`; `promptbro status` prints
  `{"running": false, "state": "offline"}` and `NameHasOwner supercleanse.PromptBro` is still
  false afterward; suite counts equal baseline plus new tests; `hyprctl configerrors` empty;
  old models dir gone, new one present with the same 11 model folders. Tell the author hotkeys are
  back and ask him to press SUPER+SHIFT+T once (that check is his; it opens his last script).

### T5. App-side bridge: status file, GActions, recents
- **Model:** sonnet. **Depends on:** T3 (T4 for running the suite with the new venv).
  **Parallel with:** T6, T7, T10.
- **Goal:** sections 3.3, 3.4, 4.2.
- **Files:** `promptbro/statusfile.py` (writer, coalescing, heartbeat, `widget_state`),
  `promptbro/recent.py`, `promptbro/controller.py` (call the writer on state/line/visibility/
  worker/override/opacity changes; `widget_status()` builder; keep `status()` unchanged),
  `promptbro/app.py` (GActions; write offline on quit; add to recents on open),
  `tests/conftest.py`, new `tests/test_statusfile.py`, `tests/test_recent.py`, and app-level tests
  under the private bus: after `open`, the status file shows `loaded`/`listening` with the right
  `lines`; `gdbus ... Activate next` advances `line` in both `status` and the file; `quit` leaves
  `state: offline`.
- **Acceptance:** all tests green; the writer never writes more than 10 times a second during a
  replay fixture run (count writes in a test); files are created 0600 in a 0700 dir.

### T6. `Model.js` and its tests
- **Model:** sonnet. **Depends on:** this plan only (contract in 4.2, 5.1, 5.3).
  **Parallel with:** T5, T7, T10.
- **Files:** `Model.js`, `tests/js/model_loader.js` (copied from Spitball), `tests/js/model.test.js`.
- **Acceptance:** `node --test tests/js` green, covering every function in 5.3, every row of the
  5.1 table, stale/missing/garbage status, recents dedup and the 8 cap, and `activateArgs` output
  matching the exact argv in 3.3.

### T7. QML: Bridge, Widget, MenuContent
- **Model:** sonnet. **Depends on:** T6. **Parallel with:** T5 (it codes against the contract).
- **Goal:** sections 4.5 and 5. Copy Spitball's patterns: the `FileView` blocks, the
  `WidgetButton` inside a collapse-free `Item`, the `KeyboardPanel` with a `FocusScope` and
  `Keys.onEscapePressed`, `Button { leftAlign: true; iconText; text; foreground; fontFamily }`,
  `PanelSeparator`, and the `_cliRunner` `Process` component for `pick-file` and `doctor`.
- **Files:** `Widget.qml`, `Bridge.qml`, `MenuContent.qml`.
- **Acceptance:** `/usr/lib/qt6/bin/qmllint` reports no errors on the three files (warnings about
  `qs.*` imports it can't resolve are fine); no file spawns a process except through `Bridge`;
  transport buttons never close the panel; every other row does.

### T8. Offscreen harness and visual check
- **Model:** sonnet. **Depends on:** T7.
- **Files:** `tests/offscreen/{render.sh,harness.qml,bridge_check.qml}`.
- **Acceptance:** `tests/offscreen/render.sh --fake-data tests/offscreen/out` exits 0, writes six
  PNGs, logs no QML errors; the Bridge check passes. The agent looks at each PNG and fixes
  clipping, overlap, or unreadable contrast before finishing. Never run without
  `QT_QPA_PLATFORM=offscreen`.

### T9. Packaging and docs
- **Model:** haiku. **Depends on:** T5, T7, T8. **Parallel with:** T10, T11.
- **Files:**
  - `manifest.json`:
    ```json
    {"schemaVersion": 1, "id": "supercleanse.promptbro", "name": "PromptBro", "version": "0.2.0",
     "author": "supercleanse", "license": "MIT",
     "description": "A teleprompter that follows your voice. Load a script from the bar, and PromptBro shows it a few lines at a time under the bar and scrolls as you speak. Speech recognition runs on your machine (sherpa-onnx, no cloud, no account, no GPU).",
     "homepage": "https://github.com/supercleanse/promptbro",
     "kinds": ["bar-widget"], "entryPoints": {"barWidget": "Widget.qml"},
     "barWidget": {"displayName": "PromptBro",
       "description": "Script icon that shows whether PromptBro is listening. Click it to open a script, paste one, move a line up or down, restart, pause, show or hide the prompter, and quit.",
       "category": "Productivity",
       "aliases": ["promptbro", "teleprompter", "prompter", "script", "autocue"],
       "allowMultiple": false, "defaultSection": "center",
       "defaults": {"showProgress": true},
       "schema": [{"key": "showProgress", "type": "boolean", "label": "Show line progress next to the icon", "defaultValue": true}]}}
    ```
  - `README.md` rewritten on Spitball's outline: what it does, Install
    (`omarchy plugin add https://github.com/supercleanse/promptbro --enable`, then **Set up
    PromptBro…** from the menu, or `promptbro setup`), the dropdown, hotkeys (with the snippet),
    Update, Remove (6.4), What it needs (pacman list from 4.3; pip pins; the model and its size;
    where files live), Configuration, CLI table, Development (dev link, tests), License.
  - `CONTRACT.md`: status.json, recent.json, doctor JSON, verbs, GActions, exit codes.
  - `LICENSE`: keep MIT, "Copyright (c) 2026 the author Williams".
  - `scripts/make_preview.py` (pycairo, runs in the venv): composes a Broadway `--render-png`
    of the panel showing `promptbro/data/sample-script.md` with the dropdown PNG from T8 on a
    theme-colored background → `preview.png`. Staged content only.
  - `tests/test_manifest.py` (adapted from Spitball: required fields, id equals `moduleName` and
    `ipcTarget`, version equals `__version__` and pyproject, `omarchy plugin validate .`, no
    symlinks in the tree, `qmllint` when present). `tests/run.sh`.
  - Commit `PROMPTBRO_PLAN.md` as `docs/PROMPTBRO_PLAN.md`.
- **Acceptance:** `omarchy plugin validate ~/AI/Projects/PromptBro` exits 0; `tests/run.sh` green.

### T10. Model download hardening
- **Model:** haiku. **Depends on:** T2. **Parallel with:** T5-T9.
- **Goal:** marketplace hygiene. Add a `sha256` to each `ModelSpec` in `models.py` (compute from
  the archives at the registry URLs once; the agent downloads them to the scratchpad, or asks the
  orchestrator to skip models it can't fetch), verify before extracting, extract with
  `tarfile.extractall(ex, filter="data")`, reject zip members with `..` or absolute paths.
- **Acceptance:** tests for a bad checksum and a path-traversal archive (tiny synthetic
  archives); the real default model's checksum matches.

### T11. Opacity verb and slider (optional; cut if time is short)
- **Model:** sonnet. **Depends on:** T5, T7.
- **Goal:** `promptbro opacity <v>` and the `opacity` GAction set the panel background alpha live
  (clamped 0.3-1.0) and save it in `~/.local/state/promptbro/state.json` as `opacity`, which wins
  over `config.toml` at the next start (document this in README Configuration); slider row in
  `MenuContent.qml`.
- **Acceptance:** app test sets 0.5 and sees it in `status.json`; the Broadway render at 0.5 matches
  the existing opacity render test pattern; harness PNG shows the slider.

### T12. Turn it on in the author's bar (the author)
- **Model:** haiku drafts the steps; **the author runs them or says OK.** **Depends on:** T9.
- **Steps:** `omarchy plugin enable supercleanse.promptbro --section center --after supercleanse.spitball`
  (shell.json hot-reloads; if the widget doesn't appear, `omarchy-shell shell rescanPlugins`,
  still with his OK). Manual checklist for the author: icon visible and dim; click opens the dropdown;
  **Open script…** shows the portal dialog filtered to scripts (check whether it opened in the
  last script's folder); prompter appears and follows his voice; up/down/restart/pause buttons
  work with the dropdown staying open; hide/show; hotkeys still work; quit turns the icon dim;
  the hotkey legend shows along the bottom; SUPER+SHIFT+T with the app off reopens the last
  script.
- **Acceptance:** the author says it works, or the failures go back to the owning task.

### T13. Public GitHub repo (the author)
- **Needs the author:** creating and pushing a public repo is his call. Proposed:
  `gh repo create supercleanse/promptbro --public --source ~/AI/Projects/PromptBro --push`
  after he reviews the README and `preview.png`. Never run by an agent without his explicit yes.

### T14. Marketplace submission (the author, later)
- **Needs the author.** Follow `omacom/omarchy-plugin-marketplace` `SUBMISSION.md`. Draft for him:
  title `[Plugin]: PromptBro`, category `Productivity`, tags `ai, bar, media`, suggested tag
  `teleprompter`. Maintainer notes must say: the venv at `~/.local/share/promptbro/venv` and
  `pip install` of pinned packages run only from `promptbro setup`; the model downloads from the
  sherpa-onnx GitHub releases with a pinned sha256; no sudo, no services, nothing outside
  PromptBro's own dirs except the optional desktop entry and `~/.local/bin` link; Hyprland
  hotkeys are printed, never written. Expect `installer`/`package-manager` capabilities and a
  `review-required` outcome, as Spitball got. Show the author the full body; he creates the issue.
  Apply whatever the reviewers flag on Spitball (#9347) first.

### T15. Cleanup (the author OK for the delete)
- **Model:** haiku. **Depends on:** T12.
- **Steps:** with the author's OK, `rm -rf ~/.local/share/teleprompter` (only the old venv is left
  there; 152 MB). Delete the `bindings.lua.bak.*`/`hyprland.lua.bak.*` made in T4 after a week,
  or leave them. Update the author's private project note
  `(a local note, not in the repo)`
  (rename to `promptbro-omarchy-plugin.md`, new paths, plugin id).
- **Acceptance:** `doctor --json` still ok; prompter still starts from the bar.

### Order at a glance

```
T0 → T1 → T2 → T3 → T4 ─┬─ T5 ─┐
            └─ T10       ├─ T6 → T7 → T8 ─┤
                         └────────────────┴→ T9 → (T11) → T12 → T13 → T14
                                                           └→ T15
```

Steps that need the author: T1 (OK to quit the running prompter), T4 (OK to move his data dirs and
edit Hyprland files), T12 (enable in the bar, any shell rescan, the live checklist), T13
(publish the repo), T14 (marketplace), T15 (delete the old venv). No sudo is needed on his
machine.

---

## 9. Open decisions for the author

1. **Public repo name:** `supercleanse/promptbro`. Recommend yes, MIT, matching Spitball.
2. **Plugin id:** `supercleanse.promptbro` (Spitball style) or the marketplace's suggested
   `io.github.supercleanse.promptbro`. Recommend `supercleanse.promptbro`. It's permanent once
   listed.
3. **Marketplace now or later:** recommend later, after a week of his own use and after Spitball's
   review settles, so its lessons carry over.
4. **`teleprompter` alias:** recommend none. The dropdown and hotkeys replace typing it.
5. **Icon:** the script-text family above. Alternative: md-account_voice (U+F05CB, a speaking head).
   Recommend script-text.
6. **Bar position:** center, right after Spitball. Recommend yes.
7. **Opacity slider:** recommend yes (T11, small). Monitor picker: recommend no.
8. **SUPER+SHIFT+T with the app off:** start it and show the last script (this plan) or do nothing.
   Recommend start and show.
9. **App-launcher entry ("PromptBro" in the app menu, runs `show`):** recommend keep.
10. **Old venv:** recommend deleting it in T15 once the new install has worked for a day.
