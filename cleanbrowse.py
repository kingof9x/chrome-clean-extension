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

def die(msg: str):
    print(f"[{APP_NAME}] error: {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    p = argparse.ArgumentParser(prog=APP_NAME, description="Ephemeral hardened Chromium with persistent trusted extensions.")
    sub = p.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="launch a clean ephemeral session")
    pr.add_argument("--variant", help="which configured variant to run")
    pr.add_argument("--keep", action="store_true", help="keep the temp profile after exit (debug)")
    pr.add_argument("extra", nargs=argparse.REMAINDER, help="extra args/URLs passed to the browser")

    ps = sub.add_parser("setup", help="edit the persistent template (install extensions, set prefs)")
    ps.add_argument("--variant", help="which configured variant to set up")

    sub.add_parser("list", help="show configured variants and template status")

    args = p.parse_args()
    cfg = load_config()
    {"run": cmd_run, "setup": cmd_setup, "list": cmd_list}[args.command](cfg, args)


if __name__ == "__main__":
    main()
