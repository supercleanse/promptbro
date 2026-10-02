"""Configuration loading and validation for promptbro."""
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths

log = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when configuration validation fails."""
    pass


@dataclass
class WindowConfig:
    """Window and rendering configuration."""
    monitor: str = "auto"   # "auto" = focused monitor; or a connector name to pin
    width: int = 720
    height: int = 240
    top_gap: int = 8
    opacity: float = 0.75   # panel background alpha only; text stays opaque
    click_through: bool = True
    hide_from_screen_share: bool = True   # emitted as a Hyprland no_screen_share layer rule
    font: str = "Adwaita Sans"
    current_pt: int = 26
    other_pt: int = 17
    history_lines: int = 3
    upcoming_lines: int = 3
    animation_ms: int = 180   # ease-out; the spoken-word highlight itself is immediate
    max_line_chars: int = 90
    show_legend: bool = True   # dim hotkey legend along the bottom edge

    def panel_height(self) -> int:
        """Window height: the script area (`height`) plus the legend strip when shown."""
        from .legend import LEGEND_STRIP_PX
        return self.height + (LEGEND_STRIP_PX if self.show_legend else 0)

    def validate(self) -> None:
        """Validate window configuration."""
        if not isinstance(self.width, int) or self.width <= 0:
            raise ConfigError(f"window.width must be a positive integer, got {self.width!r}")
        if not isinstance(self.height, int) or self.height <= 0:
            raise ConfigError(f"window.height must be a positive integer, got {self.height!r}")
        if not isinstance(self.top_gap, int) or self.top_gap < 0:
            raise ConfigError(f"window.top_gap must be a non-negative integer, got {self.top_gap!r}")
        if not isinstance(self.opacity, (int, float)) or not (0.3 <= self.opacity <= 1.0):
            raise ConfigError(f"window.opacity must be between 0.3 and 1.0, got {self.opacity!r}")
        if not isinstance(self.current_pt, int) or self.current_pt <= 0:
            raise ConfigError(f"window.current_pt must be a positive integer, got {self.current_pt!r}")
        if not isinstance(self.other_pt, int) or self.other_pt <= 0:
            raise ConfigError(f"window.other_pt must be a positive integer, got {self.other_pt!r}")
        if not isinstance(self.show_legend, bool):
            raise ConfigError(f"window.show_legend must be true or false, got {self.show_legend!r}")
        if not isinstance(self.max_line_chars, int) or self.max_line_chars <= 0:
            raise ConfigError(f"window.max_line_chars must be a positive integer, got {self.max_line_chars!r}")


@dataclass
class ASRConfig:
    """Automatic speech recognition configuration."""
    engine: str = "sherpa-onnx"
    model: str = paths.DEFAULT_MODEL
    models_dir: str = field(default_factory=lambda: str(paths.models_dir()))
    num_threads: int = 1
    device: str = "auto"   # "auto" (or ""): never a Bluetooth mic; else a node name, glob or substring
    endpoint_rule2_s: float = 1.0
    hotwords: bool = False
    vad_rms: float = 0.01    # level RMS above this counts as voice (status dot, coasting)

    def validate(self) -> None:
        """Validate ASR configuration."""
        if self.engine not in ("sherpa-onnx", "vosk"):
            raise ConfigError(f"asr.engine must be 'sherpa-onnx' or 'vosk', got {self.engine!r}")
        if not isinstance(self.num_threads, int) or self.num_threads <= 0:
            raise ConfigError(f"asr.num_threads must be a positive integer, got {self.num_threads!r}")
        if not isinstance(self.endpoint_rule2_s, (int, float)) or self.endpoint_rule2_s <= 0:
            raise ConfigError(f"asr.endpoint_rule2_s must be a positive number, got {self.endpoint_rule2_s!r}")
        if not isinstance(self.vad_rms, (int, float)) or not (0.0 <= self.vad_rms <= 1.0):
            raise ConfigError(f"asr.vad_rms must be between 0.0 and 1.0, got {self.vad_rms!r}")


@dataclass
class AlignerConfig:
    """Aligner configuration (§6 parameters)."""
    tail_tokens: int = 6
    ahead_units: int = 40
    display_coasting: bool = False
    line_complete_on_final: bool = False

    def validate(self) -> None:
        """Validate aligner configuration."""
        if not isinstance(self.tail_tokens, int) or self.tail_tokens < 0:
            raise ConfigError(f"aligner.tail_tokens must be a non-negative integer, got {self.tail_tokens!r}")
        if not isinstance(self.ahead_units, int) or self.ahead_units < 0:
            raise ConfigError(f"aligner.ahead_units must be a non-negative integer, got {self.ahead_units!r}")


@dataclass
class LogConfig:
    """Logging configuration."""
    level: str = "info"

    def validate(self) -> None:
        """Validate logging configuration."""
        valid_levels = ("debug", "info", "warning", "error", "critical")
        if self.level.lower() not in valid_levels:
            raise ConfigError(f"log.level must be one of {valid_levels}, got {self.level!r}")


@dataclass
class Config:
    """Complete promptbro configuration."""
    window: WindowConfig = field(default_factory=WindowConfig)
    asr: ASRConfig = field(default_factory=ASRConfig)
    aligner: AlignerConfig = field(default_factory=AlignerConfig)
    log: LogConfig = field(default_factory=LogConfig)

    def validate(self) -> None:
        """Validate all configuration sections."""
        self.window.validate()
        self.asr.validate()
        self.aligner.validate()
        self.log.validate()


def load(path: Optional[Path] = None) -> Config:
    """Load configuration from a TOML file.

    Args:
        path: Path to config file. If None, uses ~/.config/promptbro/config.toml.

    Returns:
        Loaded and validated Config object.

    Raises:
        ConfigError: If validation fails.
    """
    if path is None:
        path = paths.config_file()

    path = Path(path)
    config = Config()

    if not path.exists():
        log.info(f"Config file {path} does not exist, using defaults")
        config.validate()
        return config

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Process window section
    if "window" in data:
        window_data = data["window"]
        _warn_unknown_keys(window_data, set(Config().window.__dataclass_fields__.keys()), "window")
        for key, value in window_data.items():
            if key in Config().window.__dataclass_fields__:
                setattr(config.window, key, value)

    # Process ASR section
    if "asr" in data:
        asr_data = data["asr"]
        _warn_unknown_keys(asr_data, set(Config().asr.__dataclass_fields__.keys()), "asr")
        for key, value in asr_data.items():
            if key in Config().asr.__dataclass_fields__:
                setattr(config.asr, key, value)

    # Process aligner section
    if "aligner" in data:
        aligner_data = data["aligner"]
        _warn_unknown_keys(aligner_data, set(Config().aligner.__dataclass_fields__.keys()), "aligner")
        for key, value in aligner_data.items():
            if key in Config().aligner.__dataclass_fields__:
                setattr(config.aligner, key, value)

    # Process log section
    if "log" in data:
        log_data = data["log"]
        _warn_unknown_keys(log_data, set(Config().log.__dataclass_fields__.keys()), "log")
        for key, value in log_data.items():
            if key in Config().log.__dataclass_fields__:
                setattr(config.log, key, value)

    # Warn about top-level unknown keys
    known_sections = {"window", "asr", "aligner", "log"}
    _warn_unknown_keys(data, known_sections, None)

    config.validate()
    return config


def _warn_unknown_keys(data: Dict[str, Any], known_keys: set, section: Optional[str]) -> None:
    """Warn about unknown keys in configuration."""
    unknown = set(data.keys()) - known_keys
    if unknown:
        section_str = f"{section}." if section else ""
        log.warning(f"Unknown configuration keys: {', '.join(sorted(section_str + k for k in unknown))}")
