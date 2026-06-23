"""
data/etl/db_all.py
==================
Build the UNPRUNED everything-database -- every player Sports-Reference indexes,
all stats, no prune. Completely separate from the game DB (data/nfl.db): its own
file (data/db_all.db), its own runbook, and it never deletes a single row.

It reuses the game crawler's machinery in full_mode (no coverage-gap cutoff), so
NBA / WNBA / NHL / NFL each pull EVERY player off their alphabetical index,
deduped only against whatever's already loaded. The shared page cache
(data/cache/sr/) means any page already fetched for the game build is reused for
free -- you only pay for the long tail of non-notable players the game prunes.

Runbook (everything --db data/db_all.db, and NEVER prune):

    python run_full.py --skip-puzzles --db data/db_all.db          # bulk: MLB + college bulk are loaded here
    python -m data.etl.db_all --sport ALL                          # full alpha: NBA + WNBA + NHL (no cookie)
    python -m data.etl.db_all --sport NFL                          # full PFR alpha (Cloudflare cookie, ~28k, slow)
    python -m data.etl.db_all --sport COLLEGE                      # full cbb/cfb index: every NCAAF/NCAAB/NCAAW player (no cookie)
    python -m data.etl.sr_college --sport ALL --db data/db_all.db  # award flags + pre-bulk legends

ALL = NBA + WNBA + NHL (no cookie needed). NFL is opt-in: pro-football-reference
is Cloudflare-gated and the index is ~28k players, so run it on a fresh
SR_CF_CLEARANCE and expect to babysit/resume. It's resumable from the page cache.

COLLEGE = NCAAF + NCAAB + NCAAW, each crawled off its sports-reference.com letter
index (/cbb|cfb/players/{letter}-index.html) -- this is the obscure pre-coverage
long tail (pre-2003 walk-ons, never-an-award players) that the bulk and award
crawls miss. sports-reference.com isn't Cloudflare-gated, so no cookie; cbb's
index is shared by men and women, split on the women's '-w' slug suffix. Huge and
slow, but resumable from the page cache. Run a flag on its own (--sport NCAAB) or
all three with --sport COLLEGE.
"""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.sr_history import HISTORY, crawl
from data.etl.sr_college import crawl_index, INDEX
from data.etl.providers.csv_season import NHLProvider, NFLProvider
from data.etl.backfill_sr import _load_env_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("db_all")

DB_PATH = os.path.join(ROOT, "data", "db_all.db")

# Full-crawl configs. NBA/WNBA reuse the game's index / team_map / stat configs --
# their cutoff & recent_after keys are simply ignored in full_mode. NHL and NFL
# aren't in the game's HISTORY (they reach the game DB via the awards crawl), so
# they get their alpha-index configs here.
FULL = {
    "NBA":  HISTORY["NBA"],
    "WNBA": HISTORY["WNBA"],
    "NHL": {
        "domain": "https://www.hockey-reference.com",
        "alpha":  "https://www.hockey-reference.com/players/{letter}/",
        "players": "/players/", "team_map": NHLProvider.TEAM_MAP,
        "stat_table": "player_stats", "stat_league": "NHL",
        "stat_cols": {"goals": "nhl_goals", "assists": "nhl_assists",
                      "points": "nhl_points", "games": "nhl_games"},
    },
    "NFL": {
        "domain": "https://www.pro-football-reference.com",
        "alpha":  "https://www.pro-football-reference.com/players/{letter}/",
        "players": "/players/", "upper_letters": True, "team_map": NFLProvider.TEAM_MAP,
        "franchise": "nfl",   # standardize teams off the /teams/<code>/ href (registry)
        "stat_tables": [
            ("passing", {"pass_yds": "pass_yds"}),
            ("rushing_and_receiving", {"rush_yds": "rush_yds", "rec_yds": "rec_yds",
                                       "rush_receive_td": "total_tds"}),
        ],
    },
}

# ALL = the no-cookie three; NFL is opt-in (Cloudflare-gated + ~28k players).
ALL_SPORTS = ["NBA", "WNBA", "NHL"]
# COLLEGE = the cbb/cfb full player-index crawl (its own flags). Separate from
# ALL because it's huge and runs through the college crawler (sr_college), not the
# pro alpha engine. sports-reference.com (college) is not Cloudflare-gated.
COLLEGE = list(INDEX)   # ["NCAAF", "NCAAB", "NCAAW"]


def main():
    ap = argparse.ArgumentParser(description="Build the unpruned db_all (full SR alpha crawl)")
    ap.add_argument("--sport", default="ALL",
                    help="NBA / WNBA / NHL / NFL / NCAAF / NCAAB / NCAAW / ALL / COLLEGE  "
                         "(ALL = NBA+WNBA+NHL; COLLEGE = NCAAF+NCAAB+NCAAW; NFL is opt-in)")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=None, help="Cap players per sport (validation)")
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true", help="List who'd be pulled; fetch nothing extra")
    ap.add_argument("--cf-clearance", help="overrides SR_CF_CLEARANCE from .env (NFL only)")
    ap.add_argument("--no-cookie", action="store_true",
                    help="send NO cf_clearance (basketball/hockey-reference aren't gated)")
    ap.add_argument("--user-agent")
    args = ap.parse_args()

    _load_env_file(os.path.join(ROOT, ".env"))
    cf = None if args.no_cookie else (args.cf_clearance or os.environ.get("SR_CF_CLEARANCE"))
    ua = args.user_agent or os.environ.get("SR_CF_UA")

    arg = args.sport.upper()
    sports = ALL_SPORTS if arg == "ALL" else COLLEGE if arg == "COLLEGE" else [arg]
    log.info(f"db_all -> {args.db}  (full unpruned crawl: {', '.join(sports)})")
    for sp in sports:
        if sp in COLLEGE:                      # cbb/cfb letter-index crawl (sr_college)
            crawl_index(sp, args.db, args.limit, args.delay, args.dry_run, cf, ua)
        elif sp in FULL:                       # NBA/WNBA/NHL/NFL alpha crawl (sr_history)
            crawl(sp, args.db, args.limit, args.delay, args.dry_run, cf, ua,
                  full_mode=True, cfg=FULL[sp])
        else:
            log.error(f"Unknown sport {sp!r}; known: {', '.join(list(FULL) + COLLEGE)} "
                      f"(or ALL = {'+'.join(ALL_SPORTS)}, COLLEGE = {'+'.join(COLLEGE)})")


if __name__ == "__main__":
    main()
