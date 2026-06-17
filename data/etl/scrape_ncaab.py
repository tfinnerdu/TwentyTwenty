"""
data/etl/scrape_ncaab.py
------------------------
Scrapes college basketball player data from sports-reference.com/cbb/.
Handles both men's (NCAAB) and women's (NCAAW) with a --gender flag.

URL patterns:
  Men's:   sports-reference.com/cbb/players/[name-id].html
  Women's: sports-reference.com/cbb/players/[name-id]-w.html
           (women's IDs end with '-w' suffix)

Discovery:
  Men's:   /cbb/awards/award.fcgi?award=POY  (Player of Year winners)
           /cbb/leaders/pts_career.html
  Women's: /cbb/wnba/awards/ + /cbb/leaders/ with gender param

Usage:
    python data/etl/scrape_ncaab.py --gender mens
    python data/etl/scrape_ncaab.py --gender womens
    python data/etl/scrape_ncaab.py --gender mens --from-cache
    python data/etl/scrape_ncaab.py --gender womens --limit 30

Run from your machine (residential IP).
"""

import argparse, json, os, re, sys, time, logging
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

BASE_URL  = "https://www.sports-reference.com"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
DB_PATH   = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Sports-reference.com blocks award and leader pages with bot detection,
# and the award.fcgi URLs are 404 anyway. Use the static alphabetical
# player index instead. Men's and women's use the same index URL scheme
# but the pages themselves list gender -- we filter by URL suffix.
# Men's:   /cbb/players/a-m-index.htm
# Women's: /cbb/players/a-w-index.htm
PLAYER_INDEX_LETTERS = "abcdefghijklmnopqrstuvwxyz"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def cache_file(gender: str) -> str:
    return os.path.join(CACHE_DIR, f"ncaab_{gender}_raw.json")


# Tracks consecutive 403s so we can bail out of a jailed session fast
_consecutive_403s = 0
_MAX_403s = 3  # abort sport after this many in a row

def fetch(url: str, delay: float = 1.0) -> Optional[BeautifulSoup]:
    global _consecutive_403s
    time.sleep(delay)
    try:
        r = SESSION.get(url, timeout=15)

        if r.status_code == 403:
            _consecutive_403s += 1
            log.warning(
                f"HTTP 403 for {url} "
                f"({_consecutive_403s}/{_MAX_403s} consecutive -- "
                f"session may be jailed, see sports-reference.com bot policy)"
            )
            if _consecutive_403s >= _MAX_403s:
                log.error(
                    "Too many 403s in a row. Session is likely jailed (up to 24h). "
                    "Wait and retry, or try a different network/IP."
                )
            return None

        if r.status_code == 429:
            _consecutive_403s = 0
            log.warning("Rate limited (429) -- sleeping 60s then retrying")
            time.sleep(60)
            r = SESSION.get(url, timeout=15)

        if r.status_code != 200:
            log.warning(f"HTTP {r.status_code} for {url}")
            return None

        _consecutive_403s = 0  # reset on success
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.error(f"Fetch error for {url}: {e}")
        return None


def discover_player_ids(gender: str, delay: float = 1.0) -> set:
    """
    CBB uses a single shared player index for men and women:
    /cbb/players/a-index.htm, /cbb/players/b-index.htm, etc.
    Both genders appear in the same index. We collect all IDs here
    and let scrape_player() filter by gender when it fetches each page.
    """
    player_ids = set()
    for letter in PLAYER_INDEX_LETTERS:
        path = f"/cbb/players/{letter}-index.htm"
        log.info(f"  Discovering from {path}")
        soup = fetch(BASE_URL + path, delay)
        if not soup:
            continue
        for a in soup.select("a[href*='/cbb/players/']"):
            href = a.get("href", "")
            m = re.search(r"/cbb/players/([\w\-]+)\.html", href)
            if m and "-" in m.group(1):
                player_ids.add(m.group(1))
    log.info(f"Discovered {len(player_ids)} unique CBB player IDs (will filter to {gender} on scrape)")
    return player_ids


def _int_cell(row, stat: str) -> int:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell: return 0
    try: return int(cell.get_text(strip=True).replace(",","") or 0)
    except: return 0


def _float_cell(row, stat: str) -> float:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell: return 0.0
    try: return float(cell.get_text(strip=True) or 0)
    except: return 0.0


def parse_player_page(soup: BeautifulSoup, sr_id: str, gender: str) -> Optional[dict]:
    h1 = soup.select_one("div#meta h1 span") or soup.select_one("h1")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    # Detect gender from the page: women's pages have a gender indicator
    # in the breadcrumbs or stats table headers. Check for "Women" in
    # the page navigation or sport label.
    page_text = soup.get_text(" ", strip=True)
    page_is_womens = (
        "Women" in (soup.select_one("div#content") or soup).get_text(" ")
        or soup.select_one("a[href*='/cbb/schools/'][href*='women']") is not None
        or "-w" in sr_id
    )
    page_is_mens = not page_is_womens

    # Skip pages that don't match the requested gender
    if gender == "mens" and page_is_womens:
        return None
    if gender == "womens" and page_is_mens:
        return None

    sport = "NCAAB" if gender == "mens" else "NCAAW"

    meta = soup.select_one("div#meta")
    bio = {}
    if meta:
        for p in meta.select("p"):
            text = p.get_text(" ", strip=True)
            if "Position:" in text:
                pos_raw = text.split("Position:")[-1].strip().split("\n")[0].strip()
                pos_map = {
                    "point guard": "PG", "shooting guard": "SG", "guard": "G",
                    "small forward": "SF", "power forward": "PF", "forward": "F",
                    "center": "C", "forward-center": "FC", "guard-forward": "GF",
                }
                bio["position"] = pos_map.get(pos_raw.lower().split("/")[0].strip(), pos_raw[:2].upper())
            if "School:" in text or "College:" in text:
                school_link = p.select_one("a[href*='/cbb/schools/']")
                if school_link:
                    bio["college"] = school_link.get_text(strip=True)
            if "Hometown:" in text:
                hometown = text.split("Hometown:")[-1].strip()
                parts = hometown.split(",")
                if len(parts) >= 2:
                    bio["birth_city"]  = parts[0].strip()
                    bio["birth_state"] = parts[1].strip()[:2]
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth","")
                try: bio["birth_year"] = int(bio["birth_date"][:4])
                except: pass
            # High school
            if "High School:" in text:
                hs = text.split("High School:")[-1].strip().split(",")[0]
                bio["high_school"] = hs

    # Career per-game stats
    stats = {
        "ncaab_points": 0.0, "ncaab_rebounds": 0.0, "ncaab_assists": 0.0,
        "ncaab_games": 0, "ncaab_championships": 0,
        "ncaab_all_american": 0, "ncaab_player_of_year": 0,
    }

    per_game = soup.select_one("table#players_per_game") or soup.select_one("table#per_game")
    if per_game:
        cr = per_game.select_one("tfoot tr")
        if cr:
            stats["ncaab_points"]   = _float_cell(cr, "pts_per_g")
            stats["ncaab_rebounds"] = _float_cell(cr, "trb_per_g")
            stats["ncaab_assists"]  = _float_cell(cr, "ast_per_g")

    totals = soup.select_one("table#players_totals") or soup.select_one("table#totals")
    if totals:
        cr = totals.select_one("tfoot tr")
        if cr:
            g = cr.select_one("td[data-stat='g']")
            try: stats["ncaab_games"] = int(g.get_text(strip=True) or 0) if g else 0
            except: pass

    for li in soup.select("ul#bling li"):
        text = li.get_text(" ", strip=True).lower()
        if "national champion" in text or "ncaa champion" in text:
            m = re.search(r"(\d+)x", text)
            stats["ncaab_championships"] = int(m.group(1)) if m else 1
        if "all-american" in text:
            m = re.search(r"(\d+)x", text)
            stats["ncaab_all_american"] = int(m.group(1)) if m else 1
        if "player of the year" in text or "poy" in text:
            stats["ncaab_player_of_year"] = 1

    decades = set()
    for tbl in soup.select("table tbody tr[id]"):
        th = tbl.select_one("th[data-stat='season']") or tbl.select_one("th[data-stat='year_id']")
        if th:
            y = th.get_text(strip=True)[:4]
            try: decades.add(f"{(int(y) // 10) * 10}s")
            except: pass

    pos = bio.get("position","")
    pos_group = "G" if pos in ("PG","SG","G","GF") else \
                "F" if pos in ("SF","PF","F","FC","GF") else \
                "C" if pos == "C" else pos

    return {
        "sport": sport, "sr_id": sr_id, "name": name,
        "position": pos, "position_group": pos_group,
        "college": bio.get("college",""), "high_school": bio.get("high_school",""),
        "birth_date": bio.get("birth_date",""), "birth_year": bio.get("birth_year"),
        "birth_city": bio.get("birth_city",""), "birth_state": bio.get("birth_state",""),
        "birth_country": "USA",
        "teams": [bio["college"]] if bio.get("college") else [],
        "active_decades": sorted(decades),
        **stats,
    }


def scrape_player(sr_id: str, gender: str, delay: float = 1.0) -> Optional[dict]:
    url = f"{BASE_URL}/cbb/players/{sr_id}.html"
    soup = fetch(url, delay)
    return parse_player_page(soup, sr_id, gender) if soup else None


def load_into_db(players: list, conn, etl_run_id: int) -> tuple:
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0
    for p in players:
        c.execute("SELECT id FROM players WHERE sr_id=?", (p.get("sr_id"),))
        existing = c.fetchone()
        common = (
            p["name"], p.get("position"), p.get("position_group"),
            p.get("college",""), p.get("high_school",""),
            p.get("birth_date",""), p.get("birth_year"),
            p.get("birth_city",""), p.get("birth_state",""), "USA",
            json.dumps(p.get("teams",[])), json.dumps(p.get("active_decades",[])),
            p.get("ncaab_points",0), p.get("ncaab_rebounds",0),
            p.get("ncaab_assists",0), p.get("ncaab_games",0),
            p.get("ncaab_championships",0), p.get("ncaab_all_american",0),
            p.get("ncaab_player_of_year",0),
            "cbb", now, etl_run_id,
        )
        if existing:
            c.execute("""UPDATE players SET name=?,position=?,position_group=?,
                college=?,high_school=?,birth_date=?,birth_year=?,
                birth_city=?,birth_state=?,birth_country=?,
                teams=?,active_decades=?,
                ncaab_points=?,ncaab_rebounds=?,ncaab_assists=?,ncaab_games=?,
                ncaab_championships=?,ncaab_all_american=?,ncaab_player_of_year=?,
                data_source=?,last_updated=?,etl_run_id=? WHERE sr_id=?""",
                      (*common, p["sr_id"]))
            updated += 1
        else:
            c.execute("""INSERT INTO players
                (sport,sr_id,name,position,position_group,college,high_school,
                 birth_date,birth_year,birth_city,birth_state,birth_country,
                 teams,active_decades,
                 ncaab_points,ncaab_rebounds,ncaab_assists,ncaab_games,
                 ncaab_championships,ncaab_all_american,ncaab_player_of_year,
                 data_source,last_updated,etl_run_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (p["sport"], p.get("sr_id"), *common))
            added += 1
    conn.commit()
    return added, updated


def main():
    parser = argparse.ArgumentParser(description="Scrape NCAAB/NCAAW players from sports-reference.com/cbb/")
    parser.add_argument("--gender",     default="mens", choices=["mens","womens"],
                        help="mens or womens (default: mens)")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--db",         type=str, default=DB_PATH)
    args = parser.parse_args()

    sport_label = "NCAAB" if args.gender == "mens" else "NCAAW"
    cf = cache_file(args.gender)
    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info(f"=== 20/20 {sport_label} ETL ===")
    migrate(args.db)
    conn = get_conn(args.db)
    now = datetime.now(timezone.utc).isoformat()
    c = conn.cursor()
    c.execute("INSERT INTO etl_runs (sport,source,run_at,status) VALUES (?,?,?,?)",
              (sport_label, "cbb", now, "running"))
    conn.commit()
    etl_run_id = c.lastrowid

    raw_players = []
    if not args.from_cache and (args.refresh or not os.path.exists(cf)):
        player_ids = discover_player_ids(args.gender, args.delay)
        if args.limit:
            player_ids = set(list(player_ids)[:args.limit])
        log.info(f"Scraping {len(player_ids)} {sport_label} players...")
        failed = 0
        for i, pid in enumerate(sorted(player_ids), 1):
            log.info(f"  [{i}/{len(player_ids)}] {pid}")
            data = scrape_player(pid, args.gender, args.delay)
            if data: raw_players.append(data)
            else: failed += 1
        log.info(f"Done: {len(raw_players)} ok, {failed} failed")
        with open(cf, "w") as f:
            json.dump({"scraped_at": now, "gender": args.gender, "players": raw_players}, f, indent=2)
    else:
        if not os.path.exists(cf):
            log.error(f"No cache at {cf}")
            sys.exit(1)
        with open(cf) as f:
            cache = json.load(f)
        raw_players = cache["players"]
        log.info(f"Loaded {len(raw_players)} from cache")

    added, updated = load_into_db(raw_players, conn, etl_run_id)
    c.execute("UPDATE etl_runs SET players_added=?,players_updated=?,status='ok' WHERE id=?",
              (added, updated, etl_run_id))
    conn.commit()
    log.info(f"Done: {added} added, {updated} updated")
    conn.close()


if __name__ == "__main__":
    main()