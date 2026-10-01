"""Every filesystem location PromptBro uses, in one stdlib-only module.

Honors XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_STATE_HOME and XDG_RUNTIME_DIR, plus the test
override PROMPTBRO_RUNTIME_DIR. Everything is computed on call, so tests can change the
environment (or HOME) and get fresh answers.
"""
from __future__ import annotations

import os
from pathlib import Path

NAME = "promptbro"
OLD_NAME = "teleprompter"
APP_ID = "supercleanse.PromptBro"
DEFAULT_MODEL = "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25"
OLD_APP_ID = "com.aldus.Teleprompter"


def _home() -> Path:
    return Path(os.environ.get("HOME") or os.path.expanduser("~"))


def _xdg(var: str, fallback: str) -> Path:
    v = os.environ.get(var)
    return Path(v) if v and os.path.isabs(v) else _home() / fallback


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state")


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


# ---- current locations -------------------------------------------------------------------
def config_dir() -> Path:
    return config_home() / NAME


def config_file() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    return data_home() / NAME


def venv_dir() -> Path:
    return data_dir() / "venv"


def venv_python() -> Path:
    return venv_dir() / "bin" / "python3"


def deps_stamp() -> Path:
    return venv_dir() / ".promptbro-deps"


def models_dir() -> Path:
    return data_dir() / "models"


def tts_dir() -> Path:
    return data_dir() / "tts"


def state_dir() -> Path:
    return state_home() / NAME


def state_file() -> Path:
    return state_dir() / "state.json"


def recent_file() -> Path:
    return state_dir() / "recent.json"


def log_file() -> Path:
    return state_dir() / "promptbro.log"


def runtime_dir() -> Path:
    override = os.environ.get("PROMPTBRO_RUNTIME_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(base) / NAME


def status_file() -> Path:
    return runtime_dir() / "status.json"


def applications_dir() -> Path:
    return data_home() / "applications"


def desktop_file() -> Path:
    return applications_dir() / f"{APP_ID}.desktop"


def icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def bin_link() -> Path:
    return _home() / ".local" / "bin" / NAME


# ---- the old Teleprompter locations (read by migrate and doctor only) ----------------------
def old_config_dir() -> Path:
    return config_home() / OLD_NAME


def old_data_dir() -> Path:
    return data_home() / OLD_NAME


def old_state_dir() -> Path:
    return state_home() / OLD_NAME


def old_desktop_file() -> Path:
    return applications_dir() / f"{OLD_APP_ID}.desktop"


def old_icon_file() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{OLD_APP_ID}.svg"


def old_bin_link() -> Path:
    return _home() / ".local" / "bin" / OLD_NAME


def tilde(p) -> str:
    """``~``-relative spelling of a path under HOME (for config values and messages)."""
    s = str(p)
    h = str(_home())
    return "~" + s[len(h):] if s == h or s.startswith(h + os.sep) else s


def expand(p) -> str:
    """Expand a leading ``~`` against HOME (HOME as the process sees it, so tests can redirect it)."""
    s = str(p)
    return str(_home()) + s[1:] if s == "~" or s.startswith("~/") else s
