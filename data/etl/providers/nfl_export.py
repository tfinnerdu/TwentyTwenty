"""
data/etl/providers/nfl_export.py
================================
Pull NFL career data from nflverse (nfl_data_py) into the canonical
nfl_players.csv / nfl_seasons.csv that NFLProvider ingests.

    pip install nfl_data_py
    python -m data.etl.providers.nfl_export --start 1999 --end 2023 --out ./nfl_csv
    python -m data.etl.run_provider --provider nflverse --source-dir ./nfl_csv

Coverage / caveats:
  - nflverse seasonal data is offense-oriented: passing/rushing/receiving
    yards + TDs + games come through cleanly. Defensive sacks/INTs are not in
    the seasonal feed, so those stay 0 (defensive players still get bio +
    position, and honors come from awards/nfl.json).
  - Column names vary slightly across nfl_data_py versions, so fields are read
    defensively. Verify against your installed version on first run.
"""

import argparse
import csv
import os


def _val(rec, *names, default=""):
    """First present, non-NaN value among candidate column names."""
    for n in names:
        if n in rec:
            v = rec[n]
            if v is not None and v == v:  # not NaN
                return v
    return default


def _int(rec, *names):
    v = _val(rec, *names, default=0)
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _current_nfl_season() -> int:
    """Latest COMPLETE NFL season. Seasons start in September, so before then the
    prior year is the most recent complete one. Keeps the export from freezing at
    a hard-coded year (which truncates newer players + mid-trade teams)."""
    from datetime import date
    t = date.today()
    return t.year if t.month >= 9 else t.year - 1


def _per_year(importer, years, label):
    """Fetch an nfl_data_py importer season-by-season, SKIPPING any year nflverse
    hasn't published yet (its parquet 404s -- releases lag the live season). So a
    dynamic end year can't crash the export; it just stops at the latest available."""
    if importer is None:
        return []
    rows, got = [], []
    for y in years:
        try:
            rows.extend(importer([y]).to_dict("records"))
            got.append(y)
        except Exception:
            pass                                   # season not published yet
    if got:
        print(f"  nfl_export: {label} {got[0]}-{got[-1]} ({len(got)} of {len(years)} seasons)")
    return rows


def export(out_dir: str, start: int = 1999, end: int | None = None):
    import nfl_data_py as nfl  # noqa: imported lazily so the app needn't have it
    os.makedirs(out_dir, exist_ok=True)
    if end is None:
        end = _current_nfl_season()
    years = list(range(start, end + 1))

    seasonal = _per_year(nfl.import_seasonal_data, years, "seasonal")
    people = nfl.import_players().to_dict("records")

    bio = {}
    for p in people:
        pid = _val(p, "gsis_id", "player_id")
        if pid:
            bio[pid] = p

    # ALL teams per (pid, season), in chronological order. Weekly rosters catch
    # mid-season trades (e.g. Baker Mayfield 2022 = Panthers AND Rams) that the
    # seasonal roster -- one team per season -- silently drops. Fall back to the
    # seasonal roster if the weekly endpoint isn't available.
    teams_by: dict = {}        # (pid, season) -> [team, ...]

    def _add(pid, season, team):
        if pid and team:
            lst = teams_by.setdefault((pid, season), [])
            if team not in lst:
                lst.append(team)

    roster_rows = _per_year(getattr(nfl, "import_weekly_rosters", None), years, "weekly rosters")
    if not roster_rows:        # endpoint missing or nothing published -> seasonal roster
        roster_rows = _per_year(getattr(nfl, "import_seasonal_rosters", None), years, "seasonal rosters")
    for r in roster_rows:
        _add(_val(r, "player_id", "gsis_id"), _int(r, "season"), _val(r, "team", "recent_team"))

    seasons, pids = [], set()
    for s in seasonal:
        pid = _val(s, "player_id", "gsis_id")
        if not pid:
            continue
        yr = _int(s, "season")
        tlist = teams_by.get((pid, yr)) or [""]
        seasons.append([pid, yr, tlist[0],
                        _int(s, "games", "g"),
                        _int(s, "passing_yards"), _int(s, "rushing_yards"),
                        _int(s, "receiving_yards"), _int(s, "passing_tds"),
                        _int(s, "rushing_tds"), _int(s, "receiving_tds"),
                        0, 0])   # sacks, interceptions (defensive; not in feed)
        for extra in tlist[1:]:        # team-only rows so every franchise registers
            seasons.append([pid, yr, extra, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        pids.add(pid)

    players = []
    for pid in pids:
        b = bio.get(pid, {})
        name = _val(b, "display_name", "football_name", "player_name") or pid
        birth = str(_val(b, "birth_date", "birthdate"))
        players.append([pid, name, _val(b, "position", "position_group"),
                        _val(b, "college_name", "college"),
                        birth[:4] if birth[:4].isdigit() else "",
                        "", "", "USA", _int(b, "height"), _int(b, "weight"),
                        _int(b, "entry_year", "rookie_year", "draft_year"),
                        _int(b, "draft_round"), _int(b, "draft_number")])

    _write(out_dir, "nfl",
           ["player_id", "season", "team", "g", "pass_yds", "rush_yds", "rec_yds",
            "pass_td", "rush_td", "rec_td", "sacks", "interceptions"], seasons,
           ["player_id", "name", "position", "college", "birth_year", "birth_city",
            "birth_state", "birth_country", "height_inches", "weight_lbs",
            "draft_year", "draft_round", "draft_number"], players)
    return len(players), len(seasons)


def _write(out_dir, sport, season_hdr, season_rows, player_hdr, player_rows):
    with open(os.path.join(out_dir, f"{sport}_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(player_hdr); w.writerows(player_rows)
    with open(os.path.join(out_dir, f"{sport}_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(season_hdr); w.writerows(season_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1999)
    ap.add_argument("--end", type=int, default=None, help="last season (default: current complete season)")
    ap.add_argument("--out", default="./nfl_csv")
    args = ap.parse_args()
    p, s = export(args.out, args.start, args.end)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()
