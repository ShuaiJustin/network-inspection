import assert from "node:assert/strict";
import test from "node:test";

import {
  addPendingKey,
  createLatestRequestTracker,
  createOperationEpoch,
  isTaskNavigationCurrent,
  removePendingKey,
} from "../src/app/operation-epoch.js";

test("session generations reject callbacks from a logged-out or previous login", () => {
  const operations = createOperationEpoch();
  const firstSession = operations.beginSession();

  assert.equal(operations.requestsBlocked, false);
  assert.equal(operations.isCurrent(firstSession), true);

  operations.endSession();
  assert.equal(operations.requestsBlocked, true);
  assert.equal(operations.isCurrent(firstSession), false);

  const secondSession = operations.beginSession();
  assert.equal(operations.isCurrent(secondSession), true);
  assert.equal(operations.isCurrentSession(firstSession.sessionGeneration), false);
});

test("an exclusive operation invalidates old writes and always releases its boundary on failure", () => {
  const operations = createOperationEpoch({ blocked: false });
  const oldWrite = operations.capture();
  const exclusive = operations.beginExclusive();

  assert.ok(exclusive);
  assert.equal(operations.requestsBlocked, true);
  assert.equal(operations.isCurrent(oldWrite), false);
  assert.equal(operations.invalidateWorkspace(oldWrite), null);
  assert.equal(operations.invalidateWorkspace(), null);
  assert.equal(operations.isExclusiveCurrent(exclusive), true);
  assert.equal(operations.finishExclusive(exclusive), true);
  assert.equal(operations.requestsBlocked, false);
});

test("a successful destructive operation ends the session without leaving an exclusive token", () => {
  const operations = createOperationEpoch({ blocked: false });
  const exclusive = operations.beginExclusive();

  assert.equal(operations.finishExclusive(exclusive, { endSession: true }), true);
  assert.equal(operations.requestsBlocked, true);
  assert.equal(operations.isExclusiveCurrent(exclusive), false);

  const nextSession = operations.beginSession();
  assert.equal(operations.isCurrent(nextSession), true);
  assert.equal(operations.finishExclusive(exclusive), false);
  assert.equal(operations.requestsBlocked, false);
});

test("concurrent normal writes remain current while each invalidates older reads", () => {
  const operations = createOperationEpoch({ blocked: false });
  const firstWrite = operations.capture();
  const secondWrite = operations.capture();

  const afterFirst = operations.invalidateWorkspace(firstWrite);
  assert.ok(afterFirst);
  assert.equal(operations.isCurrent(firstWrite), false);
  assert.equal(operations.isOperationCurrent(firstWrite), true);
  assert.equal(operations.isOperationCurrent(secondWrite), true);

  const afterSecond = operations.invalidateWorkspace(secondWrite);
  assert.ok(afterSecond, "a concurrent write may invalidate reads using its original workspace generation");
  assert.equal(afterSecond.workspaceGeneration, afterFirst.workspaceGeneration + 1);
  assert.equal(afterSecond.operationGeneration, firstWrite.operationGeneration);
});

test("session and exclusive boundaries reject an older operation", () => {
  const operations = createOperationEpoch({ blocked: false });
  const oldWrite = operations.capture();
  const oldOperationGeneration = oldWrite.operationGeneration;
  const exclusive = operations.beginExclusive();

  assert.equal(exclusive.operationGeneration, oldOperationGeneration + 1);
  assert.equal(operations.isOperationCurrent(oldWrite), false);
  assert.equal(operations.finishExclusive(exclusive), true);
  assert.equal(operations.invalidateWorkspace(oldWrite), null);

  const releasedWrite = operations.capture();
  const ended = operations.endSession();
  assert.equal(ended.operationGeneration, releasedWrite.operationGeneration + 1);
  const started = operations.beginSession();
  assert.equal(started.operationGeneration, ended.operationGeneration + 1);
});

test("task navigation commits require both the original intent and active task", () => {
  assert.equal(isTaskNavigationCurrent(4, 4, 12, 12), true);
  assert.equal(isTaskNavigationCurrent(5, 4, 12, 12), false);
  assert.equal(isTaskNavigationCurrent(4, 4, 13, 12), false);
});

test("latest request tracker lets only the newest refresh commit", () => {
  const requests = createLatestRequestTracker();
  const polling = requests.begin();
  const fullRefresh = requests.begin();

  assert.equal(requests.isLatest(polling), false);
  assert.equal(requests.isLatest(fullRefresh), true);
  requests.invalidate();
  assert.equal(requests.isLatest(fullRefresh), false);
});

test("pending key updates preserve other concurrent operations", () => {
  const first = addPendingKey(new Set(), "1:ssh");
  const both = addPendingKey(first, "2:snmp");
  const remaining = removePendingKey(both, "1:ssh");

  assert.deepEqual([...remaining], ["2:snmp"]);
  assert.deepEqual([...both], ["1:ssh", "2:snmp"]);
});
