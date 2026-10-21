import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const sessionState = await import("../src/app/session-state.js").catch(() => null);
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const apiClient = readFileSync(new URL("../src/app/api-client.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/app/types.ts", import.meta.url), "utf8");

test("only an unauthorized request made with the current session resets local login", () => {
  assert.ok(sessionState, "session state helpers must be available to API requests");
  assert.equal(sessionState.shouldResetLocalSession(401, "token-a", "token-a"), true);
  assert.equal(sessionState.shouldResetLocalSession(401, "token-a", "token-b"), false);
  assert.equal(sessionState.shouldResetLocalSession(401, null, null), false);
  assert.equal(sessionState.shouldResetLocalSession(400, "token-a", "token-a"), false);
  assert.equal(sessionState.shouldResetLocalSession(409, "token-a", "token-a"), false);
});

test("a 401 while downloading also clears the expired local session", () => {
  const download = apiClient.slice(apiClient.indexOf("export async function downloadFile"));
  const sessionReset = apiClient.slice(apiClient.indexOf("function clearExpiredSession"), apiClient.indexOf("async function backendRequest"));
  assert.match(download, /clearExpiredSession\(response\.status, token\)/);
  assert.match(sessionReset, /shouldResetLocalSession\(responseStatus, requestToken, localStorage\.getItem\("inspection-token"\)\)/);
  assert.match(sessionReset, /localStorage\.removeItem\("inspection-token"\)/);
  assert.match(sessionReset, /window\.location\.reload\(\)/);
});

test("transport and shared domain types are split out of the main component", () => {
  assert.match(app, /from "@\/app\/api-client"/);
  assert.match(app, /from "@\/app\/types"/);
  assert.doesNotMatch(app, /async function api|async function downloadFile|type Device =|type Task =/);
  assert.match(apiClient, /export async function api<T>/);
  assert.match(apiClient, /export async function restartLocalBackend/);
  assert.match(types, /export type Device =/);
  assert.match(types, /export type Task =/);
  assert.match(types, /export type InspectionSettings =/);
});

test("a successful database restore returns to login and only failures refresh", () => {
  const start = app.indexOf("onDatabaseRestore={async");
  const end = app.indexOf("onPasswordChange={async", start);
  const restoreHandler = app.slice(start, end);

  assert.match(restoreHandler, /localStorage\.removeItem\("inspection-token"\)/);
  assert.match(restoreHandler, /setAuthNotice\("数据库已恢复，请重新登录。"\)/);
  assert.match(restoreHandler, /setToken\(null\)/);
  assert.match(restoreHandler, /clearAuthenticatedState\(\)/);
  const clearStart = app.indexOf("const clearAuthenticatedState");
  const clearEnd = app.indexOf("const logout = async", clearStart);
  const clear = app.slice(clearStart, clearEnd);
  assert.match(clear, /activeTaskIdRef\.current = null/);
  assert.match(clear, /setActiveTask\(null\)/);
  assert.match(clear, /setResults\(\[\]\)/);
  assert.match(clear, /setTasks\(\[\]\)/);
  assert.match(restoreHandler, /if \(!sessionEnded && operations\.finishExclusive\(exclusiveOperation\)\) \{[\s\S]*await refresh\(\)/);
  assert.ok(restoreHandler.indexOf("sessionEnded = true") < restoreHandler.indexOf("await refresh()"));
});
