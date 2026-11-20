// cleanbrowse control -- popup logic.
//
// Talks to the native-messaging host "dev.nynex.cleanbrowse" (the cleanbrowse
// program, registered by its "Enable in-browser button" action / `install-host`).
// The host does the real work; this popup is just the trigger.

const HOST = "dev.nynex.cleanbrowse";

const statusEl = document.getElementById("status");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

// Promise wrapper around chrome.runtime.sendNativeMessage.
function send(message) {
  return new Promise((resolve, reject) => {
    chrome.runtime.sendNativeMessage(HOST, message, (response) => {
      const err = chrome.runtime.lastError;
      if (err) {
        reject(new Error(err.message));
      } else {
        resolve(response);
      }
    });
  });
}

// On open, ping the host so we can tell the user if it isn't wired up yet.
async function checkHost() {
  try {
    const r = await send({ command: "ping" });
    if (r && r.ok) {
      setStatus("Launcher connected.", "ok");
    } else {
      setStatus("Launcher responded unexpectedly.", "err");
    }
  } catch (e) {
    setStatus(
      "Launcher not found. Open the cleanbrowse app and click " +
      "“Enable in-browser button”, then reload this extension.",
      "err"
    );
  }
}

async function startSession(variant) {
  setStatus(`Starting clean ${variant} session…`);
  try {
    const r = await send({ command: "run", variant });
    if (r && r.ok) {
      setStatus(`Clean ${variant} session launched.`, "ok");
      return true;
    }
    setStatus((r && r.error) ? r.error : "Could not start session.", "err");
  } catch (e) {
    setStatus("Launcher not found — enable it from the cleanbrowse app.", "err");
  }
  return false;
}

// Close every window in this instance -> effectively ends the current (dirty)
// session. The freshly launched clean session is a separate browser instance.
function closeThisInstance() {
  chrome.windows.getAll({}, (wins) => {
    for (const w of wins) {
      chrome.windows.remove(w.id);
    }
  });
}

document.getElementById("run-chrome").addEventListener("click", () => {
  startSession("chrome");
});

document.getElementById("run-ungoogled").addEventListener("click", () => {
  startSession("ungoogled");
});

document.getElementById("restart").addEventListener("click", async () => {
  const ok = await startSession("chrome");
  if (ok) {
    // Give the new instance a moment to come up before we close this one.
    setStatus("Clean session launched — closing this one…", "ok");
    setTimeout(closeThisInstance, 1200);
  }
});

checkHost();
