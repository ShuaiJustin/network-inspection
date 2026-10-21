export function createUpdateController({ invoke, createChannel, getToken }) {
  let state = { status: invoke ? 'idle' : 'unsupported', update: null, error: null, downloaded: false, downloadedBytes: 0, contentLength: null };
  const listeners = new Set();
  const set = patch => { state = { ...state, ...patch }; listeners.forEach(listener => listener(state)); };
  const busy = () => ['checking', 'downloading', 'installing'].includes(state.status);
  const message = error => error instanceof Error ? error.message : String(error);
  return {
    getState: () => state,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    async check() {
      if (!invoke || busy() || state.downloaded) return;
      set({ status: 'checking', error: null });
      try {
        const update = await invoke('check_app_update');
        const downloaded = Boolean(update?.downloaded);
        set({ update, downloaded, status: downloaded ? 'ready' : update ? 'available' : 'current' });
      }
      catch (error) { set({ status: 'error', error: message(error) }); }
    },
    async download() {
      if (!invoke || busy() || !state.update || state.downloaded) return;
      set({ status: 'downloading', error: null, downloadedBytes: 0, contentLength: null });
      try {
        const onEvent = createChannel();
        onEvent.onmessage = event => {
          if (state.status !== 'downloading') return;
          if (event.event === 'Started') set({ contentLength: event.data.contentLength });
          if (event.event === 'Progress') set({ downloadedBytes: state.downloadedBytes + event.data.chunkLength });
        };
        await invoke('download_app_update', { onEvent });
        set({ status: 'ready', downloaded: true });
      } catch (error) { set({ status: 'error', error: message(error) }); }
    },
    async install() {
      if (!invoke || busy() || !state.downloaded) return;
      set({ status: 'installing', error: null });
      try { await invoke('install_app_update', { sessionToken: getToken() }); }
      catch (error) { set({ status: 'ready', error: message(error) }); }
    },
  };
}
