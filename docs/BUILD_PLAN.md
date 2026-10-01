> **Note:** Written when this app was called Teleprompter; names and paths below are the old ones.

# Teleprompter — build plan

Ordered, self-contained tasks that implement `DESIGN.md`. Section numbers (§) refer to it.
Every task is written as a finished condition with an acceptance test that a machine or a
person can run. Model tiers: **haiku** = mechanical (scaffolding, configs, running known
commands), **sonnet** = code + tests against the spec, **opus** = the subtle parts.

Ground rules for every task:

- Never open a window on the author's desktop. GUI checks run under Broadway
  (`gtk4-broadwayd :7 --port 8087`, `env -u WAYLAND_DISPLAY -u DISPLAY GDK_BACKEND=broadway
  BROADWAY_DISPLAY=:7`) inside `dbus-run-session`, with `--app-id-suffix test`.
- Never install a system package. T0 lists the ones the author must approve; everything else is
  pip into the venv.
- Fake data only behind `--demo`. `--wav` and `--replay` are real-pipeline test inputs, not fakes.
- American spelling. Plain English in docstrings and docs.
- Commit at the end of each task with a message that names the task (`T3: aligner …`).

Dependency graph:

```
T0 (the author) ─┐
T1 ─────────┼─▶ T2 ─▶ T3 ──────────────┐
            ├─▶ T4 ─────────────────────┼─▶ T7 ─▶ T8 ─▶ T9 ─▶ T10 (the author on desktop)
            └─▶ T5 ─▶ T6 ───────────────┘            └─▶ T11, T12 (optional)
```

T2, T4, T5 run in parallel after T1. T3 needs T2. T6 needs T5. T7 needs T3, T4, T6.

---

## T0: System packages (the author approves and runs)

- **Model:** none (the author)
- **Depends on:** —
- **Parallel with:** T1
- **Goal:** `espeak-ng` is installed so tests can generate speech WAVs. Optional packages decided.
- **Files:** none
- **Acceptance:** `espeak-ng --version` prints 1.52.x. Optional: `sway --version`, `grim` (for
  T12); `piper --help` (nicer test voices).

```bash
sudo pacman -S --needed espeak-ng
# optional
sudo pacman -S --needed sway grim
yay -S piper-tts piper-voices-en-us
```

Nothing else is needed system-wide: gtk4, gtk4-layer-shell, python-gobject, gst-plugins-base,
gst-plugin-pipewire, wl-clipboard, python-cairo, python-yaml, ffmpeg are present.

---

## T1: Repository scaffold, venv, launcher, config defaults

- **Model:** haiku
- **Depends on:** —
- **Parallel with:** T0
- **Goal:** the package imports, the launcher runs `python -m teleprompter --version`, the venv
  has all runtime deps, and `pytest` runs (zero tests collected is fine).
- **Files:** `pyproject.toml` (name `teleprompter`, version 0.1.0, `requires-python >=3.14`,
  deps: `sherpa-onnx==1.13.8`, `numpy>=2`, `rapidfuzz>=3.14`, `metaphone==0.6`,
  `num2words>=0.5.14`; `[project.optional-dependencies] dev = ["pytest"]`, `vosk = ["vosk==0.3.45"]`),
  `README.md` (what it is, install, verbs, hotkeys — short), `LICENSE` (MIT), `.gitignore`
  (`__pycache__`, `.pytest_cache`, `tests/audio/*.wav` except `tests/audio/keep/`, `*.png` under
  `tests/out/`, `tests/fixtures/private/`), `bin/teleprompter` (exactly as §10),
  `teleprompter/__init__.py` (`__version__`), `teleprompter/__main__.py` (first line
  `from ctypes import CDLL; CDLL("libgtk4-layer-shell.so.0")` wrapped in try/except OSError,
  then `from .app import main`), `teleprompter/app.py` stub (handles `--version` only),
  `teleprompter/config.py` (dataclasses for §9 with defaults, `load(path) -> Config`, unknown
  keys warn, type/range validation), `teleprompter/data/sample-script.md` (a 20-line sample
  speech that includes a year, a dollar amount, a percentage, an acronym, a hyphenated word,
  a parenthetical stage direction, and one repeated phrase), `teleprompter/data/com.example.Teleprompter.svg`
  (simple monochrome glyph, 64×64), `tests/test_config.py`, `tests/conftest.py` (empty for now),
  `install.sh` **steps 1 and 3 only** (venv + config; models, icon, desktop, snippet come in T9).
- **Acceptance:**
  `./install.sh` creates `~/.local/share/teleprompter/venv` and
  `~/.config/teleprompter/config.toml`; `bin/teleprompter --version` prints `teleprompter 0.1.0`;
  `~/.local/share/teleprompter/venv/bin/python -c "import gi, sherpa_onnx, rapidfuzz, metaphone, num2words, numpy, cairo, yaml; gi.require_version('Gtk','4.0'); gi.require_version('Gtk4LayerShell','1.0'); gi.require_version('Gst','1.0')"`
  exits 0; `venv/bin/python -m pytest -q` passes `test_config.py` (defaults load; a bad
  `width = "x"` raises `ConfigError`; an unknown key logs a warning and loads).

---

## T2: Normalizer and script model

- **Model:** sonnet
- **Depends on:** T1
- **Parallel with:** T4, T5
- **Goal:** `script.load_text(text, max_line_chars) -> Script` produces display lines and units
  with spoken forms, weights, stopword and matchable flags per §6.1–6.2, and
  `normalize.normalize_hyp(text, script, cursor)` produces the token stream per §6.2.
- **Files:** `teleprompter/normalize.py` (`spoken_forms`, `normalize_hyp`, `HARD_FILLERS`,
  `SOFT_FILLERS`, `STOPWORDS`, `HOMOPHONES`, `Tok`), `teleprompter/script.py` (`Unit`,
  `DisplayLine`, `Script`, `load_text`, `load_file` for `.txt`/`.md`, Markdown stripping,
  stage-direction detection, line splitting), `tests/test_normalize.py`, `tests/test_script.py`.
- **Acceptance:** pytest passes with at least these assertions:
  `spoken_forms("$79")[0] == ("seventy","nine","dollars")`; `("twenty","twenty","six") in
  spoken_forms("2026")` and `("two","thousand","twenty","six")` too; `spoken_forms("40%") ==
  [("forty","percent")]`; `spoken_forms("API")` contains `("a","p","i")` and `("api",)`;
  `spoken_forms("full-stack")` contains `("full","stack")` and `("fullstack",)`;
  `spoken_forms("it's")` contains `("its",)` and `("it","is")`; `spoken_forms("2005")` contains
  `("twenty","oh","five")`; `spoken_forms("1500")` contains `("fifteen","hundred")`; no form
  contains the token `"and"` for plain cardinals. The §6.8 script yields exactly 7 display lines
  and 46 units with the listed indices; `df["thanks"] == 2`; `units[6].weight` (2026) is
  number-derived with base 1.0; `units[3]` ("the") `is_stop` and weight < 0.3; a 200-char
  paragraph splits at sentence ends into pieces ≤ 90 chars; `# Heading` loses the `#`;
  `(pause)` yields a unit with `matchable=False` while a 12-word parenthetical stays matchable;
  `normalize_hyp("um the plan uh costs", …)` == tokens `the plan costs`; `normalize_hyp("in
  2026 we", script, cursor=4)` picks `twenty twenty six`; `"you know"` becomes two soft-filler
  tokens.

---

## T3: Aligner

- **Model:** opus
- **Depends on:** T2
- **Parallel with:** T4, T5, T6
- **Goal:** `aligner.Aligner(script, params)` implements §6.3–6.6 exactly (token similarity,
  band Smith-Waterman, tiered decision with echo guard and far-jump confirmation, two cursors
  with LocalAgreement-2 stability, silence/resume handling, user keys) and passes the full
  §6.8 corpus.
- **Files:** `teleprompter/aligner.py` (`Params` dataclass with every §6 constant and
  `Params.from_config(cfg.aligner)`, `token_sim` with an LRU cache, `align_band`, `locate`,
  `Aligner.on_event(Event) -> Position`, `Aligner.on_user_key(key) -> Position`, `Position`,
  `Event`), `tests/cases/*.yaml` (all 24 cases from §6.8, one file each), `tests/test_aligner.py`
  (YAML runner; `speak()` helper in `tests/conftest.py`; invariants checked after every event),
  `tests/test_token_sim.py`.
- **Acceptance:** all corpus cases pass; `token_sim("write","right") >= 0.82`,
  `token_sim("light","night") == 0.0`, `token_sim("the","then") == 0.0`,
  `token_sim("balancing","balance") >= 0.8`, `token_sim("bro","brown",h_is_open_last=True) == 0.8`,
  `token_sim("bro","brown") == 0.0`; a 1,000-line generated script aligns 500 synthetic events
  in < 1.0 s total (`time.perf_counter`, `pytest -k perf`); the module imports no GTK/GLib;
  `Aligner` has no wall-clock reads. Property test: for 200 random event streams drawn from
  the sample script, `provisional >= committed` always and `committed` never decreases without
  a key.

---

## T4: ASR worker (sherpa-onnx) and model download

- **Model:** sonnet
- **Depends on:** T1
- **Parallel with:** T2, T3, T5, T6
- **Goal:** `python -m teleprompter.asr_worker` captures the default mic (or a WAV) through
  GStreamer, runs the sherpa-onnx streaming recognizer, and prints the §5.4 JSON events; the
  model downloads and verifies with one command.
- **Files:** `teleprompter/models.py` (registry of the three sherpa models + Vosk small with
  URL, dir name, required files, approximate size; `download(name, models_dir)`, `verify`,
  `resolve(cfg) -> ModelPaths`), `teleprompter/asr_worker.py` (argparse: `--model DIR`
  `--device NODE` `--wav FILE` `--fast|--realtime` `--threads N` `--endpoint-rule2 S`;
  GStreamer mic pipeline and WAV pipeline from §5.3; `queue.Queue` of 1600-sample float32
  blocks; decode thread; words from `▁` tokens with timestamps; `level` events every 250 ms;
  stdin control `pause|resume|quit`; clean exit on EOF/SIGTERM; `status` events), `scripts/make_test_audio.sh`,
  `tests/audio/sentences.txt` (8 sentences from the §6.8 script + the sample script),
  `tests/audio/keep/welcome.wav` (one checked-in 16 kHz WAV, < 200 KB, generated by espeak-ng),
  `tests/test_asr_worker.py`.
- **Acceptance:** `venv/bin/python -m teleprompter.models download sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8`
  leaves the four files in `~/.local/share/teleprompter/models/<dir>/` and a second run says
  "already present". `python -m teleprompter.asr_worker --wav tests/audio/keep/welcome.wav --fast`
  prints a `status ready` line, ≥ 1 `partial`, exactly ≥ 1 `final`, and exits 0 within 10 s;
  the final's normalized text has WER ≤ 25 % against "welcome back to the show"; word
  timestamps are non-decreasing; a 2 s silence WAV yields no `final` with non-empty text.
  `tests/test_asr_worker.py` skips cleanly (`pytest.skip`) when the model dir is missing.
  `echo pause | python -m teleprompter.asr_worker --wav … --realtime` produces no events after
  the pause line is read (checked with a 1 s timeout). No audio is written to disk. The mic
  path is **not** exercised by tests; T10 covers it.

---

## T5: PrompterView widget, theme, offscreen render

- **Model:** sonnet
- **Depends on:** T1
- **Parallel with:** T2, T3, T4
- **Goal:** the view draws history/current/upcoming lines with the §7.3 typography and colors,
  highlights spoken words on the current line, animates line changes with an ease-out, and can
  render itself to a PNG offscreen under Broadway.
- **Files:** `teleprompter/theme.py` (§7.4: `load_palette()`, fallbacks, `css_defines()`,
  `watch(callback)`), `teleprompter/view.py` (`PrompterView(Gtk.Widget)`: `set_script(lines)`,
  `set_position(Position)`, `jump_to(line)` (no animation), status dot, toast, empty state,
  layout cache, edge masks, reduced-motion check — start from `proto/overlay_proto.py`'s
  `Prompter`), `teleprompter/render.py` (`render_to_png(window, path)` as an offscreen Broadway render), `teleprompter/demo.py` (bundled sample script + a deterministic scripted
  event list; only importable when `--demo` is set), `tests/test_theme.py`,
  `tests/test_view_render.py`, `tests/out/.gitkeep`.
- **Acceptance:** `test_theme.py`: a temp `colors.toml` with `accent = "#123456"` is read;
  missing file → matte-black fallbacks and one warning. `test_view_render.py` (skips without
  `gtk4-broadwayd`): starts a private broadwayd, runs `python -m teleprompter --demo
  --app-id-suffix test --render-png tests/out/view.png --size 720x240 --line 3` under
  `dbus-run-session`, asserts the PNG exists, is ≥ 720×240, is not a single color, and that the
  center row contains pixels within ±12 of the accent color while the top 12 rows contain none.
  Visual check by the builder (not CI): `proto/shot.mjs`-style Playwright screenshot after two
  `next` verbs shows a moved stack. `GDK_BACKEND=broadway` is the only backend used; nothing
  is shown on the live desktop.

---

## T6: Application shell, layer-shell window, verbs

- **Model:** sonnet (escalate to opus only if layer-shell placement misbehaves on the real desktop in T10)
- **Depends on:** T5
- **Parallel with:** T3, T4
- **Goal:** `python -m teleprompter` is a single-instance `Gtk.Application` that builds the
  layer-shell window per §7.1 (plain window under Broadway), exposes every verb in §7.2 via
  `do_command_line`, loads scripts from a file or the clipboard, persists state, and applies
  click-through after map.
- **Files:** `teleprompter/app.py` (full), `teleprompter/window.py`, `teleprompter/controller.py`
  (script load, verbs, state file, manual keys → aligner → view; **worker wiring is a stub
  until T7** — with no worker the app just shows the script), `tests/test_app_cli.py`.
- **Acceptance:** under `dbus-run-session` + Broadway with `--app-id-suffix test`: start
  `--demo`; from a second process send `next`, `next`, `status` → the status JSON has
  `"line": 2`; send `prev` → line 1; `restart` → line 0; `goto 5` → 5; `hide` then `status`
  shows `"visible": false`; `open tests/data/skip.md` (a copy of the §6.8 script) → `status`
  shows that path and `"lines": 7`; `paste` with `wl-paste` stubbed by `TELEPROMPTER_PASTE_CMD="cat tests/data/skip.md"`
  loads the same; `quit` ends the process within 2 s. State file exists with the last line.
  On this Wayland desktop `python -c` import of `window.py` reports `LayerShell.is_supported()
  == True` when run through `bin/teleprompter` (checked with a `--check-layer-shell` flag that
  prints and exits without creating a window). Code review confirms: `init_for_window` is
  called before `present`, `exclusive_zone == 0`, `KeyboardMode.NONE`, namespace `teleprompter`,
  monitor chosen by connector, `set_input_region(cairo.Region())` connected to `map` when
  `click_through` is true, re-pick monitor on `items-changed`.

---

## T7: Integration — worker supervision, live tracking, pause

- **Model:** opus (process/IO plumbing on the GLib loop, latency, failure modes)
- **Depends on:** T3, T4, T6
- **Parallel with:** —
- **Goal:** the app spawns and supervises the ASR worker, feeds its events into the aligner,
  moves the view, and handles pause/resume/hide/show, worker crashes, and `--wav`/`--replay`
  inputs end to end.
- **Files:** `teleprompter/controller.py` (full: `Gio.Subprocess`, async line reader, event
  dispatch, `level` → voice flag, pause/resume protocol, restart backoff, `--wav` passthrough
  to the worker, `--replay` reader that schedules recorded events by their `t` with
  `GLib.timeout_add`), `tests/test_integration_replay.py`, `tests/fixtures/README.md`.
- **Acceptance:** under Broadway + private bus: `--replay tests/fixtures/synthetic_skip.json.gz
  --open tests/data/skip.md --app-id-suffix test --render-png tests/out/replay.png
  --render-at 1.6` (a fixture generated from the §6.8 `skip_line` events) produces a PNG whose
  center line is line 4's text (compare against `--line 4` render pixel-wise within 2 %).
  With the model present: `bin/teleprompter open tests/data/skip.md --wav tests/audio/skip_read.wav
  --app-id-suffix test --headless-status` (a flag that prints `status` JSON every second to
  stdout and quits at WAV end) ends with `"line": 6` (last line) and never prints a line lower
  than the previous one. Kill the worker with `pkill -f asr_worker` while running → the log
  shows a restart within 3 s and the status dot state goes `error` → `tracking`. `pause` →
  worker receives `pause` (log line) and status shows `"state": "paused"`; `resume` restores.
  End-to-end latency instrumentation: the controller logs `align_ms` and `event_age_ms`
  (now − event.t at the moment the view is updated) at debug level; p90 `align_ms` < 5.

---

## T8: Recorded fixtures and the replay regression suite

- **Model:** sonnet
- **Depends on:** T7 (and T0's espeak-ng)
- **Parallel with:** T9
- **Goal:** real-ASR event streams for the sample script under several misread variants are
  recorded once and replayed in CI against the aligner with the §6.8 metrics.
- **Files:** `scripts/record_fixture.py` (TTS each variant → 16 kHz WAV → `asr_worker --wav
  --fast` → `tests/fixtures/<name>.json.gz` with `script`, `events`, `truth` word end times from
  the TTS segment boundaries; variants: `clean_160wpm`, `clean_200wpm`, `filler`, `repeat`,
  `skip_line`, `restart_sentence`, `silence_2s`; `--mic` mode for a human recording, output to
  `tests/fixtures/private/` which is git-ignored), `tests/test_fixtures_replay.py` (metrics:
  overshoot == 0, false_jumps == 0, final_error ≤ 2; lag p50/p90 and stall printed), the seven
  `.json.gz` files (each < 100 KB).
- **Acceptance:** `pytest tests/test_fixtures_replay.py -q` passes offline (no model needed);
  the printed lag p50 is < 0.6 s on the clean fixtures (espeak's timing is exact, so this
  measures ASR + aligner lag); re-running `record_fixture.py` with the same TTS produces
  byte-identical WAVs (deterministic) — the event JSON may differ slightly and is only
  re-recorded on purpose.

---

## T9: Install script, desktop entry, docs, Hyprland snippet

- **Model:** haiku
- **Depends on:** T7
- **Parallel with:** T8
- **Goal:** `./install.sh` does §10 steps 1–5 idempotently; README documents install, verbs,
  hotkeys, config, and testing; the Lua snippet is printed for the author.
- **Files:** `install.sh` (full), `README.md` (full), `teleprompter/data/com.example.Teleprompter.desktop.in`,
  `docs/hyprland-snippet.lua` (the §7.1 block, with the absolute path filled in by `install.sh`
  when printing).
- **Acceptance:** running `./install.sh` twice prints "already" for venv, model, config, icon,
  desktop, and ends by printing the Lua snippet with `~/AI/Projects/Teleprompter/bin/teleprompter`
  paths; `~/.local/share/applications/com.example.Teleprompter.desktop` validates with
  `desktop-file-validate` (if installed) or at least has `Exec=`, `Icon=`, `Type=Application`;
  the script never touches `~/.config/hypr/`; `grep -c` of British spellings from the global
  spelling table over `README.md DESIGN.md BUILD_PLAN.md teleprompter/` is 0.

---

## T10: Benchmark and the author's desktop checklist

- **Model:** sonnet (script) + the author (manual)
- **Depends on:** T9
- **Parallel with:** T11, T12
- **Goal:** the ASR cost and latency are measured, not estimated, and the app is confirmed on
  the real desktop by the author using a written checklist.
- **Files:** `scripts/bench_asr.py` (feeds a 60 s WAV in 100 ms blocks in real time through
  `asr_worker --realtime` while sampling the worker's CPU via `/proc/<pid>/stat`; prints
  per-chunk decode p50/p90 ms, mean CPU % of one core, RSS; accepts `--model` to compare the
  three sherpa models), `docs/CHECKLIST.md`.
- **Acceptance:** `bench_asr.py` prints a table for the default model; the README's
  "Performance" section is updated with the measured numbers (replacing the *estimate* wording).
  Checklist items the author ticks on the live desktop: (1) `SUPER+SHIFT+T` shows the card
  centered under the bar on the laptop screen, 8 px gap, nothing else moves; (2) clicking on
  the card hits the window behind it; (3) `teleprompter paste` loads the clipboard; (4) reading
  the sample script aloud lights words and scrolls lines with no visible lag beyond ~0.3 s; (5)
  saying two off-script sentences does not move it; (6) skipping a line catches up within one
  sentence; (7) `prev`/`next`/`restart` keys work and re-anchor tracking; (8) `pause` turns the
  dot amber and the mic indicator in the bar off; (9) during a Google Meet call, `top` shows
  the worker under 40 % of one core (adjust `num_threads`/model otherwise); (10) unplugging the
  Studio Display keeps the card on `eDP-1` and tracking continues on the laptop mic; (11) the
  card is hidden by `hide` and gone after `quit`; (12) `omarchy theme set` to another theme
  followed by `reload-theme` recolors the card.

---

## T11 (optional): Vosk fallback engine

- **Model:** sonnet
- **Depends on:** T4, T7
- **Parallel with:** T10, T12
- **Goal:** `[asr] engine = "vosk"` runs `vosk-model-small-en-us-0.15` behind the same event
  contract, with word timestamps in partials and no closed grammar.
- **Files:** `teleprompter/asr_vosk.py`, `models.py` entry for the Vosk zip, `asr_worker.py`
  `--engine` switch, `tests/test_asr_vosk.py` (skips without the model).
- **Acceptance:** the T4 WAV test passes with `--engine vosk` (WER bar 35 % for the small model);
  `PartialResult` words carry `t`; `pip install -e .[vosk]` is the only extra step.

---

## T12 (optional, needs T0's sway+grim): Headless layer-shell placement test

- **Model:** opus
- **Depends on:** T6, T0 (sway, grim)
- **Parallel with:** T10, T11
- **Goal:** the real layer-shell path (anchor, margin, monitor pick, click-through region) is
  exercised offscreen under a headless wlroots compositor.
- **Files:** `tests/sway-headless.conf` (`output HEADLESS-1 mode 1800x1125`, no bar),
  `tests/test_layer_shell_headless.py`.
- **Acceptance:** the test starts `WLR_BACKENDS=headless WLR_RENDERER=pixman
  WLR_LIBINPUT_NO_DEVICES=1 sway -c tests/sway-headless.conf`, launches the app against that
  `WAYLAND_DISPLAY` with `--demo --app-id-suffix test`, waits 1 s, runs `grim` on that display,
  and asserts the card's bounding box is horizontally centered (±2 px) with its top edge at
  `top_gap` px (sway has no bar, so no offset); `hyprctl`-equivalent check via `swaymsg -t
  get_outputs` is not needed. Skips when `sway` or `grim` is missing. Never runs against the
  live `wayland-1` socket (asserts `WAYLAND_DISPLAY != "wayland-1"` before launching).
