import assert from "node:assert/strict";
import test from "node:test";

let waitForBackend;
try {
  ({ waitForBackend } = await import("../src/app/backend-ready.mjs"));
} catch {
  waitForBackend = undefined;
}

test("retries a local backend request until the sidecar becomes ready", async () => {
  assert.equal(typeof waitForBackend, "function");

  let attempts = 0;
  const result = await waitForBackend(
    async () => {
      attempts += 1;
      if (attempts < 3) throw new Error("sidecar is still starting");
      return { initialized: false };
    },
    { maxAttempts: 3, delay: async () => {} },
  );

  assert.deepEqual(result, { initialized: false });
  assert.equal(attempts, 3);
});

test("the default retry budget covers a cold sidecar start", async () => {
  // The PyInstaller onefile sidecar unpacks itself before it binds the port.
  // Measured on this machine over 6 cold starts: 9.31s / 10.34s / 10.33s /
  // 10.34s / 10.85s / 11.84s. A 10s budget therefore loses the race most of
  // the time and the window stays on "??????????".
  let attempts = 0;
  let waited = 0;
  await assert.rejects(() =>
    waitForBackend(
      async () => {
        attempts += 1;
        throw new Error("sidecar is still starting");
      },
      { delay: async (ms) => { waited += ms; } },
    ),
  );

  const budgetMs = waited;
  assert.ok(
    budgetMs >= 45000,
    `default retry budget is ${budgetMs}ms; a cold start needs at least 45s of headroom`,
  );
});

test("waitForBackend stops early once the caller cancels", async () => {
  // Without a cancel hook the loop keeps polling a backend that will never
  // answer, so the retry budget cannot be raised without also making the
  // "unreachable" message take that long to appear on a genuine failure.
  let attempts = 0;
  let cancelled = false;
  await assert.rejects(() =>
    waitForBackend(
      async () => { attempts += 1; if (attempts === 2) cancelled = true; throw new Error("down"); },
      { maxAttempts: 50, delay: async () => {}, shouldStop: () => cancelled },
    ),
  );
  assert.equal(attempts, 2, "polling must stop as soon as shouldStop() turns true");
});
