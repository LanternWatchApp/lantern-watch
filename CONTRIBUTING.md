# Contributing to Lantern Watch

Thanks for your interest in contributing. Lantern Watch exists to give every family — regardless of income — real control over their home network. Every improvement you make has a direct impact on real households.

The repo is open source under the [MIT License](LICENSE).

---

## What we're looking for

- Bug fixes
- UI/UX improvements (mobile-first — most parents use this on a phone)
- New notification channels or alert types
- Performance improvements for low-memory routers (256MB RAM target)
- Broader router support beyond GL.iNet
- Documentation improvements

If you're planning something significant, open an issue or message the maintainer first so we can align before you invest the time.

---

## Getting started

1. **Get repo access** — message the maintainer with your GitHub username
2. **Clone the repo** to your Windows or Mac dev machine
3. **Get a GL.iNet router** — tested on GL-MT6000 Flint 2 and GL-MT3000 Beryl AX3000; any GL.iNet router running OpenWrt with AdGuard Home should work
4. **Enable AdGuard Home** on the router (GL.iNet admin panel → Applications → AdGuard Home)
5. **SSH into the router** and clone the repo there:
   ```bash
   ssh root@192.168.8.1
   cd /root
   git clone https://github.com/LanternWatchApp/lantern-watch.git lantern-watch
   cp lantern-watch/lanternwatch_config.example.json lantern-watch/lanternwatch_config.json
   ```
6. Edit `lanternwatch_config.json` with your AdGuard credentials
7. Start the service:
   ```bash
   /etc/init.d/lanternwatch enable
   /etc/init.d/lanternwatch start
   ```
8. Open **http://192.168.8.1:8081**

---

## Making changes

Edit files on your dev machine, push to a branch, then pull on the router to test:

```bash
# On your dev machine
git checkout -b your-feature-name
# ... make changes ...
git push origin your-feature-name

# On the router
cd /root/lantern-watch
git fetch origin
git checkout your-feature-name
/etc/init.d/lanternwatch restart
```

---

## Submitting a pull request

1. Fork or branch from `main`
2. Keep changes focused — one feature or fix per PR
3. Test on a real router if possible; if not, describe what you tested
4. Open the PR against `main` with a clear description of what changed and why

There are no automated tests today. Manual testing on the router is the standard. If you want to add a test suite, that contribution is very welcome.

---

## Code style

- Python 3 stdlib only — no pip dependencies
- HTML lives in `pages.py` as f-strings; keep it readable, not minified
- No comments explaining what code does — only add a comment if the *why* is non-obvious
- Match the existing style in the file you're editing
- Keep router resource usage in mind — this runs on 256MB RAM

---

## Contributor License Agreement

By submitting a pull request or any other contribution to this repository, you confirm that:

1. The contribution is your own original work and you have the right to submit it.
2. You license your contribution under the same [MIT License](LICENSE) that covers this project.
3. You understand your contribution may be used, modified, and redistributed freely under those terms.

This keeps Lantern Watch legally clean for everyone and ensures it can remain free for every family.

---

## Questions

Open an issue or email [lanternwatchapp@gmail.com](mailto:lanternwatchapp@gmail.com). We're a small project and happy to help you get set up.
