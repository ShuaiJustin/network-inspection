const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

// The onefile sidecar unpacks itself to a temp directory before it binds its
// port. Cold starts measured here ranged from 9.3s to 11.8s, and a machine
// under load or with on-access virus scanning is slower still, so the budget
// has to leave real headroom above that.
const DEFAULT_MAX_ATTEMPTS = 240;
const DEFAULT_INTERVAL_MS = 250;

export async function waitForBackend(request, options = {}) {
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const intervalMs = options.intervalMs ?? DEFAULT_INTERVAL_MS;
  const delay = options.delay ?? sleep;
  const shouldStop = options.shouldStop;
  let lastError;

  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      return await request();
    } catch (error) {
      lastError = error;
      if (shouldStop?.()) break;
      if (attempt + 1 < maxAttempts) await delay(intervalMs);
    }
  }

  throw lastError;
}
