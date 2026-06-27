#!/usr/bin/env python3
"""
RicsBestBets — Notify Ric when picks are ready for approval.
Runs automatically after picks_engine.py via GitHub Actions.

Sends:
  1. Email to Ric via Resend (free, already set up)
  2. Text message via Twilio (optional — set up separately)

Required GitHub Secrets (already have):
  RESEND_API_KEY  — from resend.com
  FROM_EMAIL      — ric@ricsbestbets.com

Optional GitHub Secrets (for SMS):
  TWILIO_SID      — from twilio.com
  TWILIO_TOKEN    — from twilio.com
  TWILIO_FROM     — your Twilio phone number e.g. +17025550100
  RIC_PHONE       — your cell number e.g. +17025559999
"""

import os, json, requests
from datetime import datetime

def log(msg): print(f"[NOTIFY] {msg}")

# ── Load pending picks ──────────────────────────────────
try:
    with open("pending_picks.json") as f:
        data = json.load(f)
except Exception as e:
    log(f"Could not load pending_picks.json: {e}")
    exit(0)

picks      = data.get("released_picks", [])
free_pick  = data.get("free_pick", {})
count      = len(picks)
time_pt    = data.get("last_updated", "now")
date_str   = data.get("generated_at_pt", datetime.now().strftime("%B %d, %Y"))

if count == 0:
    log("No picks generated — skipping notification")
    exit(0)

# ── Build message ───────────────────────────────────────
free_bet   = free_pick.get("bet", "—") if free_pick else "—"
free_odds  = free_pick.get("odds", "") if free_pick else ""
free_stars = "★" * (free_pick.get("stars", 0) if free_pick else 0)

lines = [f"  • {p.get('bet','')} {p.get('odds','')} {('★'*p.get('stars',0))}" for p in picks]
pick_list = "\n".join(lines)

email_subject = f"🔥 {count} Picks Ready — {time_pt} | Approve at ricsbestbets.com/admin"

email_text = f"""Manhattan Model V11 — Picks Ready for Approval
{date_str} · {time_pt}

{count} picks generated:
{pick_list}

Free pick: {free_bet} {free_odds} {free_stars}

ACTION REQUIRED:
Go to ricsbestbets.com/admin to review, edit if needed, and publish.
Once published → auto-posts to WHOP + emails all subscribers.

——
RicsBestBets Auto System"""

email_html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="background:#07101f;font-family:Arial,sans-serif;padding:24px;color:#F6F5F2">
<div style="max-width:480px;margin:0 auto">
  <div style="background:#030b17;padding:20px;border-bottom:3px solid #c9a227;text-align:center;margin-bottom:20px">
    <div style="font-size:10px;letter-spacing:0.3em;color:#c9a227;margin-bottom:6px">MANHATTAN MODEL V11</div>
    <div style="font-size:22px;font-weight:700;color:#F6F5F2">PICKS READY TO APPROVE</div>
    <div style="font-size:12px;color:#a7adb5;margin-top:4px">{date_str} · {time_pt}</div>
  </div>

  <div style="background:#0d1f3c;border:1px solid rgba(201,162,39,0.2);border-radius:8px;padding:16px;margin-bottom:16px">
    <div style="font-size:9px;letter-spacing:0.2em;color:#c9a227;text-transform:uppercase;margin-bottom:12px">{count} Picks Generated</div>
    {"".join([f'<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05);font-size:14px"><span style="color:#F6F5F2;font-weight:600">{p.get("bet","")}</span> <span style="color:#a7adb5">{p.get("odds","")} {"★"*p.get("stars",0)}</span></div>' for p in picks])}
  </div>

  <div style="text-align:center">
    <a href="https://ricsbestbets.com/admin"
       style="display:inline-block;background:#2e78d8;color:#fff;font-weight:700;font-size:14px;
              letter-spacing:0.1em;text-transform:uppercase;padding:14px 32px;
              border-radius:6px;text-decoration:none">
      ⚡ Go to Admin Panel
    </a>
  </div>

  <div style="margin-top:16px;text-align:center;font-size:10px;color:#4a5568">
    Once published → auto-posts to WHOP + emails all subscribers
  </div>
</div>
</body></html>"""

# ── Send email via Resend ───────────────────────────────
RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
RIC_EMAIL  = os.environ.get("RIC_EMAIL", "")

if RESEND_KEY and RIC_EMAIL:
    r = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
        json={
            "from":    f"RBB System <{RIC_EMAIL}>",
            "to":      [RIC_EMAIL],
            "subject": email_subject,
            "html":    email_html,
            "text":    email_text,
        },
        timeout=10
    )
    if r.ok:
        log(f"✅ Email sent to {RIC_EMAIL}")
    else:
        log(f"❌ Email failed: {r.status_code} {r.text[:100]}")
else:
    log("Email skipped — RESEND_API_KEY or RIC_EMAIL not set")

# ── Send SMS via Twilio (optional) ──────────────────────
TWILIO_SID   = os.environ.get("TWILIO_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_TOKEN", "")
TWILIO_FROM  = os.environ.get("TWILIO_FROM", "")
RIC_PHONE    = os.environ.get("RIC_PHONE", "")

if TWILIO_SID and TWILIO_TOKEN and TWILIO_FROM and RIC_PHONE:
    sms_body = (
        f"🔥 RBB: {count} picks ready at {time_pt}!\n"
        f"Free: {free_bet} {free_odds}\n"
        f"Approve → ricsbestbets.com/admin"
    )
    r2 = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
        auth=(TWILIO_SID, TWILIO_TOKEN),
        data={"From": TWILIO_FROM, "To": RIC_PHONE, "Body": sms_body},
        timeout=10
    )
    if r2.ok:
        log(f"✅ SMS sent to {RIC_PHONE}")
    else:
        log(f"❌ SMS failed: {r2.status_code} {r2.text[:100]}")
else:
    log("SMS skipped — Twilio secrets not configured (optional, set up when ready)")

log("Done.")
