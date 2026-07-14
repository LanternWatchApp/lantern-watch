# Changelog

All notable changes to Lantern Watch are recorded here.

Versioning follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
While the project is pre-1.0, the leading `0.` signals it's still maturing:

- **PATCH** — `0.10.`**`x`** — bug fixes and small tweaks
- **MINOR** — `0.`**`x`**`.0` — new features (backward-compatible)
- **MAJOR** — **`x`**`.0.0` — breaking changes / the 1.0 milestone

Bump `VERSION` in `config.py`, add an entry here, then commit and tag `v<version>`.

## [0.10.1] — 2026-07-14

### Fixed
- **Never block Lantern Watch's own services.** AdGuard's Safe Browsing was
  flagging `ntfy.sh` (our push service and the ntfy phone app) as malware — a
  false positive that would silently break ntfy notifications. Added an always-on
  service allowlist (`ntfy.sh`, `api.telegram.org`, the update feed, GitHub, and
  the telemetry endpoint) so filtering / Safe Browsing / Parental can't block
  our push, updates, or telemetry.

## [0.10.0] — 2026-07-13

### Added
- **One-click self-updater.** "Update Now" on Settings downloads and installs the
  latest release in place and restarts — no trip to the GL.iNet plugin panel.
  Device names and all settings are preserved.
- **First-run opt-in for anonymous usage stats** (private by default), with a
  plain-language summary of exactly what is and isn't shared.
- **Adult / pornography blocklist** (HaGeZi NSFW, ~107K sites) enabled by default.
- **DNS Blocklist manager** in Settings — per-list on/off toggles grouped by
  Security / Family & Content / Ads, with a live "rule budget" meter.
- **Per-category notifications for blocked services**, so gaming/streaming
  telemetry no longer floods the dashboard.
- **Notifications for adult/gambling/dating blocklist hits** (filter-id tiering),
  and a channel-independent **notification activity log**.
- **Always-on DoH mitigation** (Firefox canary + provider hostnames) plus an
  opt-in VPN/Proxy/DoH-bypass list.
- Expanded **Lingerie / Swimwear / Retailers** blocklist packs.
- Warmer **block page** ("Return to safety") and a greatly expanded, compassionate
  **Find Help** page with vetted recovery resources and the 988 crisis line.

### Changed
- Adopted Semantic Versioning; dropped the `-beta` suffix (the leading `0.`
  already signals pre-1.0).
- Notifications now default **off** on a fresh install; the setup wizard turns on
  sensible defaults only when a channel is configured.
- Weekly-summary day list is Sunday-first, defaulting to Sunday.
- The install ping re-fires on version change, so the anonymous install record
  reflects the running build.
- Device names display without the redundant `.lan` suffix everywhere.
- Anonymous install ID is now a random per-install UUID (never MAC-derived).

### Fixed
- "Clear Data" now also clears AdGuard's query log, so cleared entries don't
  re-import.
- Install-count telemetry no longer lost on a boot-time network hiccup.
- The Find Help link renders as a real clickable link in Telegram and email.
- Silenced expected TLS-handshake tracebacks on the HTTPS block page.
- Fixed the Social "Moderate" profile Safe-Search inconsistency and stray
  pre-checked blocked-service Notify boxes on a clean install.
