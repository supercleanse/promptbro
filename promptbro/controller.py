"""Controller: script, aligner, ASR worker supervision, verbs, persisted state (DESIGN.md 8).

Owns the `Script` (script.py), the pure `Aligner` (aligner.py) and one input source:

  mic    - spawns `python -m promptbro.asr_worker` with Gio.Subprocess, reads its JSON lines
  wav    - same worker with `--wav FILE --realtime`; the run ends when the worker reports `eof`
  replay - reads a recorded event stream (.json / .json.gz) and schedules each event at its `t`
  None   - no listening (the --demo path, or tests); manual keys still work

Every partial/final goes aligner.on_event -> view.set_position on the GLib main loop.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path

from gi.repository import Gio, GLib

from . import __version__, models, paths, statusfile
from .aligner import Aligner, Event, Params, Position
from .script import Script, load_text as script_from_text

log = logging.getLogger("promptbro.controller")

RESTART_DELAY_MS = 2000
MAX_RESTARTS = 5
HEALTHY_S = 60.0          # a worker that ran this long resets the restart count
COAST_IDLE_S = 0.8        # display_coasting: cursor idle this long while voice is active
COAST_AHEAD = 8           # ... and never more than this far past committed
LEVEL_FRESH_S = 0.75      # a level event older than this says nothing about voice now
CAPTURE_STALE_S = 1.5     # listening, but no audio (level events) this long -> "no-audio"
PKG_ROOT = Path(__file__).resolve().parent.parent


def state_path() -> Path:
    return paths.state_file()


def load_replay(path: str) -> dict:
    """A replay file: {"events": [...], "script"?: str, "start_unit"?: int}, or a bare list /
    JSON lines of worker events. Gzip is detected by the .gz suffix."""
    op = gzip.open if str(path).endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        raw = f.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = [json.loads(x) for x in raw.splitlines() if x.strip()]
    if isinstance(data, list):
        data = {"events": data}
    data["events"] = sorted(data.get("events", []), key=lambda e: float(e.get("t", 0.0)))
    return data


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 2)


class Controller:
    def __init__(self, view, cfg, source: str | None = None, wav: str | None = None,
                 replay: str | None = None, model: str | None = None, device: str | None = None):
        self.view = view
        self.cfg = cfg
        self.params = Params.from_config(cfg.aligner)
        self.script: Script | None = None
        self.aligner: Aligner | None = None
        self.path: str | None = None
        self.pos = Position()
        self.paused = False
        self.visible = False
        self._save_id = 0
        self._saved_line = None
        # input source
        self.source = source            # "mic" | "wav" | "replay" | None
        self.wav = str(Path(wav).expanduser().resolve()) if wav else None
        self.replay_path = replay
        self.model = model
        self.device = cfg.asr.device if device is None else device
        self.on_finished = None         # callback when a wav/replay input ends
        # worker state
        self.proc: Gio.Subprocess | None = None
        self._stdin = None
        self._reader = None
        self._cancel = None
        self.worker_state = "stopped"   # stopped | starting | running | restarting | failed | finished
        self.worker_error = ""
        self.restarts = 0
        self._spawned_at = 0.0
        self._quitting = False
        self._eof = False
        self._restart_id = 0
        self._anchor = time.monotonic()  # monotonic time of the input's t == 0
        self._override: str | None = None  # "error" / "no-mic" while the worker is down
        # status file for the bar widget (started by the app with start_status())
        self.status_writer: statusfile.StatusWriter | None = None
        self.last_toast = ""
        self._toast_id = 0
        # replay state
        self._replay_ids: list[int] = []
        self._replay_started = False
        self._replay_data: dict | None = None
        # voice / coasting
        self.voice_active = False
        self._rms = 0.0
        self._level_t = 0.0
        self._listen_since = 0.0        # when audio was last expected to start (ready / resume)
        self._health_id = 0
        self._pid = None
        self._last_move_t = time.monotonic()
        self._last_unit = -1
        self._wps = 0.0
        self._coast_floor = -1
        self._coast_id = 0
        # latency instrumentation
        self.align_ms: deque = deque(maxlen=2000)
        self.event_age_ms: deque = deque(maxlen=2000)
        if replay:
            self._replay_data = load_replay(replay)

    # ================================================================ script
    def load_text(self, text: str, path: str | None, restore: bool = True) -> int:
        script = script_from_text(text, self.cfg.window.max_line_chars)
        if not script.lines:
            raise ValueError("script is empty")
        self.script = script
        self.aligner = Aligner(script, self.params)
        self.path = path
        self.view.set_script(script)
        line = 0
        if restore and path:
            st = self._read_state()
            if st.get("path") == path and isinstance(st.get("line"), int):
                line = max(0, min(len(script.lines) - 1, st["line"]))
        self.paused = False
        self._coast_floor = -1
        start = (self._replay_data or {}).get("start_unit")
        if start is not None:
            self.aligner.reset(int(start))
            pos = self.aligner.emit()
        elif line:
            pos = self.aligner.on_user_key("goto_line", line=line)
        else:
            pos = self.aligner.emit()
        self._apply(pos, jump=True)
        self._toast(f"Script loaded: {Path(path).name}" if path else "Script loaded")
        return len(script.lines)

    def load_file(self, path: str, restore: bool = True) -> int:
        p = str(Path(path).expanduser().resolve())
        return self.load_text(Path(p).read_text(encoding="utf-8"), p, restore)

    def load_replay_script(self) -> bool:
        """Load the script embedded in the replay file, if any (used when no --open is given)."""
        text = (self._replay_data or {}).get("script")
        if not text:
            return False
        self.load_text(text, None, restore=False)
        return True

    # ============================================================== movement
    def _apply(self, pos: Position, jump: bool = False) -> None:
        """Send a position to the view. The line never scrolls backward on its own."""
        now = time.monotonic()
        if pos.unit != self._last_unit:
            if pos.unit > self._last_unit >= 0 and pos.state == "tracking":
                dt = now - self._last_move_t
                if 0 < dt < 3:
                    inst = (pos.unit - self._last_unit) / dt
                    self._wps = inst if self._wps == 0 else 0.8 * self._wps + 0.2 * inst
            self._last_unit = pos.unit
            self._last_move_t = now
        if self._coast_floor > pos.unit and self.script:
            u = self._coast_floor
            pos = Position(self.script.units[u].line, u, pos.committed_unit, pos.line_progress, pos.state)
        if self._override and pos.state != "paused":
            pos = Position(pos.line, pos.unit, pos.committed_unit, pos.line_progress, self._override)
        line_changed = pos.line != self.pos.line
        if pos.state != self.pos.state:
            log.info("state %s -> %s (line %d, unit %d)", self.pos.state, pos.state, pos.line, pos.unit)
        self.pos = pos
        if jump:
            self.view.jump_to(pos.line)
        self.view.set_position(pos)
        if line_changed or jump:
            self._schedule_save()
        self._changed()

    def _key(self, key: str, line: int | None = None) -> None:
        if not self.aligner:
            return
        self._coast_floor = -1
        self._apply(self.aligner.on_user_key(key, line=line))

    def next(self): self._key("line_next")
    def prev(self): self._key("line_prev")
    def goto(self, n: int): self._key("goto_line", n)
    def restart(self): self._key("restart")

    # ======================================================= pause / visible
    def set_paused(self, paused: bool, why: str = "verb") -> None:
        if paused != self.paused:
            log.info("%s (%s)", "paused" if paused else "resumed", why)
        self.paused = paused
        if self.aligner:
            self._apply(self.aligner.set_paused(paused))
        else:
            self.view.set_status("paused" if paused else "holding")
        if paused:
            self._send("pause")
        else:
            self._listen_since = time.monotonic()
            had_worker = self.proc is not None
            self.ensure_input()
            if had_worker:
                self._send("resume")
        self._toast("Paused" if paused else "Listening")

    def toggle_pause(self): self.set_paused(not self.paused, "pause verb (toggle)")

    def set_visible(self, visible: bool) -> None:
        """`hide` implies pause; `show` implies resume (DESIGN 8)."""
        if visible != self.visible:
            log.info("window %s", "shown" if visible else "hidden")
        self.visible = visible
        if self.script:
            self.set_paused(not visible, "window shown" if visible else "window hidden")
        self._changed()

    # ================================================================ input
    def ensure_input(self) -> None:
        """Start listening once a script is loaded and we are not paused."""
        if not self.script or self.paused:
            return
        if self.source == "replay":
            if not self._replay_started:
                self._start_replay()
        elif self.source in ("mic", "wav"):
            if self.worker_state in ("stopped",):
                self._spawn()
        if self.params.display_coasting and not self._coast_id:
            self._coast_id = GLib.timeout_add(100, self._coast_tick)

    # ---- worker ---------------------------------------------------------
    def _model_dir(self) -> Path | None:
        if self.model:
            d = Path(self.model).expanduser()
            return d if (d / "tokens.txt").exists() else None
        if not models.verify(self.cfg.asr.model, self.cfg.asr.models_dir):
            return None
        return Path(self.cfg.asr.models_dir).expanduser() / self.cfg.asr.model

    def worker_argv(self, model_dir: Path) -> list[str]:
        a = self.cfg.asr
        argv = [sys.executable, "-m", "promptbro.asr_worker", "--model", str(model_dir),
                "--threads", str(a.num_threads), "--endpoint-rule2", str(a.endpoint_rule2_s)]
        if self.device:
            argv += ["--device", self.device]
        if self.source == "wav":
            argv += ["--wav", self.wav, "--realtime"]
        return argv

    def _spawn(self) -> None:
        self._restart_id = 0
        md = self._model_dir()
        if md is None:
            self._fail("speech model missing - run: python -m promptbro.models download",
                       toast="Speech model missing - see log")
            return
        argv = self.worker_argv(md)
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE
                                              | Gio.SubprocessFlags.STDOUT_PIPE
                                              | Gio.SubprocessFlags.STDERR_PIPE)
        pp = os.environ.get("PYTHONPATH")
        launcher.setenv("PYTHONPATH", f"{PKG_ROOT}{os.pathsep}{pp}" if pp else str(PKG_ROOT), True)
        try:
            proc = launcher.spawnv(argv)
        except GLib.Error as e:
            self._worker_down(f"spawn failed: {e.message}")
            return
        self.proc = proc
        self._eof = False
        self.worker_state = "starting"
        self._changed()
        self._spawned_at = time.monotonic()
        self._stdin = proc.get_stdin_pipe()
        self._cancel = Gio.Cancellable()
        self._reader = Gio.DataInputStream.new(proc.get_stdout_pipe())
        self._pid = proc.get_identifier()
        log.info("worker started pid=%s (%s)", self._pid, " ".join(argv[1:]))
        if not self._health_id:
            self._health_id = GLib.timeout_add(500, self._capture_health)
        self._read_next(proc)
        err = Gio.DataInputStream.new(proc.get_stderr_pipe())
        err.read_line_async(GLib.PRIORITY_LOW, self._cancel, self._on_err_line, err)
        proc.wait_async(None, self._on_exit, proc)
        if self.paused:
            self._send("pause")

    def _read_next(self, proc) -> None:
        self._reader.read_line_async(GLib.PRIORITY_DEFAULT, self._cancel, self._on_line,
                                     (proc, self._reader))

    def _on_line(self, stream, res, data) -> None:
        proc, reader = data
        try:
            line, _n = stream.read_line_finish_utf8(res)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                log.warning("worker read error: %s", e.message)
            return
        if line is None:      # EOF
            return
        if proc is self.proc:
            try:
                ev = json.loads(line)
            except ValueError:
                log.warning("worker: bad line %r", line[:200])
            else:
                self.handle_event(ev)
            if proc is self.proc and reader is self._reader:
                self._read_next(proc)

    def _on_err_line(self, stream, res, err) -> None:
        """Worker stderr (tracebacks, GStreamer/onnxruntime warnings) goes to our log file."""
        try:
            line, _n = stream.read_line_finish_utf8(res)
        except GLib.Error:
            return
        if line is None:
            return
        if line.strip():
            log.warning("worker stderr: %s", line.rstrip()[:500])
        err.read_line_async(GLib.PRIORITY_LOW, None, self._on_err_line, err)

    def _on_exit(self, proc, res, _data) -> None:
        try:
            proc.wait_finish(res)
        except GLib.Error:
            pass
        if proc is not self.proc:
            return
        how = (f"signal {proc.get_term_sig()}" if proc.get_if_signaled()
               else f"status {proc.get_exit_status()}")
        log.info("worker pid %s exited (%s)", self._pid, how)
        self.proc, self._stdin = None, None
        if self._quitting:
            self.worker_state = "stopped"
            return
        if self._eof:
            log.info("worker finished (end of WAV, %s)", how)
            self.worker_state = "finished"
            self._finish()
            return
        if time.monotonic() - self._spawned_at > HEALTHY_S:
            self.restarts = 0
        self._worker_down(f"worker exited ({how})")

    def _worker_down(self, why: str) -> None:
        if self.restarts >= MAX_RESTARTS:
            self._fail(f"{why}; gave up after {MAX_RESTARTS} restarts",
                       toast="Speech engine stopped - see log")
            return
        self.restarts += 1
        self.worker_state = "restarting"
        self.worker_error = why
        self._changed()
        log.warning("%s; restarting in %.0f s (attempt %d/%d)", why, RESTART_DELAY_MS / 1000,
                    self.restarts, MAX_RESTARTS)
        self._set_override("no-mic" if "audio" in why.lower() else "error")
        self._restart_id = GLib.timeout_add(RESTART_DELAY_MS, self._restart)

    def _restart(self) -> bool:
        self._restart_id = 0
        if not self._quitting and self.worker_state == "restarting":
            self.worker_state = "stopped"
            self._spawn()
        return GLib.SOURCE_REMOVE

    def _fail(self, why: str, toast: str) -> None:
        log.error("%s", why)
        self.worker_state = "failed"
        self.worker_error = why
        self._changed()
        self._set_override("error")
        self._toast(toast)

    def _set_override(self, state: str | None) -> None:
        self._override = state
        self._changed()
        if self.aligner:
            self._apply(self.aligner.emit())
        elif state:
            self.view.set_status(state)

    def _send(self, cmd: str) -> None:
        if self._stdin is None:
            return
        try:
            self._stdin.write_all((cmd + "\n").encode(), None)
            self._stdin.flush(None)
            log.info("worker <- %s", cmd)
        except GLib.Error as e:
            log.warning("could not send %r to worker: %s", cmd, e.message)

    # ---- replay ---------------------------------------------------------
    def _start_replay(self) -> None:
        self._replay_started = True
        evs = self._replay_data["events"]
        self._anchor = time.monotonic()
        self.worker_state = "running"
        log.info("replay started: %d events from %s", len(evs), self.replay_path)
        for ev in evs:
            ms = max(0, int(round(float(ev.get("t", 0.0)) * 1000)))
            self._replay_ids.append(GLib.timeout_add(ms, self._replay_one, ev))
        end = (float(evs[-1].get("t", 0.0)) if evs else 0.0) + 0.5
        self._replay_ids.append(GLib.timeout_add(int(end * 1000), self._replay_done))

    def _replay_one(self, ev) -> bool:
        self.handle_event(ev)
        return GLib.SOURCE_REMOVE

    def _replay_done(self) -> bool:
        log.info("replay finished")
        self.worker_state = "finished"
        self._finish()
        return GLib.SOURCE_REMOVE

    def _finish(self) -> None:
        if self.on_finished:
            self.on_finished()

    # ---- events ---------------------------------------------------------
    def handle_event(self, ev: dict) -> None:
        typ = ev.get("type")
        t = float(ev.get("t", 0.0))
        if typ == "level":
            self._rms = float(ev.get("rms", 0.0))
            self.voice_active = self._rms > self.cfg.asr.vad_rms
            self._level_t = time.monotonic()
            return
        if typ == "status":
            st = ev.get("state")
            if st == "ready":
                if self.source != "replay":
                    self._anchor = time.monotonic() - t
                self.worker_state = "running"
                self.worker_error = ""
                self._changed()
                self._listen_since = time.monotonic()
                log.info("worker ready (%s, %s)", ev.get("model", "?"), ev.get("device", "?"))
                if self._override:
                    self._set_override(None)
            elif st in ("paused", "resumed"):
                log.info("worker -> %s", st)
            elif st == "warning":
                log.warning("worker: %s", ev.get("message", ""))
            elif st == "eof":
                self._eof = True
            elif st == "error":
                msg = str(ev.get("message", ""))
                log.error("worker error: %s", msg)
                self.worker_error = msg
                self._changed()
                self._set_override("no-mic" if "audio" in msg.lower() else "error")
            return
        if typ not in ("partial", "final") or not self.aligner or self.paused:
            return
        t_abs = self._anchor + t
        a0 = time.perf_counter()
        pos = self.aligner.on_event(Event(typ, t_abs, str(ev.get("text", ""))))
        align_ms = (time.perf_counter() - a0) * 1000
        self._apply(pos)
        age_ms = (time.monotonic() - t_abs) * 1000
        self.align_ms.append(align_ms)
        self.event_age_ms.append(age_ms)
        # The recognizer's words go to the log only at debug level (`[log] level = "debug"` or
        # --log-level debug): off by default, local only, for diagnosing where tracking stalled.
        log.debug("%s line=%d unit=%d committed=%d state=%s align_ms=%.2f event_age_ms=%.0f text=%r",
                  typ, pos.line, pos.unit, pos.committed_unit, pos.state, align_ms, age_ms,
                  str(ev.get("text", "")))

    # ---- capture health -------------------------------------------------
    def audio_age(self) -> float | None:
        """Seconds since the worker last reported audio (a level event), or None if not listening."""
        if self.worker_state != "running" or self.paused or self.source not in ("mic", "wav"):
            return None
        return time.monotonic() - max(self._level_t, self._listen_since)

    def _capture_health(self) -> bool:
        """Never show "tracking" while no audio flows (e.g. a capture that did not come back
        after resume): after CAPTURE_STALE_S without audio the dot goes to "no-audio"."""
        if self._quitting:
            self._health_id = 0
            return GLib.SOURCE_REMOVE
        age = self.audio_age()
        if age is not None and age > CAPTURE_STALE_S:
            if self._override is None:
                log.warning("no audio from the worker for %.1f s while listening", age)
                self._set_override("no-audio")
        elif self._override == "no-audio" and (age is not None or self.paused):
            if age is not None:
                log.info("audio flowing again")
            self._set_override(None)
        return GLib.SOURCE_CONTINUE

    # ---- display coasting (DESIGN 6.6 optional extra, off by default) ----------------
    def _coast_tick(self) -> bool:
        if not self.params.display_coasting or not self.aligner:
            self._coast_id = 0
            return GLib.SOURCE_REMOVE
        now = time.monotonic()
        voice = self.voice_fresh()
        idle = now - self._last_move_t
        if voice and not self.paused and self._wps > 0 and idle > COAST_IDLE_S:
            a = self.aligner
            cap = min(len(self.script.units) - 1, a.committed + COAST_AHEAD)
            target = min(cap, a.provisional + int(self._wps * (idle - COAST_IDLE_S)))
            if target > max(self._coast_floor, self.pos.unit):
                self._coast_floor = target
                self._apply(a.emit())
        return GLib.SOURCE_CONTINUE

    # ====================================================== widget status file
    def start_status(self) -> None:
        """Begin writing the status file (now, on every change, and every 5 s)."""
        if self.status_writer is None:
            self.status_writer = statusfile.StatusWriter(
                self.widget_status, add_timeout=GLib.timeout_add, remove_timeout=GLib.source_remove)
        self.status_writer.start()

    def widget_status(self) -> dict:
        """The status.json body (CONTRACT 4.2), without `updated_at`. `status()` is unchanged."""
        return statusfile.build(self, __version__)

    def _changed(self) -> None:
        if self.status_writer is not None:
            self.status_writer.poke()

    def _toast(self, text: str) -> None:
        self.view.toast(text)
        self.last_toast = text
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(4000, self._toast_expired)
        self._changed()

    def _toast_expired(self) -> bool:
        self._toast_id = 0
        self.last_toast = ""
        self._changed()
        return GLib.SOURCE_REMOVE

    def set_opacity(self, value: float) -> float:
        """Panel background alpha, clamped to 0.3-1.0 (text stays opaque)."""
        v = round(min(1.0, max(0.3, float(value))), 3)
        self.cfg.window.opacity = v
        view_cfg = getattr(self.view, "cfg", None)
        if view_cfg is not None and view_cfg is not self.cfg.window:
            view_cfg.opacity = v
        if hasattr(self.view, "queue_draw"):
            self.view.queue_draw()
        self._write_state({"opacity": v})
        self._changed()
        return v

    # ================================================================ state
    def voice_level_fresh(self) -> bool:
        return time.monotonic() - self._level_t < LEVEL_FRESH_S

    def voice_fresh(self) -> bool:
        return self.voice_active and self.voice_level_fresh() and not self.paused

    def status(self) -> dict:
        return {
            "path": self.path,
            "line": self.pos.line,
            "unit": self.pos.unit,
            "committed": self.pos.committed_unit,
            "lines": len(self.script.lines) if self.script else 0,
            "state": self.pos.state,
            "visible": self.visible,
            "engine": self.cfg.asr.engine,
            "source": self.source,
            # voice: the mic heard speech in the last level window (only while listening;
            # paused or hidden means no level events, so it reads false)
            "voice": self.voice_fresh(),
            "rms": round(self._rms, 4) if self.voice_level_fresh() else None,
            "audio_age_s": (lambda a: None if a is None else round(a, 2))(self.audio_age()),
            "worker": {
                "state": self.worker_state,
                "pid": int(self.proc.get_identifier()) if self.proc and self.proc.get_identifier() else None,
                "restarts": self.restarts,
                "error": self.worker_error or None,
            },
            "latency": {
                "align_ms_p90": _pct(self.align_ms, 0.9),
                "event_age_ms_p50": _pct(self.event_age_ms, 0.5),
                "event_age_ms_p90": _pct(self.event_age_ms, 0.9),
                "n": len(self.align_ms),
            },
            "cpu": None,
        }

    def _read_state(self) -> dict:
        try:
            return json.loads(state_path().read_text())
        except (OSError, ValueError):
            return {}

    def _schedule_save(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(1000, self.save_state)

    def save_state(self) -> bool:
        self._save_id = 0
        if not self.path:
            return GLib.SOURCE_REMOVE
        self._write_state({"path": self.path, "line": self.pos.line, "visible": self.visible})
        return GLib.SOURCE_REMOVE

    def _write_state(self, update: dict) -> None:
        """Merge `update` into state.json (keeps keys such as opacity), atomically."""
        p = state_path()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            data = self._read_state()
            data.update(update)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(p)
        except OSError as e:
            log.warning("could not write state: %s", e)

    def stop_input(self) -> None:
        """Stop the worker (quit, wait up to 1 s, then kill) and any pending replay events."""
        self._quitting = True
        ctx = GLib.MainContext.default()
        for sid in self._replay_ids:
            src = ctx.find_source_by_id(sid)
            if src is not None and not src.is_destroyed():
                src.destroy()
        self._replay_ids.clear()
        for attr in ("_restart_id", "_coast_id", "_health_id"):
            sid = getattr(self, attr)
            if sid:
                GLib.source_remove(sid)
                setattr(self, attr, 0)
        proc = self.proc
        if proc is None:
            return
        self._send("quit")
        if self._cancel:
            self._cancel.cancel()
        try:
            self._stdin.close(None)
        except (GLib.Error, AttributeError):
            pass
        deadline = time.monotonic() + 1.0
        while self.proc is proc and time.monotonic() < deadline:
            if not ctx.iteration(False):
                time.sleep(0.01)
        if self.proc is proc:
            log.warning("worker did not quit in 1 s; killing it")
            proc.force_exit()
        self.proc = None
        self.worker_state = "stopped"

    def shutdown(self) -> None:
        if self._save_id:
            GLib.source_remove(self._save_id)
            self._save_id = 0
        self.save_state()
        self.stop_input()
        if self._toast_id:
            GLib.source_remove(self._toast_id)
            self._toast_id = 0
        if self.status_writer is not None:
            self.status_writer.close(__version__)
