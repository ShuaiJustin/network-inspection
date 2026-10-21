import { invoke, isTauri } from "@tauri-apps/api/core";
import { save } from "@tauri-apps/plugin-dialog";
import { writeFile } from "@tauri-apps/plugin-fs";

import { userErrorMessage } from "@/app/api-messages.js";
import { shouldResetLocalSession } from "@/app/session-state.js";

type BackendConfig = {
  url: string;
  runtime_token: string | null;
};

type ApiOptions = {
  resetSessionOnUnauthorized?: boolean;
};

export type DownloadResult =
  | { status: "saved"; path: string }
  | { status: "cancelled" };

const developmentRuntimeToken = import.meta.env.VITE_INSPECTION_RUNTIME_TOKEN || null;
let backendConfigPromise: Promise<BackendConfig> = import.meta.env.VITE_API_URL
  ? Promise.resolve({ url: import.meta.env.VITE_API_URL, runtime_token: developmentRuntimeToken })
  : import.meta.env.DEV
    ? Promise.resolve({ url: "http://127.0.0.1:8000", runtime_token: developmentRuntimeToken })
    : invoke<BackendConfig>("backend_config");
const usesManagedBackend = !import.meta.env.VITE_API_URL && !import.meta.env.DEV;

const BACKEND_UNREACHABLE = "无法连接本机巡检服务，请确认程序未被安全软件阻止后重试。";

export function describeApiError(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return userErrorMessage(detail, fallback);
  if (Array.isArray(detail)) {
    const messages = detail
      .map((entry) => (entry && typeof entry === "object" && "msg" in entry ? String((entry as { msg: unknown }).msg) : String(entry)))
      .filter(Boolean)
      .map((message) => userErrorMessage(message, "提交内容不符合要求，请检查后重试"));
    if (messages.length) return messages.join("；");
  }
  return fallback;
}

function clearExpiredSession(responseStatus: number, requestToken: string | null) {
  if (!shouldResetLocalSession(responseStatus, requestToken, localStorage.getItem("inspection-token"))) return;
  localStorage.removeItem("inspection-token");
  window.location.reload();
}

async function currentBackendConfig(): Promise<BackendConfig> {
  const observedPromise = backendConfigPromise;
  let backend: BackendConfig;
  try {
    backend = await observedPromise;
  } catch (error) {
    if (usesManagedBackend && backendConfigPromise === observedPromise) {
      backendConfigPromise = invoke<BackendConfig>("backend_config");
    }
    throw error;
  }
  if (backend.url) return backend;
  if (!usesManagedBackend) return backend;
  if (backendConfigPromise === observedPromise) {
    backendConfigPromise = invoke<BackendConfig>("backend_config");
  }
  return backendConfigPromise;
}

async function backendRequest(path: string, init?: RequestInit): Promise<{ response: Response; token: string | null }> {
  const headers = new Headers(init?.headers);
  const token = localStorage.getItem("inspection-token");
  if (token) headers.set("Authorization", "Bearer " + token);
  const backend = await currentBackendConfig();
  if (!backend.url) throw new Error(BACKEND_UNREACHABLE);
  if (backend.runtime_token) headers.set("X-Inspection-Run-Token", backend.runtime_token);
  try {
    return { response: await fetch(backend.url + path, { ...init, headers }), token };
  } catch {
    throw new Error(BACKEND_UNREACHABLE);
  }
}

export async function restartLocalBackend(): Promise<void> {
  if (import.meta.env.VITE_API_URL || import.meta.env.DEV) return;
  backendConfigPromise = invoke<BackendConfig>("restart_backend");
  await backendConfigPromise;
}

export async function api<T>(path: string, init?: RequestInit, options: ApiOptions = {}): Promise<T> {
  const { response, token } = await backendRequest(path, init);
  if (!response.ok) {
    if (options.resetSessionOnUnauthorized !== false) clearExpiredSession(response.status, token);
    const detail = await response.json().catch(() => ({}));
    throw Object.assign(new Error(describeApiError(detail.detail, "请求失败")), { status: response.status });
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

function responseFilename(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const encoded = header.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try { return decodeURIComponent(encoded).replace(/[\\/]/g, "_"); } catch { /* use fallback */ }
  }
  return header.match(/filename="?([^";]+)"?/i)?.[1]?.replace(/[\\/]/g, "_") || fallback;
}

export async function downloadFile(path: string, filename: string): Promise<DownloadResult> {
  const { response, token } = await backendRequest(path);
  if (!response.ok) {
    clearExpiredSession(response.status, token);
    const detail = await response.json().catch(() => ({}));
    throw new Error(describeApiError(detail.detail, "下载失败"));
  }
  const blob = await response.blob();
  const resolvedFilename = responseFilename(response.headers.get("Content-Disposition"), filename);
  if (isTauri()) {
    const extension = resolvedFilename.includes(".") ? resolvedFilename.split(".").pop() : undefined;
    const destination = await save({
      defaultPath: resolvedFilename,
      filters: extension ? [{ name: `${extension.toUpperCase()} 文件`, extensions: [extension] }] : undefined,
    });
    if (!destination) return { status: "cancelled" };
    await writeFile(destination, new Uint8Array(await blob.arrayBuffer()));
    return { status: "saved", path: destination };
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = resolvedFilename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  return { status: "saved", path: resolvedFilename };
}
