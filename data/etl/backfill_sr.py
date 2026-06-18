"""
data/etl/backfill_sr.py
=======================
Opt-in, polite Sports-Reference backfill for missing bio fields.

This is a SECONDARY, manual tool -- not part of run_full / build_demo_db. The
sanctioned providers are the primary source; this fills gaps they don't carry
(college, birthplace, draft, position, height/weight) by looking up existing
players on the Sports-Reference family of sites.

Politeness (so you don't get jailed again -- SR allows ~20 req/min):
  - default 5s delay + jitter  (~12 req/min, comfortably under the limit)
  - honors the Retry-After header on 429
  - aborts after 3 consecutive 403s (you're jailed; come back later)
  - caches every page under data/cache/sr/ so re-runs don't re-hit the site
  - unwraps comment-hidden tables (the classic SR gotcha)
  - only fills EMPTY fields; never overwrites existing data

Usage (run from a residential IP, slowly):
    python -m data.etl.backfill_sr --sport NBA --fields college,draft --limit 50
    python -m data.etl.backfill_sr --sport MLB --fields college --dry-run
    python -m data.etl.backfill_sr --sport NFL --fields birth_state,draft --delay 6

Cloudflare "Just a moment..." challenge (e.g. pro-football-reference):
  A JS-less client can't pass it, so paste a clearance cookie from a browser
  that did. In Chrome: open the site, let the challenge pass, then DevTools ->
  Application -> Cookies -> copy the `cf_clearance` value. The browser UA is
  hardcoded (DEFAULT_BROWSER_UA, Chrome 149); set --user-agent / SR_CF_UA only
  if yours differs -- the cookie is bound to it.

  Keep the token in your gitignored .env (it's a secret -- never in source):
    SR_CF_CLEARANCE=weE_qPdD...           # in .env, alongside CFBD_API_KEY

    pip install curl_cffi    # match Chrome's TLS fingerprint (recommended)
    python -m data.etl.backfill_sr --sport NFL --fields college --limit 1 --dry-run
    #  ^ preflight: "Matched 1" => cookie works; first-403 abort => re-grab it

  The cookie is bound to that IP + User-Agent and expires per its own Expires
  attribute (visible in DevTools; the zone sets it -- could be minutes or, as on
  PFR, a year. `__cf_bm` is the separate ~30-min one). Two guards keep us from
  hammering the site once the clearance dies: pass --cf-expires and the run
  stops before that timestamp, and in cookie mode the FIRST 403 (clearance
  revoked server-side) aborts immediately instead of firing cookie-less hits.

After it runs it rebuilds that sport's categories so new colleges/birthplaces
take effect. Treat this as backfill; we'll design a proper refresh cadence
separately.
"""

import argparse
import hashlib
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Comment

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)
from data.etl.schema import get_conn, migrate
from data.etl.load import rebuild_categories

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill_sr")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
CACHE_DIR = os.path.join(ROOT, "data", "cache", "sr")

# (domain for relative links, search endpoint) per sport
SR = {
    "NFL":  ("https://www.pro-football-reference.com", "https://www.pro-football-reference.com/search/search.fcgi"),
    "NBA":  ("https://www.basketball-reference.com",  "https://www.basketball-reference.com/search/search.fcgi"),
    "MLB":  ("https://www.baseball-reference.com",    "https://www.baseball-reference.com/search/search.fcgi"),
    "NHL":  ("https://www.hockey-reference.com",      "https://www.hockey-reference.com/search/search.fcgi"),
    "WNBA": ("https://www.basketball-reference.com",  "https://www.basketball-reference.com/wnba/search/search.fcgi"),
    "NCAAF":("https://www.sports-reference.com",      "https://www.sports-reference.com/cfb/search/search.fcgi"),
    "NCAAB":("https://www.sports-reference.com",      "https://www.sports-reference.com/cbb/search/search.fcgi"),
}

HEADERS = {
    "User-Agent": ("2020Game backfill (polite; contact: set SR_CONTACT env) "
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Default UA for cf_clearance mode -- the clearance cookie is bound to the UA
# that obtained it, so this must match the browser you grabbed it from. Hardcoded
# for convenience (no need to pass --user-agent). If Chrome auto-updates and a
# freshly grabbed cookie starts 403ing, bump this string (or pass --user-agent).
DEFAULT_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# text fields use '' as empty; these use 0
NUMERIC_FIELDS = {"draft_year", "draft_round", "draft_pick", "birth_year",
                  "height_inches", "weight_lbs"}

# SR spells positions out; normalize the common (NBA) ones
POS_FULL = {"point guard": "PG", "shooting guard": "SG", "small forward": "SF",
            "power forward": "PF", "center": "C", "guard": "G", "forward": "F"}


class Jailed(Exception):
    pass


_consecutive_403 = 0
_max_403 = 3        # abort after this many consecutive 403s; tightened to 1 in cookie mode


def _load_env_file(path):
    """Minimal .env loader (same pattern as run_full) so secrets like
    SR_CF_CLEARANCE live in the gitignored .env, never in source. Doesn't
    override real environment variables."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_expiry(s):
    """Parse the cf_clearance Expires value from DevTools (ISO 8601, e.g.
    '2027-06-18T15:49:18.188Z') into an aware datetime, or None if unparseable."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        log.warning(f"  couldn't parse --cf-expires {s!r}; expiry auto-stop disabled "
                    f"(the 403 tripwire still protects you)")
        return None


def _registrable_domain(url):
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host


def make_session(sport, cf_clearance=None, user_agent=None):
    """Build the HTTP session.

    With a browser-issued cf_clearance cookie we prefer curl_cffi (Chrome TLS
    impersonation) -- Cloudflare also checks the TLS/JA3 fingerprint, and plain
    requests has a Python fingerprint that strict zones (PFR) reject even when
    the cookie + UA are correct. Falls back to requests if curl_cffi is absent.
    """
    sess, transport = None, "requests"
    if cf_clearance:
        try:
            from curl_cffi import requests as cffi
            impersonate = "chrome"
            try:    # pin to the newest Chrome profile available -> closest JA3 to a real browser
                from curl_cffi.requests import BrowserType
                chromes = sorted((t for t in dir(BrowserType) if re.fullmatch(r"chrome\d+", t)),
                                 key=lambda s: int(s[6:]))
                if chromes:
                    impersonate = chromes[-1]
            except Exception:
                pass
            sess = cffi.Session(impersonate=impersonate)
            transport = f"curl_cffi ({impersonate} TLS impersonation)"
        except ImportError:
            log.warning("  curl_cffi not installed -> using plain requests; Cloudflare "
                        "may still 403 the cookie on its TLS fingerprint alone "
                        "(pip install curl_cffi to match Chrome)")
    if sess is None:
        sess = requests.Session()

    sess.headers.update(HEADERS)
    ua = user_agent or (DEFAULT_BROWSER_UA if cf_clearance else None)
    if ua:
        sess.headers["User-Agent"] = ua
    if cf_clearance:
        reg = _registrable_domain(SR[sport][0])
        sess.cookies.set("cf_clearance", cf_clearance, domain="." + reg, path="/")
        log.info(f"  cf_clearance applied for .{reg} via {transport} "
                 f"(IP+UA-bound; re-grab from the browser if it starts 403ing)")
        log.info(f"  User-Agent: {ua} "
                 f"({'from --user-agent' if user_agent else 'built-in default'})")
    if os.environ.get("SR_CONTACT"):
        sess.headers["From"] = os.environ["SR_CONTACT"]
    return sess


def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.md5(url.encode()).hexdigest() + ".html")


def fetch_raw(session, url, delay):
    """Polite GET. Returns (text|None, final_url). Raises Jailed on a 403 streak."""
    global _consecutive_403
    time.sleep(delay + random.uniform(0, delay * 0.3))
    try:
        r = session.get(url, timeout=25)
    except Exception as e:
        log.warning(f"request error {url}: {e}")
        return None, url

    if r.status_code == 429:
        wait = int(r.headers.get("Retry-After", "60") or 60)
        log.warning(f"429 rate-limited; honoring Retry-After ({wait}s)")
        time.sleep(wait + 1)
        r = session.get(url, timeout=25)

    if r.status_code == 403:
        _consecutive_403 += 1
        log.warning(f"403 Forbidden ({_consecutive_403}/{_max_403}) -- session may be jailed")
        if _consecutive_403 >= _max_403:
            if _max_403 == 1:
                raise Jailed("cf_clearance rejected (expired, IP changed, or never accepted "
                             "by Cloudflare) -- stopping on the first 403 so we don't keep "
                             "hitting the site cookie-less. Re-grab cf_clearance + UA.")
            raise Jailed("3 consecutive 403s -- Sports-Reference jail. Stop and retry "
                         "later (jail can last up to 24h) from a residential IP.")
        return None, r.url
    if r.status_code != 200:
        log.warning(f"HTTP {r.status_code} for {url}")
        return None, r.url
    _consecutive_403 = 0
    return r.text, r.url


def _soup(html):
    soup = BeautifulSoup(html, "lxml")
    # unwrap comment-hidden tables/divs so selectors can see them
    for c in soup.find_all(string=lambda t: isinstance(t, Comment)):
        if "<table" in c or "<div" in c:
            try:
                c.replace_with(BeautifulSoup(c, "lxml"))
            except Exception:
                pass
    return soup


def get_page(session, url, delay):
    """Cached page fetch -> soup (or None)."""
    cp = _cache_path(url)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8", errors="ignore") as f:
            return _soup(f.read())
    text, _ = fetch_raw(session, url, delay)
    if text is None:
        return None
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cp, "w", encoding="utf-8") as f:
        f.write(text)
    return _soup(text)


def resolve_url(session, sport, name, delay, rescache):
    """Map a player name to their SR page via SR search. Cached per name."""
    if name in rescache:
        return rescache[name]
    domain, search = SR[sport]
    text, final = fetch_raw(session, f"{search}?search={requests.utils.quote(name)}", delay)
    url = None
    if final and "/players/" in final:
        url = final                      # search redirected straight to the player
    elif text:
        link = (_soup(text).select_one(".search-item-name a[href*='/players/']")
                or _soup(text).select_one("a[href*='/players/']"))
        if link:
            href = link.get("href", "")
            url = href if href.startswith("http") else domain + href
    rescache[name] = url
    if not url:
        log.info(f"  no SR match for {name!r}")
    return url


def parse_meta(soup):
    """Pull bio fields from the SR meta/header block (defensive, best-effort)."""
    out = {}
    meta = soup.select_one("div#meta")
    if not meta:
        return out
    full = " ".join(p.get_text(" ", strip=True) for p in meta.select("p"))

    link = meta.select_one("a[href*='/colleges/'], a[href*='/friv/colleges'], a[href*='/schools/']")
    if link:
        out["college"] = link.get_text(strip=True)

    pm = re.search(r"Position:\s*([A-Za-z]+(?:\s[A-Za-z]+)?)", full)
    if pm:
        raw = pm.group(1).strip()
        out["position"] = POS_FULL.get(raw.lower(), raw.split()[0])

    bspan = meta.select_one("span#necro-birth[data-birth]")
    if bspan and bspan.get("data-birth", "")[:4].isdigit():
        out["birth_date"] = bspan["data-birth"]
        out["birth_year"] = int(bspan["data-birth"][:4])

    bm = re.search(r"\bin\s+[A-Za-z.\- ]+,\s*([A-Z]{2})\b", full)
    if bm:
        out["birth_state"] = bm.group(1)

    hw = re.search(r"\b(\d)-(\d{1,2}),\s*(\d{2,3})\s*lb", full)
    if hw:
        out["height_inches"] = int(hw.group(1)) * 12 + int(hw.group(2))
        out["weight_lbs"] = int(hw.group(3))

    dm = re.search(r"Draft:.*?(\d+)(?:st|nd|rd|th)\s+round.*?(\d+)(?:st|nd|rd|th)\s+(?:overall|pick)", full)
    if dm:
        out["draft_round"] = int(dm.group(1))
        out["draft_pick"] = int(dm.group(2))
    dy = re.search(r"Draft:.*?\b((?:19|20)\d{2})\b", full)
    if dy:
        out["draft_year"] = int(dy.group(1))

    return out


def is_empty(val, field):
    if field in NUMERIC_FIELDS:
        return not val or val == 0
    return not val or str(val).strip() == ""


def backfill(db, sport, fields, limit, delay, dry_run, cf_clearance=None,
             user_agent=None, cf_expires=None):
    global _consecutive_403, _max_403
    _consecutive_403 = 0
    # With a clearance cookie, a 403 means the cookie just died -- bail on the
    # first one rather than firing off three cookie-less requests that flag us.
    _max_403 = 1 if cf_clearance else 3

    expiry = _parse_expiry(cf_expires)
    if expiry and datetime.now(timezone.utc) >= expiry:
        log.error(f"cf_clearance already expired (Expires {expiry.isoformat()}); "
                  f"re-grab it from the browser before running.")
        return

    migrate(db)
    conn = get_conn(db)
    c = conn.cursor()

    parts = []
    for f in fields:
        empty = "0" if f in NUMERIC_FIELDS else "''"
        parts.append(f"({f} IS NULL OR {f}={empty})")
    cond = " OR ".join(parts)
    rows = c.execute(
        f"SELECT * FROM players WHERE sport=? AND ({cond}) ORDER BY name LIMIT ?",
        (sport, limit)).fetchall()
    log.info(f"{len(rows)} {sport} players missing one of {fields} (limit {limit})")

    session = make_session(sport, cf_clearance, user_agent)

    rescache = {}
    filled = matched = 0
    try:
        for row in rows:
            if expiry and datetime.now(timezone.utc) >= expiry:
                log.warning("cf_clearance Expires reached -- stopping before any "
                            "cookie-less request (re-grab the cookie to continue).")
                break
            name = row["name"]
            url = resolve_url(session, sport, name, delay, rescache)
            if not url:
                continue
            soup = get_page(session, url, delay)
            if not soup:
                continue
            matched += 1
            meta = parse_meta(soup)
            updates = {f: meta[f] for f in fields
                       if f in meta and meta[f] and is_empty(row[f], f)}
            if updates:
                log.info(f"  {name}: {updates}")
                if not dry_run:
                    sets = ", ".join(f"{k}=?" for k in updates)
                    c.execute(f"UPDATE players SET {sets} WHERE id=?",
                              [*updates.values(), row["id"]])
                    filled += 1
        if not dry_run:
            conn.commit()
    except Jailed as e:
        log.error(str(e))
        if not dry_run:
            conn.commit()

    log.info(f"Matched {matched}, updated {filled} players"
             + (" (dry run -- nothing written)" if dry_run else ""))
    if filled and not dry_run:
        log.info("Rebuilding categories...")
        rebuild_categories(conn, sport)
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="Polite Sports-Reference bio backfill (opt-in)")
    ap.add_argument("--sport", required=True, choices=sorted(SR))
    ap.add_argument("--fields", default="college,birth_state,draft_year,draft_round,draft_pick",
                    help="Comma-separated player columns to fill if empty")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--delay", type=float, default=5.0, help="Seconds between requests (>=4 recommended)")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cf-clearance", help="cf_clearance cookie value (overrides "
                    "SR_CF_CLEARANCE from .env, the recommended place to keep it)")
    ap.add_argument("--user-agent", help="Exact UA the cookie was issued to (overrides "
                    "SR_CF_UA / the built-in default; only needed if your Chrome differs)")
    ap.add_argument("--cf-expires", help="cf_clearance Expires value from DevTools "
                    "(e.g. 2027-06-18T15:49:18Z); the run won't make requests past it")
    args = ap.parse_args()

    # Secrets live in the gitignored .env, never in source. Flag > env > default.
    _load_env_file(os.path.join(ROOT, ".env"))
    cf_clearance = args.cf_clearance or os.environ.get("SR_CF_CLEARANCE")
    user_agent = args.user_agent or os.environ.get("SR_CF_UA")
    if not args.cf_clearance and cf_clearance:
        log.info("  cf_clearance loaded from SR_CF_CLEARANCE (.env)")

    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    log.info(f"SR backfill: sport={args.sport} fields={fields} delay={args.delay}s "
             f"limit={args.limit}{' DRY-RUN' if args.dry_run else ''}"
             f"{' +cf_clearance' if cf_clearance else ''}")
    backfill(args.db, args.sport, fields, args.limit, args.delay, args.dry_run,
             cf_clearance, user_agent, args.cf_expires)


if __name__ == "__main__":
    main()
