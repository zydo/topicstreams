#!/usr/bin/env python3
"""Per-engine rate-tolerance pressure test (one-off instrument, NOT prod).

Fires an endless stream of *distinct* search keywords at ONE engine, back to
back, with **zero** artificial delay — no pacing floor, no page-settle, no
human-simulation scroll/mouse. The only cost per request is real Chromium
render + network. It reuses the production ``SearchSource`` classes (so URLs,
headers and block detection are identical to the live scraper) and the
runtime-detected Chrome fingerprint (so we measure *rate* tolerance, not a
fingerprint-rejection CAPTCHA), then records the outcome of every request until
the engine hard-blocks this host's egress IP — a run of consecutive
429/403/503/CAPTCHA/connection-block signals — or a safety cap is hit.

Goal: a rule-of-thumb for how many flat-out queries (and what req/min) each
engine tolerates before blocking.

Usage (inside the scraper image, with the live scraper paused):
    python /app/scripts/pressure_test.py <engine> [--max-requests N]
        [--max-seconds S] [--consec-block K] [--recency hour|day|week]
        [--recycle N] [--out /tmp/ptest_<engine>.json]
"""

import argparse
import itertools
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from common.block_signals import is_network_block
from scraper.browser import build_proxy, detect_fingerprint, new_context
from scraper.sources import Ordering, Recency, get_source

# Distinct-keyword generator -------------------------------------------------
# Two shuffled word pools combined pairwise give ~thousands of natural,
# *distinct* multi-word queries (we block long before exhausting them); a
# trailing counter guarantees uniqueness even if a pool is exhausted.
_ADJ = (
    "global local breaking latest major federal regional national urban rural "
    "economic political digital climate energy housing transit border election "
    "market labor health tech space defense trade water wildfire drought storm "
    "winter summer coastal mountain desert arctic tropical northern southern "
    "eastern western central modern ancient public private rising falling record"
).split()
_NOUN = (
    "summit policy ruling outage strike protest merger lawsuit verdict budget "
    "tariff shortage surplus rally selloff blackout flood quake eruption recall "
    "ceasefire treaty sanction embargo bailout layoff hiring rollout recall2 "
    "breach leak patch launch landing orbit probe vaccine outbreak recovery "
    "drought2 harvest forecast warning advisory inquiry hearing reform pact deal "
    "scandal probe2 audit census railway pipeline reactor turbine harbor bridge"
).split()


def keyword_stream():
    pairs = list(itertools.product(_ADJ, _NOUN))
    random.shuffle(pairs)
    for a, b in pairs:
        yield f"{a} {b}"
    # Fallback (never expected to be reached before a block / the cap).
    for i in itertools.count():
        a, b = random.choice(_ADJ), random.choice(_NOUN)
        yield f"{a} {b} {i}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _percentile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, round((len(s) - 1) * q)))
    return round(s[k], 1)


def classify(source, status, final_url, content):
    """Return a block-reason string if this request was blocked, else None.

    Mirrors scraper.scraper: HTTP throttle codes first, then the engine's
    own detect_block, then the generic 'redirected off results page' signal.
    """
    if status in (429, 403, 503):
        return f"http_{status}"
    reason = source.detect_block(final_url, content) or source.redirected_off_results(
        final_url
    )
    return reason


def run(args) -> dict:
    source = get_source(args.engine)
    recency = Recency[args.recency.upper()]
    profile_dir = Path("/app/.browser_profiles") / f"ptest_{args.engine}"

    rows = []
    latencies_ok = []
    status_counts = {}
    consec_block = 0
    first_block_n = None
    stop_reason = "cap_requests"
    started = time.monotonic()
    started_wall = _now_iso()

    keywords = keyword_stream()
    nav_timeout = 30000

    with sync_playwright() as p:
        profile = detect_fingerprint(p)
        proxy = build_proxy()  # None when proxy disabled -> direct, host IP
        context = new_context(p, profile_dir, profile, proxy)
        print(
            f"[{args.engine}] start {started_wall} ua={profile.user_agent[:40]}... "
            f"cap={args.max_requests}req/{args.max_seconds}s block@{args.consec_block}",
            flush=True,
        )
        try:
            for n in itertools.count(1):
                if n > args.max_requests:
                    stop_reason = "cap_requests"
                    break
                elapsed = time.monotonic() - started
                if elapsed > args.max_seconds:
                    stop_reason = "cap_time"
                    break

                kw = next(keywords)
                url = source.build_url(
                    kw, ordering=Ordering.DATE, recency=recency, page=1
                )
                page = context.new_page()
                t0 = time.perf_counter()
                status = None
                reason = None
                err = None
                try:
                    resp = page.goto(
                        url, wait_until="domcontentloaded", timeout=nav_timeout
                    )
                    lat_ms = round((time.perf_counter() - t0) * 1000)
                    status = resp.status if resp else None
                    if status in (429, 403, 503):
                        reason = f"http_{status}"
                    else:
                        reason = classify(source, status, page.url, page.content())
                except Exception as e:
                    lat_ms = round((time.perf_counter() - t0) * 1000)
                    err = f"{type(e).__name__}: {e}"
                    # A connection-level teardown is itself a block signal.
                    if is_network_block(err):
                        reason = "network_block"
                finally:
                    page.close()

                blocked = reason is not None
                key = str(status) if status is not None else (err and "error" or "none")
                status_counts[key] = status_counts.get(key, 0) + 1
                if not blocked and status == 200:
                    latencies_ok.append(lat_ms)

                rows.append(
                    {
                        "n": n,
                        "t": round(elapsed, 2),
                        "kw": kw,
                        "status": status,
                        "lat_ms": lat_ms,
                        "blocked": blocked,
                        "reason": reason,
                        "err": err,
                    }
                )

                if blocked:
                    consec_block += 1
                    if first_block_n is None or consec_block == 1:
                        first_block_n = n if first_block_n is None else first_block_n
                    if consec_block == 1:
                        run_start_n = n
                else:
                    consec_block = 0

                if n % 25 == 0 or blocked:
                    rate = n / max(elapsed, 1e-9) * 60
                    print(
                        f"[{args.engine}] n={n} t={elapsed:6.1f}s "
                        f"~{rate:5.1f}req/min status={status} "
                        f"lat={lat_ms}ms{' BLOCK:' + reason if blocked else ''}",
                        flush=True,
                    )

                if consec_block >= args.consec_block:
                    stop_reason = "blocked"
                    break

                if n % args.recycle == 0:
                    context.close()
                    context = new_context(p, profile_dir, profile, proxy)
        finally:
            context.close()

    elapsed = time.monotonic() - started
    # The sustained flat-out rate the engine did NOT tolerate beyond: requests
    # and time up to the first block of the terminal run.
    if stop_reason == "blocked" and rows:
        # index (1-based) of the first request in the final consecutive run
        run_start = rows[-1]["n"] - (args.consec_block - 1)
        ok_n = run_start - 1
        ok_t = rows[run_start - 2]["t"] if run_start >= 2 else 0.0
    else:
        ok_n = len([r for r in rows if not r["blocked"]])
        ok_t = elapsed
    sustained_rpm = round(ok_n / ok_t * 60, 1) if ok_t > 0 else None

    summary = {
        "engine": args.engine,
        "started": started_wall,
        "finished": _now_iso(),
        "stop_reason": stop_reason,
        "total_requests": len(rows),
        "elapsed_seconds": round(elapsed, 1),
        "overall_req_per_min": round(len(rows) / elapsed * 60, 1) if elapsed else None,
        "ok_requests_before_block": ok_n,
        "seconds_before_block": round(ok_t, 1),
        "sustained_req_per_min_before_block": sustained_rpm,
        "first_block_n": first_block_n,
        "status_counts": status_counts,
        "latency_ok_ms": {
            "count": len(latencies_ok),
            "min": min(latencies_ok) if latencies_ok else None,
            "median": round(statistics.median(latencies_ok), 1) if latencies_ok else None,
            "p95": _percentile(latencies_ok, 0.95),
            "max": max(latencies_ok) if latencies_ok else None,
        },
        "max_rate_ceiling_req_per_min": (
            round(60000 / statistics.median(latencies_ok), 1) if latencies_ok else None
        ),
        "first_block_rows": [r for r in rows if r["blocked"]][:5],
    }
    return {"summary": summary, "rows": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("engine", choices=["google", "bing", "yahoo", "brave"])
    ap.add_argument("--max-requests", type=int, default=1500)
    ap.add_argument("--max-seconds", type=float, default=900.0)
    ap.add_argument("--consec-block", type=int, default=3)
    ap.add_argument("--recency", default="hour")
    ap.add_argument("--recycle", type=int, default=150)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    result = run(args)
    out = args.out or f"/app/.browser_profiles/ptest_{args.engine}.json"
    Path(out).write_text(json.dumps(result, indent=2))
    s = result["summary"]
    print("\n===== SUMMARY =====", flush=True)
    print(json.dumps(s, indent=2), flush=True)
    print(f"(full per-request rows written to {out})", flush=True)


if __name__ == "__main__":
    sys.exit(main())
