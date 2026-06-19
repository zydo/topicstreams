#!/usr/bin/env python3
"""Manage the DB-backed REST API bearer tokens at runtime.

Changes here take effect on the running server within ``api_key_cache_ttl_seconds``
(default 30s) — no restart needed. The ``TOPICSTREAMS_API_KEY`` env var is a
separate always-valid bootstrap set and is not managed here.

Run it inside the API container so it shares the DB connection settings:

    docker compose exec api python scripts/manage_api_keys.py list
    docker compose exec api python scripts/manage_api_keys.py add --label alice
    docker compose exec api python scripts/manage_api_keys.py disable 3
    docker compose exec api python scripts/manage_api_keys.py enable 3
    docker compose exec api python scripts/manage_api_keys.py delete 3

`add` mints a strong token when you don't supply one, and prints it (the only
time the full token is shown).
"""

import argparse
import sys

from common import database as db
from scripts.generate_api_key import generate_key


def _mask(token: str) -> str:
    """Show enough to recognize a token without printing the secret."""
    return token if len(token) <= 12 else f"{token[:4]}…{token[-4:]}"


def cmd_add(args: argparse.Namespace) -> int:
    token = args.token or generate_key()
    key_id = db.add_api_key(token, label=args.label)
    minted = " (generated)" if not args.token else ""
    print(f"key #{key_id} added/enabled{minted}")
    print(f"  label: {args.label or '-'}")
    print(f"  token: {token}")
    print("Clients send:  Authorization: Bearer <token>")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    keys = db.list_api_keys()
    if not keys:
        print("No DB API keys. (TOPICSTREAMS_API_KEY env tokens are not listed here.)")
        return 0
    print(f"{'ID':>4}  {'ACTIVE':<6}  {'LABEL':<20}  {'TOKEN':<14}  CREATED")
    for k in keys:
        print(
            f"{k['id']:>4}  "
            f"{'yes' if k['is_active'] else 'no':<6}  "
            f"{(k['label'] or '-'):<20}  "
            f"{_mask(k['token']):<14}  "
            f"{k['created_at']:%Y-%m-%d %H:%M}"
        )
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    if db.set_api_key_active(args.id, False):
        print(f"key #{args.id} disabled (revoked within the cache TTL)")
        return 0
    print(f"no key with id {args.id}", file=sys.stderr)
    return 1


def cmd_enable(args: argparse.Namespace) -> int:
    if db.set_api_key_active(args.id, True):
        print(f"key #{args.id} enabled")
        return 0
    print(f"no key with id {args.id}", file=sys.stderr)
    return 1


def cmd_delete(args: argparse.Namespace) -> int:
    if db.delete_api_key(args.id):
        print(f"key #{args.id} deleted")
        return 0
    print(f"no key with id {args.id}", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add (or reactivate) a token")
    p_add.add_argument("token", nargs="?", help="token value (generated if omitted)")
    p_add.add_argument("--label", help="human-readable label (e.g. a client name)")
    p_add.set_defaults(func=cmd_add)

    sub.add_parser("list", help="list all DB keys").set_defaults(func=cmd_list)

    for name, help_text, func in (
        ("disable", "disable (revoke) a key by id", cmd_disable),
        ("enable", "re-enable a disabled key by id", cmd_enable),
        ("delete", "permanently delete a key by id", cmd_delete),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("id", type=int, help="key id (see `list`)")
        p.set_defaults(func=func)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
