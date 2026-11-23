#!/usr/bin/env python3
"""
Lantern Watch — guardian_agent.py
Notify-only CI watchdog for upstream GL.iNet firmware releases and local
compatibility/safety test regressions.

On every run:
1. check_glinet_firmware() polls GL.iNet's firmware API for every model in
   MONITORED_MODELS across RELEASE / BETA / TESTING, compares against the
   snapshot in scripts/guardian_state.json, and sends an ntfy heads-up for
   anything new — with a plain-text changelog excerpt and a flag if it
   mentions anything Lantern Watch depends on (DNS, firewall, DHCP, AdGuard,
   package signing, ...). State is committed back to main only after the
   notification succeeds so the next run knows what was already seen.
2. run_tests() runs tests/test_compatibility.py and sends an ntfy heads-up if
   any compatibility, safety, or privacy check fails.

Strictly heads-up only: no Gemini calls, no automatic patching, no auto-PRs,
and no auto-merging.
"""

import os
import re
import sys
import json
import html
import subprocess
import urllib.request
from email.header import Header

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

# ── GL.iNet firmware detection ────────────────────────────────────────────────
# code -> friendly name. GL.iNet's product codes are lowercase (see
# https://firmware-api.gl-inet.com/cloud-api/products?modelType=ROUTER).
# Deliberately narrow: only models that real installs are actually phoning
# home from (via the opt-in stats ping). Lots of routers have tried Lantern
# Watch, but if nobody on a given model is reporting back, it's not worth
# watching — add a model here once telemetry shows a real user on it.
MONITORED_MODELS = {
    "mt6000": "Flint 2",     # flagship / reference model
    "mt5000": "Brume 3",
    "mt3000": "Beryl AX",    # real installs confirmed in telemetry
    "mt3600be": "Beryl 7",   # real installs confirmed in telemetry
    "mt2500": "Brume 2",     # real installs confirmed in telemetry
    "be9300": "Flint 3",     # real installs confirmed in telemetry
    "be10000": "Slate 7 Pro",  # real installs confirmed in telemetry
}

# GL.iNet ships RELEASE (what users actually run), plus BETA/TESTING as
# early warning — a breaking change usually lands in TESTING months before
# it reaches RELEASE. SNAPSHOT is a nightly, too noisy to track.
TRACKED_CHANNELS = ("RELEASE", "BETA", "TESTING")

GLINET_MODEL_INFO_API = "https://firmware-api.gl-inet.com/cloud-api/model/info?model={code}"
STATE_FILE = os.path.join(REPO_ROOT, "scripts", "guardian_state.json")

# Words in a GL.iNet changelog that mean "a human should actually look at
# this one" — the parts of the router Lantern Watch reaches into. Not meant
# to be zero-false-positive; a stray flag just earns a glance.
LW_RELEVANT_TERMS = (
    "dns", "dnsmasq", "adguard", "adblock", "resolver", "doh", "dns-over",
    "dnssec", "rebind", "iptables", "nftables", "firewall", "dhcp", "resolv",
    "opkg", "ubus", "procd", "port 53", "upstream dns", "dns hijack",
    "captive", "dns rebinding", "safe search", "safesearch",
    "signature", "check_signature", "signing", "usign",
)


def _strip_html(raw):
    """GL.iNet release notes arrive as HTML — flatten to readable plain text."""
    if not raw:
        return ""
    text = re.sub(r"</(p|li|h\d|ul|ol|div)>", "\n", raw, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _fetch_glinet_model_info(code):
    """GL.iNet's firmware list for one model, or None on any failure. None
    means 'could not check' — never treated as 'no change', just skipped and
    retried next run."""
    url = GLINET_MODEL_INFO_API.format(code=code)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "LanternWatch-Guardian/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        info = data.get("info")
        return info if isinstance(info, list) else None
    except Exception as e:
        print(f"[Guardian] GL.iNet firmware check failed for {code}: {e}")
        return None


def _latest_per_channel(info):
    """Collapse GL.iNet's flat list (a long RELEASE history plus current
    BETA/TESTING/SNAPSHOT) into just the newest entry per tracked channel."""
    latest = {}
    for entry in info:
        stage = str(entry.get("stage", "")).upper()
        if stage not in TRACKED_CHANNELS:
            continue
        rt = entry.get("release_time", "")
        if stage not in latest or rt > latest[stage].get("release_time", ""):
            latest[stage] = entry
    return latest


def _load_guardian_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None  # missing/unreadable -> treat as first run


def _save_guardian_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


_FORBIDDEN_COMMIT_PATHS = (
    # Local-only files that must never enter the public repo. The repo is public
    # and has real users — a slip here leaks operator context or live credentials
    # to everyone. This list mirrors .gitignore; listing it here too means a
    # future change to .gitignore can't silently remove the protection.
    "CLAUDE.md",
    ".claude/",
    "lanternwatch_config.json",
    "lanternwatch.db",
    "docs/internal/",
)


def _pre_commit_safety_check(staged_files):
    """Return a list of error strings for any staged file that must never be
    committed. An empty list means the commit is safe to proceed.

    Called before every commit Guardian makes so an accidental `git add --force`
    on a sensitive file is caught and blocked rather than published. This is a
    last-resort catch — .gitignore and TestSensitiveFileGuardrails are the
    primary controls; this one runs in the moment, inside the commit flow."""
    errors = []
    for path in staged_files:
        for forbidden in _FORBIDDEN_COMMIT_PATHS:
            if forbidden.endswith("/"):
                if path.startswith(forbidden):
                    errors.append(
                        f"BLOCKED: '{path}' is under {forbidden} — local-only, never public"
                    )
            elif path == forbidden or path.endswith("/" + forbidden):
                errors.append(
                    f"BLOCKED: '{path}' is a local-only file — never commit it to the public repo"
                )
    return errors


def _commit_state_file():
    """Persist scripts/guardian_state.json to main so the next run knows what
    was already seen. CI only — a local run just writes the file. Any failure
    (branch protection, a push race, whatever) is logged and shrugged off:
    the maintainer was already notified, and next run just re-detects."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("[Guardian] (local run) state file written, not committing.")
        return

    def _run(args, **kw):
        return subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True, **kw)

    try:
        _run(["git", "config", "user.email", "lanternwatchapp@gmail.com"], check=True)
        _run(["git", "config", "user.name", "Lantern Watch Guardian"], check=True)
        _run(["git", "add", "scripts/guardian_state.json"], check=True)

        # Safety check: verify only the state file is staged and nothing sensitive
        # crept in (e.g. via a git add --force in a previous step).
        diff = _run(["git", "diff", "--cached", "--name-only"])
        staged = [l.strip() for l in diff.stdout.splitlines() if l.strip()]
        errors = _pre_commit_safety_check(staged)
        if errors:
            for err in errors:
                print(f"[Guardian] {err}")
            print("[Guardian] Pre-commit safety check FAILED — aborting commit to protect public repo.")
            notify_guardian(
                "Guardian: BLOCKED sensitive-file commit",
                "A pre-commit safety check blocked a Guardian commit because sensitive "
                "files were staged:\n\n" + "\n".join(errors) + "\n\n"
                "The state file was NOT pushed. Manual investigation required.",
            )
            return
        c = _run(["git", "commit", "-m", "guardian: record GL.iNet firmware snapshot"])
        if c.returncode != 0:
            print(f"[Guardian] Nothing to commit (or commit failed): {c.stdout}{c.stderr}".strip())
            return
        for attempt in (1, 2):
            p = _run(["git", "push", "origin", "HEAD:main"])
            if p.returncode == 0:
                print("[Guardian] State file committed and pushed.")
                return
            print(f"[Guardian] Push attempt {attempt} failed: {p.stderr.strip()}")
            _run(["git", "pull", "--rebase", "--autostash", "origin", "main"])
        print("[Guardian] Could not push state file — maintainer already notified; next run re-detects.")
    except Exception as e:
        print(f"[Guardian] Could not commit state file ({e}) — will re-detect next run.")


def _relevance_flags(note_text):
    low = note_text.lower()
    return sorted({t for t in LW_RELEVANT_TERMS if t in low})


def _header_safe(value):
    """Raw HTTP header values must be Latin-1 encodable — Python's own
    urllib refuses anything outside that range (this is exactly what broke
    the 🏮 emoji in the Title/Actions headers: 'latin-1' codec can't encode
    character '\\U0001f3ee'). ntfy's docs call for RFC 2047 encoded-words
    (e.g. '=?UTF-8?B?...?='), which stdlib email.header produces. Leaves
    plain ASCII/Latin-1 text untouched."""
    try:
        value.encode("latin-1")
        return value
    except UnicodeEncodeError:
        return Header(value, "utf-8").encode()


def notify_guardian(title, message, topic=None, actions=None):
    """Send an actionable notification via ntfy.

    Deliberately no hardcoded fallback topic name — ntfy.sh's public server
    has no access control by default, so knowing the topic name is enough
    to both read and spoof notifications on it. If NTFY_TOPIC isn't set,
    skip sending rather than falling back to anything guessable."""
    topic = topic or os.environ.get("NTFY_TOPIC", "")
    if not topic:
        print("[Guardian] NTFY_TOPIC not set — skipping notification (not falling back to a guessable default).")
        return False
    ntfy_url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": _header_safe(title),
        "Priority": "high",
        "Tags": "lantern,shield",
    }
    if actions:
        headers["Actions"] = _header_safe(json.dumps(actions))
    try:
        req = urllib.request.Request(ntfy_url, data=message.encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=8):
            print("[Guardian] Notification sent successfully.")
        return True
    except Exception as e:
        print(f"[Guardian] Notification failed: {e}")
        return False


def check_glinet_firmware():
    """Poll GL.iNet's firmware API for every model in MONITORED_MODELS,
    compare each channel's latest version against the last run's snapshot,
    and ntfy the maintainer about anything new. Strictly notify-only."""
    print("[Guardian] Checking GL.iNet firmware for monitored models...")
    prev_state = _load_guardian_state()
    first_run = prev_state is None
    state = {} if first_run else json.loads(json.dumps(prev_state))  # deep copy

    changes = []   # (code, friendly, channel, new_ver, old_ver, release_time, note_text)
    checked_any = False

    for code, friendly in MONITORED_MODELS.items():
        info = _fetch_glinet_model_info(code)
        if info is None:
            continue  # couldn't reach it — leave its recorded state alone, retry next run
        checked_any = True
        latest = _latest_per_channel(info)
        model_state = dict(state.get(code, {}))
        for channel, entry in latest.items():
            new_ver = str(entry.get("version", "")).strip()
            if not new_ver:
                continue
            rt = entry.get("release_time", "")
            old_ver = model_state.get(channel, {}).get("version", "")
            if new_ver != old_ver:
                changes.append((code, friendly, channel, new_ver, old_ver, rt,
                                _strip_html(entry.get("release_note", ""))))
            model_state[channel] = {"version": new_ver, "release_time": rt}
        for gone in set(model_state) - set(latest):   # channel retired upstream
            model_state.pop(gone, None)
        state[code] = model_state

    if not checked_any:
        print("[Guardian] GL.iNet API unreachable for every model — skipping this cycle.")
        return

    # Drop any model no longer in MONITORED_MODELS so the state file stays in
    # sync with the list. Silent — the maintainer trimming the list isn't an
    # upstream event, just a quiet cleanup commit.
    state = {c: v for c, v in state.items() if c in MONITORED_MODELS}

    if first_run:
        lines = []
        for code, friendly in MONITORED_MODELS.items():
            chans = state.get(code, {})
            if chans:
                lines.append(f"• {friendly} ({code}): "
                             + ", ".join(f"{ch} {chans[ch]['version']}" for ch in sorted(chans)))
        if notify_guardian(
            "\U0001f3ee Guardian: firmware monitoring is live",
            "Now watching GL.iNet firmware for your supported models. Current versions:\n\n"
            + "\n".join(lines)
            + "\n\nYou'll get a ping whenever any of these change. Heads-up only.",
        ):
            _save_guardian_state(state)
            _commit_state_file()
        return

    if not changes:
        print("[Guardian] No GL.iNet firmware changes since last run.")
        if state != prev_state:   # release_time refresh / pruned channel — persist quietly
            _save_guardian_state(state)
            _commit_state_file()
        return

    any_relevant = False
    blocks = []
    for code, friendly, channel, new_ver, old_ver, rt, note_text in changes[:6]:
        was = f"was {old_ver}" if old_ver else "newly tracked"
        hits = _relevance_flags(note_text)
        any_relevant = any_relevant or bool(hits)
        flag = f"\n  ⚠️ May touch Lantern Watch — mentions: {', '.join(hits)}" if hits else ""
        excerpt = note_text[:450].strip()
        if len(note_text) > 450:
            excerpt += " …"
        blocks.append(f"• {friendly} ({code}) — {channel} {new_ver} ({was}), {rt}{flag}\n\n{excerpt}")
    if len(changes) > 6:
        blocks.append(f"… and {len(changes) - 6} more")

    title = ("\U0001f3ee ⚠️ GL.iNet firmware update (may affect Lantern Watch)"
             if any_relevant else "\U0001f3ee GL.iNet firmware update detected")
    body = ("\n\n───\n\n".join(blocks)
            + "\n\n───\nHeads-up only — no automatic fix was attempted. "
              "Per-model changelog: firmware-api.gl-inet.com/cloud-api/model/info?model=<code>")
    if notify_guardian(title, body, actions=[
        {"action": "view", "label": "Firmware overview",
         "url": "https://admonstrator.github.io/glinet-firmware-overview/"},
    ]):
        _save_guardian_state(state)
        _commit_state_file()
    else:
        print("[Guardian] Notification failed — NOT recording state; will retry next run.")


def run_tests():
    """Run the compatibility test suite and return (passed: bool, output: str)."""
    res = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_compatibility.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return res.returncode == 0, res.stdout + res.stderr


def main():
    # Manual test path (workflow_dispatch's test_notify input) — sends one
    # harmless ping via the real NTFY_TOPIC secret and exits immediately,
    # skipping firmware detection and the test suite.
    if os.environ.get("GUARDIAN_TEST_NOTIFY", "").lower() in ("true", "1", "yes"):
        print("[Guardian] Test-notify mode — sending a single ping, doing nothing else.")
        notify_guardian(
            "🏮 Guardian: Test Notification",
            "This is a test ping from the GL.iNet Guardian Watchdog. If you're seeing "
            "this, your ntfy topic is correctly configured and reachable.",
        )
        return 0

    # 1. Check GL.iNet firmware releases (runs first so a test failure never
    #    blocks firmware update alerts).
    check_glinet_firmware()

    # 2. Run the compatibility, safety, and privacy test suite — heads-up only.
    print("[Guardian] Running compatibility & safety test suite...")
    passed, test_output = run_tests()

    if passed:
        print("[Guardian] All compatibility & contract tests PASSED. System is healthy.")
        return 0

    print("[Guardian] Compatibility test failure detected! Sending heads-up notification...")
    print(test_output)
    notify_guardian(
        "🏮 Guardian Alert: Compatibility Test Failed",
        f"Compatibility tests failed on main. Heads-up only — please review.\n\n{test_output[:300]}",
        actions=[{"action": "view", "label": "Review CI Run", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}],
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
