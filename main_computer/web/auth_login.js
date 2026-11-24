"use strict";
(() => {
  const pickButton = document.getElementById("auth-proof-file-button");
  const pickInput = document.getElementById("auth-proof-file-input");
  const pathInput = document.getElementById("auth-proof-location");
  const copyPathButton = document.getElementById("auth-proof-copy-path");
  const status = document.getElementById("status");

  function show(message, state = "normal") {
    status.textContent = message;
    status.dataset.state = state;
  }

  async function api(path, options = {}) {
    const response = await fetch("/api/auth/bootstrap/" + path, {
      credentials: "same-origin", cache: "no-store", ...options
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error || `Authentication failed (${response.status})`);
    return result;
  }

  function fromB64(value) {
    if (typeof value !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value)) {
      throw Error("Invalid MC proof key encoding");
    }
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=";
    const bytes = Uint8Array.from(atob(padded), c => c.charCodeAt(0));
    if (bytes.length !== 32) throw Error("Invalid MC proof key length");
    return bytes;
  }

  function toB64(bytes) {
    return btoa(String.fromCharCode(...new Uint8Array(bytes)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  async function authenticate(file) {
    if (file.size > 4096) throw Error("The proof file is larger than expected");
    const proof = JSON.parse(await file.text());
    if (proof.type !== "mc-proof-v2") throw Error("This is not an MC authentication proof file");
    const bytes = fromB64(proof.key_b64);
    try {
      const challenge = await api("challenge");
      if (challenge.algorithm !== "HMAC-SHA256") throw Error("Unsupported challenge algorithm");
      const payload = new TextEncoder().encode(
        `MC-AUTH-BOOTSTRAP-V1\n${challenge.challenge_id}\n${challenge.nonce}\n`);
      const key = await crypto.subtle.importKey("raw", bytes,
        { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
      const signature = toB64(await crypto.subtle.sign("HMAC", key, payload));
      const result = await api("verify", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ challenge_id: challenge.challenge_id, signature })
      });
      if (!result.authenticated) throw Error("Authentication did not establish a session");
    } finally {
      bytes.fill(0); // JS strings remain garbage-collected; never persist the proof.
    }
  }

  copyPathButton.addEventListener("click", async () => {
    try {
      // Only the path is copied, NEVER the proof bytes or the permanent key.
      await navigator.clipboard.writeText(pathInput.value);
      show("Proof file path copied. Click Select, paste into the File name field, and press Enter.");
    } catch (_) {
      pathInput.focus();
      pathInput.select();
      show("Copy the selected path with Ctrl+C, then click Select and paste it into File name.");
    }
  });

  pickButton.addEventListener("click", () => {
    pickInput.value = "";
    pickInput.click();
  });

  pickInput.addEventListener("change", async () => {
    const file = pickInput.files && pickInput.files[0];
    if (!file) return;
    pickButton.disabled = true;
    show("Verifying proof against the secure MC session authority…");
    try {
      await authenticate(file);
      show("Authentication successful. Returning to Main Computer…", "success");
      // Re-enter the HTTPS login route: the server validates the newly issued
      // HttpOnly cookie and redirects to the safe requested MC page.
      window.location.replace(window.location.href);
    } catch (err) {
      show(`Authentication denied: ${err.message}`, "error");
      pickButton.disabled = false;
    } finally {
      pickInput.value = "";
    }
  });

  const loginHosts = new Set(["mc.test", "dev.main-computer.localhost",
    "unleashed.main-computer.localhost", "debug.main-computer.localhost", "safe.main-computer.localhost"]);
  if (location.protocol !== "https:" || !loginHosts.has(location.hostname)) {
    pickButton.disabled = true;
    show("MC authentication requires the trusted HTTPS endpoint.", "error");
    return;
  }
  api("session").then(result => {
    if (result.authenticated) {
      show("Session restored. Returning to Main Computer…", "success");
      window.location.replace(window.location.href);
    } else if (result.proof_reissued === true) {
      show("Main Computer regenerated the MC proof file after it went missing or " +
           "its format changed. Previous sessions were revoked. Select " +
           "AAA_AUTH_MAIN_COMPUTER.json to authenticate again.");
    } else if (result.proof_available === false) {
      pickButton.disabled = true;
      show("The MC proof file is missing or invalid, and MC could not restore it " +
           "from the protected authority. Authentication remains blocked; " +
           "check the MC authentication service diagnostics.", "error");
    } else {
      show("Select the saved MC proof file to authenticate.");
    }
  }).catch(err => show(`MC authentication service unavailable: ${err.message}`, "error"));
})();
