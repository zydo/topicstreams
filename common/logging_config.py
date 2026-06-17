"""Logging setup shared by the API and scraper entry points.

Human-readable text by default; set ``LOG_FORMAT=json`` for structured logs
(one JSON object per line) suited to log aggregation.
"""

import json
import logging


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(log_format: str = "text", level: int = logging.INFO) -> None:
    """Install a single stream handler on the root logger.

    ``log_format='json'`` emits structured JSON lines; anything else keeps the
    human-readable ``time - level - message`` format.
    """
    handler = logging.StreamHandler()
    if log_format.lower() == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
