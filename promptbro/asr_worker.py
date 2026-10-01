"""Streaming ASR worker: GStreamer capture -> sherpa-onnx -> JSON events on stdout (DESIGN §5.4).

Run as ``python -m promptbro.asr_worker``. Control lines on stdin: pause, resume, quit.
No audio is ever written to disk.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import queue
import signal
import sys
import threading
import time

import numpy as np

RATE = 16000
BLOCK_MS = 20       # feed the recognizer every 20 ms: a chunk decodes as soon as its audio is in
LEVEL_EVERY = 4000  # 250 ms of audio
NO_AUDIO_S = 3.0    # mic: no samples this long while listening -> restart the capture pipeline
NO_AUDIO_TRIES = 3  # ... and after this many restarts in a row with no audio, report an error
_EOS = object()


class Worker:
    def __init__(self, args):
        self.args = args
        self.q: queue.Queue = queue.Queue()
        self.out_lock = threading.Lock()
        self.paused = False
        self.quit = threading.Event()
        self.reset_req = threading.Event()  # resume: start a fresh utterance
        self.t0 = time.monotonic()
        self.audio_t = 0.0  # seconds of audio consumed (used as clock in --wav mode)
        self.pipeline = None
        self.wav = bool(args.wav)
        self.model_name = ""
        self.block = max(160, int(RATE * args.block_ms / 1000))
        self.last_audio = time.monotonic()  # mic watchdog: when samples last arrived
        self.stalled = False                # --simulate-stall (tests): drop audio after a resume
        self.pipe_lock = threading.Lock()

    # ---- output -------------------------------------------------------------------------
    def now(self) -> float:
        return round(self.audio_t if self.wav else time.monotonic() - self.t0, 3)

    def stamp(self, arrived: float | None) -> float:
        """Event time: audio time for a WAV; for the mic, when the newest sample in the decoded
        audio arrived (so the app's event_age measures decode + pipe + align, not zero)."""
        if self.wav or arrived is None:
            return self.now()
        return round(arrived - self.t0, 3)

    def emit(self, obj: dict, force: bool = False) -> None:
        with self.out_lock:
            if self.paused and not force:
                return
            sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def status(self, state: str, **kw) -> None:
        self.emit({"type": "status", "state": state, **kw, "t": self.now()}, force=True)

    # ---- capture ------------------------------------------------------------------------
    def build_pipeline(self):
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        caps = "audio/x-raw,format=F32LE,rate=16000,channels=1,layout=interleaved"
        if self.wav:
            src = f'filesrc location="{self.args.wav}" ! wavparse ! audioconvert ! audioresample'
        elif self.args.live_source:
            # tests: a live GStreamer source standing in for the mic, through the mic code path
            src = f"{self.args.live_source} ! queue ! audioconvert ! audioresample"
        else:
            tgt = f' target-object="{self.args.device}"' if self.args.device else ""
            src = f"pipewiresrc do-timestamp=true client-name=promptbro{tgt} ! queue ! audioconvert ! audioresample"
        desc = f"{src} ! {caps} ! appsink name=sink emit-signals=true sync=false max-buffers=100 drop=false"
        self.Gst = Gst
        self.pipeline = Gst.parse_launch(desc)
        sink = self.pipeline.get_by_name("sink")
        self._pending = np.zeros(0, dtype=np.float32)
        sink.connect("new-sample", self._on_sample)

    def _on_sample(self, sink):
        Gst = self.Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if ok:
            try:
                data = np.frombuffer(info.data, dtype=np.float32).copy()
            finally:
                buf.unmap(info)
            now = time.monotonic()
            if self.stalled:
                return Gst.FlowReturn.OK  # --simulate-stall: behave like a source that went quiet
            self.last_audio = now
            if self.paused and not self.wav:
                return Gst.FlowReturn.OK
            self._pending = np.concatenate([self._pending, data])
            b = self.block
            while len(self._pending) >= b:
                self.q.put((self._pending[:b], now))
                self._pending = self._pending[b:]
        return Gst.FlowReturn.OK

    # ---- control ------------------------------------------------------------------------
    def stdin_loop(self):
        for line in sys.stdin:
            cmd = line.strip().lower()
            if cmd == "pause":
                with self.out_lock:
                    self.paused = True
                if not self.wav:  # live audio heard while paused is dropped; a WAV just waits
                    self._drain_queue()
                if self.pipeline is not None:
                    # mic: NULL releases the device (mic light off); resume goes NULL -> PLAYING,
                    # the same path as startup, which is more dependable for a live source
                    # than PAUSED -> PLAYING.
                    with self.pipe_lock:
                        self.pipeline.set_state(self.Gst.State.PAUSED if self.wav else self.Gst.State.NULL)
                self.status("paused")
            elif cmd == "resume":
                if self.paused:
                    if not self.wav:
                        self._drain_queue()
                        self._pending = np.zeros(0, dtype=np.float32)
                    self.reset_req.set()
                    self.last_audio = time.monotonic()
                    self.stalled = bool(self.args.simulate_stall)
                    if self.pipeline is not None:
                        with self.pipe_lock:
                            self.pipeline.set_state(self.Gst.State.PLAYING)
                    with self.out_lock:
                        self.paused = False
                    self.status("resumed")
            elif cmd == "quit":
                self.quit.set()
                return
        # stdin EOF: a live worker exits (parent gone); a --wav run keeps going to the end.
        if not self.wav:
            self.quit.set()

    def _drain_queue(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    # ---- decode -------------------------------------------------------------------------
    def make_recognizer(self):
        import sherpa_onnx

        from . import models
        from .config import ASRConfig

        a = self.args
        if a.model:
            d = models.Path(a.model).expanduser()
            cfg = ASRConfig(model=d.name, models_dir=str(d.parent))
        else:
            cfg = ASRConfig()
        mp = models.resolve(cfg) if cfg.model in models.REGISTRY else models.resolve_dir(d)
        self.model_name = mp.name
        self.uppercase = mp.uppercase
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(mp.tokens), encoder=str(mp.encoder), decoder=str(mp.decoder), joiner=str(mp.joiner),
            num_threads=a.threads, sample_rate=RATE, feature_dim=80, provider="cpu",
            decoding_method="greedy_search", enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=a.endpoint_rule2,
            rule3_min_utterance_length=20.0,
        )

    def words_from(self, r):
        words, cur, cur_t = [], "", None
        last = 0.0
        toks = list(r.tokens)
        ts = list(r.timestamps)
        for i, tok in enumerate(toks):
            t = (ts[i] if i < len(ts) else last) + float(getattr(r, "start_time", 0.0) or 0.0)
            t = max(t, last)
            last = t
            if tok[:1] in ("▁", " ") or cur_t is None:  # NeMo models mark words with a leading space
                if cur:
                    words.append({"w": cur, "t": round(cur_t, 3)})
                cur, cur_t = tok.lstrip("▁ "), t
            else:
                cur += tok
        if cur:
            words.append({"w": cur, "t": round(cur_t, 3)})
        words = [w for w in words if w["w"]]
        if self.uppercase:
            for w in words:
                w["w"] = w["w"].lower()
        return words

    def decode_loop(self, rec):
        stream = rec.create_stream()
        utt = 0
        last_text = ""
        level_acc, level_n = 0.0, 0
        play_t0, play_a0 = None, 0.0

        def result():
            r = rec.get_result_all(stream)
            ws = self.words_from(r)
            text = " ".join(w["w"] for w in ws)
            return text, ws

        def finish_utt(arrived=None):
            nonlocal utt, last_text
            text, ws = result()
            if text.strip():
                self.emit({"type": "final", "utt": utt, "t": self.stamp(arrived), "text": text, "words": ws})
                utt += 1
            last_text = ""
            rec.reset(stream)

        while not self.quit.is_set():
            if self.reset_req.is_set():  # after resume
                self.reset_req.clear()
                rec.reset(stream)
                last_text = ""
                play_t0 = None
            if self.wav and self.paused:  # hold the WAV position (the file is queued ahead)
                time.sleep(0.02)
                continue
            try:
                item = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is _EOS:
                # tail padding: a streaming transducer needs trailing context to emit the last word
                stream.accept_waveform(RATE, np.zeros(int(0.66 * RATE), dtype=np.float32))
                stream.input_finished()
                while rec.is_ready(stream):
                    rec.decode_stream(stream)
                finish_utt()
                self.status("eof")  # tells the app the WAV ended normally (vs. a kill)
                break
            if self.paused:
                continue
            blk, arrived = item
            if self.wav and self.args.realtime:
                self.audio_t += len(blk) / RATE
                # Deadline pacing: hand each block over when its last sample would have been
                # captured live, so decode time does not accumulate as drift.
                if play_t0 is None:
                    play_t0, play_a0 = time.monotonic(), self.audio_t - len(blk) / RATE
                delay = play_t0 + (self.audio_t - play_a0) - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                blocks = [blk]
            else:
                # Live mic: take everything already queued, so a backlog after a slow decode is
                # caught up in one pass instead of one result per block. (--fast WAV runs keep
                # one block per pass so they still produce a partial stream.)
                blocks = [blk]
                while not self.wav:
                    try:
                        nxt = self.q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is _EOS:
                        self.q.put(_EOS)
                        break
                    blocks.append(nxt[0])
                    arrived = nxt[1]
                self.audio_t += sum(len(b) for b in blocks) / RATE
            for b in blocks:
                level_acc += float(np.dot(b, b))
                level_n += len(b)
                stream.accept_waveform(RATE, b)
            if level_n >= LEVEL_EVERY:
                self.emit({"type": "level", "rms": round(math.sqrt(level_acc / level_n), 4), "t": self.now()})
                level_acc, level_n = 0.0, 0
            if not rec.is_ready(stream):
                continue  # nothing new can be decoded yet: the result cannot have changed
            while rec.is_ready(stream):
                rec.decode_stream(stream)
            text, ws = result()
            if text and text != last_text:
                last_text = text
                self.emit({"type": "partial", "utt": utt, "t": self.stamp(arrived), "text": text, "words": ws})
            if rec.is_endpoint(stream):
                finish_utt(arrived)

    # ---- main ---------------------------------------------------------------------------
    def run(self) -> int:
        try:
            rec = self.make_recognizer()
        except Exception as e:  # noqa: BLE001
            self.status("error", message=f"model load failed: {e}")
            return 2
        try:
            self.build_pipeline()
        except Exception as e:  # noqa: BLE001
            self.status("error", message=f"audio pipeline failed: {e}")
            return 2
        self.status("ready", engine="sherpa-onnx", model=self.model_name, device=self.args.device or "default")
        dec = threading.Thread(target=self.decode_loop, args=(rec,))
        dec.start()
        Gst = self.Gst
        self.pipeline.set_state(Gst.State.PLAYING)
        threading.Thread(target=self.stdin_loop, daemon=True).start()  # after PLAYING so pause wins
        bus = self.pipeline.get_bus()
        rc = 0
        dead_tries = 0
        while dec.is_alive() and not self.quit.is_set():
            msg = bus.timed_pop_filtered(100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg is None:
                if not self.wav and not self.paused and time.monotonic() - self.last_audio > NO_AUDIO_S:
                    dead_tries += 1
                    if dead_tries > NO_AUDIO_TRIES:
                        self.status("error", message="no audio from the microphone")
                        rc = 1
                        self.quit.set()
                        break
                    self.status("warning", message=f"no audio for {NO_AUDIO_S:.0f} s; restarting capture "
                                                   f"({dead_tries}/{NO_AUDIO_TRIES})")
                    with self.pipe_lock:
                        self.pipeline.set_state(Gst.State.NULL)
                        self.pipeline.set_state(Gst.State.PLAYING)
                    self.stalled = False
                    self.last_audio = time.monotonic()
                elif time.monotonic() - self.last_audio < 1.0:
                    dead_tries = 0
                continue
            if msg.type == Gst.MessageType.EOS:
                while self.paused and not self.quit.is_set():  # file ended while paused: wait
                    time.sleep(0.05)
                if self.quit.is_set():
                    break
                self._pending = np.zeros(0, dtype=np.float32)
                self.q.put(_EOS)
                dec.join()
                break
            err, _dbg = msg.parse_error()
            self.status("error", message=str(err))
            rc = 1
            self.quit.set()
        self.quit.set()
        dec.join(timeout=2)
        self.pipeline.set_state(Gst.State.NULL)
        return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="promptbro.asr_worker")
    ap.add_argument("--model", help="model directory (default: config default in ~/.local/share)")
    ap.add_argument("--device", default="", help="PipeWire node name (default: system default source)")
    ap.add_argument("--wav", help="decode this WAV file instead of the microphone")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--fast", dest="realtime", action="store_false", help="WAV: as fast as possible")
    g.add_argument("--realtime", dest="realtime", action="store_true", help="WAV: real-time pacing (default)")
    ap.set_defaults(realtime=True)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--endpoint-rule2", type=float, default=1.0)
    ap.add_argument("--block-ms", type=int, default=BLOCK_MS, help="audio block size fed to the recognizer")
    ap.add_argument("--live-source", help=argparse.SUPPRESS)  # tests: gst source used like the mic
    ap.add_argument("--simulate-stall", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if not args.wav and not args.live_source and os.environ.get("PROMPTBRO_TEST"):
        print("asr_worker: PROMPTBRO_TEST is set; tests never open the microphone (use --wav)",
              file=sys.stderr)
        return 3
    w = Worker(args)
    signal.signal(signal.SIGTERM, lambda *_: w.quit.set())
    signal.signal(signal.SIGINT, lambda *_: w.quit.set())
    return w.run()


if __name__ == "__main__":
    sys.exit(main())
