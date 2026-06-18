"""
data/etl/sr_status.py
=====================
Quick "are we in Sports-Reference jail?" probe.

SR rate-limits scrapers with an HTTP 429 and a ~1-hour IP timeout (the
"jail") rather than a 403. This hits each SR host ONCE and prints the status
code + a plain-English verdict so you can tell, before kicking off a backfill,
whether you're currently blocked.

    python -m data.etl.sr_status                # probe all SR hosts (one req each)
    python -m data.etl.sr_status --sport NBA    # just basketball-reference
    python -m data.etl.sr_status --url https://www.basketball-reference.com/players/j/jamesle01.html

Uses only `requests` (already in requirements.txt). Polite: one request per
host, a short delay between hosts, a real browser UA, generous timeout.
"""

import argparse
import time

import requests

# Same polite fingerprint the backfill uses, so this reflects what a real
# backfill request would see.
HEADERS = {
    "User-Agent": ("2020Game jail-check (polite; one-shot) Mozilla/5.0 "
                   "(Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# One entry per distinct SR host (basketball-reference serves NBA + WNBA;
# sports-reference.com serves CFB + CBB) so we never hit the same host twice.
SITES = [
    ("Baseball-Reference  (MLB)",       "https://www.baseball-reference.com/"),
    ("Basketball-Reference (NBA/WNBA)", "https://www.basketball-reference.com/"),
    ("Pro-Football-Reference (NFL)",    "https://www.pro-football-reference.com/"),
    ("Hockey-Reference  (NHL)",         "https://www.hockey-reference.com/"),
    ("Sports-Reference  (NCAAF/NCAAB)", "https://www.sports-reference.com/"),
]

SPORT_HOST = {
    "MLB":  "https://www.baseball-reference.com/",
    "NBA":  "https://www.basketball-reference.com/",
    "WNBA": "https://www.basketball-reference.com/",
    "NFL":  "https://www.pro-football-reference.com/",
    "NHL":  "https://www.hockey-reference.com/",
    "NCAAF": "https://www.sports-reference.com/",
    "NCAAB": "https://www.sports-reference.com/",
}

# Phrases that show up on SR / Cloudflare block pages even when the status is
# something other than 429 (e.g. a 200 or 403 challenge body).
JAIL_HINTS = ("you have been blocked", "been temporarily blocked", "rate limit",
              "too many requests", "error 429", "429 error", "access denied",
              "checking your browser", "attention required")


def verdict(status: int) -> str:
    return {
        200: "OK — reachable, not jailed",
        429: "JAILED — HTTP 429, you're rate-limited (wait it out, ~1hr)",
        403: "BLOCKED — HTTP 403 Forbidden (IP/Cloudflare block)",
        503: "UNAVAILABLE — HTTP 503 (CDN challenge or maintenance)",
    }.get(status, f"HTTP {status} — unexpected")


def probe(label: str, url: str, timeout: int) -> int | None:
    t0 = time.time()
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
    except requests.exceptions.Timeout:
        print(f"  [---] {label}\n        TIMEOUT after {timeout}s "
              f"(host unreachable / silently dropped on this network)")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  [---] {label}\n        CONN ERROR {type(e).__name__}: {e}")
        return None

    dt = time.time() - t0
    snippet = ""
    try:
        snippet = r.raw.read(4096, decode_content=True).decode("utf-8", "ignore").lower()
    except Exception:
        pass
    finally:
        r.close()

    v = verdict(r.status_code)
    hints = [h for h in JAIL_HINTS if h in snippet]
    if r.status_code == 200 and hints:
        v = "SUSPICIOUS — 200 but the body reads like a block/challenge page"

    print(f"  [{r.status_code}] {label}  ({dt:.1f}s)  -> {v}")
    retry = r.headers.get("Retry-After")
    if retry:
        print(f"        Retry-After: {retry}  (seconds until the jail lifts)")
    if hints:
        print(f"        block-page hints in body: {hints}")
    server = r.headers.get("Server") or r.headers.get("server")
    if server and ("cloudflare" in server.lower() or r.status_code not in (200,)):
        print(f"        Server: {server}")
    return r.status_code


def main():
    ap = argparse.ArgumentParser(description="One-shot Sports-Reference jail check")
    ap.add_argument("--sport", help="Probe just this sport's SR host (NBA/MLB/NFL/NHL/WNBA/NCAAF/NCAAB)")
    ap.add_argument("--url", help="Probe a single explicit URL instead of the host homepages")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--delay", type=float, default=2.0, help="Seconds between hosts (politeness)")
    args = ap.parse_args()

    if args.url:
        print("Sports-Reference jail probe (single URL):")
        probe("custom URL", args.url, args.timeout)
        return

    if args.sport:
        host = SPORT_HOST.get(args.sport.upper())
        if not host:
            print(f"Unknown sport {args.sport!r}. Known: {', '.join(sorted(SPORT_HOST))}")
            return
        sites = [(args.sport.upper(), host)]
    else:
        sites = SITES

    print("Sports-Reference jail probe (one request per host):\n")
    codes = []
    for i, (label, url) in enumerate(sites):
        if i:
            time.sleep(args.delay)
        codes.append(probe(label, url, args.timeout))

    seen = [c for c in codes if c is not None]
    print()
    if any(c == 429 for c in codes):
        print("Verdict: JAILED on at least one host — HTTP 429 is the scrape jail. "
              "Back off and retry in ~1 hour (honor any Retry-After above).")
    elif seen and all(c == 200 for c in seen) and len(seen) == len(codes):
        print("Verdict: CLEAR — SR is reachable and not rate-limiting you right now.")
    elif not seen:
        print("Verdict: UNREACHABLE — every host timed out / errored. Likely a "
              "network/firewall block (not an SR jail). Try from another network.")
    elif seen and len(set(seen)) == 1 and seen[0] != 200:
        code = seen[0]
        print(f"Verdict: every host returned HTTP {code}. That's a consistent "
              f"{'IP/Cloudflare/proxy block' if code == 403 else 'edge response'}, "
              f"NOT a rate-limit jail (the jail is a 429). It won't lift on its "
              f"own the way a 429 does — check whether the request is leaving your "
              f"network unproxied, or try a different IP.")
    else:
        print("Verdict: MIXED — see per-host lines above.")


if __name__ == "__main__":
    main()
