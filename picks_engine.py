import requests
import json
import os
import sys
from datetime import datetime, date, timezone, timedelta

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
ODDS_BASE = "https://api.the-odds-api.com/v4"
MLB_STATS = "https://statsapi.mlb.com/api/v1"
HEADERS = {"User-Agent": "RicsBestBets/1.0", "Accept": "application/json"}
J75_TEAMS = {"Colorado Rockies", "Oakland Athletics"}

def pt(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

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

def get_current_odds(sport="baseball_mlb"):
    r = requests.get(f"{ODDS_BASE}/sports/{sport}/odds/",
        params={"apiKey": ODDS_API_KEY, "regions": "us",
                "markets": "h2h,totals", "oddsFormat": "american"},
        headers=HEADERS, timeout=30)
    remaining = r.headers.get("x-requests-remaining", "?")
    pt(f"API calls remaining: {remaining}")
    r.raise_for_status()
    return r.json()

def get_mlb_pitchers():
    try:
        today = date.today().strftime("%Y-%m-%d")
        r = requests.get(f"{MLB_STATS}/schedule",
            params={"sportId": 1, "date": today, "hydrate": "probablePitcher,team"},
            headers=HEADERS, timeout=15)
        r.raise_for_status()
        pitchers = {}
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                away = g["teams"]["away"]["team"]["name"]
                home = g["teams"]["home"]["team"]["name"]
                ap = g["teams"]["away"].get("probablePitcher", {}).get("fullName", "TBD")
                hp = g["teams"]["home"].get("probablePitcher", {}).get("fullName", "TBD")
                pitchers[f"{away}@{home}"] = {"away_pitcher": ap, "home_pitcher": hp}
        pt(f"Pitchers found: {len(pitchers)} games")
        return pitchers
    except Exception as e:
        pt(f"Pitcher fetch error: {e}")
        return {}

def get_mlb_injuries():
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
        return il
    except Exception as e:
        pt(f"Injury fetch error: {e}")
        return {}

def get_mlb_transactions():
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
        return moves
    except Exception as e:
        pt(f"Transaction fetch error: {e}")
        return []

def extract_ml(game, team_name):
    prices = []
    for book in game.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for o in market["outcomes"]:
                    if o["name"] == team_name:
                        prices.append(o["price"])
    return prices

def extract_total(game):
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

def build_signals(ml_delta, juice_flip, j75, dir_open, dir_close,
                  total_delta, total_open, total_close, transactions,
                  home, away, pitcher_info):
    signals = []
    if j75:
        park = "Coors Field" if "Colorado" in home else "Las Vegas Ballpark"
        signals.append(f"J-75: {park} mandatory Over gate")
    if abs(ml_delta) >= 2.5:
        direction = "toward home" if ml_delta > 0 else "toward away team"
        signals.append(f"ML-MOVE-1: Line moved {abs(ml_delta):.1f}pp {direction} since open")
    if juice_flip and dir_close:
        signals.append(f"J-47: Juice flipped {dir_open} to {dir_close}")
    if abs(total_delta) >= 0.5:
        direction = "UP" if total_delta > 0 else "DOWN"
        signals.append(f"Total moved {direction} {abs(total_delta):.1f}pt: {total_open} to {total_close}")
    for t in transactions:
        if home in t.get("team","") or away in t.get("team",""):
            if "Placed On" in t.get("type",""):
                signals.append(f"Signal D: {t['player']} ({t['team']}) placed on IL")
    hp = pitcher_info.get("home_pitcher","TBD")
    ap = pitcher_info.get("away_pitcher","TBD")
    if hp != "TBD" and ap != "TBD":
        signals.append(f"Starters confirmed: {ap} vs {hp}")
    if not signals:
        signals.append("Stable market — no major movement detected")
    return signals[:4]

def determine_pick(ml_delta, juice_flip, j75, dir_open, dir_close,
                   total_delta, total_open, total_close, home_dv_c, home, away):
    if j75:
        return {"type":"TOTAL","bet":f"Over {total_close}","side":"over",
                "release":True,"tag":"27GWR","track":"TRACK 2"}
    if ml_delta <= -2.5 and abs(ml_delta) - 2.0 >= 2.0:
        return {"type":"ML","bet":f"{away} ML","side":"away",
                "release":True,"tag":"27GWR" if abs(ml_delta)>=3.0 else "27GWR★","track":"TRACK 2"}
    if ml_delta >= 2.5 and ml_delta - 2.0 >= 2.0:
        return {"type":"ML","bet":f"{home} ML","side":"home",
                "release":True,"tag":"27GWR" if ml_delta>=3.0 else "27GWR★","track":"TRACK 2"}
    if juice_flip:
        if dir_close == "U":
            return {"type":"TOTAL","bet":f"Under {total_close}","side":"under",
                    "release":True,"tag":"27GWR★","track":"TRACK 2"}
        elif dir_close == "O":
            return {"type":"TOTAL","bet":f"Over {total_close}","side":"over",
                    "release":True,"tag":"27GWR★","track":"TRACK 2"}
    return {"type":"NONE","bet":"No Release","side":None,
            "release":False,"tag":"BLOCKED","track":"TRACK 2"}

def compute_stars(ml_delta, juice_flip, j75, total_delta, tag):
    if tag == "BLOCKED": return 0
    score = 0
    if j75: score += 3
    if abs(ml_delta) >= 3.5: score += 2
    elif abs(ml_delta) >= 2.5: score += 1
    if juice_flip: score += 1
    if abs(total_delta) >= 1.0: score += 1
    if score >= 4: return 5
    if score >= 3: return 4
    if score >= 2: return 3
    if score >= 1: return 2
    return 1

def get_best_odds(game, team_name=None, total_side=None, total_pt=None):
    best = None
    for book in game.get("bookmakers", []):
        for market in book.get("markets", []):
            if team_name and market["key"] == "h2h":
                for o in market["outcomes"]:
                    if o["name"] == team_name:
                        p = o["price"]
                        if best is None or (p > 0 and (best < 0 or p > best)) or (p < 0 and best < 0 and p > best):
                            best = p
            elif total_side and market["key"] == "totals":
                for o in market["outcomes"]:
                    if o["name"] == total_side and total_pt and abs(o.get("point",0) - total_pt) < 0.1:
                        p = o["price"]
                        if best is None or (p > 0 and (best < 0 or p > best)) or (p < 0 and best < 0 and p > best):
                            best = p
    if best is None: return "N/A"
    return f"+{best}" if best > 0 else str(best)

def analyze_slate(current_odds, opener_snapshot, pitchers, il_players, transactions):
    picks = []
    opener_by_id = {g["id"]: g for g in opener_snapshot}
    for game in current_odds:
        gid = game["id"]
        home = game["home_team"]
        away = game["away_team"]
        opener = opener_by_id.get(gid)
        if not opener: continue
        home_prices_o = extract_ml(opener, home)
        away_prices_o = extract_ml(opener, away)
        home_prices_c = extract_ml(game, home)
        away_prices_c = extract_ml(game, away)
        home_ml_o = consensus_price(home_prices_o)
        away_ml_o = consensus_price(away_prices_o)
        home_ml_c = consensus_price(home_prices_c)
        away_ml_c = consensus_price(away_prices_c)
        if None in [home_ml_o, away_ml_o, home_ml_c, away_ml_c]: continue
        total_o, dir_o, _ = extract_total(opener)
        total_c, dir_c, _ = extract_total(game)
        if None in [total_o, total_c]: continue
        home_dv_o, _ = devig(home_ml_o, away_ml_o)
        home_dv_c, _ = devig(home_ml_c, away_ml_c)
        ml_delta = (home_dv_c - home_dv_o) * 100
        total_delta = total_c - total_o
        juice_flip = (dir_o is not None and dir_c is not None and dir_o != dir_c)
        j75 = home in J75_TEAMS
        key = f"{away}@{home}"
        pitcher_info = pitchers.get(key, {"away_pitcher":"TBD","home_pitcher":"TBD"})
        pick_result = determine_pick(ml_delta, juice_flip, j75, dir_o, dir_c,
                                     total_delta, total_o, total_c, home_dv_c, home, away)
        signals = build_signals(ml_delta, juice_flip, j75, dir_o, dir_c,
                                total_delta, total_o, total_c, transactions,
                                home, away, pitcher_info)
        if pick_result["type"] == "ML":
            team = away if pick_result["side"] == "away" else home
            odds = get_best_odds(game, team_name=team)
        elif pick_result["type"] == "TOTAL":
            total_side = "Over" if pick_result["side"] == "over" else "Under"
            odds = get_best_odds(game, total_side=total_side, total_pt=total_c)
        else:
            odds = "N/A"
        stars = compute_stars(ml_delta, juice_flip, j75, total_delta, pick_result["tag"])
        units = "1 Unit" if stars >= 4 else ("1/2 Unit" if stars == 3 else "1/4 Unit")
        if pick_result["tag"] == "BLOCKED": units = "SKIP"
        picks.append({
            "id": gid,
            "game": f"{away} @ {home}",
            "home_team": home,
            "away_team": away,
            "pick_type": pick_result["type"],
            "bet": pick_result["bet"],
            "odds": odds,
            "stars": stars,
            "units": units,
            "track": pick_result["track"],
            "tag": pick_result["tag"],
            "released": pick_result["release"],
            "market_score": min(100, max(0, int(abs(ml_delta)*10 + (20 if juice_flip else 0) + (35 if j75 else 0)))),
            "baseball_score": min(100, max(0, 50 + (35 if j75 else 0) + (10 if pitcher_info.get("home_pitcher") != "TBD" else -5))),
            "situation_score": min(100, max(0, 50 + (40 if j75 else 0) + (10 if abs(total_delta) >= 0.5 else 0))),
            "signals": signals,
            "ml_delta": round(ml_delta, 2),
            "home_ml_open": home_ml_o,
            "home_ml_close": home_ml_c,
            "total_open": total_o,
            "total_close": total_c,
            "juice_flip": juice_flip,
            "j75": j75,
            "home_pitcher": pitcher_info.get("home_pitcher","TBD"),
            "away_pitcher": pitcher_info.get("away_pitcher","TBD"),
            "is_free_pick": False,
        })
    picks.sort(key=lambda x: (not x["released"], -x["stars"], -(x["market_score"]+x["baseball_score"]+x["situation_score"])))
    return picks

def main():
    if not ODDS_API_KEY:
        print("ERROR: ODDS_API_KEY not set")
        sys.exit(1)
    now_pt = datetime.now(timezone(timedelta(hours=-7)))
    hour_pt = now_pt.hour
    if hour_pt < 13:
        run_type = "morning"
    elif hour_pt < 18:
        run_type = "afternoon"
    else:
        run_type = "evening"
    pt(f"Run type: {run_type} | PT: {now_pt.strftime('%I:%M %p')}")
    existing = {}
    try:
        with open("pending_picks.json", "r") as f:
            existing = json.load(f)
    except:
        pass
    pt("Pulling current MLB odds...")
    try:
        current_odds = get_current_odds("baseball_mlb")
        pt(f"Got {len(current_odds)} games")
    except Exception as e:
        pt(f"ERROR pulling odds: {e}")
        sys.exit(1)
    if run_type == "morning":
        opener_snapshot = current_odds
    else:
        opener_snapshot = existing.get("opener_snapshot", current_odds)
    pt("Pulling pitcher data...")
    pitchers = get_mlb_pitchers()
    pt("Pulling injury data...")
    il_players = get_mlb_injuries()
    pt("Pulling transactions...")
    transactions = get_mlb_transactions()
    pt("Running signal engine...")
    all_picks = analyze_slate(current_odds, opener_snapshot, pitchers, il_players, transactions)
    released = [p for p in all_picks if p["released"]]
    pt(f"Released: {len(released)} picks from {len(all_picks)} games")
    if released:
        released[0]["is_free_pick"] = True
    best_play = released[0] if released else None
    free_pick = released[0] if released else None
    now_pt_str = now_pt.strftime("%B %d, %Y %I:%M %p PT")
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_pt": now_pt_str,
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
    with open("pending_picks.json", "w") as f:
        json.dump(output, f, indent=2)
    pt("Saved pending_picks.json")
    print(f"\nEngine complete — {len(released)} picks ready for review")

if __name__ == "__main__":
    main()
