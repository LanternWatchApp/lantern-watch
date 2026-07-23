#!/bin/bash
# Builds lanternwatch_VERSION_all.ipk from the repo root.
# Usage: bash build_ipk.sh
# Output: lanternwatch_VERSION_all.ipk in the repo root
#
# Requirements: bash, python3, tar
# Works on Linux, macOS, and Windows (Git Bash).

set -e

# ── Version ───────────────────────────────────────────────────────────────────

VERSION=$(grep '^VERSION' config.py | sed "s/.*= *['\"]//;s/['\"].*//")

PKG="lanternwatch_${VERSION}_all.ipk"
BUILD="$(pwd)/.ipk_build"
OUT="$(pwd)/$PKG"

echo ""
echo "Building $PKG..."
echo ""

rm -rf "$BUILD"
mkdir -p "$BUILD/data/root/lantern-watch"
mkdir -p "$BUILD/control"

# ── App files ─────────────────────────────────────────────────────────────────

APP_FILES="
  adguard.py
  alerts.py
  backup.py
  blockserver.py
  classify.py
  collector.py
  config.py
  dashboard.py
  db.py
  pages.py
  portal.py
  recovery.py
  routes.py
  scheduler.py
  lantern_logo.svg
  lantern_watch_logo.svg
  lanternwatch_config.example.json
  install.sh
  lanternwatch.initd
"

for f in $APP_FILES; do
    cp "$f" "$BUILD/data/root/lantern-watch/$f"
    echo "  + $f"
done

chmod 755 "$BUILD/data/root/lantern-watch/install.sh"
chmod 755 "$BUILD/data/root/lantern-watch/lanternwatch.initd"

# ── control ───────────────────────────────────────────────────────────────────

cat > "$BUILD/control/control" <<EOF
Package: lanternwatch
Version: $VERSION
Architecture: all
Maintainer: LanternWatch <lanternwatchapp@gmail.com>
Description: Family parental control dashboard for GL.iNet routers
 Monitor every device on your network, set bedtime and screen time schedules,
 block social media by profile, and receive push notifications via ntfy,
 Telegram, or email — all from a mobile-friendly web UI.
Depends: python3
Section: extras
Priority: optional
EOF

# ── postinst ──────────────────────────────────────────────────────────────────

cat > "$BUILD/control/postinst" <<'POSTINST'
#!/bin/sh
set -e
cd /root/lantern-watch
sh install.sh --skip-clone
exit 0
POSTINST
chmod 755 "$BUILD/control/postinst"

# ── prerm ─────────────────────────────────────────────────────────────────────

cat > "$BUILD/control/prerm" <<'PRERM'
#!/bin/sh
/etc/init.d/lanternwatch stop 2>/dev/null || true
/etc/init.d/lanternwatch disable 2>/dev/null || true
exit 0
PRERM
chmod 755 "$BUILD/control/prerm"

# ── Assemble ──────────────────────────────────────────────────────────────────

printf '2.0\n' > "$BUILD/debian-binary"

(cd "$BUILD/data"    && tar czf "$BUILD/data.tar.gz"    .)
(cd "$BUILD/control" && tar czf "$BUILD/control.tar.gz" .)
(cd "$BUILD"         && tar czf "$OUT" debian-binary control.tar.gz data.tar.gz)

rm -rf "$BUILD"

echo ""
echo "Done: $PKG  ($(du -sh "$OUT" | cut -f1))"
echo ""
echo "  Publish to the hosted feed (primary install path):"
echo "    1. copy $PKG into the lanternwatch-site repo's repo/ folder"
echo "    2. regenerate repo/Packages + Packages.gz, commit & push"
echo "       (users then install via Manage Sources -> https://lanternwatch.org/repo)"
echo ""
echo "  Or install directly via SSH / LuCI Software:"
echo "    opkg install /path/to/$PKG"
echo ""
