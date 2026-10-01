"""Tests for configuration loading and validation."""
import logging
import tempfile
from pathlib import Path

import pytest

from promptbro.config import Config, ConfigError, load


def test_defaults_load():
    """Test that default configuration loads without errors."""
    cfg = Config()
    cfg.validate()

    assert cfg.window.width == 720
    assert cfg.window.height == 240
    assert cfg.window.opacity == 0.75
    assert cfg.asr.engine == "sherpa-onnx"
    assert cfg.log.level == "info"


def test_bad_width_raises_config_error():
    """Test that invalid width value raises ConfigError."""
    cfg = Config()
    cfg.window.width = "x"  # type: ignore

    with pytest.raises(ConfigError, match="window.width must be"):
        cfg.validate()


def test_unknown_key_warns_and_loads(caplog):
    """Test that unknown configuration keys warn and load successfully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text("""
[window]
width = 720
unknown_key = "value"

[unknown_section]
foo = "bar"
""")

        with caplog.at_level(logging.WARNING):
            cfg = load(config_path)

        # Check that warnings were logged
        assert any("unknown" in record.message.lower() for record in caplog.records)

        # Check that valid config still loaded
        assert cfg.window.width == 720


def test_invalid_engine_raises_config_error():
    """Test that invalid ASR engine raises ConfigError."""
    cfg = Config()
    cfg.asr.engine = "invalid"

    with pytest.raises(ConfigError, match="asr.engine must be"):
        cfg.validate()


def test_invalid_log_level_raises_config_error():
    """Test that invalid log level raises ConfigError."""
    cfg = Config()
    cfg.log.level = "invalid"

    with pytest.raises(ConfigError, match="log.level must be"):
        cfg.validate()


def test_load_from_file():
    """Test loading configuration from a file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text("""
[window]
width = 800
height = 300
opacity = 0.95

[asr]
num_threads = 4

[log]
level = "debug"
""")

        cfg = load(config_path)

        assert cfg.window.width == 800
        assert cfg.window.height == 300
        assert cfg.window.opacity == 0.95
        assert cfg.asr.num_threads == 4
        assert cfg.log.level == "debug"


def test_missing_file_uses_defaults():
    """Test that missing config file uses defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "nonexistent.toml"

        cfg = load(config_path)

        assert cfg.window.width == 720
        assert cfg.window.height == 240


def test_default_model_is_registered_and_previous_selectable():
    from promptbro import models
    from promptbro.config import ASRConfig
    assert ASRConfig().model == models.DEFAULT_MODEL
    assert models.DEFAULT_MODEL in models.REGISTRY and models.PREVIOUS_MODEL in models.REGISTRY
    assert ASRConfig(model=models.PREVIOUS_MODEL).model == models.PREVIOUS_MODEL
