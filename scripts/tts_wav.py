#!/usr/bin/env python3
"""Synthesize a 16 kHz mono 16-bit WAV with a local sherpa-onnx piper voice (no network, no espeak-ng).

    tts_wav.py OUT.wav "first sentence" "second sentence" ... [--gap 0.6] [--voice DIR] [--speed 1.0]

Each argument is one segment; `--gap` seconds of silence go between segments (and 0.3 s at each end).
The voice defaults to ~/.local/share/promptbro/tts/vits-piper-en_US-amy-low; fetch it with
  curl -L https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-amy-low.tar.bz2 \
    | tar xj -C ~/.local/share/promptbro/tts
Deterministic: same voice + text + options -> byte-identical WAV.
"""
from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

DEFAULT_VOICE = Path.home() / ".local/share/promptbro/tts/vits-piper-en_US-amy-low"
RATE = 16000


def load_tts(voice: Path):
    import sherpa_onnx

    onnx = next(voice.glob("*.onnx"))
    cfg = sherpa_onnx.OfflineTtsConfig(
        model=sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(onnx), tokens=str(voice / "tokens.txt"), data_dir=str(voice / "espeak-ng-data"),
                noise_scale=0.0, noise_scale_w=0.0),  # no sampling noise: byte-identical output
            num_threads=1, provider="cpu"),
    )
    return sherpa_onnx.OfflineTts(cfg)


def resample(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def synth(segments: list[str], out: Path, gap: float = 0.6, voice: Path = DEFAULT_VOICE,
          speed: float = 1.0) -> float:
    tts = load_tts(voice)
    edge = np.zeros(int(0.3 * RATE), dtype=np.float32)
    parts = [edge]
    for i, text in enumerate(segments):
        a = tts.generate(text, sid=0, speed=speed)
        parts.append(resample(np.asarray(a.samples, dtype=np.float32), a.sample_rate, RATE))
        parts.append(np.zeros(int(gap * RATE), dtype=np.float32) if i < len(segments) - 1 else edge)
    pcm = np.clip(np.concatenate(parts), -1, 1)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return len(pcm) / RATE


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("segments", nargs="+")
    ap.add_argument("--gap", type=float, default=0.6)
    ap.add_argument("--voice", default=str(DEFAULT_VOICE))
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args(argv)
    voice = Path(a.voice).expanduser()
    if not (voice / "tokens.txt").exists():
        print(f"voice not found: {voice} (see the docstring for the download command)", file=sys.stderr)
        return 1
    secs = synth(a.segments, Path(a.out), a.gap, voice, a.speed)
    print(f"{a.out}: {secs:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
