import os, json, requests
from datetime import datetime

try:
    data = json.load(open("pending_picks.json"))
except Exception as e:
    print(f"SKIP: could not read pending_picks.json ({e})")
    exit(0)

picks = data.get("released_picks", [])
if not picks:
    print("SKIP: no released_picks in pending_picks.json")
    exit(0)

KEY = os.environ.get("RESEND_API_KEY", "")
EMAIL = os.environ.get("RIC_EMAIL", "")

if not KEY:
    print("FAIL: RESEND_API_KEY environment variable is empty/missing")
    exit(0)
if not EMAIL:
    print("FAIL: RIC_EMAIL environment variable is empty/missing")
    exit(0)

n = len(picks)
t = data.get("last_updated", "now")

try:
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
        json={
            "from": "RBB System <onboarding@resend.dev>",
            "to": [EMAIL],
            "subject": f"\U0001F525 {n} Picks Ready — {t} | ricsbestbets.com/admin",
            "text": f"{n} picks ready at {t}.\n\nApprove at ricsbestbets.com/admin",
        },
        timeout=10,
    )
    print(f"Resend response status: {resp.status_code}")
    print(f"Resend response body: {resp.text}")
    if 200 <= resp.status_code < 300:
        print("SUCCESS: Email sent")
    else:
        print(f"FAIL: Resend rejected the request (status {resp.status_code})")
except Exception as e:
    print(f"FAIL: request to Resend raised an exception: {e}")
