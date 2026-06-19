#!/usr/bin/env python3
"""Generate cryptographically-strong bearer tokens for the REST API.

Each token is URL-safe (safe to paste into .env, headers, and shell). Set the
output as TOPICSTREAMS_API_KEY (comma-separated for multiple) and restart the
API; clients then send `Authorization: Bearer <token>`.

Usage:
    python scripts/generate_api_key.py            # one token
    python scripts/generate_api_key.py -n 3       # three (comma-separated)
    python scripts/generate_api_key.py --bytes 48 # stronger token
"""

import argparse
import secrets


def generate_key(num_bytes: int = 32) -> str:
    """A URL-safe token with ~num_bytes*8 bits of entropy."""
    return secrets.token_urlsafe(num_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=1,
        help="How many tokens to generate (default: 1)",
    )
    parser.add_argument(
        "--bytes",
        dest="num_bytes",
        type=int,
        default=32,
        help="Entropy in bytes per token (default: 32)",
    )
    args = parser.parse_args()

    if args.count < 1 or args.num_bytes < 16:
        parser.error("count must be >= 1 and --bytes must be >= 16")

    keys = [generate_key(args.num_bytes) for _ in range(args.count)]
    # Comma-separated so the line can be pasted straight into TOPICSTREAMS_API_KEY.
    print(",".join(keys))


if __name__ == "__main__":
    main()
