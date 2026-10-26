# Optional enforced hardening (enterprise policy)

`managed_policy.json` applies `cleanbrowse`'s hardening as **locked Chromium
policy** instead of relying on template preferences. Two reasons to use it:

- You want settings *enforced* (greyed out, not just preset) — useful on client
  machines you manage.
- You want to **force-install extensions by ID** regardless of the template
  (uncomment `_ExtensionInstallForcelist` and drop the leading underscore).

Everything here is compatibility-safe: it does **not** break streaming DRM, bank
fraud checks, or "Sign in with Google".

> **Important:** Google Chrome and ungoogled-chromium read **different** policy
> locations. ungoogled-chromium uses the `Chromium` paths, not the `Google/Chrome`
> ones. Install to the location that matches the variant you're hardening.

## Windows

Policies live in the registry. The JSON here is for reference/editing; convert each
key to a registry value under:

- Chrome: `HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Google\Chrome`
- ungoogled-chromium: `HKEY_LOCAL_MACHINE\SOFTWARE\Policies\Chromium`

(Booleans → `REG_DWORD` 0/1; numbers → `REG_DWORD`; strings → `REG_SZ`; lists like
the forcelist → a numbered subkey `…\ExtensionInstallForcelist\1`, `\2`, …)

## macOS

Install as a configuration profile (`.mobileconfig`) or write the domain defaults:

- Chrome: domain `com.google.Chrome`
- ungoogled-chromium: domain `org.chromium.Chromium`

```bash
# example: enforce one key (repeat per key, or ship a .mobileconfig)
defaults write com.google.Chrome SyncDisabled -bool true
```

## Linux

Drop the JSON into the managed-policy directory (create it if needed):

- Chrome: `/etc/opt/chrome/policies/managed/cleanbrowse.json`
- Chromium / ungoogled-chromium: `/etc/chromium/policies/managed/cleanbrowse.json`

```bash
sudo mkdir -p /etc/chromium/policies/managed
sudo cp managed_policy.json /etc/chromium/policies/managed/cleanbrowse.json
```

Strip the `_comment` / `_`-prefixed helper keys before deploying — they're
documentation, not real policies. Verify what actually loaded by visiting
`chrome://policy` (or `chrome://policy` in ungoogled-chromium) and clicking Reload.
