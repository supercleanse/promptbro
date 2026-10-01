> **Note:** Written when this app was called Teleprompter; names and paths below are the old ones.

# Teleprompter — design

A small floating window at the top center of the laptop screen, just under the camera, that
shows a script a few lines at a time and scrolls to follow the author's voice. Everything runs
locally on the laptop. No cloud, no account, no GPU required.

This document is the build contract. `BUILD_PLAN.md` breaks it into ordered tasks. A verified
prototype of the window, theme, single-instance control, and scroll animation is in
`proto/overlay_proto.py` (screenshot: `proto/broadway-screenshot.png`); builders should lift
code from it rather than re-derive it.

Written 2026-09-30 from an inspection of the target machine and web research. Facts marked
**[verified]** were run or read on this machine; numbers marked *estimate* were not measured
here.

---

## 1. The machine this runs on [verified]

| Item | Value |
|:--|:--|
| Host | the dev laptop, Dell XPS 14 (DA14260) |
| OS | Omarchy 4.0.0.alpha (Arch), kernel 7.2.5-3-omarchy, Hyprland 0.56.2 (Lua config) |
| CPU | Intel Core Ultra X9 378H (Panther Lake), 16 cores, AVX2 + AVX-VNNI, no AVX-512 |
| GPU | Intel Arc B390 (integrated). **No NVIDIA, no CUDA.** ASR runs on CPU. |
| RAM | 30 GB (15 GB free at inspection), 61 GB swap |
| Audio | PipeWire 1.6.8 + WirePlumber 0.5.17. Default source when docked: Studio Display mic (`alsa_input.usb-Apple_Inc._Studio_Display_…mono-fallback`); laptop mic is `alsa_input.pci-0000_00_1f.3-platform-sof_sdw.HiFi__Mic__source` |
| Built-in display | `eDP-1`, 2880x1800 @ 120 Hz, scale 1.6 → **1800x1125 logical**, at position 0,0. The camera is top center of this panel. |
| External display | `DP-1`, Apple Studio Display 2560x1440 @ 60, scale 1, at 1800,0 (workspaces 6–10) |
| Bar | `omarchy-bar` (Quickshell, "omarchy-shell"), layer-shell **level 2 (TOP)**, **35 px** tall, top of each monitor, reserved area `[0,35,0,0]`, opaque (`~/.config/omarchy/shell.toml` sets `background-alpha = 1.0`, `[font] base-size = 16`) |
| Theme | `matte-black`. Current theme dir is **`~/.local/state/omarchy/current/theme/`** (not `~/.config/omarchy/current/`, which does not exist). `colors.toml` there: background `#121212`, foreground `#bebebe`, accent `#e68e0d`, muted `#333333`, light_foreground `#8a8a8d`, dark_foreground `#555555`, red `#D35F5F`, mode `dark` |
| Python | `/usr/bin/python3` = 3.14.7. **No pip in the system Python**; `python3 -m venv` works (ensurepip). `uv` 0.12.19 is on PATH via mise; a uv-managed 3.12 also exists but is not needed |
| GTK stack (pacman) | gtk4 4.22.4, gtk4-layer-shell 1.3.0, python-gobject 3.56.3, libadwaita 1.9.3, gst-plugins-base 1.28.6, gst-plugin-pipewire 1.6.8, portaudio 19.7.0, wl-clipboard 2.3.0, gtk4-broadwayd |
| Not installed | numpy, sounddevice, pyaudio, espeak-ng, piper, sway/cage/weston, sox. `ffmpeg` is installed. |
| Fonts | Adwaita Sans (GNOME default UI font, `gsettings font-name = 'Adwaita Sans 11'`), Noto Sans, Liberation Sans, JetBrainsMono Nerd Font. GTK theme Adwaita-dark, `color-scheme prefer-dark` |
| Env | `GDK_SCALE=3` is exported by Omarchy's `monitors.lua`; GTK4's Wayland backend ignores it (monitors still report scale_factor 2 / 1) — harmless |
| Free key combos | `SUPER+SHIFT+T`, `SUPER+SHIFT+bracketleft/right`, `SUPER+SHIFT+backslash`, `SUPER+SHIFT+Home` are unbound. (`SUPER+T` = float toggle, `SUPER+CTRL+T` = Activity, `SUPER+SHIFT+I` = a messaging app, `SUPER+SHIFT+V` = ExpressVPN.) |

Two facts decided the architecture:

1. **`Gtk4LayerShell.is_supported()` returns False from Python unless `libgtk4-layer-shell.so`
   is loaded before GTK opens the Wayland connection.** Importing the typelib first does not
   help (GI loads the .so lazily). Two fixes work **[verified]**: `ctypes.CDLL("libgtk4-layer-shell.so.0")`
   as the very first line of the entry module, or `LD_PRELOAD=/usr/lib/libgtk4-layer-shell.so`.
   We do both (the CDLL line in code, LD_PRELOAD in the launcher as belt-and-braces).
2. **sherpa-onnx 1.13.8 ships a cp314 manylinux wheel** and bundles its own onnxruntime, so
   the system Python 3.14 works with a plain venv. No source build, no Python downgrade.

---

## 2. Conventions (match the sibling apps)

This app follows the shape of the author's other GTK4 client apps:

- **Python 3.14 + PyGObject (GTK4).** Plain `Gtk`, not libadwaita (no adaptive widgets needed;
  fewer moving parts). `Gst` from PyGObject for audio. Python package `teleprompter/`, run as
  `python -m teleprompter`.
- **Venv with `--system-site-packages`** at `~/.local/share/teleprompter/venv` so the pacman
  `gi`/GTK/GStreamer bindings stay visible, pip-installing only what pacman cannot provide.
  `bin/teleprompter` is a bash launcher that `exec`s the venv Python (like `client/bin/imessages`).
- **`install.sh` is idempotent**: venv, pip deps, model download, icon, `.desktop` file,
  config skeleton. It **prints** the Hyprland Lua snippet for the author to paste; it does not edit
  `~/.config/hypr/*.lua` (the author asked to add binds himself).
- **Application id `com.example.Teleprompter`**; the layer-shell namespace is `teleprompter`.
- **Fake data only behind an explicit flag** (`--demo`), never as a fallback. Tests never touch
  the live desktop (Broadway or headless only) — the same rule the author's other GTK clients follow.
- Tests with `pytest`, in `tests/`. Plain English, American spelling everywhere.

---

## 3. What it does (behavior spec)

1. `teleprompter open speech.md` (or `teleprompter paste`) loads a script and shows the window
   at the top center of `eDP-1`, 8 px below the bar, floating over everything, never taking
   keyboard focus, never pushing other windows. Clicks pass through to whatever is underneath.
2. The window shows a stack of display lines: **3 history lines above** (faded, smaller),
   **the current line in the middle** (larger, accent color; words already spoken drawn in the
   accent, words not yet spoken in the theme foreground), **3 upcoming lines below** (normal
   foreground, fading toward the bottom edge). It starts on line 0.
3. It listens to the default microphone. As the author reads, the current line's spoken words light
   up and, when the cursor crosses into the next display line, the stack scrolls up with a
   180 ms ease-out. It never scrolls backward on its own.
4. If he skips a sentence, it catches up once it has strong evidence. If he ad-libs, stumbles,
   repeats himself, says "um", or goes quiet, it holds. If he re-reads an earlier line, it holds
   (it does not jump to a later occurrence of the same phrase).
5. Hotkeys (Hyprland binds → CLI verbs) toggle the window, pause/resume listening, step a line
   back or forward, and restart from the top. Stepping is the manual override when voice
   tracking is wrong: after a manual move, the aligner starts from that line.
6. It reads the Omarchy theme palette at startup (and on `reload-theme`), so it matches the
   desktop. A tiny status dot shows listening / paused / no-mic / ASR-error.
7. Nothing leaves the machine. Audio is never written to disk (except in the explicit
   `--record-fixture` test mode).

Non-goals for v1: editing the script in the window, multiple scripts, rich text, speaker
timing/pace stats, a settings GUI, running on the external monitor (config allows it, but the
default is `eDP-1`).

---

## 4. Architecture

```
                 ┌──────────────────────────── main process: python -m teleprompter (GTK main loop) ────────────────────────────┐
                 │                                                                                                             │
 CLI verbs ──────┼─▶ Gio.Application (HANDLES_COMMAND_LINE, single instance over the session D-Bus)                            │
 (teleprompter   │        │ actions: toggle show hide pause resume next prev restart open paste reload-theme quit status        │
  toggle, …)     │        ▼                                                                                                    │
                 │   Controller ── owns Script, Aligner (pure), Position, worker lifecycle                                       │
                 │        │  ▲                                                                                                  │
                 │        │  │ GLib IO watch on worker stdout: one JSON event per line                                          │
                 │        ▼  │                                                                                                  │
                 │   PrompterWindow (Gtk.ApplicationWindow + gtk4-layer-shell)                                                  │
                 │      └─ PrompterView (custom Gtk.Widget: Pango layouts in do_snapshot, eased scroll on the frame clock)      │
                 └───────────────────────────────────────────────┬──────────────────────────────────────────────────────────────┘
                                                                 │ subprocess (same venv python), argv + env; stdin: control lines
                                                                 ▼
                 ┌──────────────── worker: python -m teleprompter.asr_worker ───────────────────────────────────────────────────┐
                 │  GStreamer  pipewiresrc → audioconvert → audioresample → F32LE 16 kHz mono → appsink                        │
                 │      └─(streaming thread)─▶ ring buffer (20 ms blocks)  ─▶ sherpa-onnx OnlineRecognizer (decode thread)     │
                 │                                                            └─▶ stdout: {"type":"partial"|"final"|"status",…} │
                 └─────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Why a **subprocess** for ASR rather than a thread: sherpa-onnx decode calls run tens of
milliseconds each and may hold the GIL; at 120 Hz the UI has 8 ms per frame. A separate
process keeps the animation smooth, isolates a crashing model from the window, and makes the
ASR path a plain CLI that tests can drive with a WAV file. The aligner is pure Python and fast
(band of ~350 DP cells per update, well under 2 ms), so it runs on the GTK main loop.

(Blocks are 20 ms since the snappiness work; the budget below was the original estimate.)
Latency budget from mouth to pixel (*estimate*): PipeWire quantum 10–20 ms + 100 ms block
accumulation + 80 ms model lookahead + 10–30 ms decode + <2 ms align + ≤8 ms frame ≈ **220–300 ms**.
Li & Mostow (FLAIRS 2012) found a 0.2 s lag actually *improves* tracking accuracy on partial
hypotheses, so this budget is right where it should be.

### 4.1 Package layout

```
Teleprompter/
├── DESIGN.md  BUILD_PLAN.md  README.md  LICENSE(MIT)  .gitignore
├── pyproject.toml                  # name teleprompter; deps in §10; [project.scripts] not used (bin/ launcher instead)
├── bin/teleprompter                # bash: LD_PRELOAD + exec venv python -m teleprompter "$@"
├── install.sh                      # idempotent; prints the Hyprland snippet; never edits hypr config
├── teleprompter/
│   ├── __init__.py  __main__.py    # __main__: `from .app import main; raise SystemExit(main())`
│   ├── app.py                      # Gio.Application, verbs, single instance, --demo/--replay/--wav plumbing
│   ├── controller.py               # glue: script ↔ aligner ↔ worker ↔ view; pause; manual moves; state file
│   ├── window.py                   # PrompterWindow: layer-shell setup, CSS, click-through, monitor pick
│   ├── view.py                     # PrompterView: drawing + animation (from proto/overlay_proto.py)
│   ├── theme.py                    # Omarchy colors.toml → palette; fallbacks
│   ├── config.py                   # config.toml load/defaults/validation
│   ├── script.py                   # text → DisplayLines → Units (with spoken forms, weights)
│   ├── normalize.py                # shared normalizer: numbers, money, %, years, acronyms, contractions, fillers
│   ├── aligner.py                  # pure: Aligner.on_event()/on_user_key() → Position (§6)
│   ├── asr_worker.py               # subprocess entry: GStreamer/WAV → sherpa-onnx → JSON lines
│   ├── asr_vosk.py                 # optional fallback engine, same event contract (T11)
│   ├── models.py                   # model registry, download/verify, paths
│   ├── demo.py                     # --demo: bundled sample script + scripted fake event stream (explicit flag only)
│   ├── render.py                   # --render-png offscreen snapshot (Broadway)
│   ├── data/sample-script.md
│   └── data/com.example.Teleprompter.svg
├── scripts/
│   ├── make_test_audio.sh          # espeak-ng/piper → 16 kHz mono WAVs under tests/audio/
│   ├── record_fixture.py           # WAV → real ASR → tests/fixtures/*.json.gz (event stream + timestamps)
│   └── bench_asr.py                # per-chunk decode time + CPU% for a WAV, prints a table
├── tests/
│   ├── test_normalize.py  test_script.py  test_aligner.py  test_theme.py  test_config.py
│   ├── test_asr_worker.py          # skips unless the model dir exists; feeds tests/audio/*.wav
│   ├── test_view_render.py         # Broadway offscreen PNG (skips if gtk4-broadwayd missing)
│   ├── test_app_cli.py             # dbus-run-session + Broadway: verbs reach the primary instance
│   ├── cases/*.yaml                # aligner corpus (§6.8)
│   ├── audio/                      # generated WAVs (git-ignored except a tiny checked-in one)
│   └── fixtures/*.json.gz          # recorded ASR event streams (checked in; small)
└── proto/                          # the verified research prototype; reference only, not imported
```

### 4.2 Runtime files

| Path | Purpose |
|:--|:--|
| `~/.local/share/teleprompter/venv/` | the venv |
| `~/.local/share/teleprompter/models/<model-dir>/` | ASR models (§5) |
| `~/.config/teleprompter/config.toml` | user config (§9); created with defaults by `install.sh` |
| `~/.local/state/teleprompter/state.json` | last script path, last cursor line, window visibility |
| `~/.local/state/teleprompter/teleprompter.log` | rotating log (1 MB × 3); stderr also goes to the journal when launched by Hyprland |
| `~/.local/share/applications/com.example.Teleprompter.desktop`, `~/.local/share/icons/hicolor/scalable/apps/com.example.Teleprompter.svg` | app-grid entry and icon |

---

## 5. Speech recognition

### 5.1 Engine choice

**Primary: sherpa-onnx 1.13.8 (PyPI, cp314 wheel) running
`sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25`** (default since the
snappiness work; measured in BUILD_LOG "Snappiness": word end to cursor 168 ms p50 / 598 ms
p90 at 0.41 core, against 284 / 701 ms at 0.17 core for the fast-conformer below, and lower
WER). The bullets below describe the original choice, the 114M fast-conformer 80 ms model,
which stays in the registry and is selectable with `[asr] model`.

- True cache-aware streaming (`OnlineRecognizer`): partial hypotheses grow word by word and are
  append-mostly, which is what a forward-only aligner wants. Whisper-family tools re-decode a
  window and rewrite earlier words with 1–3 s lag; they were rejected for that reason.
- 114M-parameter NVIDIA FastConformer RNNT, int8, 103 MB tarball. Reported WER 6.4 % on
  LibriSpeech test-other at 80 ms lookahead; trained on a broad corpus, not LibriSpeech alone.
- CPU *estimate*: 0.15–0.4 of one core at `num_threads=2` (Nemotron 0.6B, roughly 5x larger,
  measured 0.63 cores at 560 ms chunks on a Ryzen 3900X). Leaves the machine free for a video
  call. `scripts/bench_asr.py` turns this estimate into a measurement (T10).
- Per-token timestamps (`result.tokens` with `▁` word starts, `result.timestamps` in seconds).
- Output is lowercase, no punctuation — fine, the normalizer lowercases everything anyway.
- Download: `https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8.tar.bz2`
  Files used: `encoder.int8.onnx`, `decoder.int8.onnx`, `joiner.int8.onnx`, `tokens.txt`.

**Alternates on the same engine** (config `[asr] model = …`, same API, no code change):

| Model dir | Use when | Notes |
|:--|:--|:--|
| `sherpa-onnx-streaming-zipformer-en-2023-06-26` | want script-word biasing or a lighter model | 66M Zipformer2, 320 ms chunks, WER 3.06/7.81 (LibriSpeech), supports `modified_beam_search` + hotwords. Output UPPERCASE. Files: `encoder-epoch-99-avg-1-chunk-16-left-128.int8.onnx`, matching decoder/joiner, `tokens.txt`. |
| `sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25` | accuracy upgrade | 600M, 464 MB, WER 7.67 % avg over 8 sets, punctuation + case, greedy only, no hotwords. *Estimate* 0.8–1.5 cores at 160 ms. Only keep if `bench_asr.py` shows a call unaffected. |

**Fallback engine: Vosk 0.3.45 + `vosk-model-small-en-us-0.15`** (40 MB,
`https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip`). Different stack
(Kaldi), `py3-none` wheel so it installs on 3.14, very low CPU, partials with word timestamps
(`SetPartialWords(True)`). Less accurate (9.85 % WER LibriSpeech clean). Implemented as
`asr_vosk.py` behind the same event contract (T11, optional). **Never use Vosk's closed
grammar mode** built from the script: constrained decoding makes off-script speech "jump
around" the text (Li & Mostow 2012). Soft biasing only.

Rejected: Parakeet TDT v2/v3 (offline-only in sherpa-onnx; "streaming" is VAD-segmented),
Parakeet Unified streaming exports (buffered; RTF 1.1 in the maintainers' log), NeMo directly
(multi-GB PyTorch install for the same models), faster-whisper / whisper-streaming / whisper.cpp
stream (chunked-offline, 1–3 s lag, rewrites earlier words), Moonshine v2 (re-decodes the
current line), Kyutai STT (needs a GPU), RealtimeSTT (no 3.14). The Intel GPU/NPU: the pip
wheel cannot use it; not worth an OpenVINO build.

### 5.2 sherpa-onnx API shape [from source]

```python
import sherpa_onnx
rec = sherpa_onnx.OnlineRecognizer.from_transducer(
    tokens=f"{d}/tokens.txt", encoder=f"{d}/encoder.int8.onnx",
    decoder=f"{d}/decoder.int8.onnx", joiner=f"{d}/joiner.int8.onnx",
    num_threads=2, sample_rate=16000, feature_dim=80, provider="cpu",
    decoding_method="greedy_search",
    enable_endpoint_detection=True,
    rule1_min_trailing_silence=2.4,   # endpoint after this much silence even with nothing decoded
    rule2_min_trailing_silence=1.0,   # endpoint after this much silence once something was decoded
    rule3_min_utterance_length=20.0,  # force an endpoint on very long utterances
)
stream = rec.create_stream()
# every ~100 ms of audio:
stream.accept_waveform(16000, samples_f32)        # numpy float32 in [-1, 1]
while rec.is_ready(stream):
    rec.decode_stream(stream)
r = rec.get_result_all(stream)                    # .text .tokens .timestamps .start_time
if rec.is_endpoint(stream):
    emit final; rec.reset(stream)                 # keep our own utterance counter across resets
# WAV mode: after the last chunk, stream.input_finished(); drain is_ready/decode_stream; emit final
```

Endpointing matters for the aligner: each utterance's partial is re-read on every update, so
shorter utterances (rule2 ≈ 1.0 s) keep the tail cheap and let `final` events commit words.

### 5.3 Audio capture

**Primary: GStreamer via PyGObject** — no pip wheel, integrates with GLib, PipeWire-native.

```
pipewiresrc do-timestamp=true client-name=teleprompter [target-object=<node.name>] ! queue
  ! audioconvert ! audioresample
  ! audio/x-raw,format=F32LE,rate=16000,channels=1,layout=interleaved
  ! appsink name=sink emit-signals=true sync=false max-buffers=100 drop=false
```

Both this pipeline and the WAV variant (`filesrc location=… ! wavparse ! audioconvert !
audioresample ! audio/x-raw,format=F32LE,rate=16000,channels=1 ! appsink …`) parse on this
machine **[verified]**. `pipewiresrc` without `target-object` follows the PipeWire default
source, which is what the author wants (Studio Display mic when docked, laptop mic when not). The
`new-sample` callback runs on GStreamer's streaming thread; it appends to a
`queue.Queue` of 20 ms float32 blocks (`--block-ms`, 320 samples; originally 100 ms) stamped with
their arrival time; the decode thread takes everything queued and feeds the
recognizer. For `--wav`, the worker pushes blocks in real time by default (`--realtime`) so
timing matches a live mic, or as fast as possible (`--fast`) for tests.

Fallback if `pipewiresrc` misbehaves: `pw-record --rate 16000 --channels 1 --format f32 --raw
--latency 20ms -` as a subprocess, reading 6400-byte blocks from its stdout (`pw-record` 1.6.8
has `--raw` **[verified]**). Do not use sounddevice/pyaudio (extra wheels, ALSA detour).

### 5.4 Worker contract (stdout, one JSON object per line)

```jsonc
{"type":"status","state":"ready","engine":"sherpa-onnx","model":"…80ms-int8","device":"default","t":0.0}
{"type":"partial","utt":3,"t":12.84,"text":"the quick brown",
 "words":[{"w":"the","t":12.11},{"w":"quick","t":12.35},{"w":"brown","t":12.62}]}
{"type":"final","utt":3,"t":13.90,"text":"the quick brown fox jumps","words":[…]}
{"type":"status","state":"error","message":"no audio device"}
{"type":"level","rms":0.031,"t":12.9}        // every 250 ms; drives the status dot and a "voice active" flag
```

`t` is seconds since worker start (monotonic). Words are built from tokens at `▁` boundaries;
a word's `t` is its first token's timestamp plus `start_time`. Control lines on the worker's
stdin: `pause`, `resume`, `quit`. On `pause` the mic pipeline goes to `NULL` (device released,
mic light off; a WAV pipeline only goes to `PAUSED`) and the recognizer stream is reset; on
`resume` it goes to `PLAYING` with a fresh utterance. (It used to go to `PAUSED` and back: on
the author's desktop pipewiresrc then stayed suspended after resume, so no audio arrived while the
status said "tracking". `NULL` -> `PLAYING` is the startup path.) A watchdog restarts the
capture (`NULL` -> `PLAYING`, `{"type":"status","state":"warning"}`) when no samples arrive
for 3 s while listening, and after 3 failed restarts reports `error` "no audio from the
microphone". In mic mode `t` is the arrival time of the newest sample in the decoded audio,
so the app's `event_age_ms` measures decode + pipe + align. The app also shows `no-audio`
(red dot) and never "tracking" when no level event has arrived for 1.5 s while listening.
The main process restarts a crashed worker after 2 s, at most 5 times, then shows the error dot.

---

## 6. Script model and voice-following aligner

The aligner is a **pure module**: `Aligner(script, params).on_event(ev) -> Position` and
`.on_user_key(k) -> Position`. No GTK, no clock reads (times come from the events). That makes
it replayable in tests and swappable in the UI.

### 6.1 Script → display lines → units (`script.py`)

Input: UTF-8 text or Markdown. Steps:

1. Strip Markdown syntax that is not read aloud: `#` heading marks, `*`/`_` emphasis marks,
   `>` quote marks, list bullets `-`/`*`/`1.`, link targets (`[text](url)` → `text`), HTML tags.
   Drop fenced code blocks and horizontal rules. Text in `[brackets]` or `(parentheses)` that is
   a stage direction stays visible but is **not matchable** (its units get `matchable=False`;
   NotchPrompter does the same). Only bracketed spans up to 8 words qualify, so a parenthetical
   aside in a sentence still matches.
2. Split into paragraphs on blank lines; split paragraphs into source lines on `\n`.
3. **Display line** = a source line, further split when longer than `max_line_chars` (default
   90): first at sentence ends (`.`, `?`, `!` followed by space), then at `;`, `:`, `—`, `,`, so
   each piece ≤ 90 chars where possible. A display line never splits inside a number or a
   hyphenated word. Pango then wraps a display line to the window width (a display line may
   occupy 1–3 visual rows).
4. **Unit** = one display word: `idx`, `line`, `char_start/char_end` in the line, `forms`
   (accepted spoken token tuples, longest first, from `normalize.spoken_forms`), `weight`,
   `is_stop`, `matchable`. `Script.line_first_unit[line]`, `line_last_unit[line]`, and
   `df[token]` (document frequency of each single-token form) are precomputed.

Weights: `base = 1.0` if a form is number-derived; `0.25` if stopword (~45 articles, pronouns,
auxiliaries, prepositions, and contractions of them: `you're` = `you are`); `0.5` if primary
token ≤ 3 chars; a multi-token primary form (`AI-native` → `a i native`) is judged as a whole
(`1.0` if its letters total ≥ 4, df over identical forms); else `1.0`.
`weight = base × (0.35 + 0.65 / df[primary_token])`. A "strong" unit has `weight ≥ 0.6`.
A bracketed aside of ≤ 8 words stays unmatchable for distance and gaps, but is **optional**: it
costs nothing to skip and, if the speaker does read it aloud, may match with its own weight
(`optional_weight`) instead of reading as an ad-lib.

### 6.2 Normalization (`normalize.py`, shared by both sides)

`spoken_forms(raw_token) -> list[tuple[str, ...]]`, primary first:

| Input | Forms |
|:--|:--|
| `$79` | `(seventy nine dollars)`, `(seventy nine bucks)` |
| `$79.99` | `(seventy nine ninety nine)`, `(seventy nine dollars and ninety nine cents)`, `(seventy nine dollars ninety nine)` |
| `$79/mo` | money forms × {`a month`, `per month`} |
| `$2M`, `$2 million` | `(two million dollars)` |
| `40%` | `(forty percent)`; `4.5%` → `(four point five percent)` |
| year 1000–2099 (4 digits, no `$`/`%`/comma) | `2026` → `(twenty twenty six)`, `(two thousand twenty six)`, `(two thousand and twenty six)`; `2005` → `(two thousand five)`, `(twenty oh five)`, `(twenty o five)`; `1900` → `(nineteen hundred)` |
| integer | `num2words(n)` with "and" removed, split on `[-,\s]+`; `1500` also `(fifteen hundred)` |
| decimal `3.14` | `(three point one four)` |
| ordinal `3rd` | `(third)` |
| time `3:30`, `10am` | `(three thirty)`; `(ten a m)`, `(ten am)` |
| ALL-CAPS 2–5 letters, not a dictionary word | `API` → `(a p i)`, `(api)`; `NASA` → `(nasa)`, `(n a s a)` |
| abbreviations | `dr`→doctor, `mr`→mister, `mrs`→missus, `st`→saint/street, `vs`→versus, `etc`→et cetera, `e.g.`→for example / e g, `i.e.`→that is / i e, `&`→and, `+`→plus, `#1`→number one, `w/`→with, `approx`→approximately |
| hyphen/slash `full-stack` | `(full stack)`, `(fullstack)` |
| contractions | apostrophes stripped: `it's`→`(its)`, `(it is)`; `we'll`→`(well)`, `(we will)`; `don't`→`(dont)`, `(do not)` |
| letters + digits `Q4`, `MP3` (capitals) | `(q four)`, `(q4)` |
| number + `'s` `2027's` | `(twenty twenty sevens)`, …, and the plain number forms |
| domain `example.ai`, `WordPress.org` | `(example dot ai)`, `(exampleai)` |
| all-caps pieces `V/TO` | also spelled out: `(v t o)` |
| short emphasis capitals `ALL`, `NOW`, `NEW`, … | read as the word first (`(all)`), not spelled |
| everything else | NFKC, lowercase, curly quotes straightened, letters+digits only |

`num2words` gotchas **[verified by the researcher]**: `num2words(79, to="currency", currency="USD")`
treats an int as cents (pass `Decimal("79")` and strip ", zero cents"); cardinals include British
"and" (remove it); `to="year"` gives "two thousand and five" for 2005 (add alternates by hand).
A small hand-written `spoken_forms` for years/money/percent/decimals/ordinals/time, using
`num2words` only for plain cardinals, is clearer than bending the library.

`normalize_hyp(text, script, cursor) -> list[Tok]` (Tok = `text`, `is_soft_filler`): drop
bracketed non-speech (`[BLANK_AUDIO]`, `(music)`), split on whitespace, expand each token with
`spoken_forms`; when a token has several forms (ASR emitted `2026` or `40%`), pick the form
that occurs as a contiguous token run in the units `[cursor-3, cursor+40]`, else the primary.
Drop **hard fillers** `{um, uh, er, erm, ah, uhm, hmm, mm, mhm}`. Mark **soft fillers**
`{like, so, well, okay, ok, right, actually, basically, literally, yeah, "you know", "i mean"}`
— they stay (they may be real script words) but cost little as gaps.

### 6.3 Token similarity (cached per pair)

```python
HOMOPHONE_SIM, PREFIX_SIM, PHONETIC_SIM = 0.90, 0.80, 0.82
HOMOPHONES = {to,two,too} {for,four,fore} {no,know} {by,buy,bye} {i,eye} {one,won} {be,bee}
             {see,sea} {their,there,theyre} {your,youre} {right,write,rite} {new,knew}
             {here,hear} {our,hour} {weather,whether} {then,than} {ad,add} {wood,would} …

def token_sim(h, s, h_is_open_last=False):
    if h == s:                                  return 1.0
    if s in HOMOPHONES.get(h, ()):              return HOMOPHONE_SIM
    L = min(len(h), len(s))
    if h_is_open_last and len(h) >= 3 and s.startswith(h):   return PREFIX_SIM   # half-said last word
    if L <= 3:                                  return 0.0    # short words: exact only
    best = 0.0
    cpl = common_prefix_len(h, s)
    if L >= 5 and cpl >= 4 and cpl / L >= 0.75: best = 0.80   # inflection: balance/balancing
    if h[0] == s[0]:                                          # rhyme guard: light/night → 0
        if L >= 5: best = max(best, Levenshtein.normalized_similarity(h, s))   # 1 edit in 5 → 0.80
        if L >= 5 and JaroWinkler.similarity(h, s) >= 0.92: best = max(best, 0.80)
    if L >= 4 and double_metaphone_match(h, s): best = max(best, PHONETIC_SIM)
    return best
```

Two later restrictions (2026-09-30): the half-said-word prefix rule never applies to a whole
stopword (`the` is not half of `themselves`), and joins/splits (`health care` ↔ `healthcare`) use
spelling only (Levenshtein ≥ 0.9, parts ≥ 2 spoken / 3 written letters), never sound-alikes
(`skilled` ≈ `so glad` by Double Metaphone, `purpose` ≈ `purpose I`).

`double_metaphone_match`: the primary/secondary codes (package `metaphone`, pure Python) share
a non-empty code of length ≥ 2. Verified pairs: write/right → RT, four/for → FR, cite/site → ST,
principal/principle → PRNSPL.

### 6.4 Band alignment (Smith-Waterman over `[cursor-B, cursor+W]`)

```
TAIL_TOKENS=6  BACK_UNITS=3  AHEAD_UNITS=40  AHEAD_RESUME=80
MATCH_SIM=0.75  MISMATCH=-0.7  GAP_HYP=0.6  GAP_HYP_SOFT=0.1  GAP_SCRIPT=0.35 (× max(weight,0.1))
MAX_GAP_RUN=2  END_ON_PENULT_PENALTY=0.4  SPLIT_MIN_LEN=6  STRONG_WEIGHT=0.6

align_band(hyp, lo, hi, open_last) -> [Candidate(end_unit, score, matched, strong, evidence, start_unit)]
  rows i = hyp tokens (1..m), cols j = units lo..hi; cell keeps H, M(matched), S(strong), E(evidence=Σ weight·sim),
  hyp_gap_run, scr_gap_run, ended_on_match; local floor 0 (a lone coincidental hit resets).
  transitions, take the max:
   (a) match unit j via one of its forms (k tokens): all sims ≥ MATCH_SIM → C[i-k][j-1].H + weight·(1+mean_sim)
   (b) join: hyp[i-2]+hyp[i-1] vs a single ≥6-char form of unit j             ("health care" → "healthcare")
   (c) split: hyp[i-1] (≥6 chars) vs units[j-1]+units[j] joined                ("turbocharger" → "turbo charger")
   (d) mismatch on the diagonal: C[i-1][j-1].H + MISMATCH (counts as one gap of each kind)
   (e) extra spoken word: C[i-1][j].H − (GAP_HYP_SOFT if soft filler | stutter repeat hyp[i-1]==hyp[i-2] |
       fragment hyp[i].startswith(hyp[i-1]) else GAP_HYP)
   (f) skipped script word: C[i][j-1].H − GAP_SCRIPT·max(weight,0.1)          (skipping "the" is almost free)
   gap runs of one kind capped at MAX_GAP_RUN. Non-matchable units (stage directions) only allow (f) at zero cost.
  candidates: cells in row m that ended on a match with M>0; plus row m-1 cells (score − 0.4), since the
  last spoken word may be half-said.
```

About 44 units × 6 tokens × ~1.3 forms ≈ 350 cells with cached similarities: well under 2 ms in
pure Python. Keep two rolling rows plus a copy of row m-1.

### 6.5 Decision: whether to move

Revised 2026-09-30 after the author's first real session (BUILD_LOG "Re-acquisition"): jumps are
judged only on **new evidence** and tiered by **lines** as well as units.

```
FOLLOW_MAX=3  FOLLOW_MIN_SCORE=0.6  NEAR_MAX=12  CONFIRM_TOLERANCE=2
NEAR_LINES=2  NEAR_MIN_MATCHES=3 NEAR_MIN_STRONG=1 (or 2 strong + 2 matches)  NEAR_MIN_EVIDENCE=1.0  NEAR_MARGIN=0.4
MID_LINES=4   MID_MIN_MATCHES=3  MID_MIN_STRONG=2   MID_MIN_EVIDENCE=1.8  MID_MARGIN=0.6  MID_SURE_EVIDENCE=2.8
FAR (beyond MID_LINES): FAR_MIN_MATCHES=4  FAR_MIN_STRONG=3  FAR_MIN_EVIDENCE=2.5  FAR_MARGIN=0.6  FAR_SURE_EVIDENCE=3.5
LINE_START_BONUS=0.3  RUN_BONUS=0.15  RUN_AS_STRONG=4
RIVAL_SEPARATION=3  ECHO_LOOKBACK=60  ECHO_MARGIN=0.2  DIST_COST_PER_UNIT=0.02

move_cost(delta): 0 if delta ≤ 0; 0.02·delta if ≤ FOLLOW_MAX; 0.6 + 0.02·delta in the near tier; else 1.2 + 0.02·delta

new evidence of a path = its matches after the cursor that it did NOT reach by reading straight on
  from the cursor (nothing but weak words skipped): new_matched, new_strong, new_evidence (Σ w·sim),
  new_start (first such unit), new_run (longest word-for-word run among them)
lines_ahead = line(new_start) − line(next matchable unit after the cursor)   # 0 = later in the line due next

locate(tail, cursor, open_last, pass_state, ahead, confirm, shown) -> new_cursor | None
  fast path: a one-token closed tail equal to a form of the next matchable unit → that unit
  band: [cursor−BACK_UNITS, max(cursor+ahead, last unit of the line MID_LINES past the line due next)]
        (capped at cursor+2·ahead), so a script of short lines still searches four lines ahead
  cands = align_band(...); best = max by (net, nearest); delta = best.end − cursor (matchable units)
  delta ≤ 0 → None (hold)
  delta ≥ 2 → the landing word must have been heard (exact/homophone/half-said, T8), and must earn
       what it skips: a weak landing word (weight < 0.3) only follows the previous match across weak
       words; any skipped content word needs a second match after the cursor
  straight reading (every content word from the cursor to best.end matched word for word, as one run)
       or delta ≤ FOLLOW_MAX: need matched ≥ (1 if delta==1 else 2) and score ≥ FOLLOW_MIN_SCORE
  shown (the display) already jumped past the cursor and best.end is within −2..+3 of it, with
       ≥ 2 matches after the cursor → move (an earlier update accepted that jump; this one agrees)
  otherwise a jump: bonus = LINE_START_BONUS if new_start begins a line or sentence (people resume
       there) + RUN_BONUS × (new_run − 1); a run of RUN_AS_STRONG words counts as one more strong word
  ECHO GUARD (unchanged): the phrase explained about as well by text already read → None
  near (delta ≤ NEAR_MAX or lines_ahead ≤ NEAR_LINES): (new_matched ≥ 3 and new_strong ≥ 1, or
       new_strong ≥ 2 and new_matched ≥ 2), new_evidence + bonus ≥ 1.0, lead ≥ 0.4 → move
  mid (lines_ahead ≤ MID_LINES): new_matched ≥ 3, new_strong ≥ 2, new_evidence + bonus ≥ 1.8,
       lead ≥ 0.6; move at once if new_evidence + bonus ≥ 2.8, else confirm
  far: the §6.5 original thresholds on new evidence, no bonuses; move at once if ≥ 3.5, else confirm
  confirm: remember Pending(end, tail); move when a *different* later tail lands within
       CONFIRM_TOLERANCE of this pass's pending jump or of the other pass's
```

### 6.6 Event loop: two cursors, stability, silence

```
TAIL_TENTATIVE=2  REVISE_BACK_MAX=3  HISTORY_TOKENS=6  HISTORY_RESET_S=2.5  RESUME_SILENCE_S=4.0  ADLIB_AFTER=8

state: committed=-1, provisional=-1 (display cursor ≥ committed), history=[], prev_partial=[],
       last_voice_t=-inf, commit_pass, preview_pass, misses=0, ignore_before=0

on_event(ev):                               # ev.type ∈ {partial, final}; ev.t seconds; ev.text
  silence = ev.t − last_voice_t
  if silence > HISTORY_RESET_S: history = []
  ahead = AHEAD_RESUME if silence > RESUME_SILENCE_S else AHEAD_UNITS
  toks = normalize_hyp(ev.text, script, committed)[ignore_before:]
  if not toks: return emit()                # silence / non-speech: never moves
  last_voice_t = ev.t
  stable = toks if final else toks[: max(0, min(lcp(prev_partial, toks), len(toks) − TAIL_TENTATIVE))]   # LocalAgreement-2
  if partial: prev_partial = toks
  # 1) committed pass — stable words only
  if stable:
      r = locate((history + stable)[-TAIL_TOKENS:], committed, open_last=False, commit_pass, ahead)
      if r is not None and r > committed: committed = r
  # 2) preview pass — full hypothesis, re-derived from committed every time (idempotent)
  r = locate((history + toks)[-TAIL_TOKENS:], committed, open_last=partial, preview_pass, ahead)
  target = r if r is not None else committed
  misses = 0 if r is not None else misses + 1
  if target < provisional: target = max(target, provisional − REVISE_BACK_MAX, utt_floor)   # revision: retreat ≤ 3, never below committed,
                                                                                             # never below where the last utterance ended
  provisional = max(committed, target)
  if final: history = [] if misses ≥ 2 (the utterance ended in an ad-lib) else (history + toks)[-HISTORY_TOKENS:]
             prev_partial = []; ignore_before = 0; utt_floor = provisional
  # both passes pass each other's pending far jump as confirmation, and `shown = provisional`
  return emit()

on_user_key(k):                             # the ONLY way to move backward
  line_prev: committed = provisional = line_first_unit[max(0, cur_line − 1)] − 1
  line_next: committed = provisional = line_last_unit[cur_line]
  restart:   committed = provisional = −1
  goto_line(n): committed = provisional = line_first_unit[n] − 1
  history = []; prev_partial = []; pending cleared; ignore_before = len(current utterance toks)   # words heard before the key don't count

emit() -> Position(line, unit=provisional, committed_unit, line_progress (chars spoken / line chars),
                   state ∈ {tracking, holding, adlib (misses ≥ ADLIB_AFTER), paused})
```

How each behavior falls out: fillers are dropped or cheap; an **ad-lib** never ends on a match
(or its best is behind the cursor) so the cursor holds, and the 6-token tail slides past it;
an ad-lib's common words cannot carry a jump because only new evidence counts and a weak landing
word cannot skip content; **stutters/fragments** cost 0.1; **1–3 skipped words** cost
`0.35 × weight`; **reading one or two lines ahead** is the near tier (about three words, two if
both are distinctive and start the line); **three or four lines ahead** is the mid tier (about
four or five words); further needs the original far tier; **a phrase that appears twice** resolves to the nearer copy by distance cost
and nearest-wins ties, and equal far rivals block the jump; **re-reading** is behind the cursor
so it holds, and the echo guard stops a jump to a later copy; **revisions** only touch the
preview cursor, within 3 units, never below committed; **silence** never moves; after a **long
silence** the next utterance searches 80 ahead under the same evidence rules; **backward** is
only ever a user key.

Optional extras, off by default (config flags): `display_coasting` (advance a display-only
fraction at the measured words/s when the mic shows voice but the cursor has not moved for
800 ms, capped at committed+8); `line_complete_on_final` (if ≤ 2 units remain on the current
line when a final arrives, commit the line); `hotwords` (zipformer only: pass
`units[committed−10 : committed+30]` as hotwords, refreshed as the cursor moves).

### 6.7 What the view receives

`Position.line` drives the scroll; `Position.unit` drives the within-line word highlight;
`Position.state` drives the status dot. The controller calls `view.set_position(pos)` on the
main loop; the view animates only when `line` changes.

### 6.8 Test corpus (`tests/cases/*.yaml`)

```yaml
name: skip_whole_line
script: |
  Welcome back to the show.
  In 2026 we grew revenue 40% this year.
  The plan costs $79 a month.
  Their team shipped it right on time.
  The quick brown fox jumps over the lazy dog.
  Thanks for watching, and thanks for listening.
  See you next week.
start_unit: 12
events:
  - {t: 0.0, type: partial, text: "the"}
  - {t: 0.2, type: partial, text: "the quick"}
  - {t: 0.4, type: partial, text: "the quick brown"}
  - {t: 0.6, type: partial, text: "the quick brown fox"}
  - {t: 0.8, type: partial, text: "the quick brown fox jumps"}
  - {t: 1.4, type: final,   text: "the quick brown fox jumps"}
expect:
  - {after: 0, line: 1, unit: 12}
  - {after: 1, line: 1, unit: 12}
  - {after: 2, line: 4, unit: 28}
  - {after: 3, line: 4, unit: 29}
  - {after: 4, line: 4, unit_min: 29}
  - {after: 5, line: 4, unit: 30, committed: 30}
invariants: [no_backward_without_key, provisional_ge_committed]
```

Unit indices for that script: L0 welcome 0 … show 4; L1 in 5, 2026 6, we 7, grew 8, revenue 9,
40% 10, this 11, year 12; L2 the 13 … month 18; L3 their 19 … time 25; L4 the 26, quick 27,
brown 28, fox 29, jumps 30, over 31, the 32, lazy 33, dog 34; L5 thanks 35 … listening 41;
L6 see 42, you 43, next 44, week 45.

A `speak(text, t0, wps=2.5, partial_every=0.2, revise={}, drop_last_in_final=False)` helper
in `tests/conftest.py` expands a sentence into word-by-word partials plus a final. **Required
cases** (all on the script above):

| Case | start | events | expected |
|:--|:--|:--|:--|
| clean_read | −1 | speak("welcome back to the show") | units 0..4 one per step; line 0 |
| homophone | 18 | speak("there team shipped it write on time") | ends 25, line 3 |
| number_year_primary | 4 | speak("in twenty twenty six we grew revenue forty percent this year") | 12 |
| number_year_alt | 4 | speak("in two thousand twenty six we grew") | 8 |
| number_asr_digits | 4 | speak("in 2026 we grew revenue 40% this year") | 12 |
| currency (two variants) | 12 | speak("the plan costs seventy nine dollars a month") / speak("the plan costs $79 a month") | 18 |
| filler | 12 | speak("um the plan uh costs") | 15 |
| stumble | 25 | speak("the the quick quick brown fox") | 29 |
| fragment | 25 | speak("the qu quick brown") | 28 |
| adlib_hold | 25 | speak("honestly I was surprised by that") | 25 at every step |
| adlib_then_resume | 25 | ad-lib above, then speak("the quick brown") | 28 |
| single_common_word | 25 | speak("over the") as a new utterance | stays 25 |
| next_word_fast_path | 12 | final "the" | 13 |
| skip_line | 12 | the YAML above | line 1 through "the quick"; line 4 at "…brown" (was "…jumps" until 2026-09-30) |
| near_skip | 25 | speak("the lazy dog") | 34 |
| repeated_phrase_nearest | 34 | speak("thanks for") | 36, not 40 |
| repeated_phrase_second | 37 | speak("and thanks for") | 40 |
| echo_reread | 34 | speak("the quick brown fox") | stays 34 |
| revision | 25 | partials "the quick brown box" → "the quick brown fox jumps over the lazy" → "the quick brown fox jumps over" | provisional 28 → 33 → ≥ 30; committed never decreases |
| silence | 30 | 10 s gap, then speak("over the lazy dog") | 30 during the gap; then 34 |
| long_silence_resume_elsewhere | 34 | 6 s gap, then speak("see you next week") | 45 |
| user_back_key | 34 | key line_prev | line 3; committed == provisional == 18 |
| end_of_script | 41 | speak("see you next week thank you so much") | 45, stays 45 |
| stage_direction | script with "(pause) Thanks…" | speak("thanks") | skips the unmatchable unit |

Added 2026-09-30 (`tests/data/reacquire.md`, 15 short lines like a real video script; `at_word`
expectations check the state after the k-th word of a spoken segment):
`reacquire_skip1_mid` (rest of a line and the next one skipped mid-sentence: on line 5 at the third
word back), `reacquire_skip2` (two lines skipped: by the third word), `reacquire_paraphrase` (a
paraphrased line, never ahead of the speaker), `reacquire_adlib_rejoin` (10-word ad-lib utterance,
back at the cursor by the third word), `reacquire_adlib_skip2` (ad-lib, then two lines ahead: by the
fourth word), `reacquire_shared_phrase` (the next line repeats the three words just read: those hold,
the fifth word jumps), `reacquire_far_then_continue` (three lines ahead, committed follows, the next
utterance reads on). The six `reacq_*` recordings replay the same situations through the real
recognizer and must reach the rejoin point within five spoken words.

**Recorded fixtures** (`scripts/record_fixture.py`): TTS the script with espeak-ng or piper at
several rates, with injected misreads (a filler, a repeat, a skipped line, a restart, 2 s of
silence), run it through **the real ASR worker**, and save the raw event stream
(`tests/fixtures/<name>.json.gz`: `script`, `events`, `truth` = word end times from the TTS
segments). A replay test computes: **overshoot** (events where provisional > truth + 1) must be
0; **final_error** ≤ 2 units; **lag p50/p90** (word end → cursor reaches it) reported;
**false_jumps** (moves > 3 units off truth) must be 0; **stall** (longest time trailing truth by
> 5 units) reported. Fixtures are recorded once on this machine and checked in; CI replays
them without a model.

---

## 7. Window and rendering

### 7.1 Layer-shell setup (`window.py`) [verified in proto]

```python
from ctypes import CDLL; CDLL("libgtk4-layer-shell.so.0")     # FIRST line of teleprompter/__main__.py, before `import gi`
…
win = Gtk.ApplicationWindow(application=app); win.set_decorated(False); win.add_css_class("prompter")
win.set_default_size(cfg.window.width, cfg.window.height)         # 720 × 240 logical px default
if display is GdkWaylandDisplay and LayerShell.is_supported():
    LayerShell.init_for_window(win)                                # before present()
    LayerShell.set_namespace(win, "teleprompter")
    LayerShell.set_layer(win, LayerShell.Layer.OVERLAY)            # above the bar and above fullscreen windows
    LayerShell.set_anchor(win, LayerShell.Edge.TOP, True)          # TOP only → horizontally centered
    LayerShell.set_exclusive_zone(win, 0)                          # no reservation; placed *below* the bar's zone
    LayerShell.set_margin(win, LayerShell.Edge.TOP, cfg.window.top_gap)   # 8 → window top at y = 35 + 8 = 43
    LayerShell.set_keyboard_mode(win, LayerShell.KeyboardMode.NONE)
    win.pick_monitor()   # monitors.choose_monitor(): "auto" → `hyprctl monitors -j` focused; connector pins
else:
    log.warning("layer-shell unavailable (backend %s); plain window", …)   # Broadway/tests; never silent on Wayland
win.present()
```

Why these values: `OVERLAY` (level 3) is the only layer guaranteed above the bar (TOP, level 2)
and it stays visible over a fullscreen Meet/Zoom; `exclusive_zone = 0` makes Hyprland place the
surface inside the area not reserved by the bar, so the margin is just the gap and does not
hard-code the bar height (Hyprland `arrangeLayerArray`: bounds = usable area when zone ≠ −1);
`KeyboardMode.NONE` means it never steals focus — all control is via CLI verbs from Hyprland
binds. If the monitor is unplugged/replugged, GTK's monitor list changes; on `monitors`
`items-changed`, re-pick the monitor and re-present.

**Click-through** (default on, `window.click_through = true`): after every `map`, set an empty
input region: `win.get_surface().set_input_region(cairo.Region())` (pycairo `cairo`, not
`gi.repository.cairo`). Hyprland's hit tester skips a layer surface with an empty input region,
so clicks go to the window underneath **[confirmed in Hyprland source + upstream issue #61]**.
GTK does not reset the region for an undecorated window. With click-through off, the window
simply absorbs clicks (there is no drag; position is config).

**Transparency**: GTK4 on Wayland always has alpha. CSS `window.prompter { background:
transparent; }` and a transparent `.card` child; `PrompterView` paints the single
background (theme `background` at `window.opacity` alpha, default 0.75, range 0.3-1.0) and the 1 px border, so text is never faded and the alpha is not applied twice. No compositor blur: Omarchy disables
`decoration.blur` globally, so a `blur` layer rule would do nothing; a 75 % opaque panel reads
well over anything while staying see-through.

**Hyprland rules** (the author adds these himself; `install.sh` prints them):

```lua
-- ~/.config/hypr/hyprland.lua (bottom) — layer rules for the prompter surface
hl.layer_rule({ match = { namespace = "^teleprompter$" }, animation = "fade" })
-- optional: keep the script out of screen shares (also hides it from your own screenshots)
-- hl.layer_rule({ match = { namespace = "^teleprompter$" }, no_screen_share = true })

-- ~/.config/hypr/bindings.lua
o.bind("SUPER + SHIFT + T",            "Teleprompter toggle",  "~/AI/Projects/Teleprompter/bin/teleprompter toggle")
o.bind("SUPER + SHIFT + backslash",    "Teleprompter pause",   "~/AI/Projects/Teleprompter/bin/teleprompter pause")
o.bind("SUPER + SHIFT + bracketright", "Teleprompter next",    "~/AI/Projects/Teleprompter/bin/teleprompter next")
o.bind("SUPER + SHIFT + bracketleft",  "Teleprompter prev",    "~/AI/Projects/Teleprompter/bin/teleprompter prev")
o.bind("SUPER + SHIFT + Home",         "Teleprompter restart", "~/AI/Projects/Teleprompter/bin/teleprompter restart")
```

Plain string commands, not `{ launch = … }`: a verb sent to a running instance is a short D-Bus
hop and must not spawn a uwsm scope each keypress. Key names are xkb keysyms spelled exactly as
Omarchy spells them (`backslash`, `bracketleft`, `bracketright`, `Home`); the all-caps forms
first used here (`BACKSLASH`, `HOME`, ...) never fired on the author's machine.

### 7.2 Single instance and verbs (`app.py`) [verified in proto]

`Gtk.Application(application_id="com.example.Teleprompter", flags=HANDLES_COMMAND_LINE)`. Every
invocation's argv arrives in the primary's `do_command_line`; the remote exits without
initializing GTK. Verbs:

| Verb | Effect |
|:--|:--|
| *(none)* | first launch: start hidden if no script is loaded, else show; later: `toggle` |
| `open <path>` | load a `.txt`/`.md` file, cursor to line 0, show, start listening |
| `paste` | load the clipboard text (`wl-paste --no-newline`, run from the primary), same as `open` |
| `toggle` / `show` / `hide` | window visibility (hide also pauses the worker; show resumes) |
| `pause` / `resume` / `pause` again toggles | freeze the cursor and pause the mic (worker `pause`); dot turns amber |
| `next` / `prev` | one display line forward/back (manual override; aligner re-anchors) |
| `restart` | back to line 0 |
| `goto <n>` | jump to display line n |
| `reload-theme` | re-read `colors.toml` |
| `status` | primary prints JSON (script path, line, state, engine, cpu%) to the caller via `cmdline.print_literal` |
| `quit` | stop the worker, exit |

Flags (first launch only): `--config <path>`, `--model <dir>`, `--device <pipewire node.name>`,
`--wav <file>` (feed the real ASR from a WAV instead of the mic; real aligner, real UI),
`--replay <fixture.json.gz>` (skip ASR, replay recorded events into the real aligner and UI),
`--demo` (bundled sample script + a scripted fake event stream; the only fake data path, and it
uses a separate `com.example.Teleprompter.Demo` app id with `NON_UNIQUE` so it never reaches the
real instance), `--render-png <out> [--size WxH] [--line n]` (offscreen snapshot then exit;
Broadway), `--app-id-suffix <s>` (tests: private app id).

A `teleprompter` verb sent when no instance is running starts one (that is just the first
launch). `install.sh` also creates the `.desktop` entry (`Exec=…/bin/teleprompter`, so the app
grid launches it hidden, ready for `paste`).

### 7.3 The view (`view.py`) [prototype verified under Broadway]

Custom `Gtk.Widget`, `set_overflow(HIDDEN)`, all drawing in `do_snapshot` with Pango layouts
and `snapshot.append_layout(layout, rgba)`; one animated number `_offset` eased from the old
center-to-center distance to 0 over `animation_ms` (180) with ease-out cubic on
`add_tick_callback`. Lift `Prompter` from `proto/overlay_proto.py` and add:

- **Word highlight on the current line**: build the current line's `Pango.AttrList` so
  characters `[0, units[position.unit].char_end)` use the accent color and the rest use
  `foreground`; the whole current line is bold at `current_pt`. Words light up without any
  animation; only line changes scroll.
- **Layout cache** keyed by `(line, width, style)`; cleared on width change or theme reload.
- **Edge fades**: history alpha `0.15 + 0.35·f`, upcoming alpha `0.25 + 0.55·f` where `f` is
  distance from the edge over half the height (as in the proto); plus a 12 px linear alpha mask
  at the top and bottom edges so text never sits on the rounded corners.
- **Reduced motion**: if `Gtk.Settings.gtk_enable_animations` is False, set `_offset = 0` at once.
- **Status dot**: 8 px circle at the top-right inside the card: accent = tracking, `muted` =
  holding, amber (`bright_blue` in matte-black, `#f59e0b`) = paused, `red` = worker error / no
  mic. A 3-second toast line under the dot for events ("Paused", "Script loaded: speech.md",
  "Mic: Studio Display").
- **Empty state**: when no script is loaded, the card shows one muted line: "No script —
  `teleprompter open <file>` or `teleprompter paste`".

Typography (logical px on eDP-1 at 1.6): font family from config, default `"Adwaita Sans"`
(the GNOME UI face installed here; `Inter` and `Noto Sans` as CSS fallbacks). Current line
**26 px bold**, other lines **17 px regular**, line gap 10 px, card padding 16 px, card
720 × 240 logical (that is 40 % of the 1800 px screen width; 240 px shows ~3+1+3 lines).
Colors from the palette (§7.4): current line accent; upcoming `foreground`; history
`light_foreground` (then alpha-faded); card `background` at 0.88 alpha; border `foreground` at
0.12. With `matte-black` that is orange on near-black, as in `proto/broadway-screenshot.png`.

### 7.4 Theme (`theme.py`) [verified]

Read `~/.local/state/omarchy/current/theme/colors.toml` with `tomllib`; keys `background`,
`foreground`, `accent`, `muted`, `light_foreground`, `red`, `bright_blue`, `mode`. Fall back to
the matte-black values (§1) when the file or a key is missing; log once. Do **not** use
libadwaita's accent color (gsettings says `blue`, not the theme's `#e68e0d`). Re-read on
`reload-theme`; also watch `~/.local/state/omarchy/current/` with `Gio.FileMonitor` and reload
when `theme.name` changes (Omarchy's `omarchy-theme-set` swaps the `theme` directory). Palette
colors become `@define-color tp_bg …` etc. in the CSS provider and `Gdk.RGBA`s in the view.

---

## 8. Controller (`controller.py`)

- Loads the script (`open`/`paste`), builds `Script`, builds `Aligner`, sets `view.set_script`.
- Spawns the worker (`sys.executable -m teleprompter.asr_worker --model … [--device …]`) with
  `Gio.Subprocess` (stdin pipe, stdout pipe, stderr inherited → log). Reads stdout lines with
  `Gio.DataInputStream.read_line_async`; each JSON event goes to `aligner.on_event` then
  `view.set_position`. `level` events update a `voice_active` flag (RMS > `vad_rms`, default
  0.01) used by the status dot and the optional coasting.
- `pause`: writes `pause\n` to the worker, sets `Position.state = paused`. `hide` implies pause;
  `show` implies resume. `quit` writes `quit\n`, waits 1 s, then kills.
- Manual keys: `aligner.on_user_key(...)` → `view.set_position`.
- Persists `state.json` (script path, line) on every line change (debounced 1 s) so a relaunch
  offers to resume at the same line (`open` of the same path restores the line; `restart`
  clears it).
- Worker supervision: exit → log, 2 s backoff, restart up to 5 times, then `state = error` and
  toast "Speech engine stopped — see log".

---

## 9. Configuration (`~/.config/teleprompter/config.toml`)

```toml
[window]
monitor = "auto"         # "auto" = Hyprland-focused monitor, re-picked on every show; or a connector to pin
width = 720              # logical px
height = 240
top_gap = 8              # px below the bar (exclusive_zone 0 handles the bar height)
opacity = 0.75           # panel background alpha, 0.3-1.0 (text never faded)
click_through = true
font = "Adwaita Sans"
current_pt = 26
other_pt = 17
history_lines = 3
upcoming_lines = 3
animation_ms = 180
max_line_chars = 90

[asr]
engine = "sherpa-onnx"   # or "vosk"
model = "sherpa-onnx-nemo-streaming-fast-conformer-transducer-en-80ms-int8"   # dir name under models/
models_dir = "~/.local/share/teleprompter/models"
num_threads = 2
device = ""              # PipeWire node.name; "" = default source
endpoint_rule2_s = 1.0
hotwords = false         # zipformer only

[aligner]                # any §6 parameter may be overridden here, by its name in lower case
tail_tokens = 6
ahead_units = 40
display_coasting = false
line_complete_on_final = false

[log]
level = "info"
```

`config.py` validates types and ranges, fills defaults, and warns on unknown keys. `--config`
overrides the path (tests use a temp file).

---

## 10. Packaging and install

**Runtime pip deps** (into the venv; all have cp314 wheels or are pure Python **[verified]**):
`sherpa-onnx==1.13.8` (pulls `sherpa-onnx-core`), `numpy` (any 2.x with cp314 wheel),
`rapidfuzz>=3.14`, `metaphone==0.6` (pure-Python sdist), `num2words>=0.5.14`. `pycairo`
1.29.1 and PyYAML 6.0.3 are already system packages (`python-cairo`, `python-yaml`) and are
visible through `--system-site-packages` **[verified]**. Dev deps: `pytest` (not a system
package; pip into the venv). Optional extra `vosk==0.3.45`.

**System packages: nothing new is required to run.** GTK4, gtk4-layer-shell, PyGObject,
GStreamer + PipeWire plugin, wl-clipboard, ffmpeg are already installed. For **tests** the author
must approve:

```bash
sudo pacman -S --needed espeak-ng          # local TTS for test WAVs (1.52.0, extra repo)
# optional, only if we want to exercise the real layer-shell placement offscreen:
sudo pacman -S --needed sway grim          # headless wlroots compositor + screenshot tool
# optional, nicer test voices:
yay -S piper-tts piper-voices-en-us
```

**`install.sh`** (idempotent, refuses off-Omarchy):

1. `python3 -m venv --system-site-packages ~/.local/share/teleprompter/venv`; `pip install -e .`
   (or `uv pip install` if `uv` is on PATH — either is fine; use `python -m pip` for fewer
   assumptions). Sanity-import `gi`, `Gtk 4.0`, `Gtk4LayerShell 1.0`, `Gst 1.0`, `sherpa_onnx`.
2. `python -m teleprompter.models download <model>` → curl the tarball into `models/`, verify
   size > 50 MB and that the four files exist, extract, remove the tarball. Re-run skips when
   present.
3. Write `~/.config/teleprompter/config.toml` if missing (defaults from §9).
4. Install the icon and `.desktop` file (`Exec=<repo>/bin/teleprompter`, `NoDisplay=false`,
   `Categories=AudioVideo;Utility;`).
5. Print the Lua snippets from §7.1 and the model's CPU/latency numbers from the last bench, if
   any. **Do not edit Hyprland config.**

**`bin/teleprompter`**:

```bash
#!/usr/bin/env bash
set -euo pipefail
VENV="$HOME/.local/share/teleprompter/venv"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -x "$VENV/bin/python3" ] || { echo "run install.sh first" >&2; exit 1; }
export LD_PRELOAD="/usr/lib/libgtk4-layer-shell.so${LD_PRELOAD:+:$LD_PRELOAD}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV/bin/python3" -m teleprompter "$@"
```

The `CDLL` line in `__main__.py` makes the app correct even when run without the launcher
(e.g. tests, `python -m teleprompter`).

---

## 11. Testing strategy

Hard rules: **no test ever opens a window on the live desktop**, **no test ever talks to the
real running instance** (private app id via `--app-id-suffix` + `dbus-run-session`), **no fake
data outside `--demo`**.

| Layer | How | Model needed? |
|:--|:--|:--|
| `normalize`, `script`, `config`, `theme` | plain pytest | no |
| `aligner` | pytest replays `tests/cases/*.yaml` (synthetic partial streams via `speak()`) and `tests/fixtures/*.json.gz` (recorded real-ASR streams), checks expectations and the invariants `no_backward_without_key`, `provisional_ge_committed`, `overshoot == 0`, `false_jumps == 0` | no |
| `asr_worker` | `python -m teleprompter.asr_worker --wav tests/audio/welcome.wav --fast` must emit ≥ 1 `final` whose normalized text has word error rate ≤ 25 % against the known sentence (espeak-ng is robotic; the bar is deliberately loose), and word timestamps must be monotonic. Skipped when the model dir is absent. Also `--wav` of 2 s silence → no `final` with text. | yes |
| `view` | Broadway offscreen: `gtk4-broadwayd :7 --port 8087 &`, `env -u WAYLAND_DISPLAY -u DISPLAY GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 dbus-run-session -- python -m teleprompter --demo --render-png out.png --size 720x240 --line 3` → PNG exists, non-blank, ≥ 720×240. Render via `Gtk.WidgetPaintable → renderer.render_texture → save_to_png`. Broadway's frame clock only ticks with a browser attached, so `--render-png` renders a *settled* state (`jump_to` without animation). Animation is checked visually once via Playwright (`proto/shot.mjs` pattern) — optional, not in CI. | no |
| `app` CLI | under `dbus-run-session` + Broadway with `--app-id-suffix test`: start `--demo`, then send `next` ×2 and `status`; `status` JSON shows line 2. | no |
| layer-shell placement | not automatable here: no headless Hyprland; sway is not installed. If the author approves `pacman -S sway grim`, T12 runs `WLR_BACKENDS=headless WLR_RENDERER=pixman WLR_LIBINPUT_NO_DEVICES=1 sway -c tests/sway-headless.conf` with `output HEADLESS-1 mode 1800x1125`, launches the app against that socket, screenshots with `grim`, and asserts the card's top-left pixel position (centered horizontally, y == margin since sway has no bar). Otherwise placement is checked by the author once on the real desktop (T10 manual checklist). | no |
| end to end | `bin/teleprompter open tests/data/skip.md --wav tests/audio/skip_read.wav` on the author's desktop, by the author, following the T10 checklist. | yes |

Test audio: `scripts/make_test_audio.sh` runs `espeak-ng -v en-us -s 160 -w /tmp/x.wav "…"`
then `ffmpeg -y -i /tmp/x.wav -ar 16000 -ac 1 -sample_fmt s16 tests/audio/<name>.wav`, for
each sentence in `tests/audio/sentences.txt` (and a silence file via `ffmpeg -f lavfi -i
anullsrc=r=16000:cl=mono -t 2`). If `piper` exists it is preferred (natural voice). One small
WAV (< 200 KB) is checked in so `test_asr_worker` can run without espeak-ng.

---

## 12. Risks and how the plan handles them

| Risk | Mitigation |
|:--|:--|
| Model CPU higher than estimated during a call | `bench_asr.py` (T10) measures per-chunk decode time and `pidstat` CPU; fall back to `num_threads=1`, the 20M zipformer, or Vosk |
| Partial cadence too coarse (transducer emits a word only when confident) | the 80 ms model emits sub-word pieces continuously; if word latency is still > 400 ms, try the CTC `…-ctc-en-80ms-int8` variant (`from_nemo_ctc`) which is cheaper and more eager |
| Aligner holds too often / jumps wrongly on the author's real speech | every parameter is in config; record a fixture of the author reading (with permission, `scripts/record_fixture.py --mic`), add it to the corpus, tune, re-run the suite |
| Layer-shell surface lands on the wrong monitor when undocked | `monitor = "auto"` (default) follows the focused monitor on every show; a pinned connector that is missing falls back to auto (logged); re-picks on `items-changed`. `set_monitor` on a mapped surface does not move it, so `show()` unmaps before re-picking when the target changed |
| The bar height changes (font base-size) | exclusive_zone 0 places us under whatever the bar reserves; no hard-coded 35 |
| `pipewiresrc` default-source switching mid-session | WirePlumber moves the stream when the default changes; if not, `teleprompter resume` recreates the pipeline (worker `pause`/`resume` tears down and rebuilds) |
| Window hidden from screen share also hidden from screenshots | `no_screen_share` is commented out in the printed snippet; the author decides |

---

## 13. Decisions for the author (not made here)

1. Approve `sudo pacman -S espeak-ng` (test audio). Optional: `sway grim` (headless layer-shell
   test), `piper-tts` (nicer test voices).
2. Hide the prompter from screen shares (`no_screen_share`)? It also hides it from Omarchy
   screenshots and recordings.
3. Key combos: proposed `SUPER+SHIFT+T` toggle, `+backslash` pause, `+bracketleft/right`
   prev/next line, `+Home` restart (all currently free).
4. Click-through by default (yes, proposed) — means the card cannot be clicked or dragged.
5. Try the 600M Nemotron model after benchmarking, or stay on the 114M FastConformer.
6. Allow recording a fixture of his own voice reading the sample script (stays local, under
   `tests/fixtures/`, git-ignored unless he says otherwise).
