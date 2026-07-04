#!/usr/bin/env python3
"""
Lantern Watch — recovery.py
In-memory OTP-based password recovery. Store resets on restart (intentional).
"""

import secrets
import urllib.request
from datetime import datetime, timedelta

# Reuse the proven send_telegram / send_email from alerts rather than duplicating
from alerts import send_telegram as _send_telegram
from alerts import send_email    as _send_email

_recovery_codes = {}  # {token: {code, expires_at, used, attempts}}
_reset_tokens   = {}  # {reset_token: {expires_at, used}}


def _cleanup():
    now = datetime.now()
    for store in (_recovery_codes, _reset_tokens):
        for k in list(store):
            if store[k]["expires_at"] < now:
                del store[k]


def send_recovery_code(config, code):
    """Send OTP via all configured channels. Returns list of channel names that succeeded."""
    channels = []

    # ntfy — sent inline (not via send_alert, to avoid logging the OTP to the DB)
    topic = config.get("ntfy_topic", "").strip()
    if topic:
        try:
            body = (
                f"Recovery code: {code}\n\n"
                f"Someone needs to sign in to Lantern Watch.\n"
                f"If this is you, enter this code on the login page.\n"
                f"It expires in 10 minutes.\n\n"
                f"If this wasn't you, ignore this message —\n"
                f"the code will expire on its own."
            )
            title_hdr = "Lantern Watch - Password Recovery"
            headers = {
                "Title":        title_hdr.encode("utf-8").decode("latin-1", errors="ignore"),
                "Priority":     "high",
                "Tags":         "key",
                "Content-Type": "text/plain; charset=utf-8",
            }
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=body.encode("utf-8"),
                headers=headers,
            )
            urllib.request.urlopen(req, timeout=10)
            channels.append("ntfy")
            print(f"[Recovery] ntfy sent OK")
        except Exception as e:
            print(f"[Recovery] ntfy error: {e}")

    # Telegram — uses the same send_telegram used for all other app alerts
    tg       = config.get("telegram", {})
    tg_token = tg.get("bot_token", "").strip()
    tg_chat  = tg.get("chat_id",  "").strip()
    print(f"[Recovery] Telegram configured: token={bool(tg_token)}, chat_id={bool(tg_chat)}")
    if tg_token and tg_chat:
        tg_message = (
            f"Your recovery code is:\n\n"
            f"*{code}*\n\n"
            f"Enter this on the login page within 10 minutes.\n"
            f"_If you didn't request this, ignore it — the code will expire._"
        )
        print(f"[Recovery] Calling send_telegram...")
        _send_telegram(config, tg_message, "Lantern Watch - Password Recovery")
        channels.append("Telegram")
        print(f"[Recovery] send_telegram returned")

    # Email — uses the same send_email used for all other app alerts
    em      = config.get("email", {})
    em_host = em.get("smtp_host",    "").strip()
    em_user = em.get("smtp_user",    "").strip()
    em_pwd  = em.get("smtp_password","").strip()
    em_to   = em.get("to_address",  "").strip()
    if em_host and em_user and em_pwd and em_to:
        em_message = (
            f"Your recovery code is: {code}\n\n"
            f"Enter this on the Lantern Watch login page within 10 minutes.\n\n"
            f"If you didn't request this, ignore it — the code will expire on its own."
        )
        print(f"[Recovery] Calling send_email...")
        _send_email(config, em_message, "Lantern Watch - Password Recovery Code")
        channels.append("email")
        print(f"[Recovery] send_email returned")

    print(f"[Recovery] Channels used: {channels}")
    return channels


def generate_code(config):
    """Generate and send a 6-digit OTP. Returns (token, channels) or (None, [])."""
    _cleanup()
    code     = f"{secrets.randbelow(1000000):06d}"
    channels = send_recovery_code(config, code)
    if not channels:
        return None, []
    token = secrets.token_urlsafe(32)
    _recovery_codes[token] = {
        "code":       code,
        "expires_at": datetime.now() + timedelta(minutes=10),
        "used":       False,
        "attempts":   0,
    }
    return token, channels


def verify_code(token, code):
    """Returns (True, reset_token) on success, (False, error_message) on failure."""
    _cleanup()
    entry = _recovery_codes.get(token)
    if not entry:
        return False, "Code expired or invalid. Please request a new one."
    if entry["used"]:
        return False, "This code has already been used."
    if entry["attempts"] >= 5:
        return False, "Too many attempts. Please request a new code."
    if datetime.now() > entry["expires_at"]:
        _recovery_codes.pop(token, None)
        return False, "Code expired. Please request a new one."

    entry["attempts"] += 1
    if entry["code"] != code.strip():
        remaining = 5 - entry["attempts"]
        if remaining == 0:
            return False, "Too many incorrect attempts. Please request a new code."
        return False, f"Incorrect code. {remaining} attempt{'s' if remaining != 1 else ''} remaining."

    entry["used"] = True
    reset_token = secrets.token_urlsafe(32)
    _reset_tokens[reset_token] = {
        "expires_at": datetime.now() + timedelta(minutes=15),
        "used":       False,
    }
    return True, reset_token


def validate_reset_token(reset_token):
    _cleanup()
    entry = _reset_tokens.get(reset_token)
    if not entry:
        return False
    if entry["used"]:
        return False
    if datetime.now() > entry["expires_at"]:
        _reset_tokens.pop(reset_token, None)
        return False
    return True


def consume_reset_token(reset_token):
    entry = _reset_tokens.get(reset_token)
    if entry:
        entry["used"] = True
