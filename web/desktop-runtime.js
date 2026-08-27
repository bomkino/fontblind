"use strict";

(function initialiseDesktopRuntime(root) {
  class DesktopRuntimeError extends Error {}

  function exactObject(value, keys, label) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new DesktopRuntimeError(`${label} must be an object.`);
    }
    const actual = Object.keys(value).sort();
    const expected = [...keys].sort();
    if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
      throw new DesktopRuntimeError(`${label} returned unexpected fields.`);
    }
  }

  function validateSessionEnvelope(value) {
    exactObject(value, ["ok", "session", "can_quit"], "Desktop session");
    if (value.ok !== true || typeof value.session !== "string" || !value.session ||
        typeof value.can_quit !== "boolean") {
      throw new DesktopRuntimeError("Desktop session was malformed.");
    }
    return Object.freeze({ session: value.session, canQuit: value.can_quit });
  }

  function validateShutdownEnvelope(value) {
    exactObject(value, ["ok", "shutdown"], "Desktop shutdown");
    if (value.ok !== true || value.shutdown !== true) {
      throw new DesktopRuntimeError("Desktop shutdown was not confirmed.");
    }
    return true;
  }

  function delay(milliseconds) {
    return new Promise((resolve) => root.setTimeout(resolve, milliseconds));
  }

  async function waitForShutdown(fetcher, attempts = 30) {
    if (typeof fetcher !== "function" || !Number.isInteger(attempts) || attempts < 1 || attempts > 100) {
      throw new DesktopRuntimeError("Desktop shutdown probe was invalid.");
    }
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      await delay(100);
      try {
        await fetcher("/api/session", {
          cache: "no-store",
          credentials: "same-origin"
        });
      } catch (_) {
        return true;
      }
    }
    return false;
  }

  function closedNotice() {
    const notice = document.createElement("section");
    notice.className = "app-closed-notice";
    notice.setAttribute("role", "status");
    notice.setAttribute("aria-live", "polite");
    const title = document.createElement("strong");
    title.textContent = "FONTBLIND IS CLOSED";
    const copy = document.createElement("span");
    copy.textContent = "Local files and workers were removed. You can close this tab.";
    notice.append(title, copy);
    return notice;
  }

  function mountQuitControl(session) {
    const footer = document.querySelector("footer");
    if (!footer || footer.querySelector("[data-app-quit]")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "app-quit";
    button.dataset.appQuit = "true";
    button.textContent = "QUIT FONTBLIND";
    button.setAttribute("aria-label", "Quit FontBlind and remove local files");
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = "QUITTING…";
      try {
        const response = await root.fetch("/api/shutdown", {
          method: "POST",
          headers: { "X-FontBlind-Session": session },
          cache: "no-store",
          credentials: "same-origin"
        });
        const data = await response.json();
        if (!response.ok) throw new DesktopRuntimeError("Desktop shutdown failed safely.");
        validateShutdownEnvelope(data);
        if (!await waitForShutdown(root.fetch.bind(root))) {
          throw new DesktopRuntimeError("Desktop shutdown did not complete.");
        }
        document.body.dataset.appStopped = "true";
        document.body.prepend(closedNotice());
        button.textContent = "CLOSED";
      } catch (_) {
        button.disabled = false;
        button.textContent = "QUIT FONTBLIND";
        const existing = document.querySelector("[data-desktop-runtime-error]");
        if (existing) existing.remove();
        const error = document.createElement("span");
        error.dataset.desktopRuntimeError = "true";
        error.className = "app-quit-error";
        error.setAttribute("role", "alert");
        error.textContent = "FontBlind could not close itself. Close the launcher process manually.";
        footer.append(error);
      }
    });
    footer.append(button);
  }

  async function boot() {
    try {
      const response = await root.fetch("/api/session", {
        cache: "no-store",
        credentials: "same-origin"
      });
      if (!response.ok) return;
      const session = validateSessionEnvelope(await response.json());
      if (session.canQuit) mountQuitControl(session.session);
    } catch (_) {
      // A source launch or native wrapper does not need the desktop control.
      // Failure here must never block the font workbenches.
    }
  }

  const exported = Object.freeze({ DesktopRuntimeError, validateSessionEnvelope, validateShutdownEnvelope, waitForShutdown });
  root.FontBlindDesktopRuntime = exported;
  if (typeof module !== "undefined" && module.exports) module.exports = exported;
  if (typeof document === "undefined" || typeof root.fetch !== "function") return;
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    void boot();
  }
})(typeof globalThis !== "undefined" ? globalThis : window);
