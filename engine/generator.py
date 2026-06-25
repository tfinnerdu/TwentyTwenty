"""
20/20 Chain Generator
=====================
Builds a valid 20-link chain where:
  - Each link is a pair of categories [A, B]
  - One category carries forward: [A,B] -> [B,C] -> [C,D] ...
  - Each node requires a player satisfying BOTH categories
  - No player may be reused across the chain

Improvements over v1:
  - Sport-mode filtering  (NFL / NBA / ALL / custom list)
  - Category cooldown     (recently-used categories are deprioritized)
  - Difficulty targeting  (aim for answer counts in a configured band)
  - Sport-diversity nudge (ALL mode prefers cross-sport transitions)
  - Category size penalty (giant catch-all categories downweighted)
"""

import sqlite3
import json
import math
import random
import os
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "../data/nfl.db")

# ---------------------------------------------------------------------------
# Difficulty config
# ---------------------------------------------------------------------------
# Target answer-count range per node.  Nodes outside this band aren't
# forbidden, but the scorer deprioritizes them.
#   easy   -> the MOST answers per clue (generous, well-known pools)
#   medium -> middle-of-the-pack answer counts
#   hard   -> the FEWEST answers per clue (tight intersections only)
DIFFICULTY_PRESETS = {
    "easy":   (6, 30),   # generous answer pools
    "medium": (3, 12),   # the sweet spot
    "hard":   (2, 5),    # tight intersections only
    "any":    (2, 999),  # no preference
}

# Category fields excluded at a given difficulty. 'Born in the 19X0s' clues
# (field=birth_year) are birthday trivia, not the well-known team/college/award
# pools -- so EASY puzzles leave them out, while medium/hard keep them as an
# extra constraining layer. (The 'Played in the 19X0s' active_decades clues are
# a different field and stay in at every difficulty.)
DIFFICULTY_EXCLUDE_FIELDS = {
    "easy": {"birth_year"},
}

# How many recent hops to remember for the cooldown window
COOLDOWN_WINDOW = 4

# Penalty applied to a category that was used within the cooldown window
# (multiplied into its score; 0.0 = never reuse, 1.0 = no penalty)
COOLDOWN_PENALTY = 0.15

# How much to downweight categories with very large membership
# Score multiplier = 1 / (1 + SIZE_PENALTY * log(size))
SIZE_PENALTY = 0.25

# In ALL-sport mode: reward for a transition that crosses sport boundaries
CROSS_SPORT_BONUS = 1.6


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_conn(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_categories_for_mode(conn, sport_mode: str) -> list:
    """
    Return category IDs valid for the given sport mode.

    sport_mode values:
      'NFL'        -- NFL-scoped + ALL-scoped categories only
      'NBA'        -- NBA-scoped + ALL-scoped
      'MLB'        -- MLB-scoped + ALL-scoped
      'NHL'        -- NHL-scoped + ALL-scoped
      'WNBA'       -- WNBA-scoped + ALL-scoped
      'NCAAF'      -- NCAAF-scoped + ALL-scoped
      'NCAAB'      -- NCAAB-scoped + ALL-scoped
      'NCAAW'      -- NCAAW-scoped + ALL-scoped
      'ALL'        -- every category in the DB
      'NFL,NBA'    -- comma-separated list of scopes to include
    """
    c = conn.cursor()

    if sport_mode == "ALL":
        c.execute("SELECT id, sport_scope, label, field FROM categories")
    else:
        # Build the set of scopes to include: the named sport(s) + ALL
        requested = [s.strip().upper() for s in sport_mode.split(",")]
        requested_plus_all = list(set(requested + ["ALL"]))
        placeholders = ",".join("?" * len(requested_plus_all))
        c.execute(
            f"SELECT id, sport_scope, label, field FROM categories WHERE sport_scope IN ({placeholders})",
            requested_plus_all,
        )

    return [dict(row) for row in c.fetchall()]


def get_players_for_category(conn, cat_id: str) -> set:
    c = conn.cursor()
    c.execute("SELECT player_id FROM player_categories WHERE category_id=?", (cat_id,))
    return set(row[0] for row in c.fetchall())


def get_player_sport(conn, player_id: int) -> str:
    c = conn.cursor()
    c.execute("SELECT sport FROM players WHERE id=?", (player_id,))
    row = c.fetchone()
    return row[0] if row else "?"


def get_player_name(conn, player_id: int) -> Optional[str]:
    c = conn.cursor()
    c.execute("SELECT name FROM players WHERE id=?", (player_id,))
    row = c.fetchone()
    return row[0] if row else None


def get_category_label(conn, cat_id: str) -> str:
    c = conn.cursor()
    c.execute("SELECT label FROM categories WHERE id=?", (cat_id,))
    row = c.fetchone()
    return row[0] if row else cat_id


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_intersection_map(conn, categories: list, min_shared: int = 2,
                           allowed_ids: Optional[set] = None) -> dict:
    """
    Precompute for each category which other categories share >= min_shared players.
    Returns: { cat_id -> { neighbor_cat_id -> frozenset(player_ids) } }

    Also attaches metadata used for scoring:
      _size: total membership count for this category
      _scope: sport_scope string

    allowed_ids: if given, only these player_ids count toward memberships. Used
    to keep single-sport modes pure -- ALL-scope categories (college/draft/decade)
    otherwise leak players from other sports into the pool. None = no restriction
    (ALL mode keeps its cross-sport connections).
    """
    cat_ids   = [c["id"]          for c in categories]
    cat_scope = {c["id"]: c["sport_scope"] for c in categories}

    # Fetch all memberships in one query -- much faster than N queries
    placeholders = ",".join("?" * len(cat_ids))
    c = conn.cursor()
    c.execute(
        f"SELECT category_id, player_id FROM player_categories WHERE category_id IN ({placeholders})",
        cat_ids,
    )
    cat_players: dict[str, set] = {cid: set() for cid in cat_ids}
    for row in c.fetchall():
        if allowed_ids is None or row[1] in allowed_ids:
            cat_players[row[0]].add(row[1])

    # Build adjacency
    graph: dict[str, dict] = {}
    for cat in cat_ids:
        graph[cat] = {}
        graph[cat]["_size"]  = len(cat_players[cat])
        graph[cat]["_scope"] = cat_scope[cat]

    for i, cat_a in enumerate(cat_ids):
        for cat_b in cat_ids[i+1:]:
            shared = cat_players[cat_a] & cat_players[cat_b]
            if len(shared) >= min_shared:
                graph[cat_a][cat_b] = frozenset(shared)
                graph[cat_b][cat_a] = frozenset(shared)

    return graph


# ---------------------------------------------------------------------------
# Neighbor scoring
# ---------------------------------------------------------------------------

def score_neighbor(
    neighbor: str,
    intersection_size: int,
    graph: dict,
    recent_cats: list,
    current_scope: str,
    sport_mode: str,
    target_min: int,
    target_max: int,
) -> float:
    """
    Return a score > 0 for this neighbor category.
    Higher = more likely to be chosen.
    Score of 0 = skip entirely (outside difficulty window).
    """
    # Hard floor/ceiling on answer count
    if intersection_size < target_min or intersection_size > target_max:
        return 0.0

    score = 1.0

    # --- Difficulty fit: peak at midpoint of target range ---
    mid = (target_min + target_max) / 2.0
    spread = max((target_max - target_min) / 2.0, 1.0)
    diff_fit = 1.0 - abs(intersection_size - mid) / (spread * 2)
    score *= max(0.3, diff_fit)

    # --- Size penalty: large catch-all categories are downweighted ---
    cat_size = graph[neighbor].get("_size", 1)
    if cat_size > 1:
        score *= 1.0 / (1.0 + SIZE_PENALTY * math.log(cat_size))

    # --- Cooldown: penalise recently-seen categories ---
    if neighbor in recent_cats:
        age = len(recent_cats) - recent_cats.index(neighbor)  # 1=most recent
        penalty = COOLDOWN_PENALTY * (COOLDOWN_WINDOW - age + 1) / COOLDOWN_WINDOW
        score *= max(0.05, 1.0 - penalty)

    # --- Cross-sport bonus in ALL mode ---
    if sport_mode == "ALL":
        neighbor_scope = graph[neighbor].get("_scope", "ALL")
        if neighbor_scope != current_scope and neighbor_scope != "ALL" and current_scope != "ALL":
            score *= CROSS_SPORT_BONUS

    return max(score, 0.0)


def weighted_choice(options: list, weights: list):
    """Random choice weighted by scores (no numpy required)."""
    total = sum(weights)
    if total <= 0:
        return None
    r = random.random() * total
    cumulative = 0.0
    for opt, w in zip(options, weights):
        cumulative += w
        if r <= cumulative:
            return opt
    return options[-1]


# ---------------------------------------------------------------------------
# Core DFS
# ---------------------------------------------------------------------------

def _dfs(
    chain: list,
    used_players: set,
    current_cat: str,
    graph: dict,
    chain_length: int,
    recent_cats: list,
    sport_mode: str,
    target_min: int,
    target_max: int,
    conn,
) -> Optional[list]:
    """
    Recursive DFS with backtracking.

    chain entries: (left_cat, right_cat, player_id)
    """
    if len(chain) == chain_length:
        return chain

    # Candidate neighbors: real categories only (skip _size/_scope keys)
    neighbors = [
        k for k in graph.get(current_cat, {}).keys()
        if not k.startswith("_")
    ]
    if not neighbors:
        return None

    current_scope = graph[current_cat].get("_scope", "ALL")

    # Score each neighbor
    scored = []
    for n in neighbors:
        available = graph[current_cat][n] - used_players
        if not available:
            continue
        s = score_neighbor(
            neighbor        = n,
            intersection_size = len(available),
            graph           = graph,
            recent_cats     = recent_cats[-COOLDOWN_WINDOW:],
            current_scope   = current_scope,
            sport_mode      = sport_mode,
            target_min      = target_min,
            target_max      = target_max,
        )
        if s > 0:
            scored.append((n, available, s))

    if not scored:
        return None

    # Weighted shuffle: draw without replacement using scores as weights
    order = []
    remaining = scored[:]
    while remaining:
        opts   = [x[0] for x in remaining]
        wts    = [x[2] for x in remaining]
        chosen = weighted_choice(opts, wts)
        entry  = next(x for x in remaining if x[0] == chosen)
        order.append(entry)
        remaining.remove(entry)

    for next_cat, available, _ in order:
        # Pick the player whose sport is least represented so far
        # (nudges sport diversity within the chosen node)
        player_id = _pick_player(available, chain, conn, sport_mode)

        chain.append((current_cat, next_cat, player_id))
        used_players.add(player_id)
        new_recent = (recent_cats + [next_cat])[-COOLDOWN_WINDOW:]

        result = _dfs(
            chain, used_players, next_cat, graph,
            chain_length, new_recent, sport_mode,
            target_min, target_max, conn,
        )
        if result is not None:
            return result

        chain.pop()
        used_players.discard(player_id)

    return None


def _pick_player(available: frozenset, chain: list, conn, sport_mode: str) -> int:
    """
    In ALL mode, prefer a player whose sport is under-represented in
    the chain so far. Otherwise just pick randomly.
    """
    if sport_mode != "ALL" or not chain:
        return random.choice(list(available))

    # Count sport appearances in chain so far
    sport_counts: dict[str, int] = {}
    for _, _, pid in chain:
        sp = get_player_sport(conn, pid)
        sport_counts[sp] = sport_counts.get(sp, 0) + 1

    # Weight each candidate inversely to their sport's frequency
    candidates = list(available)
    weights = []
    for pid in candidates:
        sp    = get_player_sport(conn, pid)
        count = sport_counts.get(sp, 0)
        weights.append(1.0 / (1.0 + count))

    return weighted_choice(candidates, weights)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_chain(
    chain_length: int = 20,
    sport_mode:   str = "NFL",
    difficulty:   str = "medium",
    max_attempts: int = 500,
    min_shared:   int = 2,
    seed:         Optional[int] = None,
    db_path:      str = DB_PATH,
) -> list:
    """
    Generate a valid 20/20 puzzle chain.

    Parameters
    ----------
    chain_length : int
        Number of clue-pair nodes in the chain (default 20).
    sport_mode : str
        Which sports to include. Options:
          'NFL', 'NBA', 'MLB', 'NHL', 'WNBA', 'NCAAF', 'NCAAB', 'NCAAW'
          'ALL'          -- all sports
          'NFL,NBA'      -- comma-separated combination
    difficulty : str
        Target answer-count range per node: 'easy', 'medium', 'hard', 'any'.
    max_attempts : int
        Number of random starting points to try before giving up.
    min_shared : int
        Minimum intersection size to consider two categories adjacent (default 2).
    seed : int or None
        Random seed for reproducibility.
    db_path : str
        Path to the SQLite database.

    Returns
    -------
    list of dicts, one per chain node:
        left_category, right_category, left_label, right_label,
        valid_players (sorted list), answer_count
    """
    if seed is not None:
        random.seed(seed)

    target_min, target_max = DIFFICULTY_PRESETS.get(difficulty, DIFFICULTY_PRESETS["medium"])

    conn = get_conn(db_path)

    # 1. Load categories for this sport mode
    categories = get_categories_for_mode(conn, sport_mode)

    # Difficulty-specific category exclusions (e.g. no born-in-decade clues on easy)
    drop_fields = DIFFICULTY_EXCLUDE_FIELDS.get(difficulty, set())
    if drop_fields:
        n0 = len(categories)
        categories = [c for c in categories if c.get("field") not in drop_fields]
        if len(categories) != n0:
            print(f"[generator] {difficulty}: excluded {n0 - len(categories)} "
                  f"category(ies) with field in {sorted(drop_fields)}")

    print(f"[generator] sport_mode={sport_mode!r}  difficulty={difficulty!r}  "
          f"categories={len(categories)}  target_answers={target_min}-{target_max}")

    # Restrict the player pool to the requested sport(s) so single-sport modes
    # stay pure. ALL mode keeps every player (cross-sport connections intended).
    allowed_ids = None
    if sport_mode != "ALL":
        requested = [s.strip().upper() for s in sport_mode.split(",")]
        ph = ",".join("?" * len(requested))
        allowed_ids = {row[0] for row in
                       conn.execute(f"SELECT id FROM players WHERE sport IN ({ph})", requested)}
        # A chain of N links needs N distinct players -- fail fast instead of
        # letting the DFS churn through max_attempts on an impossible target.
        if len(allowed_ids) < chain_length:
            conn.close()
            raise RuntimeError(
                f"only {len(allowed_ids)} players for sport_mode={sport_mode!r}; "
                f"need >= {chain_length} for a {chain_length}-link chain")

    # 2. Build intersection graph
    graph = build_intersection_map(conn, categories, min_shared=min_shared, allowed_ids=allowed_ids)
    active = [c for c in graph if not c.startswith("_") and
              any(not k.startswith("_") for k in graph[c])]
    print(f"[generator] active categories with edges: {len(active)}")

    if len(active) < 5:
        conn.close()
        raise RuntimeError(
            f"Not enough active categories ({len(active)}) for sport_mode={sport_mode!r}. "
            "Run the ETL pipeline to populate player data."
        )

    # 3. Try random starting points
    for attempt in range(max_attempts):
        start_cat  = random.choice(active)
        neighbors  = [k for k in graph[start_cat] if not k.startswith("_")]
        if not neighbors:
            continue

        # Score starting neighbors too
        start_scope = graph[start_cat].get("_scope", "ALL")
        start_scored = []
        for n in neighbors:
            pool = graph[start_cat][n]
            if len(pool) < min_shared:
                continue
            s = score_neighbor(
                neighbor          = n,
                intersection_size = len(pool),
                graph             = graph,
                recent_cats       = [],
                current_scope     = start_scope,
                sport_mode        = sport_mode,
                target_min        = target_min,
                target_max        = target_max,
            )
            if s > 0:
                start_scored.append((n, pool, s))

        if not start_scored:
            continue

        opts    = [x[0] for x in start_scored]
        wts     = [x[2] for x in start_scored]
        second_cat = weighted_choice(opts, wts)
        first_pool = next(x[1] for x in start_scored if x[0] == second_cat)

        first_player = _pick_player(first_pool, [], conn, sport_mode)
        chain        = [(start_cat, second_cat, first_player)]
        used         = {first_player}
        recent       = [second_cat]

        result = _dfs(
            chain, used, second_cat, graph,
            chain_length, recent, sport_mode,
            target_min, target_max, conn,
        )

        if result is not None:
            print(f"[generator] chain found after {attempt + 1} attempt(s)")

            output = []
            for left_cat, right_cat, _ in result:
                all_valid   = graph[left_cat].get(right_cat, frozenset())
                valid_names = sorted(
                    n for n in (get_player_name(conn, pid) for pid in all_valid)
                    if n is not None
                )
                output.append({
                    "left_category":  left_cat,
                    "right_category": right_cat,
                    "left_label":     get_category_label(conn, left_cat),
                    "right_label":    get_category_label(conn, right_cat),
                    "valid_players":  valid_names,
                    "answer_count":   len(all_valid),
                })

            conn.close()
            return output

    conn.close()
    raise RuntimeError(
        f"Could not generate a {chain_length}-link chain after {max_attempts} attempts "
        f"(sport_mode={sport_mode!r}, difficulty={difficulty!r}). "
        "Try 'any' difficulty or check that ETL data is loaded."
    )


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def print_chain(chain: list):
    total       = len(chain)
    avg_answers = sum(n["answer_count"] for n in chain) / total
    min_answers = min(n["answer_count"] for n in chain)
    max_answers = max(n["answer_count"] for n in chain)

    print(f"\n{'='*62}")
    print(f"  20/20 CHAIN  ({total} links)")
    print(f"  Difficulty stats: avg={avg_answers:.1f}  min={min_answers}  max={max_answers}")
    print(f"{'='*62}")
    for i, node in enumerate(chain):
        bar = "#" * min(node["answer_count"], 20)
        print(f"\nQ{i+1:02d}: [{node['left_label']}] + [{node['right_label']}]")
        print(f"     {node['answer_count']:3d} valid answers  {bar}")
        print(f"     {', '.join(node['valid_players'][:8])}"
              + ("..." if len(node["valid_players"]) > 8 else ""))


if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Generate a 20/20 puzzle chain")
    parser.add_argument("--sport",      default="NFL",    help="Sport mode (NFL/NBA/ALL/NFL,NBA/...)")
    parser.add_argument("--difficulty", default="medium", choices=["easy","medium","hard","any"])
    parser.add_argument("--length",     type=int, default=20)
    parser.add_argument("--seed",       type=int, default=None)
    parser.add_argument("--attempts",   type=int, default=500)
    args = parser.parse_args()

    chain = generate_chain(
        chain_length = args.length,
        sport_mode   = args.sport,
        difficulty   = args.difficulty,
        max_attempts = args.attempts,
        seed         = args.seed,
    )
    print_chain(chain)

    out = os.path.join(os.path.dirname(__file__), "../data/chain_sample.json")
    with open(out, "w") as f:
        json.dump(chain, f, indent=2)
    print(f"\nSaved to {out}")
