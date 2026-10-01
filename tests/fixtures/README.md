# Replay fixtures

A replay file is what `teleprompter --replay FILE` reads: recorded (or synthetic) ASR worker
events, scheduled on the GLib main loop at their `t` and fed through the real Controller and
Aligner, exactly as live worker output would be.

Format: JSON, gzip-compressed when the name ends in `.gz`.

```jsonc
{
  "name": "synthetic_skip",
  "source": "tests/cases/skip_line.yaml",   // where the events came from
  "script": "Welcome back to the show.\n…",  // optional: used when --open is not given
  "start_unit": 12,                          // optional: aligner starts here (committed = provisional)
  "events": [                                // worker events (DESIGN §5.4); t = seconds from replay start
    {"type": "partial", "t": 0.0, "text": "the"},
    {"type": "final",   "t": 1.4, "text": "the quick brown fox jumps"}
  ]
}
```

A bare JSON list of events, or JSON lines, also works. `level` and `status` events are
handled like live ones. T8 adds `truth` (word end times) for the lag metrics; the replay
reader ignores keys it does not know.

## Files

| File | What |
|:--|:--|
| `synthetic_skip.json.gz` | The §6.8 `skip_line` case: starts at unit 12 (end of line 1), speaks "the quick brown fox jumps", ends on line 4, unit 30. Used by `tests/test_integration_replay.py`. |

`synthetic_skip.json.gz` was generated from `tests/cases/skip_line.yaml` with gzip `mtime=0`
(byte-stable):

```python
import gzip, json, yaml
case = yaml.safe_load(open("tests/cases/skip_line.yaml"))
data = {"name": "synthetic_skip", "source": "tests/cases/skip_line.yaml",
        "script": case["script"], "start_unit": case["start_unit"],
        "events": [{"type": e["type"], "t": e["t"], "text": e["text"]} for e in case["events"]]}
with open("tests/fixtures/synthetic_skip.json.gz", "wb") as f, \
     gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
    g.write(json.dumps(data, indent=1).encode())
```

Recordings of a real person reading go in `private/` (git-ignored), never here.

## Test audio

The end-to-end WAV tests use `tests/audio/skip_read.wav` (git-ignored, about 530 KB): `tests/data/skip.md`
read by espeak-ng with lines 2 and 3 skipped. Make it with `scripts/make_test_audio.sh`. Without
espeak-ng the script falls back to `scripts/tts_wav.py` (a sherpa-onnx piper voice in
`~/.local/share/teleprompter/tts/`). The WAV tests skip when the file or the ASR model is missing.

## Recorded fixtures (T8)

Seven real-ASR recordings of `tests/data/skip.md`, made by `scripts/record_fixture.py` (piper
amy-low TTS -> 16 kHz WAV -> `asr_worker --wav --fast`): `clean_160wpm`, `clean_200wpm`, `filler`,
`repeat`, `skip_line`, `restart_sentence`, `silence_2s`. Each carries `events` (partial/final only),
`truth` (script unit, start and end second of each word actually spoken), and `tts` (engine, voice,
hash of the WAV). `tests/test_fixtures_replay.py` replays them through the aligner: overshoot 0,
false_jumps 0, final_error <= 2, lag p50 < 0.6 s on the clean ones (lag p90 and stall are printed;
run with `-s`). The WAVs are regenerated on demand into `tests/audio/fx_*.wav` (git-ignored) and are
byte-identical run to run; the events are only re-recorded on purpose.

A human recording: `scripts/record_fixture.py --mic NAME` (needs the speaker's consent) writes
`tests/fixtures/private/NAME.json.gz` (git-ignored). It has no `truth`; add one by hand and the
replay test scores it too.

## Two engines (snappiness work)

The top-level `tests/fixtures/*.json.gz` are recorded with the default model, Nemotron speech
streaming 0.6B at 160 ms chunks (each file's `asr` field names it). `tests/fixtures/fc80/`
holds the same seven recordings made by T8 with the former default, the NeMo fast-conformer
80 ms model. The audio is identical (same `wav_sha256_16`). `tests/test_fixtures_replay.py`
replays both sets with the same hard limits.

## Re-acquisition set (2026-09-30)

Six recordings of `tests/data/reacquire.md` (a 15-line script of short lines, like a real video
script) made the same way (`scripts/record_fixture.py --only reacq_...`, piper amy-low at 160 wpm,
default model): `reacq_skip1_mid` (rest of a line and the next one skipped mid-sentence),
`reacq_skip2` (two lines skipped), `reacq_paraphrase` (one line paraphrased), `reacq_adlib_rejoin`
(a 10-word ad-lib, then back at the cursor), `reacq_adlib_skip2` (an ad-lib, then two lines ahead),
`reacq_shared_phrase` (three words of a line, then the next line, which starts with the same three
words). Each carries `start_unit` and `rejoin_unit` (the first script word spoken after the
departure). `test_reacquire_replay` applies the hard limits above and requires the cursor to reach
`rejoin_unit` within five spoken words (recognition delay included). Default model only; there is no
fc80 copy.
