#!/usr/bin/env python3
"""
cleanbrowse -- ephemeral, hardened Chromium sessions that keep your trusted extensions.

Model
-----
A "template" profile holds your trusted extensions and your hardened settings.
Each `run`:
  1. copies that template into a throwaway profile directory,
  2. scrubs any website/user data (cookies, history, logins, site storage) from
     the copy so the session starts clean,
  3. launches the browser against the throwaway profile,
  4. destroys the throwaway profile when the browser closes.

Extensions and their settings survive between sessions; browsing data never does.
This is the "wipe everything but the extensions" behaviour, done at the right layer.

Set the template up once with `setup`, then use `run` for daily browsing.

Supports Google Chrome and ungoogled-chromium on Windows, macOS and Linux.
Requires Python 3.8+. No third-party packages.

Usage
-----
  python cleanbrowse.py setup [--variant chrome]      # configure the golden template
  python cleanbrowse.py run   [--variant chrome] [--] [extra browser args/URLs]
  python cleanbrowse.py list                          # show configured variants
  python cleanbrowse.py run --variant chrome --keep   # don't delete the temp profile (debug)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP_NAME = "cleanbrowse"
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
DEFAULT_TEMPLATES_DIR = SCRIPT_DIR / "templates"

# Companion Chrome extension: deterministic ID (from the embedded manifest key in
# extension/manifest.json). The native-messaging host only accepts connections
# from this extension. If you rebuild the extension with a different key, update
# both places.
HOST_NAME = "dev.nynex.cleanbrowse"
EXTENSION_ID = "aojnagceggjfaafhnpmcembajhoinhkk"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"

# Subdirectories/files the browser regenerates on its own -- never worth copying.
# Matched by basename anywhere in the profile tree.
COPY_IGNORE = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
    "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "GraphiteDawnCache",
    "Crashpad", "Crash Reports", "component_crx_cache", "extensions_crx_cache",
    "Safe Browsing", "optimization_guide_model_store", "Service Worker",
    "SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile",
}

# Website/user data scrubbed from the WORKING COPY after copying, so a session
# starts clean even if the template picked something up. Paths are relative to
# the profile root; "Default" is the standard profile directory. Extension data
# lives in Default/Local Extension Settings, Sync Extension Settings, Extension
# State/Rules, etc. -- none of which appear here, so extensions are preserved.
SCRUB_PATHS = [
    "Default/Cookies", "Default/Cookies-journal",
    "Default/History", "Default/History-journal", "Default/History Provider Cache",
    "Default/Visited Links",
    "Default/Top Sites", "Default/Top Sites-journal",
    "Default/Login Data", "Default/Login Data-journal",
    "Default/Login Data For Account", "Default/Login Data For Account-journal",
    "Default/Web Data", "Default/Web Data-journal",
    "Default/Shortcuts", "Default/Shortcuts-journal",
    "Default/Favicons", "Default/Favicons-journal",
    "Default/Network",
    "Default/Sessions", "Default/Session Storage",
    "Default/Local Storage", "Default/IndexedDB", "Default/File System",
    "Default/blob_storage", "Default/WebStorage",
    "Default/AutofillStrikeDatabase",
    "Default/Download Service",
    "Default/data_reduction_proxy_leveldb",
]

# Compatibility-safe flags applied every run. Deliberately minimal: nothing here
# breaks streaming DRM, bank fraud checks, or federated (Google) sign-in. Heavier
# hardening lives in the template's own settings (set during `setup`) or in an
# optional managed policy -- see policies/ and the README.
BASE_FLAGS = [
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-sync",
]

DEFAULT_CONFIG = {
    "default_variant": "chrome",
    "variants": {
        "chrome": {"browser": "chrome", "executable": None, "template": None, "flags": []},
        "ungoogled": {"browser": "ungoogled", "executable": None, "template": None, "flags": []},
    },
}


# --------------------------------------------------------------------------- #
# Browser executable auto-detection
# --------------------------------------------------------------------------- #

def _candidates(browser: str):
    """Return likely executable paths for (browser, current OS), best first."""
    plat = sys.platform
    env = os.environ
    if browser == "chrome":
        if plat.startswith("win"):
            roots = [env.get("ProgramFiles", r"C:\Program Files"),
                     env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     env.get("LocalAppData", "")]
            return [str(Path(r) / "Google" / "Chrome" / "Application" / "chrome.exe")
                    for r in roots if r]
        if plat == "darwin":
            return ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")]
        return [shutil.which("google-chrome-stable"), shutil.which("google-chrome"),
                "/opt/google/chrome/chrome"]
    # ungoogled-chromium -- paths vary a lot by packager; auto-detect is best-effort.
    if plat.startswith("win"):
        roots = [env.get("ProgramFiles", r"C:\Program Files"),
                 env.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 env.get("LocalAppData", "")]
        out = []
        for r in roots:
            if not r:
                continue
            out.append(str(Path(r) / "Chromium" / "Application" / "chrome.exe"))
            out.append(str(Path(r) / "ungoogled-chromium" / "chrome.exe"))
        return out
    if plat == "darwin":
        return ["/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Ungoogled Chromium.app/Contents/MacOS/Chromium"]
    return [shutil.which("ungoogled-chromium"), shutil.which("chromium"),
            shutil.which("chromium-browser"), "/usr/lib/chromium/chromium"]


def resolve_executable(variant_cfg: dict, name: str) -> str:
    explicit = variant_cfg.get("executable")
    if explicit:
        if not Path(explicit).exists():
            die(f"Configured executable for '{name}' does not exist:\n  {explicit}")
        return explicit
    for cand in _candidates(variant_cfg["browser"]):
        if cand and Path(cand).exists():
            return cand
    die(f"Could not auto-detect the browser for variant '{name}'.\n"
        f"Set its \"executable\" path in {CONFIG_PATH.name} "
        f"(copy config.example.json to config.json).")


# --------------------------------------------------------------------------- #
# Config / templates
# --------------------------------------------------------------------------- #

def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            die(f"{CONFIG_PATH.name} is not valid JSON: {e}")
        cfg["default_variant"] = user.get("default_variant", cfg["default_variant"])
        for vname, vcfg in user.get("variants", {}).items():
            base = cfg["variants"].get(vname, {"browser": vcfg.get("browser", "chrome"),
                                               "executable": None, "template": None, "flags": []})
            base.update({k: v for k, v in vcfg.items() if v is not None or k == "executable"})
            cfg["variants"][vname] = base
    return cfg


def template_dir(variant_cfg: dict, name: str) -> Path:
    t = variant_cfg.get("template")
    return Path(t).expanduser() if t else (DEFAULT_TEMPLATES_DIR / name)


def pick_variant(cfg: dict, requested):
    name = requested or cfg["default_variant"]
    if name not in cfg["variants"]:
        die(f"Unknown variant '{name}'. Known: {', '.join(cfg['variants'])}")
    return name, cfg["variants"][name]


# --------------------------------------------------------------------------- #
# Profile copy + scrub
# --------------------------------------------------------------------------- #

def _ignore(_dir, names):
    return [n for n in names if n in COPY_IGNORE]


def build_working_profile(template: Path) -> Path:
    work = Path(tempfile.mkdtemp(prefix=f"{APP_NAME}-"))
    profile = work / "profile"
    shutil.copytree(template, profile, ignore=_ignore, symlinks=True)
    for rel in SCRUB_PATHS:
        p = profile / rel
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    return work  # caller launches against work/"profile", deletes work on exit


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_run(cfg, args):
    name, vcfg = pick_variant(cfg, args.variant)
    exe = resolve_executable(vcfg, name)
    template = template_dir(vcfg, name)
    if not (template / "Default").exists() and not any(template.glob("*")):
        die(f"No template for '{name}' yet at:\n  {template}\n"
            f"Run:  python {Path(__file__).name} setup --variant {name}")

    work = build_working_profile(template)
    profile = work / "profile"
    cmd = [exe, f"--user-data-dir={profile}", *BASE_FLAGS, *vcfg.get("flags", []), *args.extra]
    print(f"[{APP_NAME}] {name}: launching clean session (extensions kept, browsing data wiped on exit)")
    try:
        proc = subprocess.Popen(cmd)
        proc.wait()
    except KeyboardInterrupt:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            pass
    finally:
        if args.keep:
            print(f"[{APP_NAME}] --keep set; working profile left at: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
            print(f"[{APP_NAME}] session destroyed.")


def cmd_setup(cfg, args):
    name, vcfg = pick_variant(cfg, args.variant)
    exe = resolve_executable(vcfg, name)
    template = template_dir(vcfg, name)
    template.mkdir(parents=True, exist_ok=True)
    print(f"[{APP_NAME}] SETUP MODE for '{name}'. This window edits your PERSISTENT template:")
    print(f"    {template}")
    print("    - Install only the extensions you trust.")
    print("    - Set your hardened preferences (Settings > Privacy: DNS-over-HTTPS on,")
    print("      metrics/telemetry off, browser sign-in/sync off, Safe Browsing = Standard).")
    print("    - Do NOT sign into personal accounts here; that belongs in `run` sessions.")
    print("    Close the browser when done to freeze the template.\n")
    cmd = [exe, f"--user-data-dir={template}", "--no-first-run", "--no-default-browser-check"]
    subprocess.Popen(cmd).wait()
    print(f"[{APP_NAME}] template frozen. Use:  python {Path(__file__).name} run --variant {name}")


def cmd_list(cfg, _args):
    print(f"default variant: {cfg['default_variant']}\n")
    for name, vcfg in cfg["variants"].items():
        exe = vcfg.get("executable") or "(auto-detect)"
        tmpl = template_dir(vcfg, name)
        ready = "ready" if tmpl.exists() and any(tmpl.glob("*")) else "no template yet"
        print(f"  {name}: browser={vcfg['browser']}  template={tmpl} [{ready}]")
        print(f"         executable={exe}")


# --------------------------------------------------------------------------- #
# Self-invocation helper (works both from source and as a frozen binary)
# --------------------------------------------------------------------------- #

def self_command(*cli_args):
    """Argv that re-runs THIS program with the given cleanbrowse args."""
    if getattr(sys, "frozen", False):          # PyInstaller binary
        return [sys.executable, *cli_args]
    return [sys.executable, str(Path(__file__).resolve()), *cli_args]


def _spawn_detached(cmd):
    """Launch cmd fully detached so it outlives this process."""
    kwargs = {}
    if sys.platform.startswith("win"):
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


# --------------------------------------------------------------------------- #
# Native-messaging host (the companion extension talks to this over stdio)
# --------------------------------------------------------------------------- #

def _nm_read():
    """Read one native message: 4-byte little-endian length + UTF-8 JSON."""
    import struct
    raw_len = sys.stdin.buffer.read(4)
    if len(raw_len) < 4:
        return None  # stdin closed -> extension disconnected
    (length,) = struct.unpack("<I", raw_len)
    data = sys.stdin.buffer.read(length)
    return json.loads(data.decode("utf-8"))


def _nm_write(obj):
    """Write one native message back to the extension."""
    import struct
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def run_native_host():
    """
    Entry point when the browser launches us as a native-messaging host.
    The browser passes the calling extension's origin as an argument; nothing
    may be printed to stdout except framed messages, or the protocol breaks.

    Commands (JSON from the extension):
      {"command": "ping"}                      -> {"ok": true, "pong": true}
      {"command": "list"}                      -> {"ok": true, "variants": {...}}
      {"command": "run", "variant": "chrome"}  -> launches a clean session,
                                                  returns {"ok": true} immediately
    """
    cfg = load_config()
    while True:
        try:
            msg = _nm_read()
        except Exception as e:
            _nm_write({"ok": False, "error": f"bad message: {e}"})
            return
        if msg is None:
            return
        cmd = (msg or {}).get("command")
        try:
            if cmd == "ping":
                _nm_write({"ok": True, "pong": True, "app": APP_NAME})
            elif cmd == "list":
                out = {}
                for name, vcfg in cfg["variants"].items():
                    tmpl = template_dir(vcfg, name)
                    out[name] = {"browser": vcfg["browser"],
                                 "ready": tmpl.exists() and any(tmpl.glob("*"))}
                _nm_write({"ok": True, "variants": out})
            elif cmd == "run":
                variant = msg.get("variant") or cfg["default_variant"]
                if variant not in cfg["variants"]:
                    _nm_write({"ok": False, "error": f"unknown variant '{variant}'"})
                    continue
                # Launch a detached child running the normal (blocking, self-
                # cleaning) `run` command, so the new session manages its own
                # temp-profile lifecycle and we can return at once.
                _spawn_detached(self_command("run", "--variant", variant))
                _nm_write({"ok": True, "launched": variant})
            else:
                _nm_write({"ok": False, "error": f"unknown command '{cmd}'"})
        except Exception as e:
            _nm_write({"ok": False, "error": str(e)})


# --------------------------------------------------------------------------- #
# Native-host installation (wires the browser up to call this program)
# --------------------------------------------------------------------------- #

def _host_manifest_dirs():
    """Per-OS directories where Chrome/Chromium look for host manifests."""
    home = Path.home()
    plat = sys.platform
    if plat == "darwin":
        base = home / "Library" / "Application Support"
        return {"chrome": base / "Google/Chrome/NativeMessagingHosts",
                "chromium": base / "Chromium/NativeMessagingHosts"}
    if plat.startswith("win"):
        return {}  # Windows uses the registry, handled separately
    base = home / ".config"
    return {"chrome": base / "google-chrome/NativeMessagingHosts",
            "chromium": base / "chromium/NativeMessagingHosts"}


def _make_host_launcher() -> Path:
    """
    Return an absolute path to an executable the browser can launch as the host.
    Frozen binary: that's us. From source: write a tiny shim that runs us via
    the current Python, since a manifest 'path' must be a single executable.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()

    shim_dir = SCRIPT_DIR / ".host"
    shim_dir.mkdir(exist_ok=True)
    script = str(Path(__file__).resolve())
    if sys.platform.startswith("win"):
        shim = shim_dir / "cleanbrowse-host.bat"
        shim.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
                        encoding="utf-8")
    else:
        shim = shim_dir / "cleanbrowse-host.sh"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
                        encoding="utf-8")
        os.chmod(shim, 0o755)
    return shim.resolve()


def _host_manifest(launcher: Path) -> dict:
    return {
        "name": HOST_NAME,
        "description": "cleanbrowse native-messaging host (clean-session launcher)",
        "path": str(launcher),
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN],
    }


def cmd_install_host(cfg, args):
    launcher = _make_host_launcher()
    manifest = _host_manifest(launcher)
    written = []

    if sys.platform.startswith("win"):
        import winreg
        # Store the manifest next to the launcher, then point the registry at it.
        man_path = (SCRIPT_DIR / ".host" / f"{HOST_NAME}.json").resolve()
        man_path.parent.mkdir(exist_ok=True)
        man_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for root in (r"Software\Google\Chrome\NativeMessagingHosts",
                     r"Software\Chromium\NativeMessagingHosts"):
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, root + "\\" + HOST_NAME)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(man_path))
            winreg.CloseKey(key)
            written.append(root)
    else:
        for browser, d in _host_manifest_dirs().items():
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{HOST_NAME}.json"
            path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            written.append(str(path))

    print(f"[{APP_NAME}] native host installed; launcher:\n  {launcher}")
    for w in written:
        print(f"  registered: {w}")
    print(f"[{APP_NAME}] load the extension in your browser (see extension/README.md),")
    print(f"             then its ID must be {EXTENSION_ID}.")
    return written


def cmd_uninstall_host(cfg, args):
    removed = []
    if sys.platform.startswith("win"):
        import winreg
        for root in (r"Software\Google\Chrome\NativeMessagingHosts",
                     r"Software\Chromium\NativeMessagingHosts"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, root + "\\" + HOST_NAME)
                removed.append(root)
            except FileNotFoundError:
                pass
    else:
        for d in _host_manifest_dirs().values():
            path = d / f"{HOST_NAME}.json"
            if path.exists():
                path.unlink()
                removed.append(str(path))
    print(f"[{APP_NAME}] native host removed ({len(removed)} entr{'y' if len(removed)==1 else 'ies'}).")
    for r in removed:
        print(f"  {r}")
    return removed


# --------------------------------------------------------------------------- #

def die(msg: str):
    print(f"[{APP_NAME}] error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    # When the browser launches us as a native-messaging host, it passes the
    # calling extension's origin (chrome-extension://...) as an argument. Detect
    # that before argparse and switch into the stdio host loop.
    if any(a.startswith("chrome-extension://") for a in sys.argv[1:]):
        run_native_host()
        return

    # No arguments (e.g. double-clicking the app) -> open the GUI front end.
    if len(sys.argv) == 1:
        try:
            import cleanbrowse_gui
            cleanbrowse_gui.launch()
            return
        except Exception:
            pass  # fall through to CLI help if the GUI can't be shown

    p = argparse.ArgumentParser(prog=APP_NAME, description="Ephemeral hardened Chromium with persistent trusted extensions.")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="launch a clean ephemeral session")
    pr.add_argument("--variant", help="which configured variant to run")
    pr.add_argument("--keep", action="store_true", help="keep the temp profile after exit (debug)")
    pr.add_argument("extra", nargs=argparse.REMAINDER, help="extra args/URLs passed to the browser")

    ps = sub.add_parser("setup", help="edit the persistent template (install extensions, set prefs)")
    ps.add_argument("--variant", help="which configured variant to set up")

    sub.add_parser("list", help="show configured variants and template status")

    sub.add_parser("install-host", help="register the native-messaging host for the companion extension")
    sub.add_parser("uninstall-host", help="remove the native-messaging host registration")

    args = p.parse_args()
    cfg = load_config()
    {
        "run": cmd_run, "setup": cmd_setup, "list": cmd_list,
        "install-host": cmd_install_host, "uninstall-host": cmd_uninstall_host,
    }[args.command](cfg, args)


if __name__ == "__main__":
    main()
