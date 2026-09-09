#!/usr/bin/env python3
"""
Lantern Watch — guardian_agent.py
Autonomous CI/CD guardian and self-healing agent for upstream GL.iNet & AdGuard updates.

What actually happens, end to end, when tests/test_compatibility.py fails:
1. Ask Gemini for a patch, constrained to ALLOWED_PATCH_FILES only, returned
   as full replacement file contents (never a diff) in a strict JSON shape.
2. Refuse anything that touches a file outside that allowlist, unread.
3. Apply the proposed files to a THROWAWAY SCRATCH COPY of the repo (never
   the real working tree) and run the full test suite there — including
   the privacy scanner. Only a patch that passes cleanly, in isolation,
   moves on.
4. Commit the verified patch to a new branch and open a PR titled with
   "guardian" (so the auto-merge matcher below can find it), with the
   plain-English parent summary as the PR body. Push a 1-tap ntfy
   notification either way — verified-and-opened, rejected for scope, or
   failed verification.
5. On every run (independent of the above), check_existing_pr_auto_merge
   looks for any existing open "guardian" PR that's 12+ hours old AND
   independently re-verified right now as fully green (real GitHub
   check-run results, clean mergeable state, no outstanding change
   requests — see _pr_is_safe_to_automerge) and squash-merges it. Age
   alone is never sufficient; anything short of all three checks refuses
   the merge.

No GEMINI_API_KEY configured, or Gemini's response doesn't parse, or the
patch is out of scope, or it fails verification: the guardian falls back to
just notifying the maintainer with what it found — it never applies,
commits, or opens anything it hasn't itself verified first.
"""

import os
import re
import sys
import json
import shutil
import tempfile
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

TARGET_ROUTERS = [
    "GL-MT6000",
    "GL-MT5000",
    "BE9300",
    "GL-MT2500",
    "GL-AXT1800",
    "GL-AX1800",
    "GL-SFT1200",
]

UPSTREAM_REPOS = [
    {"name": "AdGuard Home", "repo": "AdguardTeam/AdGuardHome"},
    {"name": "OpenWrt", "repo": "openwrt/openwrt"},
]


def check_upstream_releases():
    """Poll upstream releases (AdGuard Home, OpenWrt) for new version tags."""
    releases = []
    for item in UPSTREAM_REPOS:
        url = f"https://api.github.com/repos/{item['repo']}/releases/latest"
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "LanternWatch-Guardian/1.0", "Accept": "application/vnd.github+json"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                releases.append({
                    "name": item["name"],
                    "tag": data.get("tag_name"),
                    "published_at": data.get("published_at"),
                })
        except Exception as e:
            print(f"[Guardian] Release check failed for {item['name']}: {e}")
    return releases


def run_tests():
    """Run the compatibility test suite and return (passed: bool, output: str)."""
    res = subprocess.run(
        [sys.executable, "-m", "unittest", "tests/test_compatibility.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return res.returncode == 0, res.stdout + res.stderr


ALLOWED_PATCH_FILES = {"classify.py", "config.py", "db.py", "adguard.py"}


def generate_guardian_prompt(test_output, diff_summary=""):
    """Construct a tightly constrained prompt for Gemini that enforces all 5 Lantern Watch guardrails."""
    allowed = ", ".join(sorted(ALLOWED_PATCH_FILES))
    return f"""You are the Lantern Watch Autonomous Guardian AI.
Your purpose: Safely repair compatibility issues caused by upstream GL.iNet, OpenWrt, or AdGuard updates.

CRITICAL GUARDRAILS:
1. TARGET AUDIENCE: Non-technical homeschooling parents. The UI must remain warm, peaceful, reassuring, and simple. Never introduce complex jargon or manual SSH requirements.
2. ZERO LOCKOUT: Never break the safety invariants. Admin devices (parents) must NEVER be locked out of the home internet. Preserve has_admin_device, label_has_protected_identity, and fail-open routing.
3. PRIVACY & ZERO PII: Never commit, log, or leak private IPs, passwords, personal tokens, device labels, or network logs.
4. SCOPE BOUNDARY: You may ONLY modify these exact files: {allowed}. Do not touch anything else — not routes.py, pages.py, tests, workflows, or this script itself. A patch touching any other file will be automatically rejected, unread.
5. MINIMAL, PRECISE PATCH: Return only the exact code modifications necessary to pass the test suite.

FAILED TESTS & ERROR LOGS:
{test_output}

UPSTREAM CONTEXT:
{diff_summary}

Respond with ONLY a single JSON object (no prose outside it, no markdown fence needed but one is fine if present), in exactly this shape:
{{
  "parent_summary": "Two plain-English sentences for a non-technical parent: what changed and how this keeps their home safe.",
  "files": [
    {{"path": "config.py", "content": "<the COMPLETE new file content, not a diff>"}}
  ]
}}

Each "content" value must be the full, complete replacement content of that file — not a unified diff, not just the changed lines. Only include files you actually changed, and only from the allowed list above."""


def notify_guardian(title, message, topic=None, actions=None):
    """Send an actionable notification via ntfy.

    Deliberately no hardcoded fallback topic name — ntfy.sh's public server
    has no access control by default, so knowing the topic name is enough
    to both read and spoof notifications on it. A fixed default sitting in
    this public repo would defeat the whole point of a private topic (same
    reasoning the main app already follows: config.py defaults ntfy_topic
    to "", and alerts.py skips sending rather than inventing one). If
    NTFY_TOPIC isn't set, skip sending rather than fall back to anything
    guessable."""
    topic = topic or os.environ.get("NTFY_TOPIC", "")
    if not topic:
        print("[Guardian] NTFY_TOPIC not set — skipping notification (not falling back to a guessable default).")
        return
    ntfy_url = f"https://ntfy.sh/{topic}"
    headers = {
        "Title": title,
        "Priority": "high",
        "Tags": "lantern,shield",
    }
    if actions:
        headers["Actions"] = json.dumps(actions)
    try:
        req = urllib.request.Request(ntfy_url, data=message.encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=8):
            print("[Guardian] Notification sent successfully.")
    except Exception as e:
        print(f"[Guardian] Notification failed: {e}")


GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")


def call_gemini(prompt, api_key):
    """Call Gemini's generateContent REST API directly (no SDK dependency to
    keep the CI job's install step trivial). Returns the raw response text,
    or None on any failure — callers must treat None as 'no patch available'
    and fall back to just notifying the maintainer, never as license to
    proceed with something else."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192},
    }).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"[Guardian] Gemini API call failed: {e}")
        return None


def parse_patch_response(text):
    """Extract the {"parent_summary": ..., "files": [...]} JSON object the
    prompt asked Gemini to return, tolerating a ```json ... ``` fence or
    stray prose around it. Returns None if the shape doesn't match — a
    malformed response is refused, never guessed at."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        print("[Guardian] Gemini response contained no JSON object.")
        return None
    try:
        obj = json.loads(match.group(0))
    except Exception as e:
        print(f"[Guardian] Gemini response JSON did not parse: {e}")
        return None
    if not isinstance(obj, dict) or "files" not in obj or not isinstance(obj.get("files"), list):
        print("[Guardian] Gemini response JSON missing a valid 'files' list.")
        return None
    return obj


def validate_patch_scope(patch):
    """Refuse anything touching a file outside the compatibility-layer
    allowlist, or any path that isn't a bare top-level filename (blocks path
    traversal into .github/, scripts/, tests/, or anywhere else). A patch
    must never be able to edit the mechanism that is supposed to be
    checking it — that would let one bad Gemini response disable every
    guardrail at once."""
    if not patch.get("files"):
        return False, "Patch contains no files."
    for f in patch["files"]:
        path = str(f.get("path", ""))
        norm = path.replace("\\", "/")
        if "/" in norm or norm in ("", ".", ".."):
            return False, f"Patch touches a non-top-level or invalid path: {path!r}"
        if norm not in ALLOWED_PATCH_FILES:
            return False, f"Patch touches {norm!r}, outside the allowed scope {sorted(ALLOWED_PATCH_FILES)}"
        if not isinstance(f.get("content"), str) or not f["content"].strip():
            return False, f"Patch for {norm!r} has empty or non-string content."
    return True, ""


def verify_patch_in_scratch_copy(patch):
    """Apply the proposed file contents to a throwaway copy of the repo and
    run the FULL test suite (including the privacy scanner) against ONLY
    that copy. The real working tree is never touched unless this returns
    True — a patch that doesn't make the test suite pass, or that somehow
    reintroduces a privacy finding, never gets anywhere near main."""
    scratch = tempfile.mkdtemp(prefix="lw_guardian_")
    try:
        shutil.copytree(
            REPO_ROOT, scratch, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
        )
        for f in patch["files"]:
            with open(os.path.join(scratch, f["path"]), "w", encoding="utf-8") as fh:
                fh.write(f["content"])
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_compatibility"],
            cwd=scratch, capture_output=True, text=True,
        )
        return res.returncode == 0, res.stdout + res.stderr
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def open_guardian_pr(patch, gh_token):
    """Commit an already-verified patch to a new branch and open a PR titled
    with 'guardian' in it (required for check_existing_pr_auto_merge's
    matcher), body carrying the plain-English parent summary. Uses the
    workflow's own checkout + GITHUB_TOKEN — no extra credentials needed.
    Only ever called after verify_patch_in_scratch_copy has already
    returned True for this exact patch."""
    branch = f"guardian/auto-patch-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
    subprocess.run(["git", "config", "user.email", "lanternwatchapp@gmail.com"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "config", "user.name", "Lantern Watch Guardian"], cwd=REPO_ROOT, check=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=REPO_ROOT, check=True)
    for f in patch["files"]:
        with open(os.path.join(REPO_ROOT, f["path"]), "w", encoding="utf-8") as fh:
            fh.write(f["content"])
    touched = [f["path"] for f in patch["files"]]
    subprocess.run(["git", "add"] + touched, cwd=REPO_ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", f"guardian: automated compatibility patch\n\n{patch.get('parent_summary', '')}"],
        cwd=REPO_ROOT, check=True,
    )
    subprocess.run(["git", "push", "origin", branch], cwd=REPO_ROOT, check=True)

    pr_body = (
        f"**What changed, in plain English:**\n\n{patch.get('parent_summary', '(no summary provided)')}\n\n"
        f"---\n_Opened automatically by the Guardian watchdog after its proposed patch passed the "
        f"full compatibility + privacy test suite against a scratch copy of the repo. "
        f"This PR only auto-merges after 12 hours AND only once GitHub itself reports its "
        f"own checks green, its mergeable state clean, and no outstanding change requests "
        f"— see `_pr_is_safe_to_automerge` in `scripts/guardian_agent.py`. Close it, or "
        f"request changes, any time before then to stop it._"
    )
    req = urllib.request.Request(
        "https://api.github.com/repos/LanternWatchApp/lantern-watch/pulls",
        data=json.dumps({
            "title": "guardian: automated compatibility patch",
            "head": branch, "base": "main", "body": pr_body,
        }).encode(),
        headers={
            "Authorization": f"token {gh_token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _gh_get(url, gh_token):
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "LanternWatch-Guardian",
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _pr_is_safe_to_automerge(pr, gh_token):
    """The real safety gate. Age alone (the old check) proves nothing about
    whether the PR's own patch actually works — a PR titled/branched with
    "guardian" that's just old and untested must never qualify. Requires
    ALL THREE, freshly re-checked (not trusted from the initial PR list):
      1. Every check-run on the PR's head commit completed with a passing
         conclusion (success/neutral/skipped) — none failing, none still
         running.
      2. GitHub's own computed mergeable_state is "clean" (no conflicts,
         no unmet required checks/reviews from GitHub's perspective).
      3. No reviewer has an outstanding CHANGES_REQUESTED review.
    Any API failure, or anything short of all three, refuses the merge —
    this must fail closed, never open."""
    number = pr["number"]
    sha    = pr.get("head", {}).get("sha")
    if not sha:
        print(f"[Guardian] PR #{number}: no head sha, refusing to auto-merge.")
        return False

    try:
        runs = _gh_get(
            f"https://api.github.com/repos/LanternWatchApp/lantern-watch/commits/{sha}/check-runs",
            gh_token,
        ).get("check_runs", [])
    except Exception as e:
        print(f"[Guardian] PR #{number}: could not fetch check-runs ({e}) — refusing to auto-merge.")
        return False
    if not runs:
        print(f"[Guardian] PR #{number}: no check-runs reported yet — refusing to auto-merge.")
        return False
    for run in runs:
        if run.get("status") != "completed" or run.get("conclusion") not in ("success", "neutral", "skipped"):
            print(f"[Guardian] PR #{number}: check-run {run.get('name')!r} is "
                  f"{run.get('status')}/{run.get('conclusion')}, not a clean pass — refusing to auto-merge.")
            return False

    try:
        fresh_pr = _gh_get(
            f"https://api.github.com/repos/LanternWatchApp/lantern-watch/pulls/{number}",
            gh_token,
        )
    except Exception as e:
        print(f"[Guardian] PR #{number}: could not re-fetch PR state ({e}) — refusing to auto-merge.")
        return False
    if fresh_pr.get("mergeable_state") != "clean":
        print(f"[Guardian] PR #{number}: mergeable_state is "
              f"{fresh_pr.get('mergeable_state')!r}, not 'clean' — refusing to auto-merge.")
        return False

    try:
        reviews = _gh_get(
            f"https://api.github.com/repos/LanternWatchApp/lantern-watch/pulls/{number}/reviews",
            gh_token,
        )
    except Exception as e:
        print(f"[Guardian] PR #{number}: could not fetch reviews ({e}) — refusing to auto-merge.")
        return False
    if any(r.get("state") == "CHANGES_REQUESTED" for r in reviews):
        print(f"[Guardian] PR #{number}: has an outstanding CHANGES_REQUESTED review — refusing to auto-merge.")
        return False

    return True


def check_existing_pr_auto_merge():
    """
    12-Hour Auto-Merge Guardrail:
    If an open Guardian PR exists, was created > 12 hours ago, AND is
    independently verified right now as fully green (see
    _pr_is_safe_to_automerge — real check-run results, clean mergeable
    state, no outstanding change requests), auto-merge it to protect
    unattended routers. Age alone is never sufficient by itself.
    """
    gh_token = os.environ.get("GITHUB_TOKEN")
    if not gh_token:
        return

    url = "https://api.github.com/repos/LanternWatchApp/lantern-watch/pulls?state=open"
    try:
        prs = _gh_get(url, gh_token)
        for pr in prs:
            if "guardian" not in pr.get("title", "").lower() and "guardian" not in pr.get("head", {}).get("ref", "").lower():
                continue
            created_at = datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
            age_hours = (datetime.now(created_at.tzinfo) - created_at).total_seconds() / 3600.0
            print(f"[Guardian] Found active patch PR #{pr['number']} (Age: {age_hours:.1f}h)")
            if age_hours < 12.0:
                continue
            if not _pr_is_safe_to_automerge(pr, gh_token):
                continue
            print(f"[Guardian] 12-hour grace period elapsed AND PR #{pr['number']} verified green. Auto-merging...")
            merge_url = f"https://api.github.com/repos/LanternWatchApp/lantern-watch/pulls/{pr['number']}/merge"
            merge_req = urllib.request.Request(
                merge_url,
                data=json.dumps({"commit_title": f"Auto-heal: Merge Guardian patch #{pr['number']} (12h timeout)", "merge_method": "squash"}).encode(),
                headers={
                    "Authorization": f"token {gh_token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(merge_req, timeout=10) as merge_resp:
                if merge_resp.status in (200, 201):
                    notify_guardian(
                        "🏮 Guardian: Patch Auto-Deployed",
                        f"Compatibility patch #{pr['number']} was safely auto-merged after 12 hours, verified fully green.",
                        actions=[{"action": "view", "label": "View Changelog", "url": "https://github.com/LanternWatchApp/lantern-watch/commits/main"}]
                    )
    except Exception as e:
        print(f"[Guardian] Auto-merge check notice: {e}")


def main():
    # Manual test path (workflow_dispatch's test_notify input) — sends one
    # harmless ping via the real NTFY_TOPIC secret and exits immediately,
    # skipping the test suite, Gemini, and auto-merge entirely. Only way to
    # confirm the real secret actually reaches a phone without ever typing
    # or reading the topic name outside this CI run.
    if os.environ.get("GUARDIAN_TEST_NOTIFY", "").lower() in ("true", "1", "yes"):
        print("[Guardian] Test-notify mode — sending a single ping, doing nothing else.")
        notify_guardian(
            "🏮 Guardian: Test Notification",
            "This is a test ping from the GL.iNet Guardian Watchdog. If you're seeing "
            "this, your ntfy topic is correctly configured and reachable.",
        )
        return 0

    print("[Guardian] Checking upstream ecosystem releases (AdGuard Home, OpenWrt, GL.iNet models)...")
    releases = check_upstream_releases()
    for r in releases:
        print(f"  • {r['name']}: Latest tag {r['tag']} (published {r.get('published_at')})")

    print(f"[Guardian] Monitoring {len(TARGET_ROUTERS)} GL.iNet fleet models ({', '.join(TARGET_ROUTERS[:4])}...).")

    # Check for mature 12h PRs to auto-merge if tested green
    check_existing_pr_auto_merge()

    print("[Guardian] Running compatibility & safety test suite...")
    passed, test_output = run_tests()

    if passed:
        print("[Guardian] All compatibility & contract tests PASSED. System is healthy.")
        return 0

    print("[Guardian] Test failure or router update detected! Triggering Gemini Guardian repair agent...")
    print(test_output)

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if not gemini_key:
        print("[Guardian] GEMINI_API_KEY not set. Alerting maintainer via push notification...")
        notify_guardian(
            "🏮 Guardian Alert: Compatibility Test Failed",
            f"Compatibility tests detected a router patch requirement. Please review.\n\n{test_output[:300]}",
            actions=[{"action": "view", "label": "Review CI Run", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}]
        )
        return 1

    print("[Guardian] Generating self-healing patch proposal via Gemini...")
    prompt = generate_guardian_prompt(test_output)
    raw = call_gemini(prompt, gemini_key)
    patch = parse_patch_response(raw)
    if not patch:
        print("[Guardian] Gemini did not return a usable patch. Alerting maintainer instead.")
        notify_guardian(
            "🏮 Guardian Alert: Compatibility Test Failed (no patch available)",
            f"Compatibility tests are failing and no automated patch could be generated. Please review.\n\n{test_output[:300]}",
            actions=[{"action": "view", "label": "Review CI Run", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}]
        )
        return 1

    ok, why = validate_patch_scope(patch)
    if not ok:
        print(f"[Guardian] Patch rejected — out of scope: {why}")
        notify_guardian(
            "🏮 Guardian Alert: Patch Rejected (out of scope)",
            f"Gemini proposed a patch outside the allowed compatibility-layer files and it was refused, unapplied.\n\n{why}",
            actions=[{"action": "view", "label": "Review CI Run", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}]
        )
        return 1

    print("[Guardian] Verifying proposed patch against a scratch copy (full test suite + privacy scan)...")
    verified, verify_output = verify_patch_in_scratch_copy(patch)
    if not verified:
        print("[Guardian] Proposed patch FAILED verification — not opening a PR.")
        print(verify_output)
        notify_guardian(
            "🏮 Guardian Alert: Patch Failed Verification",
            f"Gemini's proposed patch did not pass the test suite in an isolated check and was discarded, never applied.\n\n{verify_output[:300]}",
            actions=[{"action": "view", "label": "Review CI Run", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}]
        )
        return 1

    print("[Guardian] Patch verified green in isolation. Opening PR for human review...")
    gh_token = os.environ.get("GITHUB_TOKEN")
    if not gh_token:
        print("[Guardian] No GITHUB_TOKEN available to open a PR. Alerting maintainer with the verified patch details instead.")
        notify_guardian(
            "🏮 Guardian: Verified Patch Ready (manual PR needed)",
            f"A patch passed full verification but could not be auto-opened as a PR (no token). "
            f"{patch.get('parent_summary', '')}",
        )
        return 0

    try:
        pr = open_guardian_pr(patch, gh_token)
    except Exception as e:
        print(f"[Guardian] Failed to open PR: {e}")
        notify_guardian(
            "🏮 Guardian Alert: Verified Patch Could Not Be Opened as a PR",
            f"A patch passed full verification but PR creation failed: {e}",
        )
        return 1

    notify_guardian(
        "🏮 Guardian: Router Patch Ready for Approval",
        f"{patch.get('parent_summary', 'A compatibility patch was generated and verified against the full safety test suite.')} "
        f"Tap below to review — it auto-merges in 12 hours only if it stays fully green with no objections.",
        actions=[
            {"action": "view", "label": "🔍 Review Patch & Diffs", "url": pr.get("html_url", "https://github.com/LanternWatchApp/lantern-watch/pulls")},
            {"action": "view", "label": "📊 View CI Test Logs", "url": "https://github.com/LanternWatchApp/lantern-watch/actions"}
        ]
    )

    print(f"[Guardian] Self-healing workflow completed successfully. PR: {pr.get('html_url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
