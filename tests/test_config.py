"""Tests for ScraperConfig accessors (engine list shape + defaults)."""

from common.config import CONFIG_PATH, ScraperConfig

# The shipped template is the out-of-the-box default; lock its shape.
EXAMPLE = CONFIG_PATH.with_name("config.yml.example")


def test_example_config_enables_all_live_engines():
    cfg = ScraperConfig(config_path=EXAMPLE)
    assert cfg.engines == ["google", "bing", "yahoo", "brave"]
    assert cfg.engine_strategy == "all"


def test_engine_defaults_when_unset(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.engines == ["google"]
    assert cfg.engine_strategy == "fallback"
