# Changelog

All notable changes to Lantern Watch are recorded here.

Versioning follows [Semantic Versioning](https://semver.org): `MAJOR.MINOR.PATCH`.
No `-beta`/`-alpha` suffixes — the leading `0.` is itself the signal that this is
early-stage software.

- **PATCH** — the third number — bug fixes and small tweaks (`0.14.1`, `0.14.2`, …).
  It is an ordinary integer, so it keeps counting past 9 (`0.14.9` → `0.14.10` →
  `0.14.100`). Never zero-pad it.
- **MINOR** — new features climb the `0.14.0 → 0.15.0 → …` series (double-digit,
  AdGuard-style, for plenty of pre-1.0 headroom).
- **MAJOR** — **`x`**`.0.0` — breaking changes / the 1.0 milestone.

Version numbers only ever climb. Both the in-app update check and `opkg` compare
them numerically, so publishing a lower number than the one already released would
leave existing routers unable to update.

Bump `VERSION` in `config.py`, add an entry here, then commit and tag `v<version>`.

## [0.14.1] — 2026-07-20

### Fixed
- **Device names now match on every page.** The dashboard, query log, and device
  detail pages showed a bare IP address (e.g. `192.168.8.230`) while the Devices
  page showed the friendly name (e.g. "Dell device") for the very same device.
  Each page worked the name out for itself and the copies had drifted apart; they
  now share one routine, so they cannot disagree again.
- **The router shows its own model** — e.g. "GL.iNet GL-MT3600BE" — instead of
  appearing as "localhost".

## [0.14.0] — 2026-07-20

First public release.

### Protection
- **AdGuard Home set up for you**, in one click during the first-run wizard —
  adult content, malware and phishing blocking, and safe search on Google, Bing
  and YouTube. No AdGuard settings to touch.
- **RAM-aware protection profiles, chosen automatically.** Routers with 1 GB+ run
  the **Full** profile with the complete local blocklists. Smaller 512 MB travel
  routers run **Lite**, which keeps memory low by filtering adult and malware
  content at a Cloudflare for Families DNS upstream instead of loading hundreds of
  thousands of rules on the router. Every parental feature works identically on
  both.
- **Choose your DNS filtering level** on Lite — Malware + Adult (default) or
  Malware only.
- **Blocked sites land on a Lantern Watch page** carrying a prominent **Find Help**
  link, including when the block came from the DNS upstream.
- **Encrypted-DNS bypass protection**, always on, plus an optional strict mode.
- **DNS blocklist manager** with per-list toggles and a live rule-budget meter.

### Family controls
- **Device dashboard** — query counts, block rates, and time online per device.
- **Pause the internet** for a device instantly or on a schedule.
- **Hours of Peace** bedtime cutoff, **Focus Times**, and **screen time limits**.
- **Social media profiles** — Open, Moderate, Strict, or Custom — applied instantly,
  plus a secure-by-default **YouTube Restricted Mode** toggle.
- **Device roles** (Personal, Admin, Work, Infrastructure, Smart Device) that
  control grouping and bulk pausing. Every role stays fully filtered.
- **Network Notice** — an optional acceptable-use notice new devices acknowledge
  before browsing.

### Notifications
- **ntfy, Telegram, and email**, each with a test button, for blocked content, new
  devices, high block rates, possible VPN use, and screen time limits.
- **Daily and weekly summaries** at a time you choose, and an in-dashboard log of
  every alert sent.
- **Update alerts** when a newer version is released.

### Living with it
- **Backup & restore** your whole setup to a file, plus optional USB auto-backup
  that survives a factory reset.
- **One-click updater** — "Update Now" installs the latest release and restarts.
- **Query history** you control: 7 to 90 days, trimmed automatically.
- **Router health** — live RAM, storage, CPU load, uptime, and database size.
- **Password recovery** by one-time code to your notification channel.

### Privacy
- Everything runs on your own router. Names, devices, domains, IP addresses, and
  browsing history never leave it.
- A small **anonymous record** is sent once a day so active installs can be
  counted: a random identifier, version, router model, memory size, and protection
  profile. The identifier is random — never derived from your hardware.
- **Optional usage stats** (which features are switched on) are offered during
  setup and can be turned off at any time in Settings.
- Update checks read GitHub's public release list and send nothing at all.
