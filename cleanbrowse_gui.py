#!/usr/bin/env python3
"""
cleanbrowse GUI -- a double-click front end for the clean-session launcher.

No terminal required. Buttons to start a clean (wiped-but-extensions-kept)
session, set up the template that holds your trusted extensions, and enable the
in-browser "restart clean" button (the companion extension's native host).

This module is normally launched by cleanbrowse.py when the program is started
with no command-line arguments (e.g. double-clicking the app), so one binary
serves as GUI, CLI, and native-messaging host.
"""

import sys
import threading

import cleanbrowse as engine


def launch():
    try:
        import tkinter as tk
        from tkinter import ttk, scrolledtext, messagebox
    except Exception:
        # No GUI toolkit / no display -- fall back to the CLI help.
        print("cleanbrowse: GUI unavailable (tkinter/display missing). "
              "Use the command line, e.g.  cleanbrowse run --variant chrome")
        return

    cfg = engine.load_config()

    root = tk.Tk()
    root.title("cleanbrowse")
    root.minsize(520, 440)

    # ----- header -----
    header = ttk.Frame(root, padding=(16, 14, 16, 6))
    header.pack(fill="x")
    ttk.Label(header, text="cleanbrowse",
              font=("TkDefaultFont", 16, "bold")).pack(anchor="w")
    ttk.Label(header,
              text="Each session keeps your trusted extensions and wipes "
                   "everything else\n(history, cookies, logins, site data) when "
                   "you close it.",
              foreground="#555").pack(anchor="w")

    body = ttk.Frame(root, padding=(16, 6, 16, 6))
    body.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="Ready.")

    log_widget = scrolledtext.ScrolledText(root, height=7, state="disabled",
                                            wrap="word", font=("TkFixedFont", 9))

    def log(line):
        log_widget.configure(state="normal")
        log_widget.insert("end", line.rstrip() + "\n")
        log_widget.see("end")
        log_widget.configure(state="disabled")

    def set_status(text):
        status_var.set(text)
        root.update_idletasks()

    # ----- helpers that run blocking work off the UI thread -----
    def run_engine_cli(args, note, on_done=None):
        set_status(note)
        log(f"$ cleanbrowse {' '.join(args)}")

        def worker():
            import subprocess
            try:
                proc = subprocess.run(engine.self_command(*args),
                                      capture_output=True, text=True)
                out = (proc.stdout or "") + (proc.stderr or "")
                for ln in out.splitlines():
                    root.after(0, log, ln)
                root.after(0, set_status, "Ready.")
                if on_done:
                    root.after(0, on_done)
            except Exception as e:
                root.after(0, log, f"error: {e}")
                root.after(0, set_status, "Something went wrong — see the log.")

        threading.Thread(target=worker, daemon=True).start()

    def start_session(variant):
        # Detached + self-cleaning: the session manages its own temp profile and
        # the GUI stays free to launch more.
        set_status(f"Launching clean {variant} session…")
        log(f"launching clean {variant} session")
        try:
            engine._spawn_detached(engine.self_command("run", "--variant", variant))
            root.after(1500, set_status, "Ready.")
        except Exception as e:
            log(f"error: {e}")
            set_status("Could not launch — see the log.")

    def variant_ready(name):
        vcfg = cfg["variants"][name]
        tmpl = engine.template_dir(vcfg, name)
        return tmpl.exists() and any(tmpl.glob("*"))

    # ----- section: start a clean session -----
    sec1 = ttk.LabelFrame(body, text="Start a clean session", padding=12)
    sec1.pack(fill="x", pady=(4, 10))

    start_buttons = {}
    hints = {}
    for name in ("chrome", "ungoogled"):
        row = ttk.Frame(sec1)
        row.pack(fill="x", pady=3)
        label = "Google Chrome" if name == "chrome" else "ungoogled-chromium"
        b = ttk.Button(row, text=f"Start clean {label}",
                       command=lambda n=name: start_session(n))
        b.pack(side="left")
        h = ttk.Label(row, text="", foreground="#a00")
        h.pack(side="left", padx=10)
        start_buttons[name] = b
        hints[name] = h

    # ----- section: set up -----
    sec2 = ttk.LabelFrame(body, text="Set up your trusted extensions", padding=12)
    sec2.pack(fill="x", pady=(0, 10))
    ttk.Label(sec2,
              text="Opens the template profile. Install only the extensions you "
                   "trust and set your\nprivacy preferences, then close the "
                   "browser to save. Do this once per browser.",
              foreground="#555").pack(anchor="w", pady=(0, 8))
    setup_row = ttk.Frame(sec2)
    setup_row.pack(fill="x")

    def refresh_ready():
        for name, b in start_buttons.items():
            if variant_ready(name):
                b.state(["!disabled"])
                hints[name].configure(text="")
            else:
                b.state(["disabled"])
                hints[name].configure(text="set up first →")

    for name in ("chrome", "ungoogled"):
        label = "Chrome" if name == "chrome" else "ungoogled"
        ttk.Button(setup_row, text=f"Set up {label}…",
                   command=lambda n=name: run_engine_cli(
                       ["setup", "--variant", n],
                       f"Setting up {n} template — close the browser when done…",
                       on_done=refresh_ready)
                   ).pack(side="left", padx=(0, 8))

    # ----- section: in-browser button -----
    sec3 = ttk.LabelFrame(body, text="In-browser button (optional)", padding=12)
    sec3.pack(fill="x", pady=(0, 10))
    ttk.Label(sec3,
              text="Lets the companion extension start a clean session from a "
                   "button inside the\nbrowser. Click once, then load the "
                   "extension (see extension/README.md).",
              foreground="#555").pack(anchor="w", pady=(0, 8))
    ttk.Button(sec3, text="Enable in-browser button…",
               command=lambda: run_engine_cli(
                   ["install-host"], "Registering the in-browser button…")
               ).pack(side="left")

    # ----- status bar + log -----
    ttk.Separator(root).pack(fill="x")
    statusbar = ttk.Frame(root, padding=(16, 4))
    statusbar.pack(fill="x")
    ttk.Label(statusbar, textvariable=status_var, foreground="#333").pack(side="left")
    log_widget.pack(fill="both", expand=False, padx=16, pady=(0, 12))

    refresh_ready()
    log("cleanbrowse ready. Set up a browser, then start a clean session.")
    root.mainloop()


if __name__ == "__main__":
    launch()
