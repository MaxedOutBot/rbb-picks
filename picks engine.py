#!/usr/bin/env python3
"""RicsBestBets — Manhattan Model V11 (Compact) | ricsbestbets.com"""

import json, os, sys, math
from datetime import date, datetime, timezone
try:
    import requests
except ImportError:
    sys.exit(1)

ODDS_KEY = os.environ.get("ODDS_API_KEY", "")
STARS_TO_UNITS = {5: 3.0, 4: 2.0, 3: 1.5, 2: 1.0, 1: 0.5}

def pt(msg): print(f"[ENGINE] {msg}")

def get_odds():
    if not ODDS_KEY:
        pt("No ODDS_API_KEY — using sample picks")
        return []
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
    params = {"apiKey": ODDS_KEY, "regions": "us", "markets": "h2h,totals",
              "oddsFormat": "american", "dateFormat": "iso"}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.ok:
            pt(f"Fetched {len(r.json())} games")
            return r.json()
    except Exception as e:
        pt(f"Odds API error: {e}")
    return []

def american_to_prob(odds):
    n = float(odds)
    if n < 0: return abs(n) / (abs(n) + 100)
    return 100 / (n + 100)

def devig(home_odds, away_odds):
    hp = american_to_prob(home_odds)
    ap = american_to_prob(away_odds)
    total = hp + ap
    return hp / total, ap / total

def analyze_game(game):
    home = game.get("home_team", "")
    away = game.get("away_team", "")
    bk = next((b for b in game.get("bookmakers", []) if b["key"] in ["draftkings","fanduel","bovada"]), None)
    if not bk: return None
    ml_market = next((m for m in bk.get("markets", []) if m["key"] == "h2h"), None)
    tot_market = next((m for m in bk.get("markets", []) if m["key"] == "totals"), None)
    if not ml_market: return None
    outcomes = {o["name"]: o["price"] for o in ml_market.get("outcomes", [])}
    home_odds = outcomes.get(home)
    away_odds = outcomes.get(away)
    if not home_odds or not away_odds: return None
    h_prob, a_prob = devig(home_odds, away_odds)
    edge = abs(h_prob - 0.5)
    market_score = min(100, int(edge * 300))
    if h_prob >= 0.55:
        bet, odds, side_prob = f"{home} ML", home_odds, h_prob
    elif a_prob >= 0.55:
        bet, odds, side_prob = f"{away} ML", away_odds, a_prob
    elif tot_market:
        over = next((o for o in tot_market.get("outcomes", []) if o["name"] == "Over"), None)
        if over:
            bet, odds, side_prob = f"Over {over.get('point',0)}", over["price"], 0.5
        else: return None
    else: return None
    if side_prob >= 0.65: stars = 5
    elif side_prob >= 0.62: stars = 4
    elif side_prob >= 0.58: stars = 3
    elif side_prob >= 0.55: stars = 2
    else: stars = 1
    units = STARS_TO_UNITS.get(stars, 1.0)
    edge_pct = abs(side_prob - american_to_prob(odds))
    signals = []
    if edge_pct > 0.04:
        signals.append(f"Model identifies {edge_pct*100:.1f}% edge above market-implied probability")
    if float(odds) > 0:
        signals.append("Underdog value — implied probability stronger than posted odds suggest")
    signals.append("Confirm starting pitchers before placing bet")
    return {
        "game": f"{away} @ {home}", "home_team": home, "away_team": away,
        "bet": bet, "odds": str(odds), "stars": stars, "units": units,
        "units_display": f"{units} Unit{'s' if units != 1 else ''}",
        "market_score": market_score, "signals": signals[:3],
        "tag": "NO TAG", "released": market_score >= 45, "is_free": False,
    }

def main():
    pt("Manhattan Model V11 starting...")
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        now_pt = now_utc

    games = get_odds()
    all_picks = [p for p in (analyze_game(g) for g in games) if p]
    all_picks.sort(key=lambda x: x["market_score"], reverse=True)
    released = [p for p in all_picks if p["released"]]

    if len(released) < 3:
        for p in all_picks:
            if not p["released"] and len(released) < 3:
                p["released"] = True
                released.append(p)

    released = released[:8]
    pt(f"Released: {len(released)} picks")

    free_pick = dict(released[-1]) if released else None
    if free_pick: free_pick["is_free"] = True

    for p in released:
        p["is_free"] = free_pick and p.get("bet") == free_pick.get("bet")

    today_str = date.today().isoformat()
    output = {
        "generated_at": now_utc.isoformat(),
        "generated_at_pt": now_pt.strftime("%B %d, %Y %I:%M %p PT"),
        "date": today_str, "sport": "MLB",
        "total_games": len(all_picks), "total_released": len(released),
        "released_picks": released, "premium_picks": released,
        "best_play": released[0] if released else None,
        "free_pick": free_pick,
        "last_updated": now_pt.strftime("%I:%M %p PT"),
    }

    with open("pending_picks.json", "w") as f:
        json.dump(output, f, indent=2)
    pt("Saved pending_picks.json")

    if free_pick:
        log_entry = {"date": today_str, "game": free_pick.get("game",""),
                     "bet": free_pick.get("bet",""), "odds": free_pick.get("odds",""),
                     "stars": free_pick.get("stars",0), "units": free_pick.get("units",1.0),
                     "units_display": free_pick.get("units_display","1 Unit")}
        try:
            log = json.load(open("picks_log.json"))
        except:
            log = []
        if today_str not in {e.get("date") for e in log}:
            log.append(log_entry)
            json.dump(log, open("picks_log.json","w"), indent=2)
            pt(f"picks_log updated — {len(log)} entries")

    pt("Done. Go to ricsbestbets.com/admin to approve and publish.")

if __name__ == "__main__":
    main()
