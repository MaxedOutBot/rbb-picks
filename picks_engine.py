"""
RICSBESTBETS — MANHATTAN MODEL AUTOMATED PICKS ENGINE
Runs 3x daily via GitHub Actions. Outputs picks.json to the repo.
Website reads picks.json automatically — no deployment ever needed.

Schedule:
  11:00 AM PT — Morning scan (establishes opener baseline)
   2:59 PM PT — Afternoon scan (main picks drop, most movement captured)
   6:29 PM PT — Evening scan (late games only)
"""

import requests
import json
import os
import sys
from datetime import datetime, date, timezone, timedelta
from statistics import median

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_BASE    = "https://api.the-odds-api.com/v4"
MLB_STATS    = "https://statsapi.mlb.com/api/v1"
ESPN_BASE    = "https://site.api.espn.com/apis/site/v2/sports"

HEADERS = {"User-Agent": "RicsBestBets/1.0", "Accept": "application/json"}

# J-75 park overrides (mandatory Over-Only baseline)
J75_TEAMS = {"Colorado Rockies", "Oakland Athletics"}

# Signal thresholds
ML_SOFT_THRESHOLD   = 2.5   # pp softening toward away = ML-MOVE-1 fires
ML_STRONG_THRESHOLD = 2.5   # pp strengthening toward home
DEVIG_GATE          = 2.0   # minimum pp edge over market to release
TOTAL_MOVE_MINOR    = 0.5   # half-point move
TOTAL_MOVE_MAJOR    = 1.0   # full-point move

# ─────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────────────────────
def implied(american):
    if american is None: return 0.5
    if american < 0: return abs(american) / (abs(american) + 100)
    return 100 / (american + 100)

def devig(ml_home, ml_away):
    ih, ia = implied(ml_home), implied(ml_away)
    total = ih + ia
    if total == 0: return 0.5, 0.5
    return ih / total, ia / total

def consensus_price(prices):
    if not prices: return None
    return sorted(prices)[len(prices) // 2]

def pt(msg): print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# ─────────────────────────────────────────────────────────────────────────────
# ODDS API
# ─────────────────────────────────────────────────────────────────────────────
def get_current_odds(sport="baseball_mlb"):
    """Get current live odds across all books."""
    r = requests.get(f"{ODDS_BASE}/sports/{sport}/odds/",
        params={"apiKey": ODDS_API_KEY, "regions": "us",
                "markets": "h2h,totals", "oddsFormat": "american"},
        headers=HEADERS, timeout=30)
    remaining = r.headers.get("x-requests-remaining", "?")
    pt(f"Odds API remaining requests: {remaining}")
    r.raise_for_status()
    return r.json()

def extract_ml(game, team_name):
    """Get all moneyline prices for a team across all books."""
    prices = []
    for book in game.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    if o["name"] == team_name:
                        prices.append(o["price"])
    return prices

def extract_total(game):
    """Get consensus total and Over/Under juice direction."""
    totals = []
    for book in game.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] == "totals":
                for o in market["outcomes"]:
                    if o["name"] == "Over" and "point" in o:
                        totals.append({"pt": o["point"], "pr": o["price"]})
    if not totals: return None, None, None
    mid = sorted(totals, key=lambda x: x["pt"])[len(totals)//2]
    p, pr = mid["pt"], mid["pr"]
    direction = "O" if pr < -115 else ("U" if pr > -100 else "N")
    return p, direction, pr

# ─────────────────────────────────────────────────────────────────────────────
# FREE DATA SOURCES — INJURIES & PITCHERS
# ─────────────────────────────────────────────────────────────────────────────
def get_mlb_pitchers():
    """Official MLB Stats API — probable pitchers (free, no key)."""
    try:
        today = date.today().strftime("%Y-%m-%d")
        r = requests.get(f"{MLB_STATS}/schedule",
            params={"sportId": 1, "date": today,
                    "hydrate": "probablePitcher,team"},
            headers=HEADERS, timeout=15)
        r.raise_for_status()
        pitchers = {}
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                away = g["teams"]["away"]["team"]["name"]
                home = g["teams"]["home"]["team"]["name"]
                ap = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
                hp = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
                pitchers[f"{away}@{home}"] = {
                    "away_pitcher": ap, "home_pitcher": hp,
                    "away_team": away, "home_team": home
                }
        pt(f"MLB pitchers: {len(pitchers)} games")
        return pitchers
    except Exception as e:
        pt(f"MLB pitcher fetch error: {e}")
        return {}

def get_mlb_injuries():
    """Official MLB Stats API — current IL."""
    try:
        r = requests.get(f"{MLB_STATS}/injuries",
            params={"sportId": 1}, headers=HEADERS, timeout=15)
        r.raise_for_status()
        il = {}
        for p in r.json().get("injuries", []):
            team = p.get("team", {}).get("name", "")
            player = p.get("person", {}).get("fullName", "")
            if team not in il: il[team] = []
            il[team].append(player)
        pt(f"MLB IL: {sum(len(v) for v in il.values())} players across {len(il)} teams")
        return il
    except Exception as e:
        pt(f"MLB injury fetch error: {e}")
        return {}

def get_espn_injuries(sport_path):
    """ESPN unofficial API — injuries for any sport (free, no key)."""
    try:
        r = requests.get(f"{ESPN_BASE}/{sport_path}/injuries",
            headers=HEADERS, timeout=15)
        r.raise_for_status()
        injuries = {}
        for team in r.json().get("injuries", []):
            tname = team.get("team", {}).get("displayName", "")
            injuries[tname] = [p.get("athlete", {}).get("displayName", "")
                               for p in team.get("injuries", [])
                               if p.get("status") in ["Out", "Doubtful", "Day-To-Day"]]
        return injuries
    except:
        return {}

def get_mlb_transactions():
    """MLB Stats API — today's roster moves (late scratches, IL placements)."""
    try:
        today = date.today().strftime("%Y-%m-%d")
        r = requests.get(f"{MLB_STATS}/transactions",
            params={"sportId": 1, "startDate": today, "endDate": today},
            headers=HEADERS, timeout=15)
        r.raise_for_status()
        moves = []
        for t in r.json().get("transactions", []):
            moves.append({
                "player": t.get("person", {}).get("fullName", ""),
                "team": t.get("toTeam", {}).get("name") or t.get("fromTeam", {}).get("name", ""),
                "type": t.get("typeDesc", ""),
            })
        pt(f"MLB transactions today: {len(moves)}")
        return moves
    except Exception as e:
        pt(f"MLB transactions error: {e}")
        return []

# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL ENGINE — THREE-SCORE SYSTEM
# ─────────────────────────────────────────────────────────────────────────────
def compute_market_score(ml_delta, juice_flip, j47_direction, total_delta, home_dv):
    """
    Score 0-100 based purely on market movement signals.
    Tier 1 signals: highest weight.
    """
    score = 0

    # ML movement (most important market signal)
    if abs(ml_delta) >= 3.5:
        score += 35
    elif abs(ml_delta) >= 2.5:
        score += 25
    elif abs(ml_delta) >= 1.5:
        score += 15

    # Juice flip (J-47)
    if juice_flip:
        score += 20

    # Total movement
    if abs(total_delta) >= 1.0:
        score += 20
    elif abs(total_delta) >= 0.5:
        score += 12

    # Closing line implied probability (extreme = lower edge)
    if 0.48 <= home_dv <= 0.58:
        score += 10  # near pick-em = more readable
    elif home_dv >= 0.75 or home_dv <= 0.25:
        score -= 5   # extreme chalk, already priced in

    return min(100, max(0, score))

def compute_baseball_score(home_team, away_team, pitchers, il_players,
                            transactions, j75):
    """
    Score 0-100 based on sport-specific signals.
    """
    score = 40  # neutral baseline

    key = f"{away_team}@{home_team}"
    pitcher_info = pitchers.get(key, {})
    hp = pitcher_info.get("home_pitcher", "TBD")
    ap = pitcher_info.get("away_pitcher", "TBD")

    # J-75 park factor (massive baseball factor)
    if j75:
        score += 35

    # Pitcher known vs TBD (more confidence when starters confirmed)
    if hp != "TBD" and ap != "TBD":
        score += 10
    elif hp == "TBD" or ap == "TBD":
        score -= 10

    # Injury signals — key players on IL
    home_il = il_players.get(home_team, [])
    away_il = il_players.get(away_team, [])
    if len(home_il) >= 3: score -= 8   # home team depleted
    if len(away_il) >= 3: score += 5   # away team more depleted = home advantage

    # Late transactions (Signal D — late scratches)
    for t in transactions:
        if (home_team in t.get("team", "") or away_team in t.get("team", "")):
            if "Placed On" in t.get("type", ""):
                score += 12  # IL placement = Signal D fires
            if "Recalled" in t.get("type", ""):
                score += 5   # callup = lineup strength change

    return min(100, max(0, score))

def compute_situation_score(j75, home_team, away_team, il_players,
                              transactions, total_delta, dir_close):
    """
    Score 0-100 based on situational factors.
    """
    score = 50  # neutral baseline

    # J-75: absolute park override
    if j75:
        score += 40  # Coors/LV = near-certain Over situation

    # Total moved in same direction as juice
    if total_delta >= 0.5 and dir_close == "O":
        score += 10  # market agrees on Over
    elif total_delta <= -0.5 and dir_close == "U":
        score += 10  # market agrees on Under

    # Signal D (late IL placement / transaction)
    for t in transactions:
        if home_team in t.get("team", "") or away_team in t.get("team", ""):
            if "Placed On" in t.get("type", ""):
                score += 15

    return min(100, max(0, score))

def build_signals(ml_delta, juice_flip, j75, dir_open, dir_close,
                   total_delta, total_open, total_close, home_dv_o,
                   home_dv_c, transactions, home_team, away_team,
                   pitcher_info):
    """Build plain-English signals for public display. No internal codes."""
    signals = []
    seen = set()

    def add(s):
        key = s.lower()[:50]
        if key not in seen:
            seen.add(key)
            signals.append(s)

    if j75:
        park = "Coors Field" if home_team == "Colorado Rockies" else "Las Vegas Ballpark"
        add(f"Game at {park} — historically one of baseball\'s highest-scoring venues")

    if abs(ml_delta) >= 2.5:
        team = home_team if ml_delta > 0 else away_team
        add(f"Sharp money has moved the line toward {team} since this morning")

    if juice_flip and dir_close:
        if dir_close == "O":
            add("Sharp money shifted to the Over late in the day — line movement confirms this direction")
        elif dir_close == "U":
            add("Sharp money shifted to the Under late in the day — line movement confirms this direction")

    if abs(total_delta) >= 1.0:
        direction = "higher" if total_delta > 0 else "lower"
        add(f"Total line moved {abs(total_delta):.1f} point(s) {direction} since this morning ({total_open} → {total_close})")
    elif abs(total_delta) >= 0.5:
        direction = "higher" if total_delta > 0 else "lower"
        add(f"Total line drifted {direction} since open ({total_open} → {total_close})")

    for t in transactions:
        if home_team in t.get("team", "") or away_team in t.get("team", ""):
            if "Placed On" in t.get("type", ""):
                add(f"Late roster move: {t[\'player\']} ({t[\'team\']}) placed on the injured list")

    hp = pitcher_info.get("home_pitcher", "TBD")
    ap = pitcher_info.get("away_pitcher", "TBD")
    away_short = away_team.split()[-1]
    home_short = home_team.split()[-1]
    if hp != "TBD" and ap != "TBD":
        add(f"Starting pitchers confirmed: {ap} ({away_short}) vs {hp} ({home_short})")
    elif hp == "TBD" or ap == "TBD":
        add("Note: One or more starters not yet announced — confirm before betting")

    if not signals:
        add("Market analysis complete — model identified value at the current line")

    return signals[:3]

def determine_pick(game, opener_snap, ml_delta, juice_flip, j75,
                    dir_open, dir_close, total_delta, total_open,
                    total_close, home_dv_c, market_score,
                    baseball_score, situation_score):
    """
    Determine what to bet (or skip) and why.
    Returns: (pick_type, bet_description, bet_side, release)
    """
    home = game["home_team"]
    away = game["away_team"]

    # GATE 1: J-75 park (hard override — always Over)
    if j75:
        return {
            "type": "TOTAL",
            "bet": f"Over {total_close}",
            "side": "over",
            "release": True,
            "tag": "27GWR",
            "track": "TRACK 2",
        }

    # GATE 2: Strong ML move + flip → side bet
    if ml_delta <= -2.5 and juice_flip and dir_close == "O":
        # Line softened toward away + total flipped Over = away team signal
        return {
            "type": "ML",
            "bet": f"{away} ML",
            "side": "away",
            "release": True,
            "tag": "27GWR",
            "track": "TRACK 2",
        }

    if ml_delta <= -2.5:
        # Home team line softened → away team value
        edge = abs(ml_delta) - 2.0
        if edge >= DEVIG_GATE:
            return {
                "type": "ML",
                "bet": f"{away} ML",
                "side": "away",
                "release": True,
                "tag": "27GWR" if edge >= 3.0 else "27GWR★",
                "track": "TRACK 2",
            }

    if ml_delta >= 2.5:
        # Home team line strengthened → home team value
        edge = ml_delta - 2.0
        if edge >= DEVIG_GATE:
            return {
                "type": "ML",
                "bet": f"{home} ML",
                "side": "home",
                "release": True,
                "tag": "27GWR" if edge >= 3.0 else "27GWR★",
                "track": "TRACK 2",
            }

    # GATE 3: Juice flip on totals
    if juice_flip:
        if dir_close == "U":
            return {
                "type": "TOTAL",
                "bet": f"Under {total_close}",
                "side": "under",
                "release": True,
                "tag": "27GWR★",
                "track": "TRACK 2",
            }
        elif dir_close == "O":
            return {
                "type": "TOTAL",
                "bet": f"Over {total_close}",
                "side": "over",
                "release": True,
                "tag": "27GWR★",
                "track": "TRACK 2",
            }

    # Total major move
    if total_delta >= 1.0:
        return {
            "type": "TOTAL",
            "bet": f"Over {total_close}",
            "side": "over",
            "release": market_score >= 65,
            "tag": "NO TAG",
            "track": "TRACK 2",
        }
    if total_delta <= -1.0:
        return {
            "type": "TOTAL",
            "bet": f"Under {total_close}",
            "side": "under",
            "release": market_score >= 65,
            "tag": "NO TAG",
            "track": "TRACK 2",
        }

    return {
        "type": "NONE",
        "bet": "No Release",
        "side": None,
        "release": False,
        "tag": "BLOCKED",
        "track": "TRACK 2",
    }

def get_best_odds(game, team_name=None, side=None, total_side=None, total_pt=None):
    """Find best available odds for a pick across all books."""
    best = None
    best_book = ""
    for book in game.get("bookmakers", []):
        for market in book.get("markets", []):
            if team_name and market["key"] == "h2h":
                for o in market["outcomes"]:
                    if o["name"] == team_name:
                        if best is None or (
                            o["price"] > 0 and (best < 0 or o["price"] > best)
                        ) or (
                            o["price"] < 0 and best < 0 and o["price"] > best
                        ):
                            best = o["price"]
                            best_book = book.get("title", "")
            elif total_side and market["key"] == "totals":
                for o in market["outcomes"]:
                    if o["name"] == total_side and abs(o.get("point", 0) - total_pt) < 0.1:
                        if best is None or (
                            o["price"] > 0 and (best < 0 or o["price"] > best)
                        ) or (
                            o["price"] < 0 and best < 0 and o["price"] > best
                        ):
                            best = o["price"]
                            best_book = book.get("title", "")

    if best is None:
        return "N/A", ""

    # Format
    if best > 0:
        return f"+{best}", best_book
    return str(best), best_book

def compute_stars(market_score, baseball_score, situation_score, tag):
    """Star rating based on Three-Score convergence."""
    avg = (market_score + baseball_score + situation_score) / 3
    min_score = min(market_score, baseball_score, situation_score)

    if tag == "27GWR" and avg >= 75 and min_score >= 60:
        return 5
    elif avg >= 70 and min_score >= 55:
        return 4
    elif avg >= 60:
        return 3
    elif avg >= 50:
        return 2
    return 1

def compute_units(stars, tag):
    """Unit size based on stars and tag."""
    if tag == "BLOCKED": return "SKIP"
    if stars == 5: return "1 Unit"
    if stars == 4: return "1 Unit"
    if stars == 3: return "½ Unit"
    if stars == 2: return "¼ Unit"
    return "¼ Unit"

# ─────────────────────────────────────────────────────────────────────────────
# MAIN ANALYSIS ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def analyze_slate(current_odds, opener_snapshot, pitchers, il_players, transactions):
    """
    Run full Manhattan Model on today's slate.
    Returns list of analyzed games, sorted by release confidence.
    """
    picks = []
    opener_by_id = {g["id"]: g for g in opener_snapshot}

    for game in current_odds:
        gid = game["id"]
        home = game["home_team"]
        away = game["away_team"]
        commence = game.get("commence_time", "")

        # Skip if no opener available
        opener = opener_by_id.get(gid)
        if not opener:
            continue

        # Extract ML
        home_prices_o = extract_ml(opener, home)
        away_prices_o = extract_ml(opener, away)
        home_prices_c = extract_ml(game, home)
        away_prices_c = extract_ml(game, away)

        home_ml_o = consensus_price(home_prices_o)
        away_ml_o = consensus_price(away_prices_o)
        home_ml_c = consensus_price(home_prices_c)
        away_ml_c = consensus_price(away_prices_c)

        if None in [home_ml_o, away_ml_o, home_ml_c, away_ml_c]:
            continue

        # Extract totals
        total_o, dir_o, _ = extract_total(opener)
        total_c, dir_c, _ = extract_total(game)
        if None in [total_o, total_c]:
            continue

        # Compute probabilities
        home_dv_o, _ = devig(home_ml_o, away_ml_o)
        home_dv_c, _ = devig(home_ml_c, away_ml_c)
        ml_delta = (home_dv_c - home_dv_o) * 100
        total_delta = total_c - total_o
        juice_flip = (dir_o is not None and dir_c is not None and dir_o != dir_c)
        j75 = home in J75_TEAMS

        # Get pitcher info
        key = f"{away}@{home}"
        pitcher_info = pitchers.get(key, {
            "away_pitcher": "TBD", "home_pitcher": "TBD"
        })

        # Three-Score System
        market_score = compute_market_score(ml_delta, juice_flip, dir_c, total_delta, home_dv_c)
        baseball_score = compute_baseball_score(home, away, pitchers, il_players, transactions, j75)
        situation_score = compute_situation_score(j75, home, away, il_players, transactions, total_delta, dir_c)

        # Determine pick
        pick_result = determine_pick(
            game, opener, ml_delta, juice_flip, j75, dir_o, dir_c,
            total_delta, total_o, total_c, home_dv_c,
            market_score, baseball_score, situation_score
        )

        # Build signals
        signals = build_signals(
            ml_delta, juice_flip, j75, dir_o, dir_c, total_delta,
            total_o, total_c, home_dv_o, home_dv_c, transactions,
            home, away, pitcher_info
        )

        # Get best odds
        if pick_result["type"] == "ML":
            team = away if pick_result["side"] == "away" else home
            odds, book = get_best_odds(game, team_name=team)
        elif pick_result["type"] == "TOTAL":
            total_side = "Over" if pick_result["side"] == "over" else "Under"
            odds, book = get_best_odds(game, total_side=total_side, total_pt=total_c)
        else:
            odds, book = "N/A", ""

        stars = compute_stars(market_score, baseball_score, situation_score, pick_result["tag"])
        units = compute_units(stars, pick_result["tag"])

        picks.append({
            "id": gid,
            "game": f"{away} @ {home}",
            "home_team": home,
            "away_team": away,
            "commence_time": commence,
            "pick_type": pick_result["type"],
            "bet": pick_result["bet"],
            "odds": odds,
            "best_book": book,
            "stars": stars,
            "units": units,
            "track": pick_result["track"],
            "tag": pick_result["tag"],
            "released": pick_result["release"],
            "market_score": market_score,
            "baseball_score": baseball_score,
            "situation_score": situation_score,
            "signals": signals,
            "ml_delta": round(ml_delta, 2),
            "home_ml_open": home_ml_o,
            "home_ml_close": home_ml_c,
            "total_open": total_o,
            "total_close": total_c,
            "juice_flip": juice_flip,
            "j75": j75,
            "home_pitcher": pitcher_info.get("home_pitcher", "TBD"),
            "away_pitcher": pitcher_info.get("away_pitcher", "TBD"),
        })

    # Sort: released first, then by stars desc, then market_score desc
    picks.sort(key=lambda x: (
        not x["released"],
        -x["stars"],
        -(x["market_score"] + x["baseball_score"] + x["situation_score"])
    ))

    return picks

# ─────────────────────────────────────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────────────────────────────────────
def main():
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY environment variable not set")
        sys.exit(1)

    # Determine which run type this is
    now_pt = datetime.now(timezone(timedelta(hours=-7)))  # Pacific Time
    hour_pt = now_pt.hour
    minute_pt = now_pt.minute

    if hour_pt < 13:
        run_type = "morning"    # 11am PT
    elif hour_pt < 18:
        run_type = "afternoon"  # 2:59pm PT — MAIN DROP
    else:
        run_type = "evening"    # 6:29pm PT — late games

    pt(f"Run type: {run_type} | PT: {now_pt.strftime('%I:%M %p')}")

    # Load existing picks.json if it exists (to get morning opener snapshot)
    existing = {}
    try:
        with open("picks.json", "r") as f:
            existing = json.load(f)
    except:
        pass

    # Pull current odds
    pt("Pulling current MLB odds...")
    try:
        current_odds = get_current_odds("baseball_mlb")
        pt(f"Got {len(current_odds)} games")
    except Exception as e:
        pt(f"ERROR pulling odds: {e}")
        sys.exit(1)

    # For morning run: save as opener snapshot
    if run_type == "morning":
        opener_snapshot = current_odds
        pt("Morning run — saving opener snapshot")
    else:
        # Use morning's opener snapshot if available
        opener_snapshot = existing.get("opener_snapshot", current_odds)
        if not existing.get("opener_snapshot"):
            pt("WARNING: No morning snapshot found — using current as opener (less accurate)")

    # Pull free data
    pt("Pulling MLB pitcher data...")
    pitchers = get_mlb_pitchers()

    pt("Pulling MLB injuries...")
    il_players = get_mlb_injuries()

    pt("Pulling MLB transactions (late scratches)...")
    transactions = get_mlb_transactions()

    # Run analysis
    pt("Running Manhattan Model signal engine...")
    all_picks = analyze_slate(
        current_odds, opener_snapshot, pitchers, il_players, transactions
    )

    released = [p for p in all_picks if p["released"]]
    pt(f"Released picks: {len(released)} | Total games analyzed: {len(all_picks)}")

    # Best play = top released pick by combined score
    best_play = None
    if released:
        best_play = max(released, key=lambda x:
                        x["market_score"] + x["baseball_score"] + x["situation_score"])

    # Free pick = 2nd best released (or best if only one)
    free_pick = None
    if released:
        if len(released) >= 2:
            free_pick = sorted(released,
                key=lambda x: x["market_score"]+x["baseball_score"]+x["situation_score"],
                reverse=True)[1]
        else:
            free_pick = released[0]

    # Build output
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_pt": now_pt.strftime("%B %d, %Y %I:%M %p PT"),
        "date": date.today().isoformat(),
        "run_type": run_type,
        "sport": "MLB",
        "total_games": len(all_picks),
        "total_released": len(released),
        "picks": all_picks,
        "released_picks": released,
        "best_play": best_play,
        "free_pick": free_pick,
        "opener_snapshot": current_odds if run_type == "morning" else existing.get("opener_snapshot", []),
        "last_updated": now_pt.strftime("%I:%M %p PT"),
    }

    # Save picks.json
    with open("pending_picks.json", "w") as f:
        json.dump(output, f, indent=2)
    pt("Saved picks.json")

    # Print summary
    print("\n" + "="*50)
    print(f"MANHATTAN MODEL — {run_type.upper()} RUN COMPLETE")
    print(f"Games analyzed: {len(all_picks)}")
    print(f"Picks released: {len(released)}")
    if best_play:
        print(f"Best Play: {best_play['bet']} | {best_play['game']}")
    print("="*50)

if __name__ == "__main__":
    main()
