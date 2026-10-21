import assert from "node:assert/strict";
import test from "node:test";
import { resolveStartupSession } from "../src/app/startup-session.js";

const okStatus = async () => ({ initialized: true });

test("a stored token that the restarted backend no longer knows is discarded", async () => {
  // The sidecar keeps sessions in memory. After a restart the browser still has
  // the old token, so trusting it renders the workbench for a dead session and
  // the user never sees the login screen.
  const session = await resolveStartupSession({
    storedToken: "token-from-previous-run",
    fetchStatus: okStatus,
    verifySession: async () => {
      const error = new Error("Administrator login required");
      error.status = 401;
      throw error;
    },
  });

  assert.equal(session.token, null);
  assert.equal(session.initialized, true);
});

test("a token the backend still accepts is kept", async () => {
  const session = await resolveStartupSession({
    storedToken: "live-token",
    fetchStatus: okStatus,
    verifySession: async () => undefined,
  });

  assert.equal(session.token, "live-token");
});

test("with no stored token the login screen is shown without probing the session", async () => {
  let probed = false;
  const session = await resolveStartupSession({
    storedToken: null,
    fetchStatus: okStatus,
    verifySession: async () => { probed = true; },
  });

  assert.equal(session.token, null);
  assert.equal(probed, false);
});

test("a transport failure while verifying is reported, not treated as a logout", async () => {
  // Dropping the token on a network blip would silently sign the user out.
  await assert.rejects(
    resolveStartupSession({
      storedToken: "live-token",
      fetchStatus: okStatus,
      verifySession: async () => { throw new Error("无法连接本机巡检服务"); },
    }),
    /无法连接本机巡检服务/,
  );
});

test("a backend that is not initialized yet reports so and keeps no token", async () => {
  const session = await resolveStartupSession({
    storedToken: "stale",
    fetchStatus: async () => ({ initialized: false }),
    verifySession: async () => { throw Object.assign(new Error("401"), { status: 401 }); },
  });

  assert.equal(session.initialized, false);
  assert.equal(session.token, null);
});
