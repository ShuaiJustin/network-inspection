import { useEffect, useRef, useState } from 'react';
import { Channel, invoke, isTauri } from '@tauri-apps/api/core';
import { getVersion } from '@tauri-apps/api/app';
import { Button } from '@/app/components/ui/button';
import { createUpdateController } from './update-controller.js';
import type { ConfirmationOptions } from './types';
import packageInfo from '../../package.json';
import './software-update.css';

export function SoftwareUpdate({ settingsVisible, busy, onConfirm }: { settingsVisible: boolean; busy: boolean; onConfirm: (options: ConfirmationOptions) => Promise<boolean | null> }) {
  const [controller] = useState(() => createUpdateController({ invoke: isTauri() ? invoke : null, createChannel: () => new Channel(), getToken: () => localStorage.getItem('inspection-token') ?? '' }));
  const [state, setState] = useState(controller.getState);
  const [version, setVersion] = useState(packageInfo.version);
  const [confirming, setConfirming] = useState(false);
  const busyRef = useRef(busy);
  busyRef.current = busy;
  useEffect(() => {
    const unsubscribe = controller.subscribe(setState);
    void controller.check();
    if (isTauri()) void getVersion().then(setVersion).catch(() => {});
    return unsubscribe;
  }, [controller]);
  const install = async () => {
    if (busy || confirming) return;
    setConfirming(true);
    try {
      if (await onConfirm({ title: '安装软件更新', message: `即将安装 v${state.update?.version}。程序将退出并启动安装器，安装完成后请重新打开。`, confirmLabel: '退出并安装' })) {
        if (!busyRef.current) await controller.install();
      }
    } finally { setConfirming(false); }
  };
  if (!settingsVisible && !state.update) return null;
  const working = ['checking', 'downloading', 'installing'].includes(state.status);
  const needsReconnect = Boolean(state.error?.includes('本地服务已重启'));
  const percent = state.contentLength ? Math.min(100, Math.round(state.downloadedBytes / state.contentLength * 100)) : null;
  return <section className={`software-update ${settingsVisible ? '' : 'software-update--floating'}`} aria-label="软件更新">
    <div><h2>软件更新</h2><p>当前版本 v{version}{state.update ? ` · 新版本 v${state.update.version}` : ''}</p></div>
    {state.status === 'unsupported' ? <p>自动更新仅支持桌面客户端，请在桌面客户端检查更新。</p> : <>
      {state.status === 'current' && <p role="status">当前已是最新版本。</p>}
      {state.status === 'checking' && <p role="status">正在检查更新…</p>}
      {state.update?.body && <details><summary>更新说明</summary><p className="software-update-notes">{state.update.body}</p></details>}
      {state.status === 'downloading' && <div role="status"><p>正在下载 {percent === null ? `${(state.downloadedBytes / 1024 / 1024).toFixed(1)} MB` : `${percent}%`}</p><progress max={state.contentLength || undefined} value={state.contentLength ? state.downloadedBytes : undefined} /></div>}
      {state.downloaded && <p role="status">更新已下载并通过签名校验，可稍后安装。{busy ? '请等待当前后台操作结束。' : ''}</p>}
      {state.error && <p className="inline-error" role="alert">{state.error}</p>}
      <div className="button-row">
        {settingsVisible && !state.downloaded && <Button variant="outline" disabled={working} onClick={() => void controller.check()}>检查更新</Button>}
        {state.update && !state.downloaded && <Button disabled={working} onClick={() => void controller.download()}>{state.status === 'downloading' ? '下载中…' : '下载更新'}</Button>}
        {needsReconnect && <Button onClick={() => window.location.reload()}>重新连接本地服务</Button>}
        {state.downloaded && <Button disabled={needsReconnect || busy || working || confirming} onClick={() => void install()}>{state.status === 'installing' ? '正在启动安装…' : '退出并安装'}</Button>}
      </div>
    </>}
  </section>;
}
