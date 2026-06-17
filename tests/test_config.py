"""Tests for ScraperConfig accessors (engine list shape + defaults)."""

from common.config import CONFIG_DIR, ScraperConfig


def test_example_config_enables_all_live_engines():
    # The shipped template is the out-of-the-box default; lock its shape.
    cfg = ScraperConfig(config_path=CONFIG_DIR / "scraper.yml.example")
    assert cfg.engines == ["google", "bing", "yahoo", "brave"]
    assert cfg.engine_strategy == "all"


def test_engine_defaults_when_unset(tmp_path):
    path = tmp_path / "scraper.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.engines == ["google"]
    assert cfg.engine_strategy == "fallback"
