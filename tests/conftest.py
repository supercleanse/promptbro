"""Shared pytest fixtures and configuration.

Isolation (structural, for every test and every subprocess a test starts):
- PROMPTBRO_TEST=1: the app refuses the real app id (supercleanse.PromptBro) and the ASR
  worker refuses the microphone, so no test can send a verb to the author's running prompter or
  open his mic, even if it forgets --app-id-suffix / dbus-run-session.
- A private session bus for the whole run (DBUS_SESSION_BUS_ADDRESS), so GApplication
  single-instance lookups can never see the real bus.
- No WAYLAND_DISPLAY/DISPLAY and GDK_BACKEND=broadway: nothing can open a window on the
  live desktop. Tests that render start their own gtk4-broadwayd.
- XDG_STATE_HOME points at a temp dir: state.json, recent.json and the log file never touch
  the real ones.
- PROMPTBRO_RUNTIME_DIR points at a temp dir: status.json never touches the real one (the
  status writer refuses to write under PROMPTBRO_TEST without it).
"""
import atexit
import os
import shutil
import subprocess
import tempfile


def _isolate() -> None:
    os.environ["PROMPTBRO_TEST"] = "1"
    for k in ("WAYLAND_DISPLAY", "DISPLAY", "BROADWAY_DISPLAY"):
        os.environ.pop(k, None)
    os.environ["GDK_BACKEND"] = "broadway"
    state = tempfile.mkdtemp(prefix="promptbro-test-state-")
    os.environ["XDG_STATE_HOME"] = state
    atexit.register(shutil.rmtree, state, True)
    runtime = tempfile.mkdtemp(prefix="promptbro-test-run-")
    os.environ["PROMPTBRO_RUNTIME_DIR"] = runtime
    atexit.register(shutil.rmtree, runtime, True)
    if shutil.which("dbus-daemon"):
        p = subprocess.Popen(["dbus-daemon", "--session", "--nofork", "--print-address=1"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        addr = p.stdout.readline().strip()
        atexit.register(p.terminate)
    else:
        addr = ""
    # An address that cannot be the real bus even if dbus-daemon is missing.
    os.environ["DBUS_SESSION_BUS_ADDRESS"] = addr or "unix:path=/nonexistent/promptbro-test-bus"


_isolate()


# ---------------------------------------------------------------- aligner helpers (T3)

import pytest  # noqa: E402


def speak(text, t0=0.0, wps=2.5, partial_every=0.2, revise=None, drop_last_in_final=False):
    """Expand a sentence into word-by-word partials plus a final (DESIGN §6.8).

    Word i starts at t0 + i/wps. A partial is emitted every ``partial_every`` seconds
    holding the words started so far, so each word shows up in about two partials.
    ``revise`` maps a word index to the wrong word the recognizer shows while that word
    is the last one in the partial. The final arrives 0.3 s after the last word.
    """
    from promptbro.aligner import Event

    words = text.split()
    n = len(words)
    revise = {int(k): v for k, v in (revise or {}).items()}
    dur = n / wps
    events = []
    k = 0
    while True:
        t = t0 + k * partial_every
        if t >= t0 + dur - 1e-9:
            break
        count = min(n, int((t - t0) * wps + 1e-9) + 1)
        shown = list(words[:count])
        if count - 1 in revise:
            shown[-1] = revise[count - 1]
        events.append(Event("partial", round(t, 3), " ".join(shown)))
        k += 1
    fin = words[:-1] if drop_last_in_final else words
    events.append(Event("final", round(t0 + dur + 0.3, 3), " ".join(fin)))
    return events


@pytest.fixture
def speak_fn():
    return speak
