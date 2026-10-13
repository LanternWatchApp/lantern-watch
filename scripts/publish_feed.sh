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

# Sign the uncompressed Packages file (opkg verifies the plaintext manifest,
# not the .gz). Uses the local usign private key via Python's Ed25519 — no
# usign binary or connected router required.
SIGNING_KEY="${LW_SIGNING_KEY:-$HOME/.ssh/lw_usign.sec}"
python3 - "$SIGNING_KEY" <<'PYEOF'
import sys, base64
from pathlib import Path

key_path = Path(sys.argv[1])
if not key_path.exists():
    sys.exit(f"Signing key not found at {key_path}\n"
             "Expected: ~/.ssh/lw_usign.sec (generated during initial repo setup)")

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:
    sys.exit("Missing dependency — run: pip install cryptography")

raw = base64.b64decode(Path(key_path).read_text().strip().splitlines()[-1])
# usign secret key layout (104 bytes):
#   [0:2]  pkalg ("Ed")  [2:4] kdf ("BK")  [4:8] rounds (0=unencrypted)
#   [8:24] salt  [24:32] checksum  [32:40] keynum  [40:72] seed  [72:104] pubkey
pkalg  = raw[0:2]
keynum = raw[32:40]
seed   = raw[40:72]    # Ed25519 seed (32 bytes, plaintext when rounds=0)

privkey = Ed25519PrivateKey.from_private_bytes(seed)
data    = Path("Packages").read_bytes()
sig     = privkey.sign(data)   # 64-byte raw Ed25519 signature

sig_blob = base64.b64encode(pkalg + keynum + sig).decode()
key_id   = keynum.hex()
Path("Packages.sig").write_text(
    f"untrusted comment: signed by key {key_id}\n{sig_blob}\n"
)
print(f"Signed Packages with key {key_id}")
PYEOF

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
