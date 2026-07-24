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

## [0.14.5] — 2026-07-24

Hardening from a full end-to-end audit. Everything tested healthy; these close the
minor gaps the audit surfaced (no user-facing feature was broken).

### Fixed
- **Lite updates no longer switch off small blocklists you enabled.** On Lite, an
  update used to disable *every* list outside the tiny default set to protect a
  512 MB router from running out of memory — including a modest list you'd turned
  on yourself. It now only disables genuinely heavy lists (the ones that actually
  cause the memory problem), so your own choices survive updates.
- **A forced protection profile now sticks.** Installing with `--force-lite` /
  `--force-full` is remembered across updates, instead of reverting to the
  RAM-based default on the next update (which never re-passes the flag).
- **The app talks to AdGuard over loopback** (`127.0.0.1`) instead of the LAN IP,
  so a router whose LAN address changes (e.g. repeater mode) can't lose its
  connection to AdGuard.

### Changed
- **"Clear Data" now reclaims the disk space** it frees (the database file used to
  stay large after a clear until the next write cycle).
- **Build safeguard:** the package build now fails if any app module isn't included
  — the exact class of mistake behind the missing Backup & Restore file in 0.14.3.

## [0.14.4] — 2026-07-23

### Fixed
- **Updating no longer resets your Safe Search / YouTube Restricted Mode choice.**
  The installer re-applies family protection on every run, and that step blanket-
  re-enabled every Safe Search engine — so an update silently switched YouTube
  Restricted Mode back on, undoing anyone who had turned it off to allow comments.
  Setup now only applies the secure-by-default all-engines-on state when Safe
  Search is currently off (a genuine first-time setup); if it's already on, it
  preserves your per-engine choices. The fix is centralized, so it also covers the
  manual "Apply Now" button — re-applying protection keeps your YouTube choice.
  (Everything else you configure already survives updates; it lives in your config
  file, which updates never overwrite. Safe Search was the one setting stored in
  AdGuard rather than the config, which is why it was the one thing affected.)

## [0.14.3] — 2026-07-23

### Fixed
- **Backup & Restore now actually ships.** The `backup.py` module was never added
  to the package build, so every installed copy was missing it — which silently
  disabled the whole feature: downloadable backup files *and* USB auto-backup. The
  app didn't crash (each backup call is a guarded, on-demand import), it just did
  nothing. `backup.py` is now included in the package, so plugging in a USB drive
  saves your setup automatically again, and manual backup/restore works. (USB
  detection itself was fine — it reads the live mount table and handles GL.iNet's
  `/tmp/mountd/...` location.)

## [0.14.2] — 2026-07-20

### Changed
- **Simplified the last step of first-run setup.** It no longer asks you to enter
  ntfy, Telegram, or email details up front — that was a lot to face on a first
  run. The step is now just the optional "share anonymous usage stats" choice, and
  notifications, schedules, and social profiles are all set up later from the
  dashboard whenever you want them.

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
