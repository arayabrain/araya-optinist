import copy
import os
import warnings

import pytest

from studio.app.common.core.logger import VALID_LOG_LEVELS, LoggingConfigHelper

SAMPLE_LOGGING_CONFIG = {
    "version": 1,
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "optinist": {"level": "DEBUG", "handlers": ["console"]},
        "snakemake": {"level": "DEBUG", "handlers": ["console"]},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "level": "DEBUG"},
        "rotating_file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "filename": "test.log",
        },
    },
}


def _fresh_config():
    return copy.deepcopy(SAMPLE_LOGGING_CONFIG)


class TestApplyLogLevelOverride:
    def test_sets_all_levels(self):
        config = _fresh_config()
        result = LoggingConfigHelper._apply_log_level_override(config, "WARNING")

        assert result["root"]["level"] == "WARNING"
        assert result["loggers"]["optinist"]["level"] == "WARNING"
        assert result["loggers"]["snakemake"]["level"] == "WARNING"
        assert result["handlers"]["console"]["level"] == "WARNING"
        assert result["handlers"]["rotating_file"]["level"] == "WARNING"

    def test_invalid_value_warns_and_keeps_defaults(self):
        config = _fresh_config()
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = LoggingConfigHelper._apply_log_level_override(config, "VERBOSE")
            assert len(w) == 1
            assert "Invalid LOG_LEVEL" in str(w[0].message)

        assert result["root"]["level"] == "INFO"
        assert result["loggers"]["optinist"]["level"] == "DEBUG"

    def test_case_insensitive(self):
        config = _fresh_config()
        result = LoggingConfigHelper._apply_log_level_override(config, "warning")

        assert result["root"]["level"] == "WARNING"
        assert result["loggers"]["optinist"]["level"] == "WARNING"

    def test_handler_without_level_key_is_skipped(self):
        config = _fresh_config()
        config["handlers"]["no_level"] = {"class": "logging.StreamHandler"}
        result = LoggingConfigHelper._apply_log_level_override(config, "ERROR")

        assert "level" not in result["handlers"]["no_level"]
        assert result["handlers"]["console"]["level"] == "ERROR"

    @pytest.mark.parametrize("level", sorted(VALID_LOG_LEVELS))
    def test_all_valid_levels_accepted(self, level):
        config = _fresh_config()
        result = LoggingConfigHelper._apply_log_level_override(config, level)
        assert result["root"]["level"] == level


class TestLogLevelFromEnvVar:
    def test_env_var_overrides_config(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        config = _fresh_config()
        log_level = os.environ.get("LOG_LEVEL")
        if log_level:
            config = LoggingConfigHelper._apply_log_level_override(config, log_level)
        assert config["root"]["level"] == "ERROR"
        assert config["loggers"]["optinist"]["level"] == "ERROR"

    def test_empty_string_env_var_skipped(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "")
        config = _fresh_config()
        log_level = os.environ.get("LOG_LEVEL")
        if log_level:
            config = LoggingConfigHelper._apply_log_level_override(config, log_level)
        assert config["root"]["level"] == "INFO"
        assert config["loggers"]["optinist"]["level"] == "DEBUG"

    def test_unset_env_var_skipped(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        config = _fresh_config()
        log_level = os.environ.get("LOG_LEVEL")
        if log_level:
            config = LoggingConfigHelper._apply_log_level_override(config, log_level)
        assert config["root"]["level"] == "INFO"
        assert config["loggers"]["optinist"]["level"] == "DEBUG"
