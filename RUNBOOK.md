# 20/20 — Full Data Refresh Runbook

The end-to-end sequence to bring every sport's data current and ship it. Commands
are PowerShell (Windows); the only non-portable line is the MLB `Remove-Item`
(bash: `rm data/cache/lahman/*.csv`). Forward slashes in `--out` paths work on
both.

**Legend:** `⏳` multi-hour, resumable (re-running just continues) · `🔑` needs a
fresh `SR_CF_CLEARANCE` cookie in `.env` (Pro-Football-Reference only — it's
Cloudflare-gated; basketball-/hockey-/sports-reference are not).

**`.env` prerequisites:** `SR_CF_CLEARANCE` (NFL crawls), `CFBD_API_KEY` (NCAAF),
`POSTGRES_DB_URL` (deploy).

---

## NCAAF — the one separate command

Refreshes NCAAF to the latest complete CFB season via the CollegeFootballData API.
Run it whenever (before the runbook is ideal); `run_full` in step 2 then ingests
it along with everything else.

```powershell
python -m data.etl.providers.cfbd_export --out data/cache/exports/ncaaf
```

---

## The runbook — everything else, top to bottom

```powershell
# 1 ─ Refresh the stale sources (network, no cookie)
python -m data.etl.providers.nfl_export --out data/cache/exports/nfl     # NFL: current season + mid-trade teams
Remove-Item data\cache\lahman\*.csv -ErrorAction SilentlyContinue         # MLB: force fresh Lahman (optional; skip to keep ~2022)
#   NHL/WNBA caches are already current; NBA recency comes from step 3, not the frozen 1999-2020 bulk

# 2 ─ Rebuild the game DB from caches  (WIPES, then re-ingests every sport incl NCAAF)
python run_full.py --skip-puzzles

# 3 ─ Add the missing players  (must come AFTER step 2, or they get wiped)
python -m data.etl.nfl_debuts --year 2025         # 🔑   NFL 2025 rookies (one page)
python -m data.etl.sr_history --sport NBA         # ⏳   NBA gap players incl Wembanyama, full names (no cookie; reuses db_all's cache)

# 4 ─ Cache the pages the backfill needs  (incremental — skips what's already on disk)
python -m data.etl.backfill_sr --sport NFL --cache-missing    # 🔑⏳  modern NFL incl the new rookies
python -m data.etl.backfill_sr --sport NBA --cache-missing    # ⏳    the frozen bulk stars (no cookie)

# 5 ─ Apply teams + stats from the cached pages  (no network)
python -m data.etl.backfill_from_cache --sport NFL --rebuild-teams --rebuild-stats
python -m data.etl.backfill_from_cache --sport NBA --rebuild-teams --rebuild-stats

# 6 ─ Finalize
python -m data.etl.reconcile_debut --sport ALL
python -m data.etl.prune --sport ALL              # preview the notability cut (no deletes)
python -m data.etl.prune --sport ALL --apply      # trim the game pool to notable players, THEN puzzles
python rebuild_puzzles.py

# 7 ─ Deploy
python load_to_postgres.py
git add puzzles; git commit -m "data: full refresh"; git pull --no-rebase --no-edit; git push
```

### Why the order is non-negotiable
`run_full` (step 2) **wipes and rebuilds** the DB from the source caches. So every
*add* (3) and *enrich* (4-5) must follow it, or they're erased. Reconcile + puzzles
(6) read the finished DB; Postgres (7) ships it. The `⏳` crawls are the time sinks
but all resumable — if a cookie expires or you Ctrl-C, refresh the cookie and re-run
the same line; it picks up where it left off (pages already fetched are cached).

---

## db_all — the unpruned research DB (separate, optional)

`db_all.db` is the everything-database: every player we've pulled, **never pruned**.
The game (`nfl.db`) doesn't depend on it — build it only if you want the full
research set, including the players prune cut from the game pool. It shares the page
cache, so there are two ways to fill it.

**Fast — straight from the cache (no network, no cookie, minutes):** ingests every
pro player page already in `data/cache/sr/`. Prune deletes DB rows, never cached
pages, so this recovers the cut players for free. It's "everything we've ever
fetched, un-pruned" — *not* the full SR universe (only pages a prior crawl actually
pulled), which is almost always what you want.

```powershell
python -m data.etl.db_all --from-cache --dry-run     # report per-sport counts first (confirms the cache has canonical links)
python -m data.etl.db_all --from-cache               # ingest cached NBA+WNBA+NHL+NFL pages into db_all.db
```

**Exhaustive — the full network alpha crawl (slow):** only for the never-fetched
long tail. Refresh the sources (step 1) first; it shares the cache, so whichever you
run second is mostly cache-fast.

```powershell
python run_full.py --skip-puzzles --db data/db_all.db          # bulk (MLB + college)
python -m data.etl.db_all --sport ALL                          # ⏳   NBA+WNBA+NHL full alpha (no cookie)
python -m data.etl.db_all --sport NFL                          # 🔑⏳ PFR full alpha (~28k, slow)
python -m data.etl.db_all --sport COLLEGE                      # ⏳   NCAAF/B/W full index (no cookie)
python -m data.etl.sr_college --sport ALL --db data/db_all.db  # ⏳   awards + pre-bulk legends
python -m data.etl.reconcile_debut --sport ALL --db data/db_all.db
```

No prune in either path — `db_all` keeps everyone by design (pruning is `nfl.db`-only).

---

## Verify after it finishes

```powershell
# per-sport cleanliness (team/school names), no network
python -m data.etl.teams --audit --sport ALL

# recent classes present in every sport (NBA should no longer be absent)
python -c "from data.etl.schema import get_conn; [print(f'  {r[0]:6} debuted 2021+: {r[1]}') for r in get_conn().execute('SELECT sport, COUNT(*) FROM players WHERE debut_year>=2021 GROUP BY sport ORDER BY sport')]"

# spot-check the names that drove this work
python -m data.etl.diagnose_player "Baker Mayfield" "Marquise Brown" "Victor Wembanyama"
```

Expect: audit clean ✓, NBA now has 2021+ debuts, Baker shows Panthers, Marquise
shows Chiefs, and Wembanyama is present as **"Victor Wembanyama"**.

---

## What this closes

| Fix | Result |
|---|---|
| Team/school standardization (8 sports) | Brady/Goff-class answers correct |
| NFL window + mid-trade teams | Baker's Panthers, Marquise's Chiefs, current seasons |
| `nfl_debuts` | 2025 rookies (Cam Ward, Travis Hunter, …) present |
| `sr_history --sport NBA` | post-2020 + pre-1999 NBA incl Wembanyama, full names |
| `--rebuild-stats` (NFL + NBA) | bulk stars un-frozen to their current career line |
| `reconcile_debut` | debut years / decades corrected (Malone 1985, etc.) |
| Dynamic exporters | no source silently freezes at a fixed year again |

> The NBA mid-era (1999-2020 beyond the ~1,074 notable-player bulk) stays thin by
> design — game mode trusts the bulk for that window. Run `db_all --sport NBA`
> (full mode, no cookie) only if you want every role player from those years.
