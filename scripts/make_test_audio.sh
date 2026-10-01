#!/usr/bin/env bash
# Generate test WAVs (16 kHz mono) from tests/audio/sentences.txt with espeak-ng.
#   tests/audio/NN.wav        -> git-ignored, one per sentence
#   tests/audio/keep/welcome.wav -> the one checked-in clip ("Welcome back to the show.")
#   tests/audio/skip_read.wav -> tests/data/skip.md read with lines 2-3 skipped (T7 end-to-end)
# Without espeak-ng, falls back to scripts/tts_wav.py (sherpa-onnx piper voice) for skip_read.wav only.
# espeak-ng output is deterministic (byte-identical across runs).
set -euo pipefail
cd "$(dirname "$0")/.."
# en-us+m2 with 300 ms of leading silence is the espeak variant the NeMo model hears best
# (with no lead-in it clips the first word: "welcome" -> "men come").
VOICE="-v en-us+m2 -s 150"
LEAD='<break time="300ms"/>'
PY="${PYTHON:-$HOME/.local/share/promptbro/venv/bin/python}"
SKIP_READ=("Welcome back to the show." "In 2026 we grew revenue 40% this year."
           "The quick brown fox jumps over the lazy dog."
           "Thanks for watching, and thanks for listening." "See you next week.")
if ! command -v espeak-ng >/dev/null; then
  echo "espeak-ng not installed (sudo pacman -S espeak-ng); making skip_read.wav with the piper voice" >&2
  exec "$PY" scripts/tts_wav.py tests/audio/skip_read.wav "${SKIP_READ[@]}"
fi
# skip_read.wav: espeak-ng SSML with 0.7 s breaks between the sentences
ssml="<speak>$LEAD"
for s in "${SKIP_READ[@]}"; do ssml+="$s<break time=\"700ms\"/>"; done
ssml+="</speak>"
espeak-ng $VOICE -m -w tests/audio/skip_read.raw.wav "$ssml"
gst-launch-1.0 -q filesrc location=tests/audio/skip_read.raw.wav ! wavparse ! audioconvert ! audioresample \
  ! audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink location=tests/audio/skip_read.wav
rm -f tests/audio/skip_read.raw.wav
mkdir -p tests/audio/keep
n=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  n=$((n + 1))
  espeak-ng $VOICE -m -w "tests/audio/$(printf '%02d' "$n").wav" "<speak>$LEAD$line</speak>"
  # normalize to 16 kHz mono 16-bit
  gst-launch-1.0 -q filesrc location="tests/audio/$(printf '%02d' "$n").wav" ! wavparse ! audioconvert ! audioresample \
    ! audio/x-raw,format=S16LE,rate=16000,channels=1 ! wavenc ! filesink location="tests/audio/$(printf '%02d' "$n").tmp"
  mv "tests/audio/$(printf '%02d' "$n").tmp" "tests/audio/$(printf '%02d' "$n").wav"
done < tests/audio/sentences.txt
cp tests/audio/01.wav tests/audio/keep/welcome.wav
ls -l tests/audio/keep/welcome.wav
