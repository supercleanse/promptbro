"""T7 integration: the real Controller + Aligner + view, driven by a replay file or the ASR worker.

Everything runs under a private gtk4-broadwayd and dbus-run-session with --app-id-suffix test;
nothing touches the live desktop, the running instance, or the microphone.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bwutil import free_display, stop_display

from promptbro import models
from promptbro.config import ASRConfig

ROOT = Path(__file__).resolve().parent.parent
SKIP = ROOT / "tests" / "data" / "skip.md"
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_skip.json.gz"
SKIP_READ = ROOT / "tests" / "audio" / "skip_read.wav"
OUT = ROOT / "tests" / "out"
ASR = ASRConfig()
HAVE_MODEL = models.verify(ASR.model, ASR.models_dir)

pytestmark = pytest.mark.skipif(shutil.which("gtk4-broadwayd") is None
                                or shutil.which("dbus-run-session") is None,
                                reason="needs gtk4-broadwayd and dbus-run-session")


@pytest.fixture
def broadway(tmp_path):
    disp = free_display()
    srv = subprocess.Popen(["gtk4-broadwayd", f":{disp}"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    time.sleep(1)
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[asr]\nmodels_dir = "{Path(ASR.models_dir).expanduser()}"\nnum_threads = 1\n')
    env = dict(os.environ, GDK_BACKEND="broadway", BROADWAY_DISPLAY=f":{disp}",
               XDG_STATE_HOME=str(tmp_path / "state"), PYTHONPATH=str(ROOT))
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DISPLAY", None)
    try:
        yield env, cfg
    finally:
        stop_display(srv, disp)


def _run(env, *args, timeout=60):
    return subprocess.run(["dbus-run-session", "--", sys.executable, "-m", "promptbro", *args],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def _statuses(text):
    return [json.loads(x) for x in text.splitlines() if x.startswith("{")]


# ------------------------------------------------------------------ PNG comparison

def _load(path):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(str(path))
    return pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_n_channels(), pb.get_pixels()


def _diff(a, b, ink_only=False):
    """Fraction of pixels that differ (any channel > 32). With ink_only, compare only whether a
    pixel is text ("ink", far from the card background), which ignores the highlight color."""
    w, h, rs, nc, da = a
    w2, h2, _, _, db = b
    assert (w, h) == (w2, h2)
    bg_o = (h // 2) * rs + 4 * nc          # card background, left edge of the center row
    bg = da[bg_o:bg_o + 3]
    n = 0
    for y in range(h):
        for x in range(w):
            o = y * rs + x * nc
            pa, pb = da[o:o + 3], db[o:o + 3]
            if ink_only:
                ia = max(abs(pa[i] - bg[i]) for i in range(3)) > 40
                ib = max(abs(pb[i] - bg[i]) for i in range(3)) > 40
                n += ia != ib
            else:
                n += max(abs(pa[i] - pb[i]) for i in range(3)) > 32
    return n / (w * h)


def test_replay_render_matches_line4(broadway):
    env, _cfg = broadway
    pngs = {k: OUT / f"{k}.png" for k in ("replay", "line4", "line1")}
    for p in pngs.values():
        p.unlink(missing_ok=True)
    r = _run(env, "--replay", str(FIXTURE), "--open", str(SKIP), "--app-id-suffix", "test",
             "--render-png", str(pngs["replay"]), "--render-at", "1.6")
    assert r.returncode == 0, r.stderr
    for line in (4, 1):
        r = _run(env, "--open", str(SKIP), "--app-id-suffix", "test",
                 "--render-png", str(pngs[f"line{line}"]), "--line", str(line))
        assert r.returncode == 0, r.stderr
    rep, l4, l1 = (_load(pngs[k]) for k in ("replay", "line4", "line1"))
    assert _diff(rep, l4) < 0.02            # same stack; only the spoken-word highlight differs
    assert _diff(rep, l4, ink_only=True) < 0.005
    assert _diff(rep, l1, ink_only=True) > 0.02   # and the comparison does discriminate


def test_replay_headless_status(broadway):
    env, cfg = broadway
    r = _run(env, "--config", str(cfg), "--replay", str(FIXTURE), "--open", str(SKIP),
             "--app-id-suffix", "test", "--headless-status", timeout=30)
    assert r.returncode == 0, r.stderr
    st = _statuses(r.stdout)
    assert st, r.stderr
    last = st[-1]
    assert (last["line"], last["unit"], last["committed"]) == (4, 30, 30)
    assert last["worker"]["state"] == "finished"
    assert last["latency"]["align_ms_p90"] < 5


# ------------------------------------------------------------------ real ASR worker on a WAV

needs_wav = pytest.mark.skipif(not (HAVE_MODEL and SKIP_READ.exists()),
                               reason="needs the ASR model and tests/audio/skip_read.wav "
                                      "(scripts/make_test_audio.sh)")


@needs_wav
def test_wav_end_to_end(broadway):
    env, cfg = broadway
    r = subprocess.run(["dbus-run-session", "--", str(ROOT / "bin" / "promptbro"), "--config", str(cfg),
                        "open", str(SKIP), "--wav", str(SKIP_READ), "--app-id-suffix", "test",
                        "--headless-status"], cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=90)
    assert r.returncode == 0, r.stderr
    st = _statuses(r.stdout)
    lines = [s["line"] for s in st]
    assert lines[-1] == 6, lines
    assert all(b >= a for a, b in zip(lines, lines[1:])), lines
    assert st[-1]["worker"]["state"] == "finished"
    assert st[-1]["latency"]["align_ms_p90"] < 5
    print("event_age_ms", st[-1]["latency"])


@needs_wav
def test_worker_crash_restart_and_pause(broadway, tmp_path):
    """Kill the worker mid-run: restart within 3 s, dot error -> tracking. Then pause/resume."""
    env, cfg = broadway
    base = [sys.executable, "-m", "promptbro", "--app-id-suffix", "test"]
    out, err = tmp_path / "out.txt", tmp_path / "err.txt"
    send = " ".join(base)
    script = f"""
set -e
send() {{ {send} "$@"; }}
{send} --config {cfg} open {SKIP} --wav {SKIP_READ} --headless-status >{out} 2>{err} & P=$!
sleep 3.5
PID=$(send status | python3 -c 'import json,sys; print(json.load(sys.stdin)["worker"]["pid"])')
echo "KILL $PID $(date +%H:%M:%S.%N)"
kill $PID  # SIGTERM, like `pkill -f asr_worker`, but only this test's worker
sleep 6.5
send pause; sleep 1.5; echo "PAUSED $(send status)"; send resume; sleep 1; echo "RESUMED $(send status)"
wait $P
"""
    r = subprocess.run(["dbus-run-session", "--", "bash", "-c", script], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    log = err.read_text()
    assert r.returncode == 0, r.stderr + log
    # restart logged within 3 s of the kill
    def secs(hms):
        h, m, sec = hms.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)

    kill_t = secs(r.stdout.split("KILL ")[1].split()[1])
    starts = [ln for ln in log.splitlines() if "worker started pid=" in ln]
    assert len(starts) == 2, log
    assert "restarting in 2 s (attempt 1/5)" in log
    restart_s = secs(starts[1].split()[0]) - kill_t
    assert 0 < restart_s < 3.0, restart_s
    print("restart_s", round(restart_s, 2))
    # status dot: error while down, then tracking again
    states = [s["state"] for s in _statuses(out.read_text())]
    i = states.index("error")
    assert "tracking" in states[i:], states
    # pause reaches the worker and the status; resume restores
    assert "worker <- pause" in log and "worker -> paused" in log
    assert json.loads(r.stdout.split("PAUSED ")[1].splitlines()[0])["state"] == "paused"
    assert "worker <- resume" in log and "worker -> resumed" in log
    assert json.loads(r.stdout.split("RESUMED ")[1].splitlines()[0])["state"] != "paused"
    lines = [s["line"] for s in _statuses(out.read_text())]
    assert all(b >= a for a, b in zip(lines, lines[1:])), lines
    assert lines[-1] == 6, lines
