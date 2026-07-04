# Lantern Watch — Privacy Policy

*Last updated: June 2026*

## The short version

Lantern Watch runs entirely on your own router. We do not have access to your network, your devices, or your family's internet activity. The only data that ever leaves your router is a small anonymous ping when you check for software updates — and it contains no personal information.

---

## 1. What we do NOT collect

We never collect, store, or see:

- The names or identities of any devices on your network
- IP addresses of your devices
- DNS queries your family makes
- Websites visited
- Blocked domains
- Any content filtered by AdGuard Home
- Your dashboard username or password
- Your notification credentials (ntfy topic, Telegram token, email address)
- Your router's IP address or network configuration

All of this data lives exclusively on your router and is never transmitted anywhere.

## 2. What the update check sends

When you click **"Check for Updates"** in the Lantern Watch dashboard, one anonymous ping is sent to our update server. It contains:

| Field | What it is |
|---|---|
| `install_id` | A one-way hash derived from your router's hardware — stable per physical device, cannot be reversed to identify your device or network |
| `version` | Your Lantern Watch software version |
| `router_model` | Your router hardware model (e.g. "GL.iNet GL-MT6000") |
| `openwrt_version` | Your OpenWrt firmware version |
| `adguard_connected` | Whether AdGuard Home is reachable (true/false) |
| `device_count` | Number of labelled devices in your config |
| `social_profile` | Your social media blocking profile (e.g. "open", "teen") |
| `features.*` | Which features are enabled: screen time, bedtime, focus times, notifications |

No field identifies you, your household, or your location. The `install_id` is a one-way hash that cannot be reversed — it counts unique routers without revealing anything about you or your network.

This ping is optional — it only happens when you click the button. Lantern Watch does not send any data automatically in the background.

## 3. Notification channels

If you configure ntfy, Telegram, or email notifications, your credentials are stored in `lanternwatch_config.json` on your router only. They are never sent to Lantern Watch servers.

Alerts are sent directly from your router to your chosen notification service (ntfy.sh, Telegram, or your SMTP provider). Lantern Watch is not a relay — we never see your alerts.

## 4. Self-hosted nature

Lantern Watch is self-hosted software. When you install it, you are running it on hardware you own, on a network you control. We have no servers, no accounts, no cloud dashboard. There is no way for us to access your data even if we wanted to.

## 5. Affiliate links

The Lantern Watch website and documentation contain affiliate links (currently Amazon). Clicking these links may place tracking cookies managed by Amazon. We earn a small commission on qualifying purchases. This tracking is entirely on Amazon's side and subject to [Amazon's privacy policy](https://www.amazon.com/gp/help/customer/display.html?nodeId=468496).

## 6. Website analytics

[lanternwatch.org](https://lanternwatch.org) may use basic, privacy-respecting analytics (page views, no personal identifiers). No third-party advertising trackers are used.

## 7. Children's privacy

Lantern Watch is designed to help parents protect children's online safety. We do not collect any data about children or anyone else. All data stays on your router.

## 8. Contact

If you have privacy questions: [lanternwatchapp@gmail.com](mailto:lanternwatchapp@gmail.com)
