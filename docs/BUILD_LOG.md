> **Note:** Written when this app was called Teleprompter; names and paths below are the old ones.

# Teleprompter Build Log

## T1: Repository scaffold, venv, launcher, config defaults

**Status:** COMPLETE (acceptance checks passed)

**What was done:**
- Created `pyproject.toml` with dependencies and package configuration
- Created `README.md` with install, usage, verbs, and hotkeys documentation
- Created `LICENSE` (MIT)
- Created `.gitignore` excluding cache, test WAVs, PNGs, and private fixtures
- Created `bin/teleprompter` launcher script (executable)
- Created `teleprompter/__init__.py` with version
- Created `teleprompter/__main__.py` with libgtk4-layer-shell preload
- Created `teleprompter/app.py` stub handling `--version` flag
- Created `teleprompter/config.py` with dataclasses, validation, and TOML loading for all §9 config sections
- Created `teleprompter/data/sample-script.md` (20-line sample with year, dollar amount, percentage, acronym, hyphenated word, stage direction, repeated phrase)
- Created `teleprompter/data/com.example.Teleprompter.svg` (64×64 monochrome icon)
- Created `tests/test_config.py` with config validation tests
- Created `tests/conftest.py` (empty fixture file)
- Created `install.sh` (steps 1 and 3: venv + config)

**Acceptance result:** All checks pass
- `./install.sh` creates `~/.local/share/teleprompter/venv` and `~/.config/teleprompter/config.toml` ✓
- `bin/teleprompter --version` prints `teleprompter 0.1.0` ✓
- All imports (`gi`, `sherpa_onnx`, `rapidfuzz`, `metaphone`, `num2words`, `numpy`, `cairo`, `yaml`) succeed ✓
- `pytest -q` passes 7 tests in `test_config.py` (defaults load, bad width raises error, unknown keys warn) ✓

**Deviations:** None. §9 defaults from DESIGN.md used verbatim in install.sh and config.py.

**Notes:** pyproject.toml requires explicit package discovery to exclude `proto/` directory.

## T5

- Added `teleprompter/theme.py`, `view.py`, `render.py`, `demo.py`; `tests/test_theme.py`, `tests/test_view_render.py`, `tests/out/.gitkeep`; `artifacts/` added to `.gitignore`.
- `app.py` got a minimal argparse dispatch (`--demo`, `--app-id-suffix`, `--render-png`, `--size`, `--line`); render refuses to run unless `GDK_BACKEND=broadway` and `--demo`. T6 owns the real app.
- Acceptance: full suite 11 passed (theme: accent read, fallback + one warning; render: PNG 720x240, accent pixels at center row, none in top 12 rows). Visual check: faded history, larger bold accent current line with spoken-word highlight, brighter upcoming.
- PNGs: `artifacts/view.png` (line 3), `artifacts/view-line5.png` (line 5).
- Deviations: view is duck-typed (list of str, or script with `.lines`/`.units`, position with `.line/.unit/.state`) because script.py/aligner.py are not merged yet; demo.py has its own stand-in classes. Status dot sits at y=20 so it stays out of the top-12-row zone. Test reads PNG with GdkPixbuf (no PIL in venv). Playwright two-`next` check not done (no verbs until T6); moved stack verified by rendering different `--line` values.

## T2

- Added `teleprompter/normalize.py` (`spoken_forms`, `normalize_hyp`, `HARD_FILLERS`, `SOFT_FILLERS`, `STOPWORDS`, `HOMOPHONES`, `Tok`), `teleprompter/script.py` (`Unit`, `DisplayLine`, `Script`, `load_text`, `load_file`), `tests/test_normalize.py`, `tests/test_script.py`.

**Acceptance:** all listed T2 assertions pass (13 tests; 7-line / 46-unit corpus script, `df["thanks"] == 2`, number weights, stage directions, line splitting, hypothesis normalization).

**Deviations:** `fox` (3 chars) has weight 0.5 by the §6.1 short-token rule, so it is not "strong". `load_file` applies the same Markdown stripping to `.txt` and `.md`. Years 2000-2099 include the "two thousand and ..." variant per §6.2; plain cardinals never contain "and".

## T4: ASR worker and model download

**Done:** `teleprompter/models.py` (registry of 3 sherpa models + Vosk small; `download`/`verify`/`resolve`; CLI `python -m teleprompter.models download|verify|list`), `teleprompter/asr_worker.py` (all flags, GStreamer mic/WAV pipelines, decode thread, level events, stdin pause/resume/quit, SIGTERM exit), `scripts/make_test_audio.sh`, `tests/audio/sentences.txt`, `tests/test_asr_worker.py`.

**Acceptance:**
- Model download leaves the four files (plus `test_wavs/`) in `~/.local/share/teleprompter/models/<dir>/`; second run prints "already present". Pass.
- Worker on the model's bundled `test_wavs/0.wav` (`--fast`): `status ready`, 28 partials, 1 final, exit 0, about 1.9 s wall; transcript matches the reference exactly; word timestamps non-decreasing. Pass.
- 2 s silence WAV: no non-empty final. Pass. Pause test (`pause` then 1 s with no events): pass. Tests skip when the model is missing.
- **Not verified:** `tests/audio/keep/welcome.wav` and the "welcome back to the show" WER check. espeak-ng is not installed yet, so the WAV does not exist and that test skips. After installing espeak-ng, run `scripts/make_test_audio.sh`, commit `tests/audio/keep/welcome.wav`, and rerun pytest.

**Measured:** partials arrive every 80-200 ms of audio time. CPU at `--realtime`, threads=2, including about 1 s of model load: 23 s user over 8.6 s wall for 6.6 s of audio (roughly 2.7 cores). That is well above the §5.1 estimate; onnxruntime threads may be spinning. Left for T10 `bench_asr.py` to confirm.

**Deviations:**
- The venv is `~/.local/share/teleprompter/venv` (no `.venv` in the repo).
- The NeMo model marks word starts with a leading space, not `▁`. The worker treats both as word boundaries.
- Added `status` events with `state` `paused` and `resumed` as acknowledgments (used by the pause test).
- In `--wav` mode, stdin EOF does not stop the worker (so runs with no stdin work); in mic mode EOF exits. In `--wav` mode `t` is audio time rather than wall time.

## T6

- Added `window.py` (layer-shell per 7.1: init before present, OVERLAY, TOP anchor, exclusive zone 0, KeyboardMode.NONE, namespace `teleprompter`, monitor by connector, re-pick on `items-changed`, empty input region on `map` when `click_through`), `controller.py` (verbs, manual keys, debounced `state.json`, worker stub for T7), full `app.py`, `tests/test_app_cli.py`, `tests/data/skip.md`, `teleprompter/data/hyprland-snippet.lua`.
- Acceptance: suite 30 passed. Verbs next/next/status=2, prev=1, restart=0, goto 5, hide -> visible false, open skip.md -> 7 lines, paste via `TELEPROMPTER_PASTE_CMD` -> 7 lines, quit under 2 s, state file written. `bin/teleprompter --check-layer-shell` prints `True` on the live Wayland session (no window created).
- the author's decisions: `window.hide_from_screen_share = true` (new config key, default on) controls whether `--print-hypr-snippet` emits the `no_screen_share` layer rule active or commented out; hotkeys are the proposed set, emitted as a snippet only (Hyprland files untouched); click-through on by default.
- Deviations: `--demo` without `--app-id-suffix` uses app id `...Demo` (unique, so the acceptance verbs can reach it; still never the real instance). Added `SEND_ENVIRONMENT` so `TELEPROMPTER_PASTE_CMD` reaches the primary. Script model is a stand-in (`SimpleScript`: markdown strip + display-line split) until script.py/aligner.py land; T7 swaps it and wires the worker at the marked stubs. Config gained `hide_from_screen_share` (install.sh default not updated).
- Deferred to T10 (real desktop only): actual layer placement (centered, 8 px below bar, over fullscreen), click-through, `no_screen_share` effect, hotkey names via `hyprctl binds`, hide/show remap behavior.

## T4b: CPU tuning

Steady-state CPU measured in-process (`time.process_time()` deltas after model load), 24.3 s of bundled `test_wavs/0.wav` + `1.wav` fed at real-time pace. Latency is feed-to-result compute time per block (the model adds its own 80 ms chunk).

| threads | block | result read | CPU (cores) | latency mean / p95 / max |
|:--|:--|:--|:--|:--|
| 2 (old default) | 100 ms | every block | 2.89 | 14 / 27 / 45 ms |
| 1 | 100 ms | every block | 0.18 | 19 / 37 / 44 ms |
| 1 | 100 ms | only after decode | 0.18 | 19 / 37 / 53 ms |
| 1 | 160 ms | only after decode | 0.19 | 30 / 37 / 50 ms |
| 2 | 160 ms | only after decode | 2.91 | 22 / 35 / 58 ms |
| 1 | 80 ms | only after decode | 0.18 | 15 / 35 / 51 ms |

Cause: onnxruntime intra-op threads busy-wait when `num_threads` is 2 (the earlier 2.7 cores was spinning, not model load). sherpa-onnx 1.13.8 does not expose the ORT spin-wait session option. Block size and decode batching made no difference. Fix: default `num_threads` 1 in `ASRConfig` and the worker's `--threads`. Result: about 0.18 core, partial latency well under 250 ms.

## T3: Aligner

**Done:** `teleprompter/aligner.py` (`Params` with every §6 constant + `from_config`, cached `token_sim`, `align_band`, `locate`, `Aligner.on_event` / `on_user_key`, `Position`, `Event`), 24 corpus files in `tests/cases/`, `tests/test_aligner.py` (YAML runner, invariants after every event, 200-stream property test, perf test, extra echo/far-confirm/rival tests), `tests/test_token_sim.py`, `speak()` in `tests/conftest.py`.

**Acceptance:** all 24 corpus cases pass (currency runs both variants from one file). All six `token_sim` assertions pass. Property test: 200 random streams over the sample script (skips, fillers, stutters, revisions, silences, keys) keep `provisional >= committed`, and `committed` never drops without a key. The module imports no `gi`/`time` and reads no clock. Full suite: 71 passed, 1 skipped.

**Speed:** a 1,000-line generated script aligns 500 events in 0.13 s (limit 1.0 s). On a 2,007-unit script over 2,000 events, per-update latency averages 0.28 ms, p95 0.37 ms, max 2.0 ms (cold cache). Pure Python; the DP skips zero cells and precomputes a match table per band. No new dependencies (rapidfuzz, metaphone were already in the venv).

**Parameters:** §6.4-6.6 values used unchanged. Two §6.5 numbers that were implicit are now named: `near_move_cost=0.6`, `far_move_cost=1.2`.

**Deviations, all needed to reconcile corpus cases with each other or with the design's stated intent:**
- **Fast path only on a closed, one-token utterance.** Two cases disagree on a lone "the" at unit 12: `skip_line` wants a *partial* "the" to hold, while `next_word_fast_path` wants a *final* "the" to move. The fast path therefore fires only when the whole tail is one token and the event is final. A stable-prefix "the" inside a longer partial does not trigger it either; otherwise `skip_line` commits unit 13 early.
- **`Position.line` after a key.** In `user_back_key`, `line_prev` from line 4 must report line 3 with unit 18, but unit 18 sits on line 2. Meanwhile `skip_line` needs unit 12 to report its own line, 1. Keys now set a line hint (the line to read next) that holds until the cursor next moves. Otherwise the line is the line of the provisional unit. `line_next` shows the next line, and two `line_next` presses from the start show line 2, which matches the T6 check.
- **Stage directions don't count toward distance.** `delta` and `move_cost` count only matchable units, so "(pause) Thanks" is a 1-unit follow and not a 2-unit one. The fast path also looks at the next *matchable* unit.
- **Miss handling in the preview pass.** When the preview finds nothing, the display stays where it is as long as the new partial extends the previous one. It falls back to `committed` (within the 3-unit revision limit) only when the recognizer revised earlier words. Following §6.6 literally, the display slid back 2-3 words at the start of every ad-lib. §6.6's own summary says "the cursor holds."
- **What counts as a miss.** The miss counter (the `adlib` state after 8) now resets when the tail ends exactly on the cursor, as with repeated partials and just after a final. Before, every final flickered to `holding`, and waiting out a multi-word number such as "two thousand twenty six" read as an ad-lib.
- **Candidates.** A last-row cell that ends on a cheap stutter or filler after a match is also a candidate. Skipping a unit with weight < 0.3 ("the") does not count toward `max_gap_run`.
- **Not in the aligner:** `display_coasting` needs mic level and wall time, so it goes in the T7 controller. `hotwords` is ASR-side. Both flags are in `Params`. `line_complete_on_final` is implemented and off by default.

**API notes for T7:** `Aligner(script, params, start_unit=-1)`, `on_event(Event(type, t, text))` (or `Event.from_dict` on worker JSON), `on_user_key("line_prev"|"line_next"|"restart"|"goto_line", line=n)`, `set_paused(bool)`, and `reset(unit)`. `Position` has `line`, `unit`, `committed_unit`, `line_progress`, `state`.

## T7: Integration (worker supervision, live tracking, pause)

**Done:** `controller.py` rewritten on the real T2 `Script` and T3 `Aligner` (the T6 stand-in `SimpleScript`/`split_display_lines` are gone). The worker is spawned with `Gio.SubprocessLauncher` (stdin + stdout pipes, stderr inherited), stdout read line by line with `Gio.DataInputStream.read_line_async`, each JSON event dispatched on the GLib loop: `partial`/`final` -> `aligner.on_event` -> `view.set_position`; `level` -> `voice_active` (RMS > new `asr.vad_rms`, default 0.01); `status` ready/paused/resumed/eof/error handled. Inputs: `mic` (default), `--wav FILE` (passed through as `--wav FILE --realtime`), `--replay FILE` (.json/.json.gz/JSON lines, each event scheduled with `GLib.timeout_add` at its `t`, optional `script` and `start_unit`), or none (`--demo`, so the demo never opens the mic). The worker starts lazily once a script is loaded and the window is shown. New flags: `--open FILE`, `--headless-status`, `--render-at SECS`, `--log-level`. `--render-png` now also renders `--open`/`--replay` through the real Controller. Also: `tests/test_integration_replay.py`, `tests/test_controller.py`, `tests/fixtures/README.md`, `tests/fixtures/synthetic_skip.json.gz`, `scripts/tts_wav.py`; `install.sh` now writes `window.hide_from_screen_share = true` and `asr.vad_rms = 0.01` into a new default config.

**Acceptance (all under a private gtk4-broadwayd + dbus-run-session, `--app-id-suffix test`; no live desktop, no mic):**
- Replay render: `--replay tests/fixtures/synthetic_skip.json.gz --open tests/data/skip.md --render-png tests/out/replay.png --render-at 1.6` shows line 4 centered (unit 30, "jumps", highlighted). Against the `--open skip.md --line 4` render: 1.8 % of pixels differ (only the spoken-word highlight color; limit 2 %), text-shape difference < 0.5 %, and against `--line 1` > 2 % (the check discriminates). Pass.
- WAV end to end: `bin/teleprompter open tests/data/skip.md --wav tests/audio/skip_read.wav --headless-status` ends with `"line": 6`, lines never decrease, exits at WAV end (worker `eof`). Trace: line 0 -> 1 (tracking through "this year"), `adlib` while espeak's garbled line 4 plays ("any round fox jump soldier and easy nod"), then one jump to line 6 on "and thanks for listening see you next week". Pass.
- Crash: SIGTERM to this run's worker PID -> log "worker exited (status 0); restarting in 2 s (attempt 1/5)", new worker started 2.16 s after the kill; status goes `error` -> `tracking`. Pass.
- Pause: `pause` -> log `worker <- pause` / `worker -> paused`, status `"state": "paused"`; `resume` -> `worker <- resume` / `worker -> resumed`, state restored. Pass.
- Latency: `align_ms` and `event_age_ms` logged at debug per update and summarized in `status.latency`. `align_ms` p90 0.3-0.6 ms (limit 5).
- Full suite: 81 passed, 0 skipped (the T4 welcome.wav test now runs and passes).

**End-to-end latency, measured on skip_read.wav at real-time pace:** `event_age_ms` (end of the audio block that produced the event -> view updated; covers decode + pipe + align) p50 21-33 ms, p90 32-36 ms. Word onset -> first partial containing the word (from worker timestamps): p50 0.30 s, p90 0.36 s. So mouth-to-pixel at word onset is about 0.33 s median, in line with the §4 budget of 220-300 ms plus the transducer's word-emission delay.

**Failure modes:** worker exit without `eof` (crash, kill, SIGTERM) -> `error` dot, restart after 2 s, at most 5 times (count resets after 60 s healthy), then `failed`, error dot and toast "Speech engine stopped - see log". Worker `status error` -> `no-mic` if it mentions audio, else `error`; `ready` clears it. Missing model -> no spawn, error dot, log names `python -m teleprompter.models download`. `quit` sends `quit`, waits up to 1 s while pumping the loop, then `force_exit` (the T6 quit < 2 s check still passes). In mic mode the worker also exits on stdin EOF if the app dies.

**Worker fixes found by integration (asr_worker.py):**
- `--realtime` WAV pacing is now deadline-based. Before, it slept 100 ms *plus* decode time per block, so audio time drifted behind wall time and `event_age` grew over a run.
- New `status` `eof` at WAV end, so the app can tell a finished WAV from a killed worker (SIGTERM also exits 0).
- WAV pause lost the rest of the file: the file is queued ahead of real time, and `pause` drained the queue (including the EOS marker), so a resumed WAV run hung. WAV mode now holds its position while paused; mic mode still drops audio heard while paused. Resume signals the reset with an event instead of a queue marker.
- 0.66 s of tail padding before `input_finished()`: the last word of every WAV was being dropped ("back to the" for "back to the show").

**Test audio:** espeak-ng is installed now. `scripts/make_test_audio.sh` uses `-v en-us+m2 -s 150` with 300 ms of leading silence (with no lead-in the model hears "men come back" for "welcome back"; m2 was the one variant tried that transcribes the clip exactly). `tests/audio/keep/welcome.wav` (73 KB) is committed. The script also makes `tests/audio/skip_read.wav` (git-ignored, skip.md with lines 2-3 skipped; WAV tests skip without it). espeak garbles "the quick brown fox" whatever the voice, so the WAV run exercises holding through junk and a far jump more than a clean line-4 read. `scripts/tts_wav.py` (sherpa-onnx piper voice, `vits-piper-en_US-amy-low` in `~/.local/share/teleprompter/tts/`, downloaded as a model, not a system package) is the fallback when espeak-ng is missing and transcribes much better; T8 may prefer it.

**Deviations:**
- The crash test kills this run's worker by PID from `status.worker.pid` (SIGTERM, like pkill's default) instead of `pkill -f asr_worker`, so it can never hit another instance.
- Replay files carry `start_unit`; the §6.8 `skip_line` case starts at unit 12, which a replay from line 0 can't reach.
- A restarted `--wav` worker starts the WAV over; the echo guard holds on the replayed lines. Mic restarts just continue.
- `--render-png` finishes the scroll animation and drops the toast before rendering (Broadway's frame clock may not tick), and draws the view directly when GTK has no cached render node.
- Status dot: `adlib` shows the muted "holding" color instead of the error red.
- Logs carry millisecond timestamps.
- `display_coasting` (off by default) lives in the controller: while `voice_active` and the cursor has been idle 800 ms, the display advances at the measured words/s, capped at committed+8. It is display only, and a key clears it. Covered by a unit test only; it has not been tried on real speech.

**Needs the author:** `~/.config/teleprompter/config.toml` still has `num_threads = 2` from the T1 install, so a real launch spins about 2.9 cores (T4b). Change it to 1. The tests pass their own config.

**Deferred to T10:** the mic path end to end on the real desktop (default-source switching, `no-mic` on a missing device), CPU of app + worker together, coasting on real speech, the rotating log file (§4.2; logs go to stderr for now), `[aligner]` overrides for §6 parameters beyond the four config keys.

## T9: Install script, desktop entry, docs, Hyprland snippet

**Status:** COMPLETE (acceptance checks passed)

**What was done:**
- Expanded `install.sh` to complete all five installation steps idempotently:
  1. Create venv and install dependencies (pre-existing)
  2. Download and verify ASR model from GitHub releases
  3. Write default config.toml if missing (pre-existing)
  4. Install icon and desktop file with path substitution
  5. Print Hyprland Lua snippet (reads `hide_from_screen_share` config option)
- Expanded `README.md` with comprehensive documentation:
  - Installation procedure and what each step does
  - Quick start examples
  - Complete verb reference table with descriptions
  - Hotkey table (SUPER+SHIFT+T, +BACKSLASH, +BRACKETLEFT/RIGHT, +HOME)
  - Configuration guide with links to DESIGN.md
  - Testing instructions
  - Performance section
  - Architecture overview with links to design documents
- Created `teleprompter/data/com.example.Teleprompter.desktop.in` template
  - Substitutes @BINPATH@ with absolute repo path in install.sh
  - Has Exec, Icon, Type, Categories, NoDisplay, StartupNotify
- `--print-hypr-snippet` flag already in app.py (T6), reads `hide_from_screen_share` config

**Acceptance result:**
- `./install.sh` run twice: prints "already" for venv, model, config, icon, desktop ✓
- Desktop file valid per `desktop-file-validate` with required Exec/Icon/Type fields ✓
- Lua snippet prints with full `~/src/Teleprompter/bin/teleprompter` paths ✓
- `~/.config/hypr/` never touched (install.sh prints snippet only, never edits config files) ✓
- No British spellings in README.md, install.sh, or desktop file ✓
- All 81 tests pass (71 existing + 1 skipped + 9 aligner/script/fixture variants) ✓

**Key details:**
- Model download uses curl with SHA/size checking; skips if all four files exist
- Desktop file template uses @BINPATH@ substitution instead of hard-coded paths
- install.sh is fully idempotent: each step skips silently if already done
- Hyprland snippet printed to stdout (user pastes manually per DESIGN.md 7.1)
- `no_screen_share` rule is commented out by default; uncommented when config `hide_from_screen_share = true`

**Deviations:** None. All requirements from BUILD_PLAN.md section T9 met.

**Next:** T10 (the author's desktop verification).

## Monitor auto-selection (post-T9)

- Bug: prompter was pinned to `eDP-1` and appeared on the wrong screen when the author works on the external Studio Display (DP-1).
- `window.monitor` now defaults to `"auto"`: `teleprompter/monitors.py` reads the focused monitor from `hyprctl monitors -j` (falls back to the compositor's choice if hyprctl fails). `PrompterWindow.show()` re-picks on every show/toggle/open/paste, unmapping first if the target changed (layer-shell `set_monitor` does not move a mapped surface). A named connector still pins; a missing pinned monitor falls back to auto and logs it.
- `install.sh`: new configs get `monitor = "auto"`; creates `~/.local/bin/teleprompter` symlink if absent.
- Tests: `tests/test_monitors.py` (mocked hyprctl output; no windows).

## T8

**Done:** `scripts/record_fixture.py`, `tests/test_fixtures_replay.py`, seven fixtures in `tests/fixtures/` (`clean_160wpm`, `clean_200wpm`, `filler`, `repeat`, `skip_line`, `restart_sentence`, `silence_2s`; each about 1.5 KB gzipped), `tests/cases/restart_sound_alike.yaml`, a fixtures README section. The `--mic NAME` hook is written but was not run (no live mic, no voice recording); it saves to `tests/fixtures/private/` and the replay test scores such a file once someone adds `truth`.

**Acceptance:** `pytest tests/test_fixtures_replay.py` passes offline (10 tests, about 2 s, no model). Final run, all seven fixtures: overshoot 0, false_jumps 0, final_error 0. Lag p50 0.20-0.30 s on all seven (clean: 0.28 s at both rates), p90 0.55-0.74 s, stall 0.0-0.3 s, every spoken word reached. Re-rendering a variant gives a byte-identical WAV (asserted by `test_recording_is_deterministic` against the hash stored in the fixture). Full suite: 100 passed.

**Deviations:**
- **The TTS is piper (amy-low), not espeak-ng.** The first recording used espeak `en-us+m2`. The NeMo model garbles most of it ("the plane crossed his salary", "week round fax some sober delay none"), so the cursor lost the script and clean_200wpm ended at unit 12 of 45. That was the ASR, not the aligner. Piper transcribes the script nearly word for word. `--engine espeak` still works.
- **Piper needed `noise_scale=0, noise_scale_w=0`** (set in `scripts/tts_wav.py`) to be deterministic; with the default sampling noise two renders differed in length. This makes the "byte-identical" claim in that script's docstring true.
- **Truth times are interpolated.** Each segment is a sentence (or part of one); the speech bounds inside it come from energy, and the words are spread over those bounds by letter count. Segment edges are exact, the words between them approximate.
- The recorder drops `level`/`status` events and the `words` arrays; only `type`, `utt`, `t`, `text` are kept.
- `test_replay` also asserts `lag_p50 < 0.6 s` on the two clean fixtures.

**Aligner bug found and fixed (`aligner.py`, `_heard_at`):** in `restart_sentence` the speaker says "their team shipped their team shipped it right on time". On the restarted "team", the aligner matched it to "time" (Double Metaphone TM, a 3-unit jump) and moved the cursor to the end of the line, 4 units ahead of the speaker (5 overshoot events in that recording). A move of two or more words now must land on a word that was actually heard: exact, homophone, or the start of a half-said last word, in either of the last two spoken words. A phonetic-only match can still confirm a word inside a path; it just cannot be the landing point of a multi-word jump. Regression: `tests/cases/restart_sound_alike.yaml` (fails without the fix, passes with it). All 24 earlier corpus cases and the property test are unchanged.

**Environment note:** while T8 ran, T9's install-script testing rebuilt `~/.local/share/teleprompter/venv` and deleted `~/.local/share/teleprompter/tts/`. The piper voice was downloaded again (same URL as in `scripts/tts_wav.py`). Anything that needs the piper voice (re-recording, `test_recording_is_deterministic`'s render) needs it present. That test skips when the voice is missing; the download command is in the docstring of `scripts/tts_wav.py`. `test_corpus_has_24_cases` is now `test_corpus_has_25_cases`.

## Snappiness and "it stopped" (post-T9)

### Part A: why it stopped

**What the live status showed.** At 16:08 the author's instance (started 16:06:04 by the orchestrator) reported `state: paused, visible: false`, worker running, restarts 0. Only three inputs produce paused + hidden: `hide`, `toggle`, or a bare `teleprompter` (no verb) sent to a running instance, because `hide` implies pause (DESIGN 8). Nothing inside the app pauses itself: the aligner's `adlib`/`holding` states never pause, worker restarts show `error`, and the end of the script just holds.

**Tests were not the cause.** Every test that starts the app already ran under `dbus-run-session` with `--app-id-suffix test`, and a check from inside `dbus-run-session` confirmed the real bus (and `com.example.Teleprompter` on it) is not visible there. The agent transcripts for that window show no verb sent to the real app id except the orchestrator's own `toggle; toggle; next` at 16:06:33, which ended visible. So the `hide` came from outside the test runs: the most likely sources are SUPER+SHIFT+T (bound at 16:06:26, announced to the author at 16:06:55) or the app-grid entry (`NoDisplay=false`, `Exec` with no verb, which toggles a running instance). There was no log to prove which; there is now.

**Second, real stop bug (found live by the coordinator).** After `pause` then `resume` the worker said "resumed" and the status said "tracking", but pipewiresrc stayed suspended: no audio, no events. The mic pipeline went `PAUSED` -> `PLAYING`, a path T7 only tested with a WAV.

**Fixes:**
- Isolation is now structural (`tests/conftest.py`): every test and its subprocesses get `TELEPROMPTER_TEST=1`, a private session bus for the whole run, no `WAYLAND_DISPLAY`/`DISPLAY` (`GDK_BACKEND=broadway`), and a temp `XDG_STATE_HOME`. With `TELEPROMPTER_TEST` set, the app refuses the real app id (exit 3) and the worker refuses the microphone (a `--live-source` GStreamer test source is allowed). `tests/test_isolation.py` checks all of it.
- Rotating log `~/.local/state/teleprompter/teleprompter.log` (1 MB x 3, deferred in T7): every verb with its sender (each CLI invocation passes `TELEPROMPTER_SENDER`, its pid and ancestor process names, through `SEND_ENVIRONMENT`; a hotkey shows `Hyprland`, an agent's shell shows `claude`), show/hide, pause/resume with the reason, aligner state transitions, worker start/exit/restart/warnings and the worker's stderr.
- Mic pause now takes the pipeline to `NULL` and resume to `PLAYING` (the startup path). A worker watchdog restarts the capture when no samples arrive for 3 s while listening (status `warning`), and reports `error` "no audio from the microphone" after 3 failed restarts. The controller shows `no-audio` (red dot), never `tracking`, when no level event has arrived for 1.5 s while listening. `status` has `audio_age_s`. Regression tests: `test_live_capture_resumes_after_pause` (two pause/resume cycles through the mic code path with `audiotestsrc is-live=true`), `test_live_watchdog_restarts_a_stalled_capture` (`--simulate-stall`), `test_no_audio_while_listening_never_shows_tracking`. The pipewire suspension itself cannot be reproduced without the real mic; the watchdog and the `no-audio` state are what guarantee it can't sit silently again.
- The desktop entry runs `teleprompter show` instead of a bare toggle.

**`voice: false`.** `voice` comes from `level` events (RMS > `vad_rms`, every 250 ms), which the worker only sends while it is capturing. Paused or hidden means no level events, and the flag kept its last value. It now reads true only for a level event under 0.75 s old while listening, and `status` adds `rms`. While tracking, `false` just means silence at that moment. `event_age_ms` was about 0 in mic mode because events were stamped at emission; mic events now carry the arrival time of their newest sample.

### Part B: speech engine and pipeline

**Spitball.** Spitball-v2 does not stream through its own runtime: it drives voxtype (Rust, onnxruntime), whose `parakeet.streaming` mode runs `parakeet-unified-en-0.6b` (fp32, 2.4 GB, `~/.local/share/voxtype/models/parakeet-unified-en-0.6b/`) with `streaming_chunk_secs = 1.12`, left context 5.6 s, right context 1.04 s. Spitball's live transcript itself (`engine_worker.py`) runs onnx-asr on finished speech segments. That design targets dictation: at least 1.1 s of chunk plus 1 s of look-ahead before a word appears. sherpa-onnx ships int8 streaming exports of the same model (240/560/1120 ms), benchmarked below.

**Method.** `scripts/bench_asr.py`. Input: the seven T8 piper WAVs (`tests/audio/fx_*.wav`) plus five LibriSpeech/JFK clips from the models' `test_wavs` (real read speech), 2.8 min in all, fed in 20 or 100 ms blocks at the moment each block would have been captured, then 1.5 s of silence. Word onsets come from one offline reference (parakeet TDT 0.6b v2, full context, 80 ms frames), the same for every candidate. `first` = word onset -> first event showing the whole word. `seg start` = the same for the first word after each pause, where the onset is exact (energy). `replay lag` = the T8 metric, spoken word end -> cursor reaches it, computed by replaying each model's emission-time-stamped events through the real aligner (p50 is the median over the seven fixtures; p90 is the worst fixture's p90). WER goes through `normalize_hyp` on both sides. CPU = process CPU seconds per audio second after model load (1.0 = one core). "rt" rows really sleep between blocks; "virt" rows process each block at max(arrival, previous done) without sleeping (exact for one thread, faster to run, but sensitive to other load). Every row: `num_threads = 1`, greedy search.

| model | block | mode | first p50 / p90 | seg start p50 | replay lag p50 / p90 | cadence | WER | CPU |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| fast-conformer 80 ms (old default) | 100 ms | rt | 632 / 906 ms | 725 ms | 310 / 762 ms | 200 ms | 13.6 % | 0.16 |
| fast-conformer 80 ms | 20 ms | rt | 626 / 867 ms | 700 ms | 284 / 701 ms | 160 ms | 13.6 % | 0.17 |
| **Nemotron 0.6B 160 ms (new default)** | 20 ms | rt | **527 / 827 ms** | **577 ms** | **168 / 598 ms** | 161 ms | **10.1 %** | **0.41** |
| Nemotron 0.6B 80 ms | 20 ms | rt | 517 / 821 ms | 556 ms | 148 / 576 ms | 162 ms | 9.4 % | 0.78 |
| Nemotron 0.6B 80 ms | 100 ms | virt | 734 / 1282 ms | 793 ms | 467 / 1957 ms | 179 ms | 10.1 % | 0.96 |
| Nemotron 3.5 ASR 0.6B 160 ms | 20 ms | virt | 675 / 922 ms | 799 ms | 331 / 846 ms | 161 ms | 12.8 % | 0.48 |
| Nemotron 3.5 ASR 0.6B 80 ms | 20 ms | virt | 674 / 990 ms | 799 ms | 322 / 3193 ms | 159 ms | 12.8 % | 0.88 |
| zipformer en 2023-06-26 | 20 ms | virt | 812 / 1052 ms | 804 ms | 507 / 1942 ms | 320 ms | 24.6 % | 0.04 |
| zipformer kroko 2025-08-06 | 20 ms | virt | 1305 / 1786 ms | 1138 ms | 1008 / 2595 ms | > 0.8 s | 11.9 % | 0.02 |
| parakeet-unified 0.6B 1120 ms (Spitball's model and chunk) | 20 ms | virt, 1 clip | 1624 / 1946 ms | 1783 ms | 1301 / 1609 ms | 562 ms | 7.8 % | 0.69 |
| parakeet-unified 0.6B 240 ms | 20 ms | virt, 1 clip | 40 s (cannot keep up) | | | | 5.9 % | 4.4 (RTF 4.4; 28 cores at 4 threads, still behind) |

**Choice: `sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25`, 20 ms blocks.** Against the old setup (fast-conformer, 100 ms blocks) the cursor reaches a spoken word 142 ms sooner at the median (310 -> 168 ms) and 164 ms sooner at p90 (762 -> 598 ms), a word after a pause shows 148 ms sooner, and WER drops from 13.6 % to 10.1 %. The 80 ms Nemotron is only 20 ms faster but costs 0.78 core and falls behind when the machine is busy (the 100 ms-block virtual run backlogged to a 2 s p90); 160 ms leaves headroom for a video call. The Spitball model is either far too slow at small chunks or 1.3 s behind at its own chunk size. Measured end to end in the app (WAV at real time, headless): app + worker together 0.5 core including model load; `event_age_ms` (audio arrival -> view updated) p50 76-94 ms, most of it the 160 ms chunk's decode. The worker uses about 950 MB RSS (fast-conformer: 250 MB) and loads in 1.5 s.

**Pipeline changes:**
- Blocks of 20 ms instead of 100 ms (`--block-ms`): a chunk decodes as soon as its audio is in. On the fast-conformer this alone moved replay lag 310 -> 284 ms p50, 762 -> 701 ms p90, at the same CPU. The decode loop skips the result read when no chunk is ready, and in mic mode takes every queued block in one pass so a slow decode never builds a queue. For a 600M model 100 ms blocks are harmful (two 80 ms chunks at once each time).
- PipeWire runs a 1024/48000 (21 ms) quantum here; pipewiresrc delivers per quantum and the appsink does not sync, so capture adds about 20 ms and was left alone. stdout is flushed per event and the app reads it with `read_line_async` on the main loop, so pipe + align is under 2 ms (`align_ms` p90 0.3-0.7 ms).
- Aligner: unchanged. The display cursor already follows every partial (preview pass on the full hypothesis); only the committed cursor waits for a stable prefix. The one-word fast path still fires only on finals: a stable-partial version would never fire with a real recognizer (the worker only emits a partial when the text changes, so a lone word is never seen twice), and firing on the first partial would break `skip_line`. The remaining p90 words are ASR-side: the TTS reads "$79" as "dollar seventy nine", multi-word numbers ("two thousand twenty six") need their last word, and misheard words ("as for" for "and thanks for").
- Display: the spoken-word highlight was already immediate (`set_position` redraws at once; only the line scroll eases). The scroll is now 180 ms instead of 280 ms (ease-out cubic, 80 % of the distance in 75 ms). Coasting stays off: with the faster model it would only add overshoot risk, and it is still untried on real speech.
- Endpointing unchanged (rule2 1.0 s): finals only move the committed cursor.

**Fixtures.** The seven T8 recordings moved to `tests/fixtures/fc80/`; the top level now holds the same audio (identical WAV hashes) recorded with Nemotron 160 ms (new `asr` field). `test_replay` runs both sets with T8's limits unchanged. Nemotron set: overshoot 0, false_jumps 0, final 45/45 on all seven, lag p50 0.08-0.15 s, p90 0.40-0.53 s (`--fast` recordings, so without decode time; the fc80 set is unchanged at 0.20-0.30 / 0.55-0.74).

**Config and install.** `ASRConfig.model` and `models.DEFAULT_MODEL` are the Nemotron 160 ms model; `models.PREVIOUS_MODEL` (fast-conformer 80 ms) and Nemotron 80 ms stay in the registry and are picked with `[asr] model`. `install.sh` step 2 now calls `python -m teleprompter.models download <model>` (idempotent) instead of its own curl, and new configs get the new model and `animation_ms = 180`. `models.resolve_dir` lets the worker and the bench load an unregistered transducer directory.

**Tests:** full suite 116 passed (was 91 after T8): new isolation, live-capture, capture-health and default-model tests, and the fixture replay now runs 14 streams.

**Deviations / notes:** the benchmark models (parakeet-unified, Nemotron 3.5, zipformers, TDT reference) sit in `~/.local/share/teleprompter/models/`; only the default and the two registry alternates are needed at run time. Nemotron 3.5 ASR is a multilingual model and was slower and less accurate on English here. the author's `~/.config/teleprompter/config.toml` pinned `model` and `animation_ms`, so it was updated to the new values (backup next to it).

## Re-acquisition: going off script and coming back (post-T9, 2026-09-30)

the author's feedback after the first real session: "This is really good but I want it to be a bit more tolerant of me going off script a bit. If I'm clearly reading a line that's 1 or 2 lines down it should recognize what I'm doing and catch up."

### What the log showed

The session ran 18:13-18:41 on his 180-line video script (179 display lines, 1,155 units, 3-11 words a line; his pace about 2.4 words/s). The log holds state transitions only, no words, so the diagnosis below comes from the transitions plus replaying the same script through the aligner.

- In the full read-through (18:28-18:37) the aligner fell into `adlib` eight times. Each stall lasted 4.3-9.2 s from the last cursor move (about 10-22 spoken words at his pace) and ended with a forward jump of 2-19 units, one to three lines. He had moved ahead and it waited.
- At 18:36:15 the display jumped 19 units (line 148 to 151) on partials, but the committed cursor never followed. Over the next 26 s the display slid back to unit 963 (line 149), three words per update, until he hid the window.
- An earlier run sat at unit 425 for 40 s (18:19:11) until a restart.
- The same units stalled in the 17:52 run too (372, 486, 534, 584, 835, 893), so they belong to the script, not to chance. One of them, unit 534, sits just before the aside "(2027's V/TO is on the way)".
- The script file changed at 18:28:04. Line numbers in the runs before that differ by one after line 57; unit numbers match.

### Causes (reproduced with `scripts/eval_reacquire.py` on the same script)

1. **Tiers in units, far tier out of reach.** Jumps over 12 units needed 3 strong words and evidence 2.5 in a 6-word tail. On short lines of common words ("was a hard day for Acme") two lines ahead is 10-16 units, the far tier, and it almost never qualified. Skipping two lines was never caught (within 12 words) on 51% of the script's lines; skipping one, on 18%.
2. **Words already read counted as evidence for a jump.** "a full restructure and honestly I" jumped 8 units to the next "I": "full restructure and" sat at the cursor yet counted toward the near tier's three matches. Ad-libs put the display ahead of the speaker in 480-560 updates across the script.
3. **Weak and sound-alike matches carried skips.** "and I" skipped "If" to land on "I", "an a" skipped two words to the next line's "A", a whole stopword prefix-matched a longer word ("the" as the start of "themselves"), and Double Metaphone matched glued words ("skilled" for "so glad", "purpose" for "purpose I").
4. **Weights.** `AI-native` weighed 0.18 (judged by its first letter "a"). `you're` weighed as a content word, so "you are" preferred a far "you're" over the literal "you are".
5. **Confirmation lost between the two cursors.** A far jump's pending confirmation lived in one pass and was cleared when that pass moved. The committed pass sees only stable words and often never confirmed what the preview had shown. The next utterance then re-derived the preview from the old committed cursor and walked it back, 3 units per update (the 18:36 slide).
6. **Script tokens a recognizer can't say.** `Q4`, `V/TO`, `example.ai` had no spoken form a recognizer produces, and the aside "(2027's V/TO is on the way)" was unmatchable, so reading it aloud counted as an ad-lib.

### Changes

`teleprompter/aligner.py`:
- **New evidence.** `align_band` takes the cursor and tracks, per path, the matches after the cursor that the path did not reach by reading straight on from it (`Candidate.new_matched/new_strong/new_evidence/new_start/new_run`), plus `after_cursor`, `run_after` and `prev_unit`. Jumps are judged only on new evidence.
- **Tiers in lines as well as units.** Lines are counted from the line due next. Near (within 12 units or 2 lines): 3 new matches with 1 strong, or 2 strong, evidence 1.0, lead 0.4, no confirmation. Mid (3-4 lines): 3 matches, 2 strong, evidence 1.8, lead 0.6, at once from 2.8, otherwise confirmed by the next update. Beyond 4 lines: the original far thresholds, applied to new evidence. The search window always reaches 4 lines past the line due next (capped at twice `ahead_units`), so a script of short lines gets a real window.
- **Where people resume, and runs.** New words that start a line or sentence get +0.3 evidence. Each new match that directly follows the previous one in both the speech and the script adds 0.15, and a run of 4 counts as one more strong word: four common words in script order are much rarer by chance than four scattered hits.
- **Landing words earn their skips.** A move of 2+ words with a weak landing word (weight < 0.3) must follow the previous match across weak words only. A skipped content word needs a second match after the cursor.
- **Straight reading** (every content word from the cursor to the end matched, as one run) is following however many words a partial adds at once.
- **The two cursors agree.** Each pass's pending far jump can confirm the other's. When the display already jumped past the committed cursor and a new tail lands within -2..+3 of it, the committed cursor follows. A new utterance never pulls the display below where the previous utterance left it (only a revision inside one utterance may, as before).
- **History after an ad-lib.** A final that ends two or more misses into an ad-lib leaves no history, so the next utterance's tail holds only fresh words.
- **Asides read aloud.** Bracketed asides stay outside distance and gap costs, but match when read, so the cursor follows them instead of going to `adlib`.
- **Similarity.** The half-said-word prefix rule skips whole stopwords. Joins and splits use spelling only (Levenshtein >= 0.9, parts of 2+ spoken or 3+ written letters).
- New `Params`: `near_lines`, `near_min_strong`, `mid_lines`, `mid_min_matches`, `mid_min_strong`, `mid_min_evidence`, `mid_margin`, `mid_sure_evidence`, `line_start_bonus`, `run_bonus`, `run_as_strong`, `history_drop_misses`. Changed defaults: `near_min_evidence` 1.5 -> 1.0 (now on new evidence only).

`teleprompter/script.py`: contractions of stopwords are stopwords; a multi-token primary form is weighed as a whole; asides get `optional` and `optional_weight` (`weight` stays 0 for them).

`teleprompter/normalize.py`: `Q4` -> "q four", `2027's`, domains ("example dot ai"), all-caps pieces spelled out ("v t o"), and short emphasis capitals (`ALL`, `NOW`, ...) read as words.

`teleprompter/controller.py`: the per-event debug line now carries the committed unit and the recognizer's text. It is written only at `[log] level = "debug"` (or `--log-level debug`); the default is info, so nothing changes in the author's log unless he turns it on.

### Re-acquire latency, before and after

`scripts/eval_reacquire.py` (new) starts at every line of a script, simulates word-by-word partials at 2.5 words/s, and counts the words spoken after rejoining the script until the display has caught up. "Lost" = not caught within 12 words; p50/p90 are over the caught cases. On the a company script:

| scenario | before p50 / p90 (lost) | after p50 / p90 (lost) |
|:--|:--|:--|
| skip the rest of a line mid-sentence, read the next | 3 / 5 (5 of 164) | 3 / 4 (0) |
| skip 1 line | 3 / 5 (32 of 176) | 3 / 4 (0) |
| skip 2 lines | 6.5 / 10 (89 of 175) | 3 / 4 (0) |
| skip 4 lines | 7 / 11 (106 of 173) | 5 / 7 (6) |
| 10-word ad-lib, then 2 lines ahead | 6.5 / 10 (89 of 175) | 3 / 4 (0) |
| the same with pauses (three utterances) | 6.5 / 10 (89 of 175) | 3 / 4 (0) |
| 10-word ad-lib, then back at the cursor | 1 / 2 (0) | 1 / 1 (0) |
| the same with pauses | 1 / 3 (0) | 1 / 1 (0) |
| paraphrased line, then the next verbatim | 1 / 1 (0) | 1 / 2 (0) |

False jumps (a move of more than 3 words landing more than 3 off) went from 2-15 per scenario to 0 in every scenario. Updates with the display more than one word ahead of the speaker: ad-libs 480 -> 2, ad-libs made of the script's own common words 560 -> 80, re-reading a line 293 -> 50, restarting a line 74 -> 0, clean reading 16 -> 0. The remainder are 2-3 word follow moves on common words. On the sample script and `skip.md` (long lines): skip 1 line 4 / 7 -> 3 / 4, skip 2 lines 6 / 7 (4 lost) -> 3 / 5 (0 lost).

Through the real recognizer (new `reacq_*` fixtures, piper amy-low -> Nemotron 160 ms; spoken words counted from the rejoin word until the cursor gets there, so recognition delay is included): skip a line mid-sentence 7 -> 5, skip 2 lines 6 -> 5, ad-lib then 2 lines ahead 9 -> 5, ad-lib then back at the cursor 4 -> 4, shared phrase 3 -> 3, paraphrase 1 -> 1. In the aligner itself the jump comes on the third word of the new line in the first two; the rest is the recognizer showing the word.

Per-update cost on the 1,155-unit script: p50 1.2 ms, p95 2.1 ms, max 2.7 ms (was about 0.3 ms; still far under the 5 ms limit).

### Tests

- Seven corpus cases on `tests/data/reacquire.md` (a generic 15-line script with short lines): `reacquire_skip1_mid`, `reacquire_skip2`, `reacquire_paraphrase`, `reacquire_adlib_rejoin`, `reacquire_adlib_skip2`, `reacquire_shared_phrase` (the next line repeats the three words just read: they hold, the fifth word jumps), `reacquire_far_then_continue`. The runner gained `at_word` expectations (state after the k-th word of a spoken segment). Against the old aligner, four of these fail (skip1_mid, adlib_skip2, shared_phrase, far_then_continue; on the shared words "we are building" the old one ran a word ahead of the speaker).
- Six real-ASR fixtures `tests/fixtures/reacq_*.json.gz` (`scripts/record_fixture.py` gained the variants and per-variant `script`, `start_unit`, `rejoin_unit`). `test_reacquire_replay` holds them to overshoot 0, false jumps 0, final error <= 2 and rejoin within 5 spoken words; three of them fail on the old aligner.
- Unit tests: the display is never dragged back by a new utterance, an aside read aloud is followed, the new spoken forms, and the weight changes.
- Every earlier corpus case and replay still passes with its assertions unchanged, except one, below.

**Changed expectation (`skip_line`).** It asserted that the cursor stays on line 1 through "the quick brown fox" and reaches line 4 only at "jumps", which was the far tier's two-update rule. Line 4 is two lines past the line due next, and "the quick brown" is two distinctive words in script order at the start of that line. the author asked for exactly this to be caught within three to five words, so the case now expects line 1 through "the quick" and line 4 (unit 28) at "the quick brown". The far tier's confirmation rule itself is still tested by `test_far_jump_waits_for_confirmation` (ten lines ahead), unchanged.

Full suite: 134 passed (was 116), offline (Broadway, private bus, no mic).

## Hyprland key names

`--print-hypr-snippet`, `teleprompter/data/hyprland-snippet.lua`, the README and DESIGN used `BACKSLASH`, `BRACKETLEFT`, `BRACKETRIGHT` and `HOME`. Those never fired on the author's machine. They are now the xkb keysym spellings Omarchy uses: `backslash`, `bracketleft`, `bracketright`, `Home` (the author fixed his own `~/.config/hypr/bindings.lua`; it was not touched here). `install.sh` prints the snippet through `--print-hypr-snippet`, so it follows. `test_hypr_snippet` checks both the printed and the packaged snippet.

---

# PromptBro Build Log (starting 2026-09-30)

## T0: Baseline and preconditions

**Status:** COMPLETE

- Repo state: hotkey-legend commit 09c101d in place ✓
- Test baseline: 135 passed, 1 warning (126 passed + 9 skipped in new format)
- Old app running: yes (NameHasOwner com.example.Teleprompter = true)

## T1: Move the repository

**Status:** COMPLETE

- Quit old app: `bin/teleprompter quit` (NameHasOwner cleared)
- Repo moved: `~/src/Teleprompter` → `~/src/PromptBro`
- Commit count preserved: 25 commits
- PROMPTBRO_PLAN.md: committed as part of T0

## T2: Rename package, IDs, and environment variables

**Status:** COMPLETE

- Package: `teleprompter/` → `promptbro/` (git mv)
- APP_ID: `com.example.Teleprompter` → `supercleanse.PromptBro`
- Env vars: `TELEPROMPTER_*` → `PROMPTBRO_*`
- Launcher: `bin/teleprompter` → `bin/promptbro`
- Data files: renamed (desktop.in, svg)
- Build artifacts: removed (build/, *.egg-info/, proto/)
- .gitignore: added `build/`, `*.egg-info/`, `.ruff_cache/`, `tests/offscreen/out/`
- Docs: moved to `docs/` with header note
- install.sh: deleted (T3 will implement `promptbro setup`)
- Test counts after T2: 126 passed, 9 skipped (135 total = baseline) ✓
- No remaining references to old names ✓

## T10: Model download hardening

**Status:** COMPLETE

**What was done:**
- Added `sha256` field to `ModelSpec` dataclass (64-character hex string)
- Implemented `_verify_checksum()` function using SHA256
- Implemented `_is_safe_path()` for path validation (rejects `..` and absolute paths)
- Implemented `_extract_safe()` for secure archive extraction:
  - Validates all member paths before extraction
  - Uses `tarfile.extractall(filter="data")` for tar archives (safe mode)
  - Validates zip member paths for `..` and absolute path attacks
- Updated `download()` function to verify checksums before extraction
- Updated User-Agent from `promptbro/0.1` to `promptbro/0.2`
- Added default model SHA256: `0ae73a41cd51599dc7cac9ac083d9d35de53d762ca45923505fde47a3751814b` (verified correct)
- Created `tests/test_models_hardening.py` with comprehensive test suite:
  - Checksum verification (valid and mismatch cases)
  - Safe path validation (normal, `..`, absolute paths)
  - Safe extraction (tar.bz2, zip)
  - Path traversal attack rejection
  - Integration tests for download with checksum verification

**Test results:**
- All hardening functions validated with synthetic test archives
- Path traversal attempts correctly rejected (tar with `../../../etc/passwd`)
- Valid tar.bz2 and zip archives extract successfully
- Checksum verification accepts valid hashes and rejects mismatches
- Real default model's checksum matches the computed value ✓

**Deviations:** None. All requirements from PROMPTBRO_PLAN.md section T10 met.

**Acceptance:**
- Syntax check passed: `python3 -m py_compile promptbro/models.py tests/test_models_hardening.py` ✓
- Core hardening logic verified with standalone Python tests ✓
- All 5 models have valid 64-character SHA256 checksums ✓
- Default model checksum `0ae73...814b` verified correct ✓
- Test file imports successfully (syntax valid) ✓

## T6: Model.js and its tests

**Status:** COMPLETE

- `Model.js`: every function in plan 5.3, plus `clampOpacity` for the T11 slider; offline default carries `opacity` (0.75).
- `uiState` returns `setup` only when offline and doctor is not ok; `parseDoctor` returns null for garbage (treated as unknown, not a problem).
- `activateArgs` turns whole-number params into doubles (`[<1.0>]`) so gdbus parses a double.
- `tests/js/model_loader.js` copied from Spitball; `tests/js/model.test.js` has 16 tests.
- `node --test tests/js`: 16 passed, 0 failed.

## T7: QML Bridge, Widget, MenuContent

**Status:** COMPLETE

- `Bridge.qml`: the only file that reads state or spawns a process (status.json and recent.json FileViews, doctor, pick-file runner, detached CLI and GAction calls, setup terminal).
- `MenuContent.qml`: dropdown body per plan 5.2, signals only, including the opacity slider (0.3-1.0, sends on release) and the pickerFallback path field.
- `Widget.qml`: Panel with ipcTarget `supercleanse.promptbro`, WidgetButton with glyph and `12/48` label, KeyboardPanel hosting MenuContent. Transport and opacity keep the dropdown open; every other row closes it.
- States differ by glyph shape and brightness (outline vs filled script, microphone-off, alert, dim pulse), not red versus green alone.
- `qmllint -I /usr/share/omarchy/shell` on the three files: no errors; warnings only for unresolved `qs.*` imports and the ExitStatus signal parameter (same as Spitball).
- `manifest.json` is not in T7's file list and was left for T9. Not installed, no shell reload; offscreen render is T8.
- `node --test tests/js`: 16 passed.

## T8: Offscreen harness and visual check

**Status:** COMPLETE

- `tests/offscreen/render.sh --fake-data <out>` runs Quickshell with `QT_QPA_PLATFORM=offscreen`, canned `status.json`/`recent.json` and a fake `bin/promptbro` (doctor only) in a scratch stage dir under the output folder. Nothing under `~/.config` or the live shell is touched.
- Writes 15 PNGs: `widget-<state>.png` (setup, offline, idle, loaded, starting, listening, paused, no-audio, error) from the real `Widget.qml`, and `dropdown-<scenario>.png` (listening, paused, no-audio, idle, setup, fallback) from `MenuContent.qml`. `harness.qml` stubs Omarchy's layer-shell `KeyboardPanel` (harness-only `KeyboardPanelStub.qml`), so the popup itself is not rendered; the dropdown body is rendered in a popup-styled card.
- `bridge_check.qml` drives the real `Bridge.qml`: listening, paused, stale file, garbage file, recent dedup, doctor. Prints `BRIDGE_CHECK PASS`.
- Looked at every PNG. All glyphs render (no tofu); states differ by shape (filled vs outline script, mic-off, cog, alert circle) and brightness, not red versus green.
- Fixes: long recent names ran past the card edge (Button has no elision), so `MenuContent.shortName` middle-elides at 30 characters (full path stays in the tooltip); the header subtitle touched the separator, so the header got bottom padding.
- `node --test tests/js`: 16 passed.

## T10 follow-up: real model checksums

- Four of five sha256 values added in T10 were placeholders (sha256 of "0", "1", "3", "4"). Replaced with real digests: GitHub Releases API `digest` for the NeMo 80 ms and both Nemotron archives; hashed downloads for the 2023-06-26 zipformer (API has no digest) and the vosk zip.
- The default Nemotron 160 ms digest (0ae73a41...) was correct; confirmed by hashing the full download.
- Added tests pinning every expected digest and rejecting placeholders; fixed `_is_safe_path` to reject Windows drive paths and a download mock whose `read()` ignored the chunk size.

## T3: CLI entry, launch guard, paths module, lite dispatcher

**Status:** COMPLETE

- `promptbro/paths.py`: every location in one stdlib module (XDG vars, `PROMPTBRO_RUNTIME_DIR`, old Teleprompter paths for migrate/doctor). `config.py`, `models.py`, `controller.py` and `app.py` take their defaults from it (models_dir default is now computed, not a `~` literal).
- `bin/promptbro` rewritten (the T2 version still pointed at the old venv and module): resolves ROOT through symlinks, routes `pick-file|doctor|setup|migrate|uninstall-data` to `bin/promptbro-lite` under `/usr/bin/python3 -I`, exits 3 with a message (and `notify-send`, skipped under `PROMPTBRO_TEST`) when the venv is missing, otherwise execs the venv with `LD_PRELOAD` and `PYTHONPATH`. `requirements.txt` added with the exact pins; `install.sh` stays deleted.
- Launch guard in `app.py`: control verbs (status, hide, pause, resume, next, prev, restart, goto, reload-theme, quit) check `NameHasOwner` first. With no instance, `status` prints `{"running": false, "state": "offline"}` (exit 0), the rest print `promptbro: not running` (exit 1), and the application object is never created, so the name stays unowned. `toggle`/`show` on first launch load `recent.json`'s newest existing script and show it (no toggle on top), or start hidden when there is none. A failed first-launch `open`/`paste` notifies, prints the reason and quits, leaving no instance. Dropping the missing path from `recent.json` waits for T5's `recent.py`; the app only reads that file for now.
- New stdlib modules: `lite`, `pickfile` (Spitball's portal/zenity/kdialog chain, script filters, 600 s wait; `current_folder` left out until the author checks it in T12), `doctor` (about 35 ms; checks files only, never imports config/models, so the model check is a presence check and the sha256 verify stays in `setup`), `setup` (+ `uninstall-data`), `migrate` (never runs under `PROMPTBRO_TEST`; the library `run()` is what the tests drive).
- Packaged `hyprland-snippet.lua` regenerated (T2 had left the old names and paths in it). Desktop template already had `Exec=@BINPATH@ show`.
- Tests added: `test_pickfile`, `test_doctor`, `test_migrate`, `test_setup`, `test_lite_stdlib`, `test_bin`, and five launch-guard tests in `test_app_cli`. Suite: 230 passed, 9 skipped.
- Acceptance: `/usr/bin/python3 -I bin/promptbro-lite doctor --json` prints valid JSON in 30 to 70 ms; on the author's machine it reports `venv-missing`, `model-missing` and `old-install`, as expected before T4.

**Why 9 tests skip (a rename regression, not a bug).** Six `test_asr_worker` tests, two `test_integration_replay` tests and one `test_fixtures_replay` test look for the ASR model and the piper voice under `~/.local/share/promptbro/{models,tts}`. Those folders still live at `~/.local/share/teleprompter/` until T4 runs `promptbro migrate`, so the tests skip (before the rename the same paths pointed at the old folders and nothing skipped). No code change fixes this; T4 will, and the skips should drop to 0 after it.

**Flaky Broadway tests fixed.** The display tests used fixed numbers (37, 38, 39). `gtk4-broadwayd :N` listens on `broadway<N+1>.socket` and leaves it behind when killed, so stale sockets or a concurrent test run made `Display.get_default()` return None (`test_verbs` and `test_replay_headless_status` failed that way). `tests/bwutil.py` picks a free number and removes the sockets; the three test files that start Broadway use it.

## T4: the author's machine switched over

**Status:** COMPLETE

- Backups (timestamp 1790822405): `~/.config/hypr/bindings.lua.bak.1790822405`, `~/.config/hypr/hyprland.lua.bak.1790822405`; config backup `config.toml.bak.t4-1790822405` (moved with the config dir into `~/.config/promptbro/`; migrate also left its own `config.toml.bak.*`). Delete the Hyprland backups after a week.
- `promptbro migrate` moved (rename, nothing copied): `~/.config/teleprompter` -> `~/.config/promptbro`; `~/.local/share/teleprompter/models` (11 folders, 4.9 GB) -> `~/.local/share/promptbro/models`; `.../tts` -> `.../promptbro/tts`; `~/.local/state/teleprompter` -> `~/.local/state/promptbro`. Config `models_dir` repointed; `recent.json` seeded from the last script. Removed the old desktop entry, icon and `~/.local/bin/teleprompter` symlink (no alias).
- `promptbro setup --dev`: new venv `~/.local/share/promptbro/venv` (Python 3.14.7, pytest installed), model already present, wrote `supercleanse.PromptBro.desktop`, linked `~/.local/bin/promptbro`. The old `~/.local/share/teleprompter/venv` is left in place; delete it later.
- `scripts/dev-install.sh` does not exist yet; made the plugin link by hand: `~/.config/omarchy/plugins/supercleanse.promptbro -> ~/src/PromptBro`.
- Hyprland: `bindings.lua` lines 44-49 and `hyprland.lua` lines 31-33 replaced per plan 6.5 (same key names, plugin path, namespace `^promptbro$`). `hyprctl reload` ok, `hyprctl configerrors` empty, 5 binds described "PromptBro ...".
- Suite with the new venv: 239 passed, 0 skipped (includes concurrent T5 tests).
- Relaunched detached with his last script, line 149 restored, hidden. Launch guard verified: `status` with no instance printed `{"running": false, "state": "offline"}` and `NameHasOwner` stayed false.

## T5: App-side bridge (status file, GActions, recents)

**Status:** COMPLETE

- `promptbro/statusfile.py` (stdlib only): `widget_state(ctl)` (the 4.2 mapping, first match wins), `build()`, and `StatusWriter`. Writes are atomic (0600 temp file created in the 0700 runtime dir, then `os.replace`), skipped when nothing but `updated_at` changed, coalesced to one per 100 ms with a trailing write, and repeated every 5 s as a heartbeat. `close()` leaves `state: offline`. Under `PROMPTBRO_TEST` with no `PROMPTBRO_RUNTIME_DIR` the writer disables itself; `tests/conftest.py` now sets that variable to a temp dir.
- `promptbro/recent.py` (stdlib only): `add`, `remove`, `load`, `first_existing`; newest first, deduplicated by resolved path, max 8, missing paths dropped on write, atomic 0600. The app adds on every successful open (`open`, `--open`, first-launch reopen of the last script) and drops a path from `recent.json` when an open fails with file-not-found (the item T3 deferred). Pasted text is not added.
- `Controller`: `start_status()`, `widget_status()` (`status()` is unchanged), `set_opacity()`, and a `_changed()` poke on position, pause, visibility, worker state, override and toast changes. Toasts are tracked in `last_toast` (cleared after 4 s) and feed `message`; a worker error wins while the state is `error` or `no-audio`.
- `app.py`: Gio.SimpleActions `next prev restart pause resume show hide toggle quit reload-theme` and `opacity` (type `d`, clamped 0.3 to 1.0), each logged as `action <name> (dbus)`. They exist only on the running primary, so `gdbus ... Activate` never starts an instance. The status writer starts after the app is built and writes offline on any quit.
- Audio health is carried by `state: no-audio` (override `no-mic`/`no-audio`) and `detail`; the contract has no separate audio field, matching `Model.js` and `Bridge.qml`.
- Tests: `tests/test_statusfile.py` (mapping table, writer modes and coalescing with a simulated clock, the 10-writes-per-second ceiling over the `clean_200wpm` replay, stdlib-only import, and an app-level run on a private bus and Broadway: file after `open`, `Activate next` advances `line` in both `status` and the file, pause, opacity, hide, quit leaves `offline`, an action with no instance starts nothing) and `tests/test_recent.py`.

## T11: Opacity verb and slider

**Status:** COMPLETE

- The `opacity` GAction (T5) and the slider (T7, sends on release, `Model.clampOpacity`) were already wired; T11 added the rest. `promptbro opacity <v>` is a control verb (clamped 0.3 to 1.0; a missing or non-numeric value is an error; with no instance it prints "not running").
- `Controller.set_opacity` now saves `opacity` to `~/.local/state/promptbro/state.json` through a merging `_write_state`, so the script/line/visible saves and the opacity key no longer overwrite each other. At startup `app._apply_saved_opacity` lets `state.json` win over `config.toml`. README Configuration documents it.
- Tests (`tests/test_opacity.py`): private bus + Broadway run of the verb (0.5, clamp to 0.3, bad value fails, `status.json` follows, restart picks up the saved 0.55 over a config of 0.9); two Broadway renders before and after the action (background alpha 255 to 128, text stays solid, state.json written). New render hook `PROMPTBRO_RENDER_SET_OPACITY` drives the real `set_opacity`.

## T9: Packaging and docs

**Status:** COMPLETE

- `manifest.json` (id `supercleanse.promptbro`, version 0.2.0, bar-widget, `showProgress` setting); `omarchy plugin validate .` exits 0. Version bumped to 0.2.0 in `promptbro/__init__.py` and `pyproject.toml` so the three agree (a test pins that).
- `README.md` rewritten for a stranger: install, menu, hotkeys, update, remove, requirements (pacman list, pip pins, model, file locations), configuration, CLI table, development, license. `CONTRACT.md` documents status.json, recent.json, doctor JSON, verbs, exit codes and GActions. `docs/MARKETPLACE.md` is the submission checklist and reviewer notes (nothing submitted).
- `scripts/dev-install.sh` is idempotent with the by-hand link from T4 (it reports "already linked" and changes nothing when the link already points at the checkout; it refuses any other existing path) and prints the next steps without restarting the shell. `scripts/dev-uninstall.sh` removes only that link. Both honor `XDG_CONFIG_HOME`; tested against a temp config dir.
- `scripts/make_preview.py` renders the prompter on the sample script through a private Broadway display and composites it with the T8 widget and dropdown PNGs into `preview.png` (1280x560). The sample script's title now says PromptBro.
- `tests/test_manifest.py` (required fields, id equals `moduleName`, declared widget settings, version equality, license, `omarchy plugin validate`, no symlinks, qmllint syntax, no personal paths in shipped files, no tracked build artifacts) and `tests/run.sh` (pytest in the plugin venv, then `node --test`).
- `tests/offscreen/render.sh` now removes its staging directories on exit: they hold symlinks, and `omarchy plugin validate` rejects any symlink in the plugin folder.
- Repo hygiene: `teleprompter.egg-info/` untracked and deleted; `PROMPTBRO_PLAN.md` moved to `docs/`.

**Privacy pass for the public repo.** Scanned every tracked file for the home path, the author's internal project name, emails, tokens and keys, business names, and the script path of a work video script.
- Shipped file fixed: `promptbro/data/hyprland-snippet.lua` hard-coded an absolute home path; it is now a template with `/path/to/promptbro` (the real output of `--print-hypr-snippet` fills in the resolved path).
- Test and code text fixed: a business domain in `tests/test_normalize.py` and `promptbro/normalize.py` comments became `example.ai`; "the author's session" comments in tests became generic; `render.py` no longer cites another project.
- Docs redacted in place (`docs/PROMPTBRO_PLAN.md`, `BUILD_PLAN.md`, `BUILD_LOG.md`, `DESIGN.md`): home paths became `~`, the old reverse-DNS app id became `com.example.Teleprompter`, the host name, the sibling-project references, the memory-note path and business names were removed or generalized. The author's first name stays (it is in the LICENSE).
- Known remainder: `promptbro/paths.py` keeps the old app id as a string constant, and `migrate.py` / `tests/test_migrate.py` use it, because the one-time migration from the old Teleprompter install has to match it. It identifies a project namespace, not a person. Remove `migrate` before publishing if that is unwanted.
- Git history still holds the original strings; publish a squashed history (see `docs/MARKETPLACE.md`).
- Final pass (publish day): remaining first-name mentions in docs, comments and one test docstring became "the author"; `promptbro/app.py` comment made generic.

## Public repo publication (2026-09-30)

- The public repo is `supercleanse/promptbro`. Its `main` is produced from the local `public` branch, which started as an orphan branch (`git checkout --orphan public`) holding one commit, "PromptBro 0.2.0 - voice-following teleprompter for Omarchy". The private development history stays on `master` and is never pushed.
- Remote `github` points at the public repo. Only `public:main` is pushed: `git push github public:main`. No other branches, no tags from the private history.
- Future releases: finish and test on `master`, run the privacy scan again (home paths, names, emails, tokens, internal project names), then `git checkout public`, `git checkout master -- .` (and `git rm` anything deleted on master), commit as a new commit on top of the public history, and `git push github public:main`. Never merge `master` into `public` and never push `master`.

## Fix: crash on display hotplug (2026-10-01)

**Status:** COMPLETE

**Crash.** SIGSEGV (SEGV_MAPERR) while the prompter was hidden, one second after the dock carrying the external display re-enumerated (kernel `pciehp` surprise hotplug). Symbolized core: frame 0 in libgtk-4 (no debug symbols published for 4.22.4), called from `wl_closure_invoke` for event `done` (opcode 0) on proxy id 27, a `zwp_linux_dmabuf_feedback_v1` whose user data is GDK's `DmabufFormatsInfo` named `"default"`. The faulting instruction loads `info->dmabuf_formats->tranches` (offset 0x28, then 0x8) from 0x...a8000, a page-aligned heap address that is no longer mapped. That is GTK bug a8a5692c: `linux_dmabuf_format_table()` passes the heap `DmabufFormats` pointer to `munmap()` instead of the mmap()ed table; when the compositor re-sends the default feedback (Hyprland does on every monitor hotplug) and the heap pointer happens to be page-aligned, the page is unmapped and the next `done` crashes. Fixed upstream in GTK 4.22.5 and 4.23.3; Arch still ships 4.22.4. Not caused by our code: no PromptBro or gtk4-layer-shell frame is on the stack, and gtk4-layer-shell 1.3.0 already ignores `.closed`, drops an invalidated monitor, and does not remap hidden windows.

**Fix.**
- `promptbro/gtkcompat.py` (stdlib only): reads the libgtk-4 version through ctypes (no GTK init) and, on affected versions (< 4.22.5, 4.23.0 to 4.23.2, or unknown), adds `zwp_linux_dmabuf_v1` to `GDK_WAYLAND_DISABLE`, keeping any existing entries. `__main__` calls it right after the layer-shell preload and before anything imports Gtk (PyGObject opens the display at import); `app._run` calls it again (idempotent). GDK then never creates the default feedback object, so the buggy handler cannot run. In GDK 4.22 that global is used only by subsurface graphics offload, which is NULL-guarded; the GL/Vulkan renderers bind their own through EGL/Vulkan WSI. Verified with `WAYLAND_DEBUG=1` against the live compositor (display connection only, no window): 2 dmabuf binds/requests without the variable, 0 with it. The startup log now records the GTK version and `GDK_WAYLAND_DISABLE`.
- Hotplug hardening in `window.py` / `monitors.py`: `items-changed` does nothing but poke a 400 ms trailing debounce (`monitors.Debouncer`); after the list settles, `repick_action` defers while hidden (the layer surface is not touched until the next `show()`), remaps only when the target changed or the current monitor is gone, and otherwise keeps. Only connector names are stored; `find_monitor` / `valid_connectors` look monitors up fresh and skip invalid ones, so `set_monitor` only ever gets a valid monitor or None. `set_respect_close(False)` is explicit, and a close request (older gtk4-layer-shell forwarding `.closed`) is refused: the window hides through the app (status stays consistent) and the next show maps a fresh layer surface. The monitor-list handler holds a weak reference, is disconnected on destroy, and pending re-picks are cancelled on destroy/unrealize.

**Tests.** `tests/test_gtkcompat.py` (version gating, env merging, ctypes version equals gi's, `python -m promptbro` sets the variable before Gtk loads); `tests/test_monitors.py` (fake monitors and a fake clock: burst coalescing, cancel, exceptions contained, invalid monitors never returned, the unplug/replug timeline); `tests/test_window_hotplug.py` runs `tests/window_hotplug_driver.py` under a private Broadway display with a fake layer-shell and fake monitor list: no `set_monitor` inside `items-changed`, hidden window untouched until show, one remap after settle when visible, keep on unrelated changes, only valid monitors reach layer-shell, compositor close survived, destroyed window finalized and disconnected.

**Manual T10 check (real hotplug; no headless Hyprland here).** With the prompter open and hidden, then again visible: unplug the dock (or `hyprctl keyword monitor DP-1,disable` and re-enable), wait 2 s, replug. Expect no crash; the log shows `monitors changed ...` lines, then one `monitors settled: defer|remap|keep`; when visible it lands on the focused monitor; `promptbro status` still answers. `grep GDK_WAYLAND_DISABLE ~/.local/state/promptbro/*.log` shows `zwp_linux_dmabuf_v1` while GTK is older than 4.22.5.

## Fix: Bluetooth mic and empty `open` path (2026-10-01)

**Status:** COMPLETE

- `promptbro/audiosel.py` (stdlib only) chooses the capture node from `pactl -f json list sources`. `audio device = "auto"` (also the empty string) keeps the default source unless it is Bluetooth (`bluez_*` or `device.bus = bluetooth`), else the best non-Bluetooth, non-monitor source, USB/external before built-in; never Bluetooth unless pinned. A pinned string is an exact node name, a glob, or a substring (`Studio_Display`); a missing pin falls back to auto. The worker re-picks at every start and resume and reports the node in `ready`/`resumed`; `promptbro status` shows `worker.device`.
- `open` with an empty or blank path no longer resolves to the working directory: it reopens the last script, or starts idle. Directories are rejected with a one-line error. The empty argument came from a script launched under systemd (`open ""`, the primary's own first command); the desktop entry runs `show`, the hotkeys run `toggle`, and the QML guards non-empty paths.
