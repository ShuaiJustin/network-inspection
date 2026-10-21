// The sidecar stores sessions in memory, so every restart invalidates the token
// kept in localStorage. Verify it before rendering the workbench, otherwise a
// dead session hides the login screen behind a page that cannot load any data.
export async function resolveStartupSession({ storedToken, fetchStatus, verifySession }) {
  const status = await fetchStatus();
  if (!storedToken) return { initialized: status.initialized, token: null };

  try {
    await verifySession();
  } catch (error) {
    if (error?.status === 401) return { initialized: status.initialized, token: null };
    throw error;
  }
  return { initialized: status.initialized, token: storedToken };
}
