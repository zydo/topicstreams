"""Shared block-signal detection used by both the scraper and the API.

Some engines block at the network layer rather than with an HTTP status: the
server (or an upstream proxy) tears down the TCP connection, so the browser
nav fails with a ``net::ERR_CONNECTION_*`` error and there is *no* HTTP
response to read a 429/403/503 from. Yahoo does exactly this — after a burst it
serves a persistent empty HTTP 500 (Server: ATS, Connection: close) and a real
browser nav then fails ``net::ERR_CONNECTION_CLOSED`` (see
docs/BLOCK_SIGNAL_FINDINGS.md). Treating these as a block (not a transient
glitch) lets the scraper bench the engine and the monitor label it correctly.

Deliberately narrow: only connection-level *refusals/teardowns* count. A plain
navigation timeout (``TimeoutError``) is a transient failure we neither punish
(cooldown) nor flag (health) — it stays "other"/non-block, matching the
existing contract in scraper/cooldown.py.
"""

# Substrings matched case-insensitively against a scrape's error message
# (``"{ExceptionType}: {message}"``). These are Chromium/Playwright net errors
# that mean the connection was refused or torn down before a usable response —
# a server actively closing the door, i.e. a block.
NETWORK_BLOCK_PATTERNS = (
    "err_connection_closed",
    "err_connection_reset",
    "err_connection_refused",
    "err_connection_aborted",
    "err_connection_failed",
    "err_socket_not_connected",
    "err_empty_response",
)


def is_network_block(error_message: str | None) -> bool:
    """True if ``error_message`` looks like a connection-level block/teardown.

    Returns False for ``None``/empty and for transient errors such as
    navigation timeouts.
    """
    if not error_message:
        return False
    haystack = error_message.lower()
    return any(pattern in haystack for pattern in NETWORK_BLOCK_PATTERNS)
