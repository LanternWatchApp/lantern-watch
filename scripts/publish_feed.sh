#!/bin/bash
# Publishes a built .ipk to the lanternwatch-site opkg feed (the "Update Now"
# button and GL.iNet's Manage Sources both pull from here — NOT from GitHub
# Releases, which is a separate, unrelated publish target).
#
# Usage: publish_feed.sh <path-to-ipk> <version>
# Requires a git identity that can push to lanternwatch-site over SSH (in CI,
# via a deploy key loaded by webfactory/ssh-agent; locally, your own key).
set -euo pipefail

PKG_PATH="$1"
VERSION="$2"
PKG="$(basename "$PKG_PATH")"
SITE_REPO="git@github.com:lanternwatch/lanternwatch-site.git"
WORKDIR="$(mktemp -d)"

git clone --depth 1 "$SITE_REPO" "$WORKDIR"
cp "$PKG_PATH" "$WORKDIR/repo/$PKG"

cd "$WORKDIR/repo"
SIZE=$(stat -c%s "$PKG")
MD5=$(md5sum "$PKG" | cut -d' ' -f1)
SHA256=$(sha256sum "$PKG" | cut -d' ' -f1)

cat > Packages <<PKGEOF
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
Filename: $PKG
Size: $SIZE
MD5Sum: $MD5
SHA256Sum: $SHA256
PKGEOF

gzip -n -k -f -9 -c Packages > Packages.gz   # -n: no embedded mtime, so identical
                                              # input always produces identical output

cd "$WORKDIR"
git config user.name "Lantern Watch"
git config user.email "lanternwatchapp@gmail.com"
git add repo/

if git diff --cached --quiet; then
    echo "Feed already up to date at $VERSION — nothing to publish."
    exit 0
fi

git commit -m "Publish lanternwatch $VERSION to the opkg feed"
git push origin main
echo "Published $PKG to the opkg feed."
