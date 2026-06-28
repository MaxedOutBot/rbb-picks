import json, os, requests
from datetime import date, datetime, timezone, timedelta

KEY = os.environ.get("ODDS_API_KEY", "")
STARS_TO_UNITS = {5: 3.0, 4: 2.0, 3: 1.5, 2: 1.0, 1: 0.5}

BOOKS = ["draftkings","fanduel","betmgm","caesars","pointsbetus","betrivers","wynnbet","barstool"]
MAX_ODDS = -180      # Nothing worse than -180 (no -181, -200, etc.)
MIN_BOOKS = 3        # Need at least 3 books in agreement
CENTS_WINDOW = 15    # Books must be within 15 cents of each other to count as agreeing

def fetch():
    if not KEY:
        print("[ENGINE] No ODDS_API_KEY")
        return []
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
    params = {"apiKey":KEY,"regions":"us","markets":"h2h,totals",
              "oddsFormat":"american","dateFormat":"iso"}
    try:
        r = requests.get(url, params=params, timeout=15)
        if not r.ok:
            print(f"[ENGINE] API error {r.status_code}")
            return []
        games = r.json()
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=10)
        upcoming, skipped = [], 0
        for g in games:
            ct = g.get("commence_time","")
            if not ct:
                skipped += 1
                continue
            try:
                gt = datetime.fromisoformat(ct.replace("Z","+00:00"))
                if gt.tzinfo is None:
                    gt = gt.replace(tzinfo=timezone.utc)
                if gt > cutoff:
                    upcoming.append(g)
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        print(f"[ENGINE] {len(upcoming)} upcoming / {skipped} already started — filtered out")
        return upcoming
    except Exception as e:
        print(f"[ENGINE] Fetch error: {e}")
        return []

def american_to_prob(o):
    n = float(o)
    return abs(n)/(abs(n)+100) if n < 0 else 100/(n+100)

def prob_to_american(p):
    p = max(0.01, min(0.99, p))
    return round(-p/(1-p)*100) if p >= 0.5 else round((1-p)/p*100)

def consensus_odds(game, team):
    """
    Find the largest cluster of books within CENTS_WINDOW of each other.
    If cluster has MIN_BOOKS or more, average those books and return consensus.
    A lagging/outlier book is simply not counted — never disqualifies the pick.
    Returns None only if fewer than MIN_BOOKS books agree within the window.
    """
    # Collect all available odds for this team
    book_odds = []
    for bk in game.get("bookmakers",[]):
        if bk.get("key") not in BOOKS:
            continue
        ml = next((m for m in bk.get("markets",[]) if m["key"]=="h2h"), None)
        if not ml:
            continue
        for outcome in ml.get("outcomes",[]):
            if outcome.get("name") == team:
                book_odds.append(float(outcome["price"]))
                break

    if len(book_odds) < MIN_BOOKS:
        return None  # Not enough books covering this game at all

    # Find the largest cluster: for each odds value as anchor,
    # count how many other odds are within CENTS_WINDOW
    best_cluster = []
    for anchor in book_odds:
        cluster = [o for o in book_odds if abs(o - anchor) <= CENTS_WINDOW]
        if len(cluster) > len(best_cluster):
            best_cluster = cluster

    if len(best_cluster) < MIN_BOOKS:
        print(f"[ENGINE] {team}: no cluster of {MIN_BOOKS}+ books within {CENTS_WINDOW} cents — skip")
        return None

    # Average the cluster via probability math
    avg_prob = sum(american_to_prob(o) for o in best_cluster) / len(best_cluster)
    consensus = prob_to_american(avg_prob)

    outliers = [o for o in book_odds if o not in best_cluster]
    print(f"[ENGINE] {team}: cluster={best_cluster} → {consensus} | lagging/outlier={outliers}")
    return consensus

def analyze(game):
    home = game.get("home_team","")
    away = game.get("away_team","")

    ho = consensus_odds(game, home)
    ao = consensus_odds(game, away)
    if ho is None or ao is None:
        return None

    # Hard cap: nothing worse than MAX_ODDS (-180)
    if ho < MAX_ODDS or ao < MAX_ODDS:
        print(f"[ENGINE] {away} @ {home} skipped — odds {ho}/{ao} exceed -{abs(MAX_ODDS)} cap")
        return None

    hp = american_to_prob(ho) / (american_to_prob(ho)+american_to_prob(ao))
    ap = 1 - hp

    if hp >= 0.55:
        bet, odds, sp = home+" ML", ho, hp
    elif ap >= 0.55:
        bet, odds, sp = away+" ML", ao, ap
    else:
        return None

    # De-vig edge gate
    edge = sp - american_to_prob(odds)
    if edge < 0.02:
        return None

    stars = 5 if sp>=0.65 else 4 if sp>=0.62 else 3 if sp>=0.58 else 2
    units = STARS_TO_UNITS.get(stars, 1.0)

    books_used = len([b for b in game.get("bookmakers",[]) if b.get("key") in BOOKS])
    sigs = [
        f"Consensus line {odds} — averaged across {books_used} sportsbooks within 15 cents",
        f"Model edge: {edge*100:.1f}pp above market-implied probability",
        "Confirm starting pitcher before placing bet"
    ]

    return {
        "game":          away+" @ "+home,
        "bet":           bet,
        "odds":          str(int(odds)),
        "stars":         stars,
        "units":         units,
        "units_display": str(units)+" Unit"+("s" if units!=1 else ""),
        "signals":       sigs,
        "market_score":  int(edge*400),
        "edge_pp":       round(edge*100, 2),
        "books_used":    books_used,
        "released":      True,
        "is_free":       False,
        "tag":           "NO TAG"
    }

# ── Main ─────────────────────────────────────────────────────
print("[ENGINE] Manhattan Model V11 — consensus odds engine")
games = fetch()
picks = [x for x in (analyze(g) for g in games) if x]
picks.sort(key=lambda x: x["market_score"], reverse=True)
picks = picks[:8]
print(f"[ENGINE] {len(picks)} picks after all filters")

fp = dict(picks[-1]) if picks else None
if fp:
    fp["is_free"] = True

today = date.today().isoformat()
try:
    from zoneinfo import ZoneInfo
    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
except Exception:
    now_pt = datetime.now(timezone.utc)

out = {
    "date":            today,
    "generated_at_pt": now_pt.strftime("%B %d, %Y %I:%M %p PT"),
    "last_updated":    now_pt.strftime("%I:%M %p PT"),
    "sport":           "MLB",
    "total_games":     len(games),
    "total_released":  len(picks),
    "released_picks":  picks,
    "premium_picks":   picks,
    "free_pick":       fp,
    "best_play":       picks[0] if picks else None
}

with open("pending_picks.json","w") as f:
    json.dump(out, f, indent=2)
print(f"[ENGINE] Saved {len(picks)} picks to pending_picks.json")

if fp:
    try:
        log = json.load(open("picks_log.json"))
    except Exception:
        log = []
    if today not in {e.get("date") for e in log}:
        log.append({"date":today,"bet":fp["bet"],"odds":fp["odds"],
                    "stars":fp["stars"],"units":fp["units"],
                    "units_display":fp["units_display"]})
        with open("picks_log.json","w") as f:
            json.dump(log, f, indent=2)

print("[ENGINE] Done — go to ricsbestbets.com/admin to publish")
