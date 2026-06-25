import requests
import json
import os
import sys
from datetime import datetime

WHOP_API_KEY = os.environ.get("WHOP_API_KEY", "")
WHOP_PRODUCT_ID = os.environ.get("WHOP_PRODUCT_ID", "")
WHOP_API_BASE = "https://api.whop.com"

def pt(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def format_picks(picks_data):
    date_str = picks_data.get("generated_at_pt", "Today")
    released = picks_data.get("released_picks", [])
    best = picks_data.get("best_play")
    lines = []
    lines.append(f"RICSBESTBETS — {date_str.upper()}")
    lines.append("Sports Market Intelligence — Manhattan Model V11")
    lines.append("─────────────────────────────")
    lines.append("")
    if not released:
        lines.append("No releases today.")
        lines.append("")
        lines.append("~ Ric has spoken.")
        return "\n".join(lines)
    if best:
        lines.append("BEST PLAY OF THE DAY")
        lines.append(f"{best.get('bet','')} {best.get('odds','')}")
        lines.append(f"{best.get('game','')}")
        stars = "★" * (best.get('stars') or 0)
        lines.append(f"{stars}  {best.get('units','')}  [{best.get('tag','')}]")
        for sig in (best.get("signals") or [])[:3]:
            lines.append(f"✓ {sig}")
        lines.append("")
        lines.append("─────────────────────────────")
        lines.append("")
    lines.append(f"FULL CARD — {len(released)} picks")
    lines.append("")
    for i, pick in enumerate(released, 1):
        stars = "★" * (pick.get('stars') or 0)
        free = " — FREE PICK" if pick.get("is_free_pick") else ""
        lines.append(f"{i}. {pick.get('bet','')} {pick.get('odds','')}{free}")
        lines.append(f"   {pick.get('game','')}")
        lines.append(f"   {stars}  {pick.get('units','')}  [{pick.get('tag','')}]")
        for sig in (pick.get("signals") or [])[:2]:
            lines.append(f"   ✓ {sig}")
        lines.append("")
    lines.append("─────────────────────────────")
    lines.append("~ Ric has spoken.")
    lines.append("@ricsbestbets · ricsbestbets.com")
    return "\n".join(lines)

def post_to_whop(title, content):
    if not WHOP_API_KEY or not WHOP_PRODUCT_ID:
        pt("WHOP secrets not set — skipping")
        return False
    headers = {
        "Authorization": f"Bearer {WHOP_API_KEY}",
        "Content-Type": "application/json",
    }
    endpoints = [
        f"{WHOP_API_BASE}/v2/products/{WHOP_PRODUCT_ID}/posts",
        f"{WHOP_API_BASE}/v2/experiences/{WHOP_PRODUCT_ID}/posts",
        f"{WHOP_API_BASE}/v5/products/{WHOP_PRODUCT_ID}/forum_posts",
    ]
    body = {"title": title, "content": content, "pinned": False}
    for endpoint in endpoints:
        try:
            pt(f"Trying: {endpoint}")
            r = requests.post(endpoint, headers=headers, json=body, timeout=20)
            if r.status_code in [200, 201]:
                pt("Posted to WHOP successfully")
                return True
            elif r.status_code == 404:
                continue
            else:
                pt(f"Status {r.status_code}: {r.text[:200]}")
        except Exception as e:
            pt(f"Error: {e}")
    pt("All WHOP endpoints failed")
    return False

def main():
    try:
        with open("picks.json", "r") as f:
            picks_data = json.load(f)
    except Exception as e:
        pt(f"Could not read picks.json: {e}")
        sys.exit(1)
    released = picks_data.get("released_picks", [])
    if not released:
        pt("No picks to post")
        sys.exit(0)
    date_str = picks_data.get("generated_at_pt", "Today")
    title = f"Today's Picks — {date_str}"
    content = format_picks(picks_data)
    pt(f"Posting {len(released)} picks to WHOP")
    success = post_to_whop(title, content)
    if success:
        pt("Done — subscribers can see picks now")
    else:
        pt("WHOP post failed — check log above")
        sys.exit(1)

if __name__ == "__main__":
    main()
