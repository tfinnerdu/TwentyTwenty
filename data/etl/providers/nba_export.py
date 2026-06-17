"""
data/etl/providers/nba_export.py
================================
Pull NBA career data from the official stats.nba.com (via nba_api) into the
canonical nba_players.csv / nba_seasons.csv that NBAProvider ingests.

    pip install nba_api
    python -m data.etl.providers.nba_export --start 1996 --end 2024 --out ./nba_csv
    # add --enrich for position/college/draft/birth (slower: 1 call per player)
    python -m data.etl.run_provider --provider nba_csv --source-dir ./nba_csv

Coverage / caveats:
  - Per-season totals via LeagueDashPlayerStats (one call per season) -> fast,
    covers 1996-97 onward. Stats/games/teams come through directly.
  - Bio (position/college/draft/birth) needs CommonPlayerInfo per player, so
    it's behind --enrich. Without it, PPG/RPG/APG/games/team categories work
    and honors come from awards/nba.json; position/college stay blank.
  - stats.nba.com rate-limits; a small delay between calls is used.
"""

import argparse
import csv
import os
import time


def _season_str(y: int) -> str:
    return f"{y}-{str(y + 1)[-2:]}"


def _height_in(h) -> int:
    try:
        ft, inch = str(h).split("-")
        return int(ft) * 12 + int(inch)
    except Exception:
        return 0


def export(out_dir: str, start: int = 1996, end: int = 2024,
           enrich: bool = False, delay: float = 0.6):
    from nba_api.stats.endpoints import leaguedashplayerstats
    os.makedirs(out_dir, exist_ok=True)

    seasons, pids = [], {}     # pid -> name
    for y in range(start, end + 1):
        ss = _season_str(y)
        df = leaguedashplayerstats.LeagueDashPlayerStats(
            season=ss, per_mode_detailed="Totals",
            season_type_all_star="Regular Season").get_data_frames()[0]
        for r in df.to_dict("records"):
            pid = r["PLAYER_ID"]
            pids[pid] = r.get("PLAYER_NAME", "")
            seasons.append([pid, y, r.get("TEAM_ABBREVIATION", ""),
                            int(r.get("GP", 0) or 0), int(r.get("PTS", 0) or 0),
                            int(r.get("REB", 0) or 0), int(r.get("AST", 0) or 0)])
        time.sleep(delay)

    players = []
    for pid, name in pids.items():
        pos = college = birth_year = height = weight = ""
        dy = dr = dn = ""
        if enrich:
            try:
                from nba_api.stats.endpoints import commonplayerinfo
                info = commonplayerinfo.CommonPlayerInfo(player_id=pid).get_data_frames()[0].to_dict("records")[0]
                raw = (info.get("POSITION", "") or "").split("-")[0]
                pos = {"Guard": "G", "Forward": "F", "Center": "C"}.get(raw, "")
                college = info.get("SCHOOL", "") or ""
                birth_year = str(info.get("BIRTHDATE", ""))[:4]
                height = _height_in(info.get("HEIGHT", ""))
                weight = info.get("WEIGHT", "") or ""
                dy, dr, dn = info.get("DRAFT_YEAR", ""), info.get("DRAFT_ROUND", ""), info.get("DRAFT_NUMBER", "")
                time.sleep(delay)
            except Exception:
                pass
        players.append([f"{pid}", name, pos, college,
                        birth_year if str(birth_year).isdigit() else "",
                        "", "", "USA", height, weight, dy, dr, dn])

    with open(os.path.join(out_dir, "nba_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(players)
    with open(os.path.join(out_dir, "nba_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "pts", "trb", "ast"])
        w.writerows(seasons)
    return len(players), len(seasons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1996)
    ap.add_argument("--end", type=int, default=2024)
    ap.add_argument("--out", default="./nba_csv")
    ap.add_argument("--enrich", action="store_true", help="Add bio via CommonPlayerInfo (slow)")
    args = ap.parse_args()
    p, s = export(args.out, args.start, args.end, args.enrich)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()
