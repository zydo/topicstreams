# Postmortem: Host hang & stack outage (2026-06-13)

**Severity:** High — full outage. `localhost:5000` / `public_ip:5000` unreachable; host
unresponsive to SSH; recovered only via a GCP console reset.

**Host:** GCP VM `hatchway` (us-west1-a), 7.7 GiB RAM, **no swap**, 28 GB disk (73% used).

## Impact

- Web UI / API unreachable from ~05:44 UTC until manual recovery.
- The VM was fully wedged (no SSH, GCP metadata server timing out) and required a
  hard reset from the GCP console.
- After the reset the stack still did **not** come back on its own (see "Second
  failure" below).

## Timeline (UTC, 2026-06-12 → 06-13)

- **06-12 07:20** — Scraper container (Chromium) starts; begins a ~23h continuous run.
- **06-12 17:25** — GCP metadata server (`169.254.169.254`) starts timing out every
  ~15 min — earliest sign the host was losing the ability to service basic I/O.
- **06-13 02:46–05:11** — Postgres checkpoints that normally take seconds begin taking
  30–75s (disk/memory thrashing).
- **06-13 05:44 → 05:58** — `systemd-journald` logs `Under memory pressure, flushing
  caches` continuously, ~once per 20–30s, until the journal dies at **05:58:28**.
- **06-13 ~05:58** — Host livelocks. No OOM-killer line ever fires.
- **06-13 05:59:49** — Manual GCP console reset; host boots.
- **06-13 ~06:01** — Scraper + API auto-restart (they have `restart: unless-stopped`);
  **Postgres does not** (no restart policy). API enters a crash loop.

## Root cause

**Memory exhaustion on a swap-less host → kernel livelock.**

The scraper launches a single Chromium context **once** and reuses it across an
infinite scrape loop (`scraper/main.py`). Chromium memory grows unbounded over
thousands of cycles. With **zero swap configured**, once RAM was exhausted the
kernel had no relief valve: instead of cleanly OOM-killing a process it thrashed on
cache reclaim until it became unresponsive. That is why no OOM-killer message
appears — the box livelocked rather than killing the offender.

## Second failure (why the reset didn't restore service)

1. **Postgres had no `restart:` policy** in `docker-compose.yml` (only `api` and
   `scraper` did). After reboot the daemon brought back `api`/`scraper` but not the
   DB, so the API crash-looped on `could not translate host name "postgres"`.
2. **The auto-restarted API container had a broken network endpoint** (empty
   `EndpointID`) — a known side effect of ungraceful reboots. `compose up` alone did
   not fix it; the container had to be force-recreated.

Note: `depends_on: condition: service_healthy` only orders startup during
`compose up`. It is **not** honored when the Docker daemon individually restarts
containers after a host reboot.

## Actions taken (recovery)

1. Brought the stack up with `docker compose up -d`; Postgres came back healthy.
2. API still crash-looped with an empty network `EndpointID`; fixed by
   `docker compose up -d --force-recreate api`. API returned HTTP 200.
3. Confirmed port `5000` published on `0.0.0.0` and the scraper actively scraping.

## Actions taken (durable fixes)

1. **Added 2 GiB swap** (swap-on-a-file, no repartitioning). Turns a hard livelock
   into a recoverable cgroup/OOM kill. Sized at 2 GiB because the root disk is
   already 73% full (~7.8 GB free). Exact steps:

   ```bash
   sudo fallocate -l 2G /swapfile        # or: sudo dd if=/dev/zero of=/swapfile bs=1M count=2048
   sudo chmod 600 /swapfile              # mkswap/swapon refuse a world-readable file
   sudo mkswap /swapfile                 # format as swap
   sudo swapon /swapfile                 # activate immediately (no reboot)
   # persist across reboots:
   echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
   ```

   Verify: `swapon --show` (shows `/swapfile file 2G`) and `free -h` (`Swap: 2.0Gi`).
   To reverse: `sudo swapoff /swapfile && sudo rm /swapfile` and remove the fstab line.
2. **Added `restart: unless-stopped` to the `postgres` service** so the DB returns
   on its own after any reboot.
3. **Capped the scraper's memory** (`mem_limit: 1500m`, `memswap_limit: 1500m`) so a
   Chromium leak is cgroup-OOM-killed and restarted instead of taking down the host.
4. **Recycle the Chromium context every 50 cycles** (`BROWSER_RECYCLE_CYCLES` in
   `scraper/main.py`) to release accumulated browser memory at the source. The
   on-disk persistent profile survives the recycle.

## Follow-ups / not yet done

- Consider small `mem_limit`s on `api` and `postgres` too, so no single container can
  starve the host.
- Consider a host-level memory alert (the metadata-server timeouts and journald
  "memory pressure" lines were both early warnings that went unnoticed).
- SSH is being continuously brute-forced from the public internet (many
  `maximum authentication attempts exceeded` lines). Consider restricting SSH ingress
  to known IPs or moving to IAP-tunneled access.
- Revisit swap sizing once disk usage is reduced; 2 GiB is conservative.
