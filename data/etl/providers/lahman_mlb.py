"""
data/etl/providers/lahman_mlb.py
================================
MLB provider backed by the Lahman / Chadwick Bureau Baseball Databank.

Why Lahman:
  - Openly licensed (CC-BY-SA) -- download once, own it, redistribute in
    the game. No bot-blocking, no per-call rental terms.
  - Complete history back to 1871.
  - Ships the awards data our categories need: MVP, Cy Young, Gold Glove,
    All-Star selections, and World Series titles (via SeriesPost x Appearances).

Data source (networked run):
    https://github.com/chadwickbureau/baseballdatabank   (core/ + contrib/ CSVs)

The CSVs are downloaded once into data/cache/lahman/ and reused. For an
offline / firewalled environment, point --source-dir at a folder that
already holds the CSVs (see providers/_fixture_mlb.py for the schema).

Usage:
    python -m data.etl.run_provider --provider lahman_mlb
    python -m data.etl.run_provider --provider lahman_mlb --source-dir /path/to/csvs
"""

import csv
import logging
import os
import sys
import urllib.request
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers.base import Provider

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/chadwickbureau/baseballdatabank/master"
# data/cache/lahman -- under data/cache/ which .gitignore excludes (the raw
# CSVs are ~50MB and regenerable, so they never get committed).
DEFAULT_CACHE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache", "lahman"
)

# logical name -> path within the repo. Stored locally as the basename.
LAHMAN_FILES = {
    "People":       "core/People.csv",
    "Batting":      "core/Batting.csv",
    "Pitching":     "core/Pitching.csv",
    "Appearances":  "core/Appearances.csv",
    "AllstarFull":  "core/AllstarFull.csv",
    "SeriesPost":   "core/SeriesPost.csv",
    "Teams":        "core/Teams.csv",
    "AwardsPlayers": "contrib/AwardsPlayers.csv",
}

# Award strings as they appear in AwardsPlayers.awardID
AWARD_MVP = "Most Valuable Player"
AWARD_CY  = "Cy Young Award"
AWARD_GG  = "Gold Glove"


# ---------------------------------------------------------------------------
# small parse helpers (CSV cells are strings, often empty)
# ---------------------------------------------------------------------------

def _i(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _f(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _nickname(team_name: str) -> str:
    """'Boston Red Sox' -> 'Red Sox';  'New York Yankees' -> 'Yankees'."""
    if not team_name:
        return ""
    for multi in ("Red Sox", "White Sox", "Blue Jays"):
        if team_name.endswith(multi):
            return multi
    return team_name.split()[-1]


class LahmanMLBProvider(Provider):
    sport = "MLB"
    source_name = "lahman"

    def __init__(self, data_dir: str | None = None,
                 base_url: str = DEFAULT_BASE_URL, refresh: bool = False):
        self.data_dir = data_dir or DEFAULT_CACHE
        self.base_url = base_url.rstrip("/")
        self.refresh = refresh

    # -- file acquisition ---------------------------------------------------

    def _local(self, logical: str) -> str:
        return os.path.join(self.data_dir, f"{logical}.csv")

    def ensure_data(self):
        """Make sure every CSV is present locally, downloading what's missing."""
        os.makedirs(self.data_dir, exist_ok=True)
        for logical, repo_path in LAHMAN_FILES.items():
            dest = self._local(logical)
            if os.path.exists(dest) and not self.refresh:
                continue
            url = f"{self.base_url}/{repo_path}"
            log.info(f"  downloading {logical} <- {url}")
            try:
                urllib.request.urlretrieve(url, dest)
            except Exception as e:
                raise RuntimeError(
                    f"Could not download {logical} from {url} ({e}). "
                    f"Supply the CSVs locally with --source-dir instead."
                ) from e

    def _rows(self, logical: str):
        path = self._local(logical)
        if not os.path.exists(path):
            return
        with open(path, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)

    # -- parse + aggregate --------------------------------------------------

    def fetch(self) -> Iterable[dict]:
        self.ensure_data()

        team_nick = self._team_nicknames()
        ws_winners = self._world_series_winners()

        # per-player accumulators
        appear: dict = {}   # pid -> {teams:[], team_years:set, years:set, pos:{}}
        for r in self._rows("Appearances"):
            pid = r["playerID"]
            a = appear.setdefault(pid, {"teams": [], "team_years": set(),
                                        "years": set(), "pos": {}})
            year = _i(r["yearID"])
            team = r["teamID"]
            a["years"].add(year)
            a["team_years"].add((year, team))
            nick = team_nick.get(team)
            if nick and nick not in a["teams"]:
                a["teams"].append(nick)
            for col, key in (("G_p", "P"), ("G_c", "C"), ("G_1b", "1B"),
                             ("G_2b", "2B"), ("G_3b", "3B"), ("G_ss", "SS"),
                             ("G_of", "OF"), ("G_dh", "DH")):
                a["pos"][key] = a["pos"].get(key, 0) + _i(r.get(col))

        batting: dict = {}  # pid -> {ab,h,hr,rbi}
        for r in self._rows("Batting"):
            b = batting.setdefault(r["playerID"], {"ab": 0, "h": 0, "hr": 0, "rbi": 0})
            b["ab"]  += _i(r.get("AB"));  b["h"]   += _i(r.get("H"))
            b["hr"]  += _i(r.get("HR"));  b["rbi"] += _i(r.get("RBI"))

        pitching: dict = {}  # pid -> {w,so,sv,gs,g,er,ipouts}
        for r in self._rows("Pitching"):
            p = pitching.setdefault(r["playerID"],
                                    {"w": 0, "so": 0, "sv": 0, "gs": 0,
                                     "g": 0, "er": 0, "ipouts": 0})
            p["w"]  += _i(r.get("W"));   p["so"] += _i(r.get("SO"))
            p["sv"] += _i(r.get("SV"));  p["gs"] += _i(r.get("GS"))
            p["g"]  += _i(r.get("G"));   p["er"] += _i(r.get("ER"))
            p["ipouts"] += _i(r.get("IPouts"))

        awards: dict = {}   # pid -> {mvp,cy,gg}
        for r in self._rows("AwardsPlayers"):
            a = awards.setdefault(r["playerID"], {"mvp": 0, "cy": 0, "gg": 0})
            aid = r.get("awardID", "")
            if aid == AWARD_MVP:
                a["mvp"] += 1
            elif aid == AWARD_CY:
                a["cy"] += 1
            elif aid == AWARD_GG:
                a["gg"] += 1

        allstar: dict = {}  # pid -> set(years)
        for r in self._rows("AllstarFull"):
            allstar.setdefault(r["playerID"], set()).add(_i(r["yearID"]))

        for person in self._rows("People"):
            pid = person["playerID"]
            if pid not in appear and pid not in batting and pid not in pitching:
                continue  # no MLB activity recorded
            yield self._build(pid, person, appear.get(pid), batting.get(pid),
                              pitching.get(pid), awards.get(pid),
                              allstar.get(pid), ws_winners)

    # -- lookups ------------------------------------------------------------

    def _team_nicknames(self) -> dict:
        """teamID -> nickname, taking the most recent club name for that id."""
        latest: dict = {}
        for r in self._rows("Teams"):
            tid = r.get("teamID")
            if not tid:
                continue
            year = _i(r.get("yearID"))
            if tid not in latest or year >= latest[tid][0]:
                latest[tid] = (year, _nickname(r.get("name", "")))
        return {tid: nick for tid, (_, nick) in latest.items()}

    def _world_series_winners(self) -> set:
        """{(yearID, teamID)} for every World Series winner."""
        winners = set()
        for r in self._rows("SeriesPost"):
            if r.get("round") == "WS":
                winners.add((_i(r["yearID"]), r["teamIDwinner"]))
        return winners

    # -- per-player payload -------------------------------------------------

    def _build(self, pid, person, appear, batting, pitching,
               awards, allstar_years, ws_winners) -> dict:
        appear   = appear   or {"teams": [], "team_years": set(), "years": set(), "pos": {}}
        batting  = batting  or {"ab": 0, "h": 0, "hr": 0, "rbi": 0}
        pitching = pitching or {"w": 0, "so": 0, "sv": 0, "gs": 0, "g": 0, "er": 0, "ipouts": 0}
        awards   = awards   or {"mvp": 0, "cy": 0, "gg": 0}
        allstar_years = allstar_years or set()

        # name
        first = person.get("nameFirst") or person.get("nameGiven", "")
        name = f"{first} {person.get('nameLast', '')}".strip()

        # birth
        by, bm, bd = _i(person.get("birthYear")), _i(person.get("birthMonth")), _i(person.get("birthDay"))
        birth_date = f"{by:04d}-{bm:02d}-{bd:02d}" if by and bm and bd else ""

        # career span / decades
        years = appear["years"]
        debut_year = _i(person.get("debut")[:4]) if person.get("debut") else (min(years) if years else None)
        final_year = _i(person.get("finalGame")[:4]) if person.get("finalGame") else (max(years) if years else None)
        decades = sorted({f"{(y // 10) * 10}s" for y in years})

        # batting / pitching career lines
        avg = round(batting["h"] / batting["ab"], 3) if batting["ab"] else 0.0
        era = round(27 * pitching["er"] / pitching["ipouts"], 2) if pitching["ipouts"] else 0.0

        # World Series titles: years where the player's (year, team) won it all
        championships = len({y for (y, t) in appear["team_years"] if (y, t) in ws_winners})

        # position + group
        position, group = self._position(appear["pos"], pitching)

        return {
            "sport": "MLB",
            "sr_id": pid,
            "name": name,
            "position": position,
            "position_group": group,
            "birth_date": birth_date,
            "birth_year": by or None,
            "birth_city": person.get("birthCity", ""),
            "birth_state": person.get("birthState", ""),
            "birth_country": person.get("birthCountry", "") or "USA",
            "height_inches": _i(person.get("height")) or None,
            "weight_lbs": _i(person.get("weight")) or None,
            "teams": appear["teams"],
            "active_decades": decades,
            "debut_year": debut_year,
            "final_year": final_year,
            # Lahman has no amateur-draft data -> leave NULL so MLB players
            # aren't falsely tagged "Undrafted" (draft categories skip NULLs).
            "draft_year": None,
            "draft_round": None,
            "draft_pick": None,
            # MLB stat columns
            "mlb_batting_avg": avg,
            "mlb_hr": batting["hr"],
            "mlb_rbi": batting["rbi"],
            "mlb_hits": batting["h"],
            "mlb_era": era,
            "mlb_wins": pitching["w"],
            "mlb_strikeouts": pitching["so"],
            "mlb_saves": pitching["sv"],
            "mlb_war": 0.0,  # not in Lahman core; supplement later if needed
            "mlb_all_star": len(allstar_years),
            "mlb_championships": championships,
            "mlb_mvps": awards["mvp"],
            "mlb_cy_young": awards["cy"],
            "mlb_gold_gloves": awards["gg"],
        }

    @staticmethod
    def _position(pos_games: dict, pitching: dict) -> tuple:
        """Primary position (by games) -> (position, position_group)."""
        if not pos_games or max(pos_games.values(), default=0) == 0:
            return "", ""
        primary = max(pos_games, key=pos_games.get)
        if primary == "P":
            starter = pitching["gs"] >= 0.5 * pitching["g"] if pitching["g"] else False
            return ("SP", "SP") if starter else ("RP", "RP")
        group = {"C": "C", "1B": "IF", "2B": "IF", "3B": "IF",
                 "SS": "IF", "OF": "OF", "DH": ""}.get(primary, "")
        return primary, group
