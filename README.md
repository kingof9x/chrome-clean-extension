# cleanbrowse

[![CI](https://github.com/kingof9x/chrome-clean-extension/actions/workflows/ci.yml/badge.svg)](https://github.com/kingof9x/chrome-clean-extension/actions/workflows/ci.yml)

Ephemeral, hardened Chromium sessions that **keep your trusted extensions** but
**wipe all browsing data every time** — a LibreWolf-style privacy experience on a
Chromium base, without breaking streaming DRM, bank logins, or "Sign in with
Google" (phone 2FA each session is expected and intended).

Runs on **Windows, macOS, and Linux** and wraps **Google Chrome** and
**ungoogled-chromium** (two independent "variants", each with its own template).

## How it works

You can't make Chrome selectively wipe itself, so `cleanbrowse` wraps it instead:

1. You build a **template profile** once (`setup`) — install only the extensions
   you trust and set your hardened preferences. This is the only thing that persists.
2. Every launch (`run`) **copies** the template to a throwaway profile, **scrubs**
   website/user data out of the copy (cookies, history, saved logins, site storage,
   cache), launches the browser against the copy, and **deletes** the copy on exit.

Extensions and their settings survive. Browsing data never does. There is no
separate "deep clean" button because **every session is already a clean room** —
closing the window is the deep clean.

```
template/  (persists: extensions + hardened prefs)
   │  copy, minus caches
   ▼
temp profile/  ──scrub cookies/history/logins/storage──►  launch browser
   │
   ▼  (on exit)
deleted
```

## Install

You need **Google Chrome and/or ungoogled-chromium** installed either way. Then
pick one of the two paths below.

### Option A — prebuilt binary (no Python needed)

Each tagged release ships a single-file executable for Windows, macOS and Linux,
built automatically by GitHub Actions.

1. Go to the repo's **Releases** page and download the file for your OS:
   - `cleanbrowse-windows.exe`
   - `cleanbrowse-macos`
   - `cleanbrowse-linux`
2. Put it somewhere on your `PATH` (or just keep it in a folder you'll run it from).
3. Make it executable on macOS/Linux, and clear the macOS quarantine flag:
   ```bash
   chmod +x cleanbrowse-macos            # or cleanbrowse-linux
   xattr -d com.apple.quarantine cleanbrowse-macos 2>/dev/null || true   # macOS only
   ```
4. Run it. Everywhere the examples below say `python cleanbrowse.py`, use the binary
   name instead, e.g. `./cleanbrowse-macos setup --variant chrome`.

> macOS Gatekeeper may still block an unsigned binary on first launch. If so:
> right-click → Open once, or `System Settings → Privacy & Security → Open Anyway`.
> The release binaries are unsigned (no Apple Developer / Windows code-signing cert).

### Option B — run from source

Requires **Python 3.8+** (no third-party packages).

```bash
git clone https://github.com/kingof9x/chrome-clean-extension
cd chrome-clean-extension
python cleanbrowse.py list
```

## Quick start

```bash
# 1. (optional) point the launcher at your browsers
cp config.example.json config.json       # edit executable paths if auto-detect fails

# 2. build each template once — install trusted extensions, set hardened prefs, close
python cleanbrowse.py setup --variant chrome
python cleanbrowse.py setup --variant ungoogled

# 3. daily use
python cleanbrowse.py run --variant chrome
python cleanbrowse.py run --variant ungoogled

# open a URL directly, or pass extra browser flags after --
python cleanbrowse.py run --variant chrome -- https://news.ycombinator.com
```

`python cleanbrowse.py list` shows which variants are configured and whether each
template exists yet.

## Setting up a hardened template

In the `setup` window (Settings), the compatibility-safe hardening is:

- **Privacy & security → Use secure DNS**: On (DNS-over-HTTPS)
- Turn **off** browser sign-in and sync
- **Safe Browsing**: Standard protection (not Enhanced — Enhanced sends URLs to Google)
- Turn off "Make searches and browsing better", metrics, and promotional tabs
- Turn off the built-in password manager if you use a password-manager extension instead
- Install **only** extensions you trust (a password manager, a content blocker, etc.)

Then close the browser to freeze the template. Avoid signing into personal
accounts in `setup` — accounts belong in `run` sessions, which get wiped.

## Compatibility notes (read these)

- **Streaming DRM (Widevine).** Google Chrome ships Widevine, so Netflix/Spotify/
  etc. work out of the box. **ungoogled-chromium does not ship Widevine** — you must
  add it for DRM video to play. The `ungoogled` variant is the right choice for
  general de-Googled browsing; use the `chrome` variant for DRM streaming.
- **Google sign-in.** Works on the `chrome` variant. ungoogled-chromium is
  sometimes flagged by Google as an "insecure browser"; if that happens, use the
  `chrome` variant for Google-account logins.
- **2FA every session is by design.** Wiping cookies removes every "remember this
  device" token, so phone 2FA fires on each login. That's the cost of a clean room.
- **No aggressive fingerprint spoofing.** Unlike LibreWolf's RFP, this intentionally
  does *not* spoof canvas/WebGL/timezone, because that is exactly what trips bank
  fraud systems and streaming device checks. Hardening here is limited to the things
  that don't break real sites.
- **Templates are machine-bound.** Chrome encrypts parts of a profile with an
  OS/user key and HMAC-signs `Secure Preferences`. A template built on one machine
  may show a one-time "settings were reset" nag if copied to another. Build the
  template on the machine where you'll use it.

## Optional: enforced hardening via policy

`policies/managed_policy.json` applies the same hardening as locked enterprise
policy instead of (or on top of) template preferences — useful for client machines
you manage, or to force-install extensions by ID. See `policies/README.md`;
note Chrome and ungoogled-chromium read **different** policy locations.

## Files

```
cleanbrowse.py            the launcher (setup / run / list)
config.example.json       copy to config.json to override exe paths, flags, template dirs
policies/                 optional enterprise-policy hardening + install notes
.github/workflows/        CI (cross-OS validation) + Release (builds standalone binaries)
.gitignore                keeps config.json and templates/ out of git
```

## Releasing / building binaries yourself

Standalone binaries are built by the **Release** workflow. To cut a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

That builds `cleanbrowse` for all three OSes with PyInstaller and attaches them to
a new GitHub Release. To build locally instead:

```bash
pip install pyinstaller
pyinstaller --onefile --name cleanbrowse cleanbrowse.py
# result in dist/cleanbrowse (or dist\cleanbrowse.exe on Windows)
```

## Testing checklist

Run this before tagging a release, and re-run the **Core behaviour** and
**Compatibility** sections on each OS you actually use. "Session" below means one
`run` → browse → close cycle.

### Core behaviour — the wipe/keep contract

- [ ] `setup --variant chrome` opens the template; installed extensions and prefs persist after closing.
- [ ] `setup --variant ungoogled` does the same for the ungoogled template.
- [ ] `list` shows both variants and correctly reports template "ready" vs "no template yet".
- [ ] After `run`, trusted extensions are present and enabled in the session.
- [ ] Extension **settings** carry over between sessions (e.g. password-manager vault config, content-blocker filter lists).
- [ ] Browsing data does **not** carry over: new session has no history, no cookies, no autofill, no open tabs from last time.
- [ ] Logging into a site, closing, and reopening requires logging in again (cookies/site storage gone).
- [ ] Downloads initiated in a session land in the real Downloads folder and survive the wipe (they're outside the temp profile).
- [ ] On exit, the temp profile directory is actually deleted (check the OS temp dir for leftover `cleanbrowse-*` folders).
- [ ] `run --keep` leaves the temp profile behind and prints its path (debug path works).
- [ ] Force-quitting / crashing the browser doesn't leave a `cleanbrowse-*` temp profile lingering forever (note any that do).

### Compatibility — the things LibreWolf-style hardening usually breaks

- [ ] **Streaming DRM:** Netflix/Spotify/Disney+ play video on the `chrome` variant (Widevine present).
- [ ] ungoogled variant: confirm DRM behaviour (expected to fail until Widevine is added) and that the README matches reality.
- [ ] **Google sign-in** works on the `chrome` variant, including phone 2FA.
- [ ] ungoogled variant: note whether Google flags it as an "insecure browser".
- [ ] A bank login completes without being blocked by fraud/device checks.
- [ ] "Sign in with Google" (federated SSO) on a third-party site completes (third-party cookie flow not broken).
- [ ] 2FA fires every session (expected) — confirm no stale "remember this device" token survives the wipe.
- [ ] DNS-over-HTTPS is active in-session (`chrome://settings/security` or a DNS leak check).

### Cross-platform

- [ ] Browser auto-detection finds Chrome on Windows, macOS, and Linux without a `config.json`.
- [ ] Auto-detection finds (or gracefully fails with a clear message for) ungoogled-chromium, which usually needs an explicit `executable` path.
- [ ] A `config.json` with custom `executable`, `flags`, and `template` paths is honoured.
- [ ] Paths with spaces (e.g. macOS `/Applications/Google Chrome.app/...`) work.
- [ ] Template built on one machine, used on the same machine — no "settings were reset" nag.

### Policy / fleet (optional feature)

- [ ] `policies/managed_policy.json` installed to the correct per-OS location is reflected at `chrome://policy`.
- [ ] Chrome vs ungoogled read their **different** policy locations correctly (both documented paths work).
- [ ] `ExtensionInstallForcelist` (when enabled) force-installs the listed extension by ID.
- [ ] Policy hardening does not break DRM, bank, or Google login (re-run Compatibility with policy applied).

### CI / release automation

- [ ] CI workflow passes on all three OSes for the supported Python versions.
- [ ] Pushing a `v*` tag builds all three binaries and attaches them to a GitHub Release.
- [ ] Each downloaded binary runs `--help` and `list` on its OS **without Python installed**.
- [ ] macOS/Linux binaries are executable after `chmod +x`; the Gatekeeper "open anyway" step is accurate.

## Caveats

- `run` launches one browser process and waits for it to exit before wiping. If your
  browser hands off to an already-running instance and returns immediately, the wipe
  would fire early — don't have the same variant already open when you `run`.
- This protects data **at rest between sessions**. It is not anti-forensics and not
  a Tor-style network-anonymity tool; your IP and network traffic are unchanged.

## Project status

**Status:** Waiting on Callum  
**Waiting on:** Callum: run the testing checklist  
**Notes:** Clean-session Chrome launcher. CI and release workflows are in place.  
**Last updated:** 2026-10-28

