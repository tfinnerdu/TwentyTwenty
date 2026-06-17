"""
data/etl/providers/_fixture_mlb.py
==================================
Writes a small Lahman-schema fixture (30 real MLB greats) so the provider
pipeline can be exercised end to end without network access. The CSV
headers/columns match the real Chadwick Baseball Databank, so the exact
same parser runs against this and against the full ~20k-player dataset.

Stats are real-but-approximate career lines -- accurate enough that the
threshold categories (.300 avg, 500 HR, 300 wins, 3000 K, ...) select the
right players. Not a stats source; a test fixture.
"""

import csv
import os

# teamID -> full club name (drives nickname extraction in the provider)
TEAM_NAMES = {
    "NYA": "New York Yankees", "BOS": "Boston Red Sox", "ATL": "Atlanta Braves",
    "ML1": "Milwaukee Braves", "SFN": "San Francisco Giants", "PIT": "Pittsburgh Pirates",
    "NY1": "New York Giants", "SEA": "Seattle Mariners", "CIN": "Cincinnati Reds",
    "SLN": "St. Louis Cardinals", "TEX": "Texas Rangers", "PHI": "Philadelphia Phillies",
    "DET": "Detroit Tigers", "BAL": "Baltimore Orioles", "WS1": "Washington Senators",
    "HOU": "Houston Astros", "CAL": "California Angels", "NYN": "New York Mets",
    "ARI": "Arizona Diamondbacks", "CHN": "Chicago Cubs", "SDN": "San Diego Padres",
    "MIL": "Milwaukee Brewers", "MON": "Montreal Expos", "LAN": "Los Angeles Dodgers",
    "BRO": "Brooklyn Dodgers", "OAK": "Oakland Athletics",
}

POS_COL = {"OF": "G_of", "C": "G_c", "1B": "G_1b", "2B": "G_2b",
           "3B": "G_3b", "SS": "G_ss", "P": "G_p"}


def P(id, first, last, by, state, city, debut, final, pos, team,
      bat=None, pit=None, mvp=0, cy=0, gg=0, as_=0, ws=(), extra=(), country="USA"):
    return dict(id=id, first=first, last=last, by=by, country=country, state=state,
                city=city, debut=debut, final=final, pos=pos, team=team, bat=bat,
                pit=pit, mvp=mvp, cy=cy, gg=gg, as_=as_, ws=list(ws), extra=list(extra))


# bat = (AB, H, HR, RBI)   pit = (W, SO, SV, GS, G)
PLAYERS = [
    P("ruthba01", "Babe", "Ruth", 1895, "MD", "Baltimore", 1914, 1935, "OF", "NYA",
      bat=(8398, 2873, 714, 2214), mvp=1, as_=2,
      ws=[(1927, "NYA"), (1928, "NYA"), (1932, "NYA"), (1918, "BOS")], extra=[("BOS", 1918)]),
    P("aaronha01", "Hank", "Aaron", 1934, "AL", "Mobile", 1954, 1976, "OF", "ATL",
      bat=(12364, 3771, 755, 2297), mvp=1, gg=3, as_=21, ws=[(1957, "ML1")], extra=[("ML1", 1957)]),
    P("bondsba01", "Barry", "Bonds", 1964, "CA", "Riverside", 1986, 2007, "OF", "SFN",
      bat=(9847, 2935, 762, 1996), mvp=7, gg=8, as_=14, extra=[("PIT", 1990)]),
    P("maysswi01", "Willie", "Mays", 1931, "AL", "Westfield", 1951, 1973, "OF", "SFN",
      bat=(10881, 3283, 660, 1903), mvp=2, gg=12, as_=20, ws=[(1954, "NY1")], extra=[("NY1", 1954)]),
    P("griffke02", "Ken", "Griffey", 1969, "PA", "Donora", 1989, 2010, "OF", "SEA",
      bat=(9801, 2781, 630, 1836), mvp=1, gg=10, as_=13, extra=[("CIN", 2005)]),
    P("pujolal01", "Albert", "Pujols", 1980, "", "Santo Domingo", 2001, 2022, "1B", "SLN",
      country="D.R.", bat=(11421, 3384, 703, 2218), mvp=3, gg=2, as_=10,
      ws=[(2006, "SLN"), (2011, "SLN")]),
    P("rodrial01", "Alex", "Rodriguez", 1975, "NY", "New York", 1994, 2016, "SS", "NYA",
      bat=(10566, 3115, 696, 2086), mvp=3, gg=2, as_=14, ws=[(2009, "NYA")],
      extra=[("SEA", 1996), ("TEX", 2003)]),
    P("schmimi01", "Mike", "Schmidt", 1949, "OH", "Dayton", 1972, 1989, "3B", "PHI",
      bat=(8352, 2234, 548, 1595), mvp=3, gg=10, as_=12, ws=[(1980, "PHI")]),
    P("mantlmi01", "Mickey", "Mantle", 1931, "OK", "Spavinaw", 1951, 1968, "OF", "NYA",
      bat=(8102, 2415, 536, 1509), mvp=3, gg=1, as_=16,
      ws=[(1956, "NYA"), (1961, "NYA"), (1962, "NYA")]),
    P("rosepe01", "Pete", "Rose", 1941, "OH", "Cincinnati", 1963, 1986, "OF", "CIN",
      bat=(14053, 4256, 160, 1314), mvp=1, gg=2, as_=17,
      ws=[(1975, "CIN"), (1976, "CIN"), (1980, "PHI")], extra=[("PHI", 1980)]),
    P("cobbty01", "Ty", "Cobb", 1886, "GA", "Narrows", 1905, 1928, "OF", "DET",
      bat=(11434, 4189, 117, 1944), mvp=1),
    P("jeterde01", "Derek", "Jeter", 1974, "NJ", "Pequannock", 1995, 2014, "SS", "NYA",
      bat=(11195, 3465, 260, 1311), gg=5, as_=14,
      ws=[(1996, "NYA"), (1998, "NYA"), (1999, "NYA"), (2000, "NYA"), (2009, "NYA")]),
    P("gwynnto01", "Tony", "Gwynn", 1960, "CA", "Los Angeles", 1982, 2001, "OF", "SDN",
      bat=(9288, 3141, 135, 1138), gg=5, as_=15),
    P("ripkeca01", "Cal", "Ripken", 1960, "MD", "Havre de Grace", 1981, 2001, "SS", "BAL",
      bat=(11551, 3184, 431, 1695), mvp=2, gg=2, as_=19, ws=[(1983, "BAL")]),
    P("johnswa01", "Walter", "Johnson", 1887, "KS", "Humboldt", 1907, 1927, "P", "WS1",
      pit=(417, 3509, 0, 666, 802), mvp=1, ws=[(1924, "WS1")]),
    P("ryanno01", "Nolan", "Ryan", 1947, "TX", "Refugio", 1966, 1993, "P", "HOU",
      pit=(324, 5714, 0, 773, 807), as_=8, ws=[(1969, "NYN")],
      extra=[("CAL", 1973), ("TEX", 1990), ("NYN", 1969)]),
    P("clemero02", "Roger", "Clemens", 1962, "OH", "Dayton", 1984, 2007, "P", "BOS",
      pit=(354, 4672, 0, 707, 709), cy=7, mvp=1, as_=11,
      ws=[(1999, "NYA"), (2000, "NYA")], extra=[("NYA", 1999), ("HOU", 2004), ("TOR", 1997)]),
    P("johnsra05", "Randy", "Johnson", 1963, "CA", "Walnut Creek", 1988, 2009, "P", "ARI",
      pit=(303, 4875, 0, 603, 618), cy=5, as_=10, ws=[(2001, "ARI")],
      extra=[("SEA", 1995), ("NYA", 2005)]),
    P("maddugr01", "Greg", "Maddux", 1966, "TX", "San Angelo", 1986, 2008, "P", "ATL",
      pit=(355, 3371, 0, 740, 744), cy=4, gg=18, as_=8, ws=[(1995, "ATL")], extra=[("CHN", 1988)]),
    P("seaveto01", "Tom", "Seaver", 1944, "CA", "Fresno", 1967, 1986, "P", "NYN",
      pit=(311, 3640, 0, 647, 656), cy=3, as_=12, ws=[(1969, "NYN")], extra=[("CIN", 1977)]),
    P("riverma01", "Mariano", "Rivera", 1969, "", "Panama City", 1995, 2013, "P", "NYA",
      country="Panama", pit=(82, 1173, 652, 10, 1115), as_=13,
      ws=[(1996, "NYA"), (1998, "NYA"), (1999, "NYA"), (2000, "NYA"), (2009, "NYA")]),
    P("hoffmtr01", "Trevor", "Hoffman", 1967, "CA", "Bellflower", 1993, 2010, "P", "SDN",
      pit=(61, 1133, 601, 0, 1035), as_=7, extra=[("MIL", 2009)]),
    P("martipe02", "Pedro", "Martinez", 1971, "", "Manoguayabo", 1992, 2009, "P", "BOS",
      country="D.R.", pit=(219, 3154, 0, 409, 476), cy=3, as_=8, ws=[(2004, "BOS")],
      extra=[("MON", 1997), ("NYN", 2005)]),
    P("koufasa01", "Sandy", "Koufax", 1935, "NY", "Brooklyn", 1955, 1966, "P", "LAN",
      pit=(165, 2396, 0, 314, 397), cy=3, mvp=1, gg=1, as_=7,
      ws=[(1963, "LAN"), (1965, "LAN"), (1955, "BRO")], extra=[("BRO", 1955)]),
    P("benchjo01", "Johnny", "Bench", 1947, "OK", "Oklahoma City", 1967, 1983, "C", "CIN",
      bat=(7658, 2048, 389, 1376), mvp=2, gg=10, as_=14, ws=[(1975, "CIN"), (1976, "CIN")]),
    P("rodriiv01", "Ivan", "Rodriguez", 1971, "", "Manati", 1991, 2011, "C", "TEX",
      country="P.R.", bat=(10270, 2844, 311, 1332), mvp=1, gg=13, as_=14, extra=[("DET", 2004)]),
    P("berrayo01", "Yogi", "Berra", 1925, "MO", "St. Louis", 1946, 1965, "C", "NYA",
      bat=(7555, 2150, 358, 1430), mvp=3, as_=18,
      ws=[(1950, "NYA"), (1951, "NYA"), (1953, "NYA"), (1956, "NYA"), (1961, "NYA")]),
    P("smithoz01", "Ozzie", "Smith", 1954, "AL", "Mobile", 1978, 1996, "SS", "SLN",
      bat=(9396, 2460, 28, 793), gg=13, as_=15, ws=[(1982, "SLN")], extra=[("SDN", 1980)]),
    P("henderi01", "Rickey", "Henderson", 1958, "IL", "Chicago", 1979, 2003, "OF", "OAK",
      bat=(10961, 3055, 297, 1115), mvp=1, gg=1, as_=10, ws=[(1989, "OAK")], extra=[("NYA", 1985)]),
    P("robinfr02", "Frank", "Robinson", 1935, "TX", "Beaumont", 1956, 1976, "OF", "BAL",
      bat=(10006, 2943, 586, 1812), mvp=2, gg=1, as_=14,
      ws=[(1966, "BAL"), (1970, "BAL")], extra=[("CIN", 1961)]),
]


def _w(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        wr.writerows(rows)


def write_fixture(dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)

    people, batting, pitching, appear = [], [], [], []
    allstar, awards, series = [], [], []
    ws_set = set()

    for p in PLAYERS:
        people.append([p["id"], p["by"], 1, 1, p["country"], p["state"], p["city"],
                       p["first"], p["last"], f'{p["first"]} {p["last"]}', 190, 73,
                       f'{p["debut"]}-04-01', f'{p["final"]}-09-30'])

        # appearance (year, team) pairs: debut+final on primary, plus extras and WS years
        pairs = {(p["debut"], p["team"]), (p["final"], p["team"])}
        pairs |= {(y, t) for (t, y) in p["extra"]}
        pairs |= {(y, t) for (y, t) in p["ws"]}
        col = POS_COL[p["pos"]]
        for (yr, team) in sorted(pairs):
            games = {c: 0 for c in ("G_p", "G_c", "G_1b", "G_2b", "G_3b", "G_ss", "G_of", "G_dh")}
            games[col] = 40 if p["pos"] == "P" else 130
            appear.append([yr, team, "AL", p["id"], 150, 0, 150, 150,
                           games["G_p"], games["G_c"], games["G_1b"], games["G_2b"],
                           games["G_3b"], games["G_ss"], 0, 0, 0, games["G_of"], games["G_dh"]])

        if p["bat"]:
            ab, h, hr, rbi = p["bat"]
            batting.append([p["id"], p["debut"], 1, p["team"], "AL", 150, ab, 0, h, 0, 0, hr, rbi])
        if p["pit"]:
            w, so, sv, gs, g = p["pit"]
            pitching.append([p["id"], p["debut"], 1, p["team"], "AL", w, 0, g, gs, 0, 0, sv,
                             12000, 0, 1500, so])

        yr = p["debut"] + 3
        for _ in range(p["as_"]):
            allstar.append([p["id"], yr, 0, "", p["team"], "AL", 1, 0]); yr += 1
        yr = p["debut"] + 5
        for award, n in (("Most Valuable Player", p["mvp"]), ("Cy Young Award", p["cy"]),
                         ("Gold Glove", p["gg"])):
            for _ in range(n):
                awards.append([p["id"], award, yr, "AL", "", ""]); yr += 1

        for (y, t) in p["ws"]:
            ws_set.add((y, t))

    for (y, t) in sorted(ws_set):
        series.append([y, "WS", t, "AL", "", "NL", 4, 2, 0])

    teams = [[2000, "AL", tid, tid, name] for tid, name in sorted(TEAM_NAMES.items())]

    _w(os.path.join(dest_dir, "People.csv"),
       ["playerID", "birthYear", "birthMonth", "birthDay", "birthCountry", "birthState",
        "birthCity", "nameFirst", "nameLast", "nameGiven", "weight", "height",
        "debut", "finalGame"], people)
    _w(os.path.join(dest_dir, "Batting.csv"),
       ["playerID", "yearID", "stint", "teamID", "lgID", "G", "AB", "R", "H", "2B", "3B",
        "HR", "RBI"], batting)
    _w(os.path.join(dest_dir, "Pitching.csv"),
       ["playerID", "yearID", "stint", "teamID", "lgID", "W", "L", "G", "GS", "CG", "SHO",
        "SV", "IPouts", "H", "ER", "SO"], pitching)
    _w(os.path.join(dest_dir, "Appearances.csv"),
       ["yearID", "teamID", "lgID", "playerID", "G_all", "GS", "G_batting", "G_defense",
        "G_p", "G_c", "G_1b", "G_2b", "G_3b", "G_ss", "G_lf", "G_cf", "G_rf", "G_of",
        "G_dh"], appear)
    _w(os.path.join(dest_dir, "AllstarFull.csv"),
       ["playerID", "yearID", "gameNum", "gameID", "teamID", "lgID", "GP", "startingPos"],
       allstar)
    _w(os.path.join(dest_dir, "AwardsPlayers.csv"),
       ["playerID", "awardID", "yearID", "lgID", "tie", "notes"], awards)
    _w(os.path.join(dest_dir, "SeriesPost.csv"),
       ["yearID", "round", "teamIDwinner", "lgIDwinner", "teamIDloser", "lgIDloser",
        "wins", "losses", "ties"], series)
    _w(os.path.join(dest_dir, "Teams.csv"),
       ["yearID", "lgID", "teamID", "franchID", "name"], teams)

    return len(PLAYERS)


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "./lahman_fixture"
    n = write_fixture(out)
    print(f"Wrote {n}-player Lahman fixture to {out}")
