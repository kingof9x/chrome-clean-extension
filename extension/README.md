# cleanbrowse control — companion extension

A tiny toolbar button that starts a clean cleanbrowse session from inside the
browser. It does **not** do the wiping itself (a Chrome extension can't) — it
sends a message to the cleanbrowse app, which does the work.

## What it can and can't do

- **Can:** start a fresh clean session, and "end this session & restart clean"
  (launches a new clean instance, then closes the current windows).
- **Can't:** start the very first session (you must open the cleanbrowse app for
  that — the browser has to be running before you can click a button in it), and
  can't work until the native host is enabled (one click, below).

## Setup (once)

1. **Enable the connection.** Open the cleanbrowse app and click
   **"Enable in-browser button…"** (or run `cleanbrowse install-host`). This
   registers the native-messaging host the extension talks to.
2. **Load the extension:**
   - Go to `chrome://extensions` (or `chromium://extensions`).
   - Turn on **Developer mode** (top right).
   - Click **Load unpacked** and select this `extension/` folder.
   - Confirm the extension's ID is **`aojnagceggjfaafhnpmcembajhoinhkk`**. It must
     match, because the native host only accepts that ID. (The fixed ID comes from
     the `key` in `manifest.json`.)
3. Click the cleanbrowse toolbar icon. The status line should say
   **"Launcher connected."**

For the button to be present in your clean sessions, install this extension in
the **template** profile too (run the app's **Set up…**, load it there, close).

## If it says "Launcher not found"

- Make sure you did step 1 (enable the connection) **for the same browser** you're
  loading the extension into — Chrome and ungoogled-chromium are registered
  separately.
- Reload the extension after enabling the host.
- Confirm the extension ID matches the one above.

## Rebuilding with your own ID

The deterministic ID is baked in via the `key` field in `manifest.json`. If you
generate your own key, update `EXTENSION_ID` in `cleanbrowse.py` to match, or the
host will reject the connection.
