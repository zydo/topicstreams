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


def test_web_search_engines_default_is_google_only(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.web_search_engines == ["google"]  # single-engine web search for now


def test_web_search_engines_override(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  web_search:\n    engines: [google, bing, yahoo]\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.web_search_engines == ["google", "bing", "yahoo"]


def test_example_config_web_search_is_google_only(tmp_path):
    cfg = ScraperConfig(config_path=EXAMPLE)
    assert cfg.web_search_engines == ["google"]


def test_web_search_max_in_flight_default_and_override(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    assert ScraperConfig(config_path=path).web_search_max_in_flight == 4  # default
    path.write_text("scraper:\n  web_search:\n    max_in_flight: 2\n")
    assert ScraperConfig(config_path=path).web_search_max_in_flight == 2


def test_pacing_per_engine_override_and_default(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(
        "scraper:\n"
        "  pacing:\n"
        "    default_min_interval: 1.5\n"
        "    per_engine:\n"
        "      brave: 4.0\n"
    )
    cfg = ScraperConfig(config_path=path)
    assert cfg.min_interval_for("brave") == 4.0
    assert cfg.min_interval_for("google") == 1.5  # falls back to default


def test_pacing_defaults_when_unset(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.min_interval_for("google") == 2.0
    assert cfg.pacing_jitter_ratio == 0.25


def test_saturation_defaults_and_overrides(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text("scraper:\n  scrape_interval: 60\n")
    cfg = ScraperConfig(config_path=path)
    assert cfg.saturation_canary_engines == ["brave"]
    assert cfg.saturation_robust_threshold == 2

    path.write_text(
        "scraper:\n"
        "  saturation:\n"
        "    canary_engines: [brave, yahoo]\n"
        "    robust_threshold: 1\n"
    )
    cfg = ScraperConfig(config_path=path)
    assert cfg.saturation_canary_engines == ["brave", "yahoo"]
    assert cfg.saturation_robust_threshold == 1
