export function createOperationEpoch({ blocked = true } = {}) {
  let sessionGeneration = 0;
  let workspaceGeneration = 0;
  let operationGeneration = 0;
  let requestsBlocked = blocked;
  let exclusiveToken = null;

  const capture = () => ({ sessionGeneration, workspaceGeneration, operationGeneration });
  const isOperationCurrent = (epoch) => Boolean(epoch)
    && epoch.sessionGeneration === sessionGeneration
    && epoch.operationGeneration === operationGeneration;
  const isCurrent = (epoch) => isOperationCurrent(epoch)
    && epoch.workspaceGeneration === workspaceGeneration;
  const isExclusiveCurrent = (token) => token === exclusiveToken && isCurrent(token);

  return {
    get sessionGeneration() {
      return sessionGeneration;
    },
    get workspaceGeneration() {
      return workspaceGeneration;
    },
    get operationGeneration() {
      return operationGeneration;
    },
    get requestsBlocked() {
      return requestsBlocked;
    },
    capture,
    isCurrent,
    isOperationCurrent,
    isCurrentSession(generation) {
      return generation === sessionGeneration;
    },
    beginSession() {
      sessionGeneration += 1;
      workspaceGeneration += 1;
      operationGeneration += 1;
      requestsBlocked = false;
      exclusiveToken = null;
      return capture();
    },
    endSession() {
      sessionGeneration += 1;
      workspaceGeneration += 1;
      operationGeneration += 1;
      requestsBlocked = true;
      exclusiveToken = null;
      return capture();
    },
    invalidateWorkspace(expectedEpoch) {
      if (requestsBlocked) return null;
      if (expectedEpoch && !isOperationCurrent(expectedEpoch)) return null;
      workspaceGeneration += 1;
      return capture();
    },
    beginExclusive() {
      if (requestsBlocked) return null;
      workspaceGeneration += 1;
      operationGeneration += 1;
      requestsBlocked = true;
      const token = { ...capture(), id: Symbol("exclusive-workspace-operation") };
      exclusiveToken = token;
      return token;
    },
    isExclusiveCurrent,
    finishExclusive(token, { endSession = false } = {}) {
      if (!isExclusiveCurrent(token)) return false;
      exclusiveToken = null;
      if (endSession) {
        sessionGeneration += 1;
        workspaceGeneration += 1;
        operationGeneration += 1;
        requestsBlocked = true;
      } else {
        requestsBlocked = false;
      }
      return true;
    },
  };
}

export function isTaskNavigationCurrent(currentIntentId, expectedIntentId, currentTaskId, expectedTaskId) {
  return currentIntentId === expectedIntentId && currentTaskId === expectedTaskId;
}

export function createLatestRequestTracker() {
  let latestRequestId = 0;
  return {
    begin() {
      latestRequestId += 1;
      return latestRequestId;
    },
    invalidate() {
      latestRequestId += 1;
      return latestRequestId;
    },
    isLatest(requestId) {
      return requestId === latestRequestId;
    },
  };
}

export function addPendingKey(keys, key) {
  return new Set([...keys, key]);
}

export function removePendingKey(keys, key) {
  const next = new Set(keys);
  next.delete(key);
  return next;
}
