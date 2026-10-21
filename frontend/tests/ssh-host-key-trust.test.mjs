import assert from "node:assert/strict";
import test from "node:test";

const hostKeyTrustModule = await import("../src/app/ssh-host-key-trust.js").catch(() => null);

test("SSH host-key trust prompt identifies the device, algorithm, and SHA256 fingerprint", () => {
  assert.equal(typeof hostKeyTrustModule?.formatSshHostKeyTrustPrompt, "function");

  const prompt = hostKeyTrustModule.formatSshHostKeyTrustPrompt({
    ip: "10.99.60.5",
    hostKeyAlgorithm: "rsa-sha2-512",
    hostFingerprint: "SHA256:first-fingerprint",
    changed: false,
  });

  assert.match(prompt, /10\.99\.60\.5/);
  assert.match(prompt, /rsa-sha2-512/);
  assert.match(prompt, /SHA256:first-fingerprint/);
});
