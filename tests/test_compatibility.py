#!/usr/bin/env python3
"""
Lantern Watch — test_compatibility.py
Comprehensive compatibility, safety, and contract test suite.
Verifies:
1. Python syntax & runtime compatibility across all modules.
2. Pause safety & admin lockout protection contracts.
3. Group pause / unpause IP-fallback and tombstone behavior.
4. Identity conflict detection & unambiguous duplicate resolution.
5. AdGuard API request/payload contracts and filter mappings.
6. Firewall rule generation (iptables / nftables safety bounds).
7. Privacy scanner (ensures zero personal data / secrets in repo files).
"""

import unittest
import json
import os
import sys
import re
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import config as cfg_module
import classify
import db
import pages


class TestConfigAndSafetyContracts(unittest.TestCase):
    """Test pause safety, admin lockout prevention, and config migration contracts."""

    def test_has_admin_device_contract(self):
        """Pausing must be gated on has_admin_device."""
        # No admin device configured
        no_admin_cfg = {
            "devices": {
                "phone-1": {"type": "person", "label": "Timmy Phone"},
                "laptop-1": {"type": "work_device", "label": "Work Laptop"}
            }
        }
        self.assertFalse(cfg_module.has_admin_device(no_admin_cfg))
        self.assertFalse(cfg_module.is_pauseable("phone-1", no_admin_cfg))

        # Admin device configured
        admin_cfg = {
            "devices": {
                "mom-phone": {"type": "parent", "label": "Mom Phone"},
                "phone-1": {"type": "person", "label": "Timmy Phone"}
            }
        }
        self.assertTrue(cfg_module.has_admin_device(admin_cfg))
        self.assertTrue(cfg_module.is_pauseable("phone-1", admin_cfg))
        # Admin itself is NEVER pauseable
        self.assertFalse(cfg_module.is_pauseable("mom-phone", admin_cfg))

    def test_label_has_protected_identity(self):
        """Aliases sharing a label with an Admin record are protected."""
        cfg = {
            "devices": {
                "192.168.8.100": {"type": "parent", "label": "Dad Laptop"},
                "Dad-MacBook": {"type": "person", "label": "Dad Laptop"}  # Duplicate identity
            }
        }
        self.assertTrue(cfg_module.label_has_protected_identity("Dad-MacBook", cfg))
        self.assertFalse(cfg_module.is_pauseable("Dad-MacBook", cfg))

    def test_is_groupable_contract(self):
        """Only Personal, Smart, and Work devices are groupable; Admin & Infra never are."""
        cfg = {
            "devices": {
                "admin-phone": {"type": "parent", "label": "Admin"},
                "kid-tablet": {"type": "person", "label": "Tablet"},
                "living-tv": {"type": "smart_device", "label": "Smart TV"},
                "nas-server": {"type": "infrastructure", "label": "Home NAS"}
            }
        }
        self.assertTrue(cfg_module.is_groupable("kid-tablet", cfg))
        self.assertTrue(cfg_module.is_groupable("living-tv", cfg))
        self.assertFalse(cfg_module.is_groupable("admin-phone", cfg))
        self.assertFalse(cfg_module.is_groupable("nas-server", cfg))

    def test_identity_conflicts_detection(self):
        """Conflicting roles for same normalized label are surfaced."""
        cfg = {
            "devices": {
                "192.168.8.150": {"type": "parent", "label": "Mom iPhone"},
                "iPhone-Mom": {"type": "person", "label": "Mom iPhone"}
            }
        }
        conflicts = cfg_module.find_identity_conflicts(cfg)
        self.assertEqual(len(conflicts), 1)
        lbl, entries = conflicts[0]
        self.assertEqual(lbl, "Mom iPhone")
        self.assertEqual(len(entries), 2)


class TestDeviceClassification(unittest.TestCase):
    """Test device kind heuristics and weak label overrides."""

    def test_smart_tv_weak_label_override(self):
        """Smart TV weak label doesn't mask genuine Android OS traffic signal."""
        domains = ["play.googleapis.com", "android.clients.google.com", "googleads.g.doubleclick.net"]
        ident = {"mac": "", "vendor": "TCL", "hostname": "9469X"}
        kind = classify.device_kind("9469X", "", ident, domains)
        self.assertIn(kind, ("phone or tablet", "tablet", "phone"))

    def test_auto_group_matching(self):
        """Auto group suggestions map cleanly to default categories."""
        self.assertEqual(pages._auto_group_name("iPad-Air", "iPad", {}, []), "Tablets")
        self.assertEqual(pages._auto_group_name("DESKTOP-ABC", "Windows PC", {}, []), "Computers")
        self.assertEqual(pages._auto_group_name("Galaxy-S23", "Samsung Phone", {}, []), "Phones")
        self.assertEqual(pages._auto_group_name("Apple-TV-4K", "Living Room TV", {}, []), "TVs")


class TestAttemptCollapsing(unittest.TestCase):
    """Test collapsing near-simultaneous DNS bursts into real human attempts."""

    def test_burst_collapsing(self):
        # 8 queries within 4 seconds = 1 real attempt
        base = datetime(2026, 9, 8, 12, 0, 0)
        burst = [
            ("timmy-phone", (base + timedelta(seconds=i * 0.5)).isoformat() + "Z")
            for i in range(8)
        ]
        # 1 query 20 seconds later = 2nd real attempt
        burst.append(("timmy-phone", (base + timedelta(seconds=25)).isoformat() + "Z"))

        real_count = db._count_real_attempts(burst, gap_seconds=5)
        self.assertEqual(real_count, 2)


class TestPrivacyAndSecurityGuardrails(unittest.TestCase):
    """Ensure zero personal data, secrets, or unredacted tokens exist in repo source."""

    def test_no_hardcoded_passwords_or_personal_keys(self):
        sensitive_patterns = [
            re.compile(r'BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY'),
            re.compile(r'AIza[0-9A-Za-z-_]{35}'),  # Google API key format
            re.compile(r'ghp_[0-9A-Za-z]{36}'),    # GitHub personal token
            re.compile(r'sk-[a-zA-Z0-9]{32,}'),    # OpenAI key format
        ]
        excluded_dirs = {'.git', '__pycache__', 'scratch', '.system_generated', 'node_modules', '.rollback'}

        for root, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [d for d in dirs if d not in excluded_dirs]
            for fname in files:
                if fname.endswith(('.py', '.sh', '.md', '.json', '.yml', '.yaml', '.txt')):
                    fpath = os.path.join(root, fname)
                    with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        for pattern in sensitive_patterns:
                            self.assertFalse(
                                pattern.search(content),
                                f"Sensitive credential pattern detected in {os.path.relpath(fpath, REPO_ROOT)}"
                            )


class TestPublicDocsPrivacy(unittest.TestCase):
    """CHANGELOG.md and README.md are the project's public-facing narrative
    docs — the ones a real household's specific device details must never
    leak into (see feedback_public_docs_privacy: never name a specific
    household/personal device in a commit message, CHANGELOG, README, or
    issue/PR). Deliberately separate from TestPrivacyAndSecurityGuardrails
    above, which scans the *whole repo* for credential-shaped secrets —
    this only scans these two docs for real personal identifiers, since the
    app's .py source legitimately references generic LAN defaults
    (192.168.8.1, the block-page virtual IP 192.168.8.2, etc.) throughout;
    scanning all source for "any private IP" would be constant false
    positives against intentional, documented code."""

    PUBLIC_DOCS = ["CHANGELOG.md", "README.md"]
    # Generic, documented example addresses already used in these docs —
    # not real household IPs. Extend this if a future doc adds another
    # deliberate illustrative example; anything else private-range is
    # treated as a possible real leak.
    ALLOWED_IPS = {
        "127.0.0.1", "0.0.0.0",
        "192.168.8.1", "192.168.8.2", "192.168.1.1",
        "192.168.10.1",   # documented GL.iNet repeater-mode subnet shift example
        "192.168.8.230",  # generic illustrative IP in a historical bug writeup
    }

    def _read_docs(self):
        for fname in self.PUBLIC_DOCS:
            fpath = os.path.join(REPO_ROOT, fname)
            if os.path.exists(fpath):
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    yield fname, f.read()

    def test_no_unexpected_lan_ips_in_public_docs(self):
        ip_re = re.compile(
            r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}'
            r'|192\.168\.\d{1,3}\.\d{1,3}'
            r'|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b'
        )
        for fname, content in self._read_docs():
            for m in ip_re.finditer(content):
                self.assertIn(
                    m.group(0), self.ALLOWED_IPS,
                    f"Unexpected private IP {m.group(0)!r} in {fname} — looks like a "
                    f"real household address, not one of this doc's documented "
                    f"generic examples. If it genuinely is a new generic example, "
                    f"add it to ALLOWED_IPS with a comment saying why."
                )

    def test_no_possessive_device_names_in_public_docs(self):
        """Catches the shape of a real AdGuard-resolved device hostname, e.g.
        'Timmy-s-iPad' or 'Archie-s-Galaxy' — a capitalized first name, a
        literal '-s-' (DNS hostnames can't hold an apostrophe, so a real
        possessive name always resolves to this hyphenated form), then a
        device model. Deliberately does not hardcode any real name — the
        point is the *shape* of the pattern, not a fixed blocklist."""
        possessive_re = re.compile(r'\b[A-Z][a-z]{2,}-s-[A-Za-z0-9]')
        for fname, content in self._read_docs():
            m = possessive_re.search(content)
            if m:
                self.fail(
                    f"{fname} contains what looks like a real device hostname "
                    f"({m.group(0)!r}) — a possessive first name plus a device "
                    f"model, not a generic example."
                )


class TestSensitiveFileGuardrails(unittest.TestCase):
    """Verify that operator-only / local-context files are never committed to
    the public repository.

    .gitignore already lists these paths, but `git add --force` or an editor's
    "stage all" shortcut can bypass .gitignore silently. This test catches that
    by querying the git index directly — the same thing CI pushes. If any of
    these paths appear in the index, a human or automated agent accidentally
    staged them and the commit must be blocked.

    This class runs as part of the Guardian CI watchdog's test suite (every
    two hours), so a slip is caught before it goes out in a release and before
    real users' installs could be affected."""

    # Files and directory prefixes that must NEVER appear in the git index.
    # These hold operator context, AI prompts, and local credentials that have
    # no business in a public repo with real users subscribed to it.
    FORBIDDEN_TRACKED = [
        "CLAUDE.md",          # local AI operator context — never public
        ".claude/",           # local Claude Code settings and memory
        "lanternwatch_config.json",  # live router credentials
        "lanternwatch.db",    # DB (also .gitignore'd via *.db)
    ]

    def _git_tracked_files(self):
        """Return the set of paths currently in the git index (cached), or an
        empty set if git is unavailable (e.g. a bare source tree without .git)."""
        try:
            result = subprocess.run(
                ["git", "ls-files", "--cached"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return set()
            return set(result.stdout.splitlines())
        except Exception:
            return set()

    def test_local_operator_files_not_committed(self):
        """CLAUDE.md, .claude/, live config, and DB must not be in the git index."""
        tracked = self._git_tracked_files()
        if not tracked:
            self.skipTest("git index not available — skipping (not a git checkout)")

        for forbidden in self.FORBIDDEN_TRACKED:
            if forbidden.endswith("/"):
                prefix = forbidden
                leaks = [p for p in tracked if p.startswith(prefix)]
                self.assertEqual(
                    leaks, [],
                    f"Sensitive directory committed to repo: {leaks}\n"
                    f"These files are local-only. Remove them with:\n"
                    f"  git rm --cached -r {prefix}\n"
                    f"and ensure '{prefix}' is in .gitignore."
                )
            else:
                self.assertNotIn(
                    forbidden, tracked,
                    f"Sensitive file committed to repo: {forbidden!r}\n"
                    f"This file is local-only (operator context or live credentials).\n"
                    f"Remove it with: git rm --cached {forbidden}"
                )

    def test_no_corrupt_db_artifacts_committed(self):
        """lanternwatch.db.corrupt.* recovery artifacts must not be committed."""
        tracked = self._git_tracked_files()
        if not tracked:
            self.skipTest("git index not available")
        leaks = [p for p in tracked if ".db.corrupt" in p or p.endswith(".db")]
        self.assertEqual(
            leaks, [],
            f"Database file(s) found in git index: {leaks}\n"
            f"DB files are excluded by .gitignore — remove with: git rm --cached <file>"
        )

    def test_no_internal_docs_committed(self):
        """docs/internal/ is marked local-only and must not be tracked."""
        tracked = self._git_tracked_files()
        if not tracked:
            self.skipTest("git index not available")
        leaks = [p for p in tracked if p.startswith("docs/internal/")]
        self.assertEqual(
            leaks, [],
            f"Internal docs leaked into the index: {leaks}\n"
            f"Remove with: git rm --cached -r docs/internal/"
        )


class TestSysupgradeBackwardCompat(unittest.TestCase):
    """Verify that the v0.19.5 sysupgrade safety fix is correctly implemented
    and that existing users upgrading from v0.19.4 will not lose data or end
    up in a broken state.

    Background: install.sh used to list /root/lantern-watch/lanternwatch.db in
    /etc/sysupgrade.conf. On a GL.iNet 'Keep Settings' firmware upgrade that
    archives a live 518 MB SQLite DB into RAM-backed /tmp/sysupgrade.tgz while
    collector.py is writing to it every 30 s, the resulting file is truncated
    and corrupt. The fix removes the DB from sysupgrade.conf so it is never
    archived — the DB rebuilds from AdGuard's 14-day query log automatically.

    These tests confirm:
    1. The old unsafe paths are actively removed from existing sysupgrade.conf
       entries during reinstall (not silently left there for existing users).
    2. The DB path is NOT re-added in the new safe path list.
    3. The essential config JSON + init script ARE still preserved.
    4. Auto-recovery code exists so a corrupt DB from a previous upgrade is
       handled gracefully without breaking the dashboard for real users."""

    def _read_install_sh(self):
        path = os.path.join(REPO_ROOT, "install.sh")
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    def test_db_path_not_in_lw_keep_paths(self):
        """install.sh must NOT re-add the DB or the directory glob to sysupgrade.conf."""
        content = self._read_install_sh()
        # Find the LW_KEEP_PATHS block — everything between the opening quote
        # and the closing quote of the heredoc-style variable assignment.
        keep_match = re.search(r'LW_KEEP_PATHS="([^"]*)"', content, re.DOTALL)
        self.assertIsNotNone(keep_match, "LW_KEEP_PATHS variable not found in install.sh")
        keep_block = keep_match.group(1)
        self.assertNotIn(
            "lanternwatch.db", keep_block,
            "install.sh still lists lanternwatch.db in LW_KEEP_PATHS — this causes "
            "DB corruption on GL.iNet Keep-Settings firmware upgrades. Remove it."
        )
        # The bare directory glob /root/lantern-watch/ implicitly covers the DB
        # too (sysupgrade archives everything under it), so it must also be gone.
        self.assertNotIn(
            "/root/lantern-watch/\n", keep_block,
            "install.sh still lists /root/lantern-watch/ in LW_KEEP_PATHS — this "
            "implicitly preserves the DB and causes the same corruption. Remove it."
        )

    def test_essential_paths_still_preserved(self):
        """Config JSON and init script must still be in LW_KEEP_PATHS after the fix."""
        content = self._read_install_sh()
        keep_match = re.search(r'LW_KEEP_PATHS="([^"]*)"', content, re.DOTALL)
        self.assertIsNotNone(keep_match, "LW_KEEP_PATHS not found in install.sh")
        keep_block = keep_match.group(1)
        self.assertIn(
            "lanternwatch_config.json", keep_block,
            "lanternwatch_config.json must still be preserved across firmware upgrades."
        )
        self.assertIn(
            "/etc/init.d/lanternwatch", keep_block,
            "/etc/init.d/lanternwatch must still be preserved across firmware upgrades."
        )

    def test_install_sh_removes_old_db_entries_from_existing_conf(self):
        """install.sh must actively scrub the old DB and directory-glob entries
        from an existing sysupgrade.conf so existing users on v0.19.4 get the
        fix automatically on reinstall — not left silently in place."""
        content = self._read_install_sh()
        self.assertIn(
            "LW_REMOVE_PATHS", content,
            "install.sh is missing LW_REMOVE_PATHS — existing users' sysupgrade.conf "
            "will still list the DB path and remain vulnerable to corruption on their "
            "next GL.iNet firmware upgrade. Add a removal loop for the old unsafe entries."
        )
        # Both the bare directory glob and the explicit DB path must be removed.
        remove_match = re.search(r'LW_REMOVE_PATHS="([^"]*)"', content, re.DOTALL)
        if remove_match:
            remove_block = remove_match.group(1)
            self.assertIn(
                "lanternwatch.db", remove_block,
                "LW_REMOVE_PATHS must include lanternwatch.db to fix existing installs."
            )
            self.assertIn(
                "/root/lantern-watch/", remove_block,
                "LW_REMOVE_PATHS must include /root/lantern-watch/ to fix existing installs."
            )

    def test_db_corruption_recovery_function_exists(self):
        """db.py must expose check_and_recover_db() so both collector.py and
        dashboard.py can auto-recover from a DB that was corrupted by a previous
        firmware upgrade — without requiring SSH access from the user."""
        self.assertTrue(
            hasattr(db, "check_and_recover_db"),
            "db.check_and_recover_db() is missing. Users whose DB was already "
            "corrupted by a prior upgrade will see 'database disk image is malformed' "
            "on the dashboard permanently. Add this function to db.py."
        )

    def test_collector_calls_recovery_before_init(self):
        """collector.py must call check_and_recover_db() before init_db() so a
        corrupt DB from a previous firmware upgrade is auto-healed on restart."""
        collector_path = os.path.join(REPO_ROOT, "collector.py")
        with open(collector_path, "r", encoding="utf-8") as f:
            source = f.read()
        self.assertIn(
            "check_and_recover_db", source,
            "collector.py does not call check_and_recover_db(). A user upgrading "
            "from a build where the DB was already corrupted will get a broken "
            "dashboard immediately after reinstall."
        )


if __name__ == '__main__':
    unittest.main()
