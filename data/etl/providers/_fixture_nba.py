"""
data/etl/providers/_fixture_nba.py
==================================
Writes a small NBA season-stats fixture (22 real greats) in the canonical
nba_csv schema, so the NBA provider + awards overlay can be exercised
end to end without network access. Names match data/etl/awards/nba.json so
the curated honors overlay lands on them.

Career totals are real-but-approximate (enough that the PPG/RPG/APG/games
thresholds select the right players). Split across two season rows so the
season->career aggregation and multi-decade logic get exercised.
"""

import csv
import os
import re


def _id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


# name, pos, college, birthYear, city, state, country, height_in, weight_lb,
# draftYear, draftRound, draftNumber, teams[], careerG, PTS, TRB, AST
PLAYERS = [
    ("Michael Jordan",     "G", "North Carolina", 1963, "Brooklyn", "NY", "USA", 78, 216, 1984, 1, 3,  ["CHI"], 1072, 32292, 6672, 5633),
    ("LeBron James",       "F", "",               1984, "Akron", "OH", "USA", 81, 250, 2003, 1, 1,     ["CLE", "LAL"], 1492, 40000, 11000, 11000),
    ("Kareem Abdul-Jabbar","C", "UCLA",           1947, "New York", "NY", "USA", 86, 225, 1969, 1, 1,  ["MIL", "LAL"], 1560, 38387, 17440, 5660),
    ("Magic Johnson",      "G", "Michigan State", 1959, "Lansing", "MI", "USA", 81, 215, 1979, 1, 1,   ["LAL"], 906, 17707, 6559, 10141),
    ("Larry Bird",         "F", "Indiana State",  1956, "West Baden", "IN", "USA", 81, 220, 1978, 1, 6, ["BOS"], 897, 21791, 8974, 5695),
    ("Bill Russell",       "C", "San Francisco",  1934, "Monroe", "LA", "USA", 82, 215, 1956, 1, 2,    ["BOS"], 963, 14522, 21620, 4100),
    ("Wilt Chamberlain",   "C", "Kansas",         1936, "Philadelphia", "PA", "USA", 85, 275, 1959, 1, 3, ["PHI", "LAL"], 1045, 31419, 23924, 4643),
    ("Tim Duncan",         "F", "Wake Forest",    1976, "Christiansted", "", "USA", 83, 250, 1997, 1, 1, ["SAS"], 1392, 26496, 15091, 4225),
    ("Kobe Bryant",        "G", "",               1978, "Philadelphia", "PA", "USA", 78, 212, 1996, 1, 13, ["LAL"], 1346, 33643, 7047, 6306),
    ("Shaquille O'Neal",   "C", "LSU",            1972, "Newark", "NJ", "USA", 85, 325, 1992, 1, 1,    ["ORL", "LAL"], 1207, 28596, 13099, 3026),
    ("Hakeem Olajuwon",    "C", "Houston",        1963, "Lagos", "", "Nigeria", 84, 255, 1984, 1, 1,   ["HOU"], 1238, 26946, 13748, 3058),
    ("Stephen Curry",      "G", "Davidson",       1988, "Akron", "OH", "USA", 74, 185, 2009, 1, 7,     ["GSW"], 960, 23800, 4500, 6100),
    ("Kevin Durant",       "F", "Texas",          1988, "Washington", "DC", "USA", 82, 240, 2007, 1, 2, ["OKC", "GSW"], 1080, 29000, 7600, 4700),
    ("Kevin Garnett",      "F", "",               1976, "Greenville", "SC", "USA", 83, 240, 1995, 1, 5, ["MIN", "BOS"], 1462, 26071, 14662, 5445),
    ("Dirk Nowitzki",      "F", "",               1978, "Wurzburg", "", "Germany", 84, 245, 1998, 1, 9, ["DAL"], 1522, 31560, 11489, 3651),
    ("David Robinson",     "C", "Navy",           1965, "Key West", "FL", "USA", 85, 235, 1987, 1, 1,  ["SAS"], 987, 20790, 10497, 2441),
    ("John Stockton",      "G", "Gonzaga",        1962, "Spokane", "WA", "USA", 73, 175, 1984, 1, 16,  ["UTA"], 1504, 19711, 4051, 15806),
    ("Patrick Ewing",      "C", "Georgetown",     1962, "Kingston", "", "Jamaica", 84, 240, 1985, 1, 1, ["NYK"], 1183, 24815, 11607, 2215),
    ("Scottie Pippen",     "F", "Central Arkansas", 1965, "Hamburg", "AR", "USA", 80, 228, 1987, 1, 5, ["CHI"], 1178, 18940, 7494, 6135),
    ("Allen Iverson",      "G", "Georgetown",     1975, "Hampton", "VA", "USA", 72, 165, 1996, 1, 1,   ["PHI"], 914, 24368, 3394, 5624),
    ("Dwyane Wade",        "G", "Marquette",      1982, "Chicago", "IL", "USA", 76, 220, 2003, 1, 5,   ["MIA"], 1054, 23165, 4933, 5701),
    ("Giannis Antetokounmpo", "F", "",            1994, "Athens", "", "Greece", 83, 243, 2013, 1, 15,  ["MIL"], 780, 17940, 7488, 3744),
]


def write_fixture(dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    players, seasons = [], []

    for (name, pos, college, by, city, state, country, hin, wlb,
         dy, dr, dno, teams, g, pts, trb, ast) in PLAYERS:
        pid = _id(name)
        players.append([pid, name, pos, college, by, city, state, country,
                        hin, wlb, dy, dr, dno])
        early, late = by + 21, by + 32
        t0, t1 = teams[0], teams[-1]
        # split totals across two season rows (early team, late team)
        seasons.append([pid, early, t0, g // 2, pts // 2, trb // 2, ast // 2])
        seasons.append([pid, late, t1, g - g // 2, pts - pts // 2,
                        trb - trb // 2, ast - ast // 2])

    with open(os.path.join(dest_dir, "nba_players.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "name", "position", "college", "birth_year",
                    "birth_city", "birth_state", "birth_country", "height_inches",
                    "weight_lbs", "draft_year", "draft_round", "draft_number"])
        w.writerows(players)

    with open(os.path.join(dest_dir, "nba_seasons.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "season", "team", "g", "pts", "trb", "ast"])
        w.writerows(seasons)

    return len(PLAYERS)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "./nba_fixture"
    print(f"Wrote {write_fixture(out)}-player NBA fixture to {out}")
