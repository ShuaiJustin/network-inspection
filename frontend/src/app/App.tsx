import { ChangeEvent, FormEvent, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Archive,
  CheckCircle2,
  Camera,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Database,
  Download,
  FileSpreadsheet,
  FolderOpen,
  GitCompareArrows,
  HardDriveDownload,
  Info,
  KeyRound,
  LayoutDashboard,
  LogOut,
  MonitorCog,
  Network,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ScrollText,
  Settings,
  Terminal,
  Moon,
  Sun,
  Upload,
  X,
  Trash2,
} from "lucide-react";

import { Button } from "@/app/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/app/components/ui/card";
import { Input } from "@/app/components/ui/input";
import { api, downloadFile, restartLocalBackend } from "@/app/api-client";
import { waitForBackend } from "@/app/backend-ready.mjs";
import { resolveStartupSession } from "@/app/startup-session.js";
import { activeTaskAfterDeletion, canDeleteInspectionTask, canExportInspectionReport, canRetryInspectionTask, deletableTaskIds, describeTaskDeletion, resultTaskForNavigation, taskNeedsPolling } from "@/app/task-state.js";
import { formatInspectionItemValue, inspectionItemLabel, inspectionItemRows, inspectionReferenceText } from "@/app/inspection-items.js";
import { PRIMARY_RESULT_FILTERS, RESULT_FILTERS, filterResults, formatMetric, keepSelectionVisible, primaryInspectionFinding, resultFilterCount, sortResultsByAttention, summarizeResults, taskProgressPercent } from "@/app/results-view.js";
import { TASK_FILTERS, filterTasks, partitionVisibleTasks, summarizeTasks, taskProgressTone } from "@/app/task-view.js";
import { noticeAutoDismissMs } from "@/app/notice-tone.js";
import { validateThresholdRanges } from "@/app/threshold-settings.js";
import { adapterCapabilityRows, filterAdapterRows, verifiedAdapterRows } from "@/app/adapter-state.js";
import { formatSshHostKeyTrustPrompt } from "@/app/ssh-host-key-trust.js";
import { ADMIN_PASSWORD_MIN_LENGTH, validateAdminCredentials } from "@/app/password-policy.js";
import { applyTheme, nextTheme, persistTheme, readStoredTheme, resolveInitialTheme } from "@/app/theme-preference.js";
import { buildDevicePayload } from "@/app/device-payload.js";
import { defaultPlatformId, deviceTypeOptions, deviceTypeText, platformOptions, vendorOptions, vendorText } from "@/app/device-catalog.js";
import { translateApiMessage, userErrorMessage } from "@/app/api-messages.js";
import { importIssueRows } from "@/app/import-errors.js";
import { formatDateTime } from "@/app/date-format.js";
import { addPendingKey, createLatestRequestTracker, createOperationEpoch, isTaskNavigationCurrent, removePendingKey } from "@/app/operation-epoch.js";
import type { AdapterCapability, BackupRecord, ConfirmationOptions, ConnectionTestResult, Device, DeviceCatalog, ImportIssue, InspectionSettings, LogDeleteResult, LogPage, Notice, NoticeTone, Page, Result, Task, VerifiedAdapter } from "@/app/types";
import {
  DEFAULT_SNMP_V3_SECURITY_LEVEL,
  SNMP_V3_AUTH_PROTOCOLS,
  SNMP_V3_PRIV_PROTOCOLS,
  SNMP_V3_SECURITY_LEVELS,
  needsNewSnmpV3Secrets,
} from "@/app/snmp-v3.js";

const navItems: Array<{ id: Page; label: string; icon: typeof LayoutDashboard }> = [
  { id: "dashboard", label: "首页", icon: LayoutDashboard },
  { id: "devices", label: "设备管理", icon: Network },
  { id: "tasks", label: "巡检任务", icon: ClipboardCheck },
  { id: "results", label: "巡检结果", icon: Activity },
  { id: "adapters", label: "设备适配", icon: CheckCircle2 },
  { id: "backup", label: "配置备份", icon: Archive },
  { id: "settings", label: "系统设置", icon: Settings },
  { id: "logs", label: "运行日志", icon: ScrollText },
];

type OperationEpoch = {
  sessionGeneration: number;
  workspaceGeneration: number;
  operationGeneration: number;
};

const conclusionText: Record<string, string> = {
  success: "连接成功",
  normal: "正常",
  warning: "警告",
  critical: "严重",
  connection_failed: "连接失败",
  authentication_failed: "认证失败",
  data_fetch_failed: "数据获取失败",
  queued: "等待执行",
  running: "正在执行",
  completed: "已完成",
  partial_failed: "部分失败",
  failed: "执行失败",
  cancelled: "已取消",
  unsupported: "不支持",
  not_applicable: "不适用",
  snmp: "SNMP",
  ssh: "SSH",
};

function StatusPill({ value }: { value: string }) {
  const neutralStatuses = ["queued", "running", "unsupported", "not_applicable", "cancelled"];
  const kind = value === "normal" || value === "completed" || value === "success"
    ? "ok"
    : value === "warning" || value === "partial_failed"
      ? "warning"
      : neutralStatuses.includes(value)
        ? "neutral"
        : "error";
  return <span className={`status-pill status-pill--${kind}`}>{conclusionText[value] ?? value}</span>;
}

async function renderScreenshotCanvas(element: HTMLElement) {
  const { default: html2canvas } = await import("html2canvas");
  return html2canvas(element, {
    backgroundColor: getComputedStyle(document.documentElement).getPropertyValue("--c-surface").trim() || "#ffffff",
    scale: 2,
  });
}

function App() {
  const [page, setPage] = useState<Page>("dashboard");
  const [devices, setDevices] = useState<Device[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [settings, setSettings] = useState<InspectionSettings | null>(null);
  const [catalog, setCatalog] = useState<DeviceCatalog>({ vendors: [], device_types: [] });
  const [results, setResults] = useState<Result[]>([]);
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [deviceSearch, setDeviceSearch] = useState("");
  const [isDeviceDialogOpen, setDeviceDialogOpen] = useState(false);
  const [deviceToEdit, setDeviceToEdit] = useState<Device | null>(null);
  const [isTaskDialogOpen, setTaskDialogOpen] = useState(false);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationOptions | null>(null);
  const [importIssues, setImportIssues] = useState<ImportIssue[]>([]);
  const [loading, setLoading] = useState(false);
  const [testingConnections, setTestingConnections] = useState<Set<string>>(() => new Set());
  const [backupInProgress, setBackupInProgress] = useState(false);
  const [exportingReport, setExportingReport] = useState(false);
  const [importing, setImporting] = useState(false);
  const [exclusiveBusy, setExclusiveBusy] = useState(false);
  const [deletingTaskIds, setDeletingTaskIds] = useState<number[]>([]);
  const [cancellingTaskIds, setCancellingTaskIds] = useState<number[]>([]);
  const [retryingTaskIds, setRetryingTaskIds] = useState<number[]>([]);
  const [deletingDeviceIds, setDeletingDeviceIds] = useState<number[]>([]);
  const [reconnectingBackend, setReconnectingBackend] = useState(false);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("inspection-token"));
  const [authInitialized, setAuthInitialized] = useState<boolean | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [backendAttempt, setBackendAttempt] = useState(0);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [backendWaitSeconds, setBackendWaitSeconds] = useState(0);
  const workspaceRefreshInFlightRef = useRef<Promise<void> | null>(null);
  const taskRefreshInFlight = useRef<symbol | null>(null);
  const taskDataRequestsRef = useRef(createLatestRequestTracker());
  const operationEpochRef = useRef(createOperationEpoch());
  const testingConnectionsRef = useRef(new Set<string>());
  const backupInProgressRef = useRef(false);
  const exportingReportRef = useRef(false);
  const importingRef = useRef(false);
  const deletingTaskIdsRef = useRef(new Set<number>());
  const cancellingTaskIdsRef = useRef(new Set<number>());
  const retryingTaskIdsRef = useRef(new Set<number>());
  const deletingDeviceIdsRef = useRef(new Set<number>());
  const reconnectingBackendRef = useRef(false);
  const activeTaskIdRef = useRef<number | null>(null);
  const taskNavigationIntentRef = useRef(0);
  const confirmationResolverRef = useRef<((confirmed: boolean) => void) | null>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);
  const hasPendingParentOperation = () => importingRef.current
    || backupInProgressRef.current
    || testingConnectionsRef.current.size > 0
    || deletingTaskIdsRef.current.size > 0
    || cancellingTaskIdsRef.current.size > 0
    || retryingTaskIdsRef.current.size > 0
    || deletingDeviceIdsRef.current.size > 0;
  const [theme, setTheme] = useState<string>(() =>
    resolveInitialTheme(readStoredTheme(window.localStorage), window.matchMedia?.("(prefers-color-scheme: dark)").matches),
  );

  useEffect(() => {
    applyTheme(document.documentElement, theme);
    persistTheme(window.localStorage, theme);
  }, [theme]);

  const notify = (message: string, tone: NoticeTone) => {
    setError(null);
    setNotice({ message, tone });
  };

  const confirmAction = (options: ConfirmationOptions): Promise<boolean | null> => {
    if (confirmationResolverRef.current) return Promise.resolve(null);
    setConfirmation(options);
    return new Promise((resolve) => { confirmationResolverRef.current = resolve; });
  };

  const settleConfirmation = (confirmed: boolean) => {
    const resolve = confirmationResolverRef.current;
    confirmationResolverRef.current = null;
    setConfirmation(null);
    resolve?.(confirmed);
  };

  useEffect(() => () => {
    confirmationResolverRef.current?.(false);
    confirmationResolverRef.current = null;
  }, []);

  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0, behavior: "auto" });
  }, [page]);

  const invalidateWorkspaceRequests = (expectedEpoch?: OperationEpoch) => {
    const nextEpoch = operationEpochRef.current.invalidateWorkspace(expectedEpoch);
    if (!nextEpoch) return null;
    taskDataRequestsRef.current.invalidate();
    workspaceRefreshInFlightRef.current = null;
    taskRefreshInFlight.current = null;
    setLoading(false);
    return nextEpoch;
  };

  const refresh = (taskToRefresh: Task | null = activeTask): Promise<void> => {
    const operations = operationEpochRef.current;
    if (!token || operations.requestsBlocked) return Promise.resolve();
    if (workspaceRefreshInFlightRef.current) return workspaceRefreshInFlightRef.current;
    const requestEpoch = operations.capture();
    const taskRequestId = taskDataRequestsRef.current.begin();
    const refreshRequest = (async () => {
      setLoading(true);
      setError(null);
      try {
        const [nextDevices, nextTasks, nextSettings, nextCatalog] = await Promise.all([
          api<Device[]>("/devices"),
          api<Task[]>("/tasks"),
          api<InspectionSettings>("/settings"),
          api<DeviceCatalog>("/catalog/devices"),
        ]);
        if (!operations.isCurrent(requestEpoch)) return;
        setDevices(nextDevices);
        setSettings(nextSettings);
        setCatalog(nextCatalog);
        if (taskDataRequestsRef.current.isLatest(taskRequestId)) setTasks(nextTasks);
        if (taskToRefresh && taskDataRequestsRef.current.isLatest(taskRequestId)) {
          const taskId = taskToRefresh.id;
          const detail = await api<Task & { results: Result[] }>(`/tasks/${taskId}`);
          if (operations.isCurrent(requestEpoch)
            && taskDataRequestsRef.current.isLatest(taskRequestId)
            && activeTaskIdRef.current === taskId) {
            setActiveTask(detail);
            setResults(detail.results);
          }
        }
      } catch (err) {
        if (operations.isCurrent(requestEpoch)) {
          setError(userErrorMessage(err, "无法连接本机巡检服务"));
        }
      } finally {
        if (operations.isCurrent(requestEpoch)) setLoading(false);
        if (workspaceRefreshInFlightRef.current === refreshRequest) workspaceRefreshInFlightRef.current = null;
      }
    })();
    workspaceRefreshInFlightRef.current = refreshRequest;
    return refreshRequest;
  };

  const refreshTaskState = async () => {
    const operations = operationEpochRef.current;
    if (!token || operations.requestsBlocked || deletingTaskIdsRef.current.size || cancellingTaskIdsRef.current.size || taskRefreshInFlight.current) return;
    const requestKey = Symbol("task-refresh");
    const requestEpoch = operations.capture();
    const taskRequestId = taskDataRequestsRef.current.begin();
    taskRefreshInFlight.current = requestKey;
    const taskId = page === "results" ? activeTaskIdRef.current : null;
    try {
      const nextTasks = await api<Task[]>("/tasks");
      if (!operations.isCurrent(requestEpoch) || !taskDataRequestsRef.current.isLatest(taskRequestId)) return;
      setTasks(nextTasks);
      if (taskId !== null) {
        const detail = await api<Task & { results: Result[] }>(`/tasks/${taskId}`);
        if (operations.isCurrent(requestEpoch)
          && taskDataRequestsRef.current.isLatest(taskRequestId)
          && activeTaskIdRef.current === taskId) {
          setActiveTask(detail);
          setResults(detail.results);
        }
      }
    } catch (err) {
      if (operations.isCurrent(requestEpoch) && taskDataRequestsRef.current.isLatest(taskRequestId)) {
        setError(userErrorMessage(err, "无法更新巡检进度"));
      }
    } finally {
      if (taskRefreshInFlight.current === requestKey) taskRefreshInFlight.current = null;
    }
  };

  useEffect(() => {
    let cancelled = false;
    setBackendError(null);
    setBackendWaitSeconds(0);
    const startedAt = Date.now();
    const ticker = window.setInterval(
      () => setBackendWaitSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      1000,
    );
    void waitForBackend(
      () => resolveStartupSession({
        storedToken: localStorage.getItem("inspection-token"),
        fetchStatus: () => api<{ initialized: boolean }>("/auth/status"),
        verifySession: () => api<void>("/auth/session", undefined, { resetSessionOnUnauthorized: false }),
      }),
      { shouldStop: () => cancelled },
    )
      .then((session) => {
        if (cancelled) return;
        if (session.token) operationEpochRef.current.beginSession();
        else operationEpochRef.current.endSession();
        if (!session.token) {
          localStorage.removeItem("inspection-token");
          setToken(null);
        }
        setAuthInitialized(session.initialized);
      })
      .catch(() => {
        if (!cancelled) setBackendError("无法连接本机巡检服务，请确认程序未被安全软件阻止后重新连接。");
      })
      .finally(() => window.clearInterval(ticker));
    return () => { cancelled = true; window.clearInterval(ticker); };
  }, [backendAttempt]);
  useEffect(() => { if (!authInitialized || !token) return; void refresh(); }, [token, authInitialized]);

  useEffect(() => {
    const hasActiveTask = tasks.some(taskNeedsPolling)
      || page === "results" && Boolean(activeTask && taskNeedsPolling(activeTask));
    if (!token || !hasActiveTask) return;
    const timer = window.setInterval(() => { void refreshTaskState(); }, 1200);
    return () => window.clearInterval(timer);
  }, [token, tasks, activeTask, page]);

  const filteredDevices = useMemo(() => devices.filter((device) =>
    [device.name, device.ip, vendorText(catalog, device.vendor), deviceTypeText(catalog, device.device_type), device.group_name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()
      .includes(deviceSearch.toLowerCase())
  ), [devices, catalog, deviceSearch]);

  const selectTask = async (task: Task, navigateToResults = true, expectedIntentId?: number) => {
    const operations = operationEpochRef.current;
    if (operations.requestsBlocked) return;
    const taskId = task.id;
    const intentId = expectedIntentId ?? taskNavigationIntentRef.current + 1;
    if (expectedIntentId === undefined) taskNavigationIntentRef.current = intentId;
    if (taskNavigationIntentRef.current !== intentId) return;
    const requestEpoch = operations.capture();
    activeTaskIdRef.current = taskId;
    try {
      const detail = await api<Task & { results: Result[] }>(`/tasks/${taskId}`);
      if (!operations.isOperationCurrent(requestEpoch)
        || !isTaskNavigationCurrent(taskNavigationIntentRef.current, intentId, activeTaskIdRef.current, taskId)) return;
      setActiveTask(detail);
      setResults(detail.results);
      if (navigateToResults) setPage("results");
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)
        && isTaskNavigationCurrent(taskNavigationIntentRef.current, intentId, activeTaskIdRef.current, taskId)) {
        setError(userErrorMessage(err, "无法获取任务详情"));
      }
    }
  };

  const navigateToPage = (nextPage: Page) => {
    const intentId = ++taskNavigationIntentRef.current;
    if (nextPage !== "results") {
      setPage(nextPage);
      return;
    }
    const taskForResults = resultTaskForNavigation(tasks, activeTask);
    if (taskForResults) {
      void selectTask(taskForResults, true, intentId);
      return;
    }
    setPage("results");
  };

  useEffect(() => {
    if (page !== "results" || activeTask) return;
    const taskForResults = resultTaskForNavigation(tasks, null);
    if (taskForResults) void selectTask(taskForResults);
  }, [page, tasks, activeTask]);

  const toggleDevice = (id: number) => setSelectedIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const selectAll = (ids: number[]) => setSelectedIds((current) => ids.every((id) => current.includes(id)) ? current.filter((id) => !ids.includes(id)) : [...new Set([...current, ...ids])]);

  const exportReport = async () => {
    if (!activeTask) return;
    const operations = operationEpochRef.current;
    if (operations.requestsBlocked || exportingReportRef.current) return;
    const requestEpoch = operations.capture();
    exportingReportRef.current = true;
    setExportingReport(true);
    try {
      const result = await downloadFile(`/excel/report/${activeTask.id}`, `inspection-report-${activeTask.id}.xlsx`);
      if (operations.isOperationCurrent(requestEpoch) && result.status === "saved") {
        notify(`巡检报告已保存：${result.path}`, "success");
      }
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "巡检报告下载失败"));
    } finally {
      exportingReportRef.current = false;
      setExportingReport(false);
    }
  };

  const exportDevices = async () => {
    const operations = operationEpochRef.current;
    if (operations.requestsBlocked) return;
    const requestEpoch = operations.capture();
    try {
      const result = await downloadFile("/excel/devices-export", "device-list.xlsx");
      if (operations.isOperationCurrent(requestEpoch) && result.status === "saved") notify(`设备清单已保存：${result.path}`, "success");
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "设备清单导出失败"));
    }
  };

  const downloadTemplate = async () => {
    const operations = operationEpochRef.current;
    if (operations.requestsBlocked) return;
    const requestEpoch = operations.capture();
    try {
      const result = await downloadFile("/excel/template", "device-import-template.xlsx");
      if (operations.isOperationCurrent(requestEpoch) && result.status === "saved") notify(`导入模板已保存：${result.path}`, "success");
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "模板下载失败"));
    }
  };

  const testConnection = async (device: Device, protocol: "snmp" | "ssh") => {
    const operations = operationEpochRef.current;
    const key = `${device.id}:${protocol}`;
    if (operations.requestsBlocked || testingConnectionsRef.current.has(key)) return;
    let requestEpoch = operations.capture();
    const deviceName = device.name ?? device.ip;
    testingConnectionsRef.current.add(key);
    setTestingConnections((current) => addPendingKey(current, key));
    notify(`正在测试 ${deviceName} 的 ${protocol.toUpperCase()} 连通性，请稍候…`, "progress");
    try {
      const result = await api<ConnectionTestResult>(`/devices/${device.id}/connection-test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ protocol }),
      });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const requiresHostKeyTrust = protocol === "ssh"
        && ["host_fingerprint_untrusted", "host_fingerprint_changed"].includes(result.failure_stage ?? "")
        && result.host_fingerprint
        && result.host_key_algorithm;
      if (requiresHostKeyTrust) {
        const confirmed = await confirmAction({
          title: result.failure_stage === "host_fingerprint_changed" ? "SSH 主机密钥已变化" : "确认 SSH 主机密钥",
          message: formatSshHostKeyTrustPrompt({
            ip: device.ip,
            hostKeyAlgorithm: result.host_key_algorithm,
            hostFingerprint: result.host_fingerprint,
            changed: result.failure_stage === "host_fingerprint_changed",
          }),
          confirmLabel: result.failure_stage === "host_fingerprint_changed" ? "确认新密钥" : "信任密钥",
          danger: result.failure_stage === "host_fingerprint_changed",
        });
        if (confirmed === null) {
          if (operations.isOperationCurrent(requestEpoch)) notify(`${deviceName} 的 SSH 主机密钥等待确认，但当前已有其他确认操作，请稍后重新测试。`, "warning");
          return;
        }
        if (!operations.isOperationCurrent(requestEpoch)) return;
        if (confirmed) {
          await api<Device>(`/devices/${device.id}/ssh-host-key/trust`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              expected_host_fingerprint: result.host_fingerprint,
              expected_host_key_algorithm: result.host_key_algorithm,
            }),
          });
          if (!operations.isOperationCurrent(requestEpoch)) return;
          const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
          if (!nextEpoch) return;
          requestEpoch = nextEpoch;
          if (operations.isOperationCurrent(requestEpoch)) notify(`${deviceName} 的 SSH 主机密钥已确认，请重新测试连通性。`, "success");
          await refresh();
          return;
        }
      }
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const reason = result.message ? `：${userErrorMessage(result.message, "设备返回了未识别的异常，请查看运行日志")}` : "";
      const summary = `${deviceName} ${result.protocol} ${conclusionText[result.status] ?? result.status}${reason}`;
      if (result.status === "success") notify(summary, "success");
      else { setNotice(null); setError(summary); }
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "连通性测试失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        testingConnectionsRef.current.delete(key);
        setTestingConnections((current) => removePendingKey(current, key));
      }
    }
  };

  const importExcel = async (event: ChangeEvent<HTMLInputElement>) => {
    const operations = operationEpochRef.current;
    if (importing || operations.requestsBlocked) return;
    let requestEpoch = operations.capture();
    const input = event.target;
    const file = input.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    importingRef.current = true;
    setImporting(true);
    try {
      const response = await api<{ imported: number; errors: Array<{ row: number; reason: string }> }>("/excel/import", { method: "POST", body });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return;
      requestEpoch = nextEpoch;
      if (response.errors.length) {
        setImportIssues(importIssueRows(response.errors));
        notify(`导入未执行，${response.errors.length} 行需要修正`, "warning");
      } else {
        setImportIssues([]);
        notify(`已导入 ${response.imported} 台设备`, "success");
      }
      await refresh();
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "导入失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        importingRef.current = false;
        setImporting(false);
      }
      input.value = "";
    }
  };

  const deleteDevice = async (device: Device) => {
    if (deletingDeviceIdsRef.current.has(device.id)) return;
    if (!await confirmAction({
      title: "删除设备",
      message: `确定删除设备 ${device.name ?? device.ip} 吗？历史巡检和备份记录会保留。`,
      confirmLabel: "删除设备",
      danger: true,
    })) return;
    const operations = operationEpochRef.current;
    let requestEpoch = invalidateWorkspaceRequests();
    if (!requestEpoch) return;
    deletingDeviceIdsRef.current.add(device.id);
    setDeletingDeviceIds((current) => [...new Set([...current, device.id])]);
    try {
      await api<void>(`/devices/${device.id}`, { method: "DELETE" });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return;
      requestEpoch = nextEpoch;
      setSelectedIds((current) => current.filter((id) => id !== device.id));
      if (operations.isOperationCurrent(requestEpoch)) notify("设备已删除，历史任务和备份记录已保留", "success");
      await refresh();
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "删除设备失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        deletingDeviceIdsRef.current.delete(device.id);
        setDeletingDeviceIds((current) => current.filter((id) => id !== device.id));
      }
    }
  };

  const cancelTask = async (task: Task) => {
    if (cancellingTaskIdsRef.current.has(task.id)) return;
    if (!await confirmAction({
      title: "取消巡检任务",
      message: `确定取消巡检任务“${task.name}”吗？已完成结果会保留。`,
      confirmLabel: "取消任务",
      danger: true,
    })) return;
    const operations = operationEpochRef.current;
    const navigationIntentId = taskNavigationIntentRef.current;
    const expectedActiveTaskId = activeTaskIdRef.current;
    let requestEpoch = invalidateWorkspaceRequests();
    if (!requestEpoch) return;
    cancellingTaskIdsRef.current.add(task.id);
    setCancellingTaskIds((current) => [...new Set([...current, task.id])]);
    try {
      const updated = await api<Task>(`/tasks/${task.id}/cancel`, { method: "POST" });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return;
      requestEpoch = nextEpoch;
      if (operations.isOperationCurrent(requestEpoch)) notify("任务已取消，已完成设备的结果已保留", "success");
      await refresh(null);
      if (!operations.isOperationCurrent(requestEpoch)) return;
      if (expectedActiveTaskId === updated.id
        && isTaskNavigationCurrent(taskNavigationIntentRef.current, navigationIntentId, activeTaskIdRef.current, expectedActiveTaskId)) {
        await selectTask(updated, false, navigationIntentId);
      }
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "取消任务失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        cancellingTaskIdsRef.current.delete(task.id);
        setCancellingTaskIds((current) => current.filter((id) => id !== task.id));
      }
    }
  };

  const retryTask = async (task: Task) => {
    if (!canRetryInspectionTask(task) || retryingTaskIdsRef.current.has(task.id)) return;
    const operations = operationEpochRef.current;
    const navigationIntentId = taskNavigationIntentRef.current;
    const expectedActiveTaskId = activeTaskIdRef.current;
    let requestEpoch = invalidateWorkspaceRequests();
    if (!requestEpoch) return;
    retryingTaskIdsRef.current.add(task.id);
    setRetryingTaskIds((current) => [...new Set([...current, task.id])]);
    try {
      const retried = await api<Task>(`/tasks/${task.id}/retry`, { method: "POST" });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return;
      requestEpoch = nextEpoch;
      if (operations.isOperationCurrent(requestEpoch)) notify(`已重新创建任务“${retried.name}”`, "success");
      await refresh(null);
      if (!operations.isOperationCurrent(requestEpoch)) return;
      if (expectedActiveTaskId === task.id
        && isTaskNavigationCurrent(taskNavigationIntentRef.current, navigationIntentId, activeTaskIdRef.current, expectedActiveTaskId)) {
        await selectTask(retried, true, navigationIntentId);
      }
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "重新执行任务失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        retryingTaskIdsRef.current.delete(task.id);
        setRetryingTaskIds((current) => current.filter((id) => id !== task.id));
      }
    }
  };

  const deleteTasks = async (selected: Task[]): Promise<boolean> => {
    if (!selected.length) return false;
    const ids = selected.map((task) => task.id);
    if (ids.some((id) => deletingTaskIdsRef.current.has(id))) return false;
    if (!await confirmAction({
      title: selected.length === 1 ? "删除巡检任务" : `删除 ${selected.length} 个巡检任务`,
      message: describeTaskDeletion(selected),
      confirmLabel: "确认删除",
      danger: true,
    })) return false;
    const operations = operationEpochRef.current;
    const navigationIntentId = taskNavigationIntentRef.current;
    const expectedActiveTaskId = activeTaskIdRef.current;
    let requestEpoch = invalidateWorkspaceRequests();
    if (!requestEpoch) return false;
    ids.forEach((id) => deletingTaskIdsRef.current.add(id));
    setDeletingTaskIds((current) => [...new Set([...current, ...ids])]);
    try {
      let deletedCount = selected.length;
      let skippedCount = 0;
      if (selected.length === 1) {
        await api<void>(`/tasks/${selected[0].id}`, { method: "DELETE" });
      } else {
        const response = await api<{ deleted: number; skipped: number[] }>("/tasks", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ task_ids: ids }) });
        deletedCount = response.deleted;
        skippedCount = response.skipped.length;
      }
      if (!operations.isOperationCurrent(requestEpoch)) return false;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return false;
      requestEpoch = nextEpoch;
      let taskToRefresh: Task | null = null;
      if (isTaskNavigationCurrent(taskNavigationIntentRef.current, navigationIntentId, activeTaskIdRef.current, expectedActiveTaskId)) {
        taskToRefresh = activeTaskAfterDeletion(activeTask, selected);
        if (taskToRefresh !== activeTask) {
          activeTaskIdRef.current = null;
          setActiveTask(null);
          setResults([]);
        }
      }
      notify(
        skippedCount
          ? `已删除 ${deletedCount} 个任务，${skippedCount} 个运行中任务未删除`
          : selected.length === 1
            ? "巡检任务已删除，设备清单和已导出的报告已保留"
            : `已删除 ${deletedCount} 个巡检任务，设备清单和已导出的报告已保留`,
        skippedCount ? "warning" : "success",
      );
      await refresh(taskToRefresh);
      if (!operations.isOperationCurrent(requestEpoch)) return false;
      return true;
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "删除任务失败"));
      return false;
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        ids.forEach((id) => deletingTaskIdsRef.current.delete(id));
        setDeletingTaskIds((current) => current.filter((id) => !ids.includes(id)));
      }
    }
  };

  const createBackup = async (deviceIds: number[] = selectedIds) => {
    const operations = operationEpochRef.current;
    if (backupInProgressRef.current || operations.requestsBlocked) return;
    if (!deviceIds.length) {
      notify("请先选择要备份的设备", "warning");
      return;
    }
    let requestEpoch = operations.capture();
    backupInProgressRef.current = true;
    setBackupInProgress(true);
    try {
      const response = await api<{ records: Array<{ status: string }> }>("/backup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(deviceIds) });
      if (!operations.isOperationCurrent(requestEpoch)) return;
      const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
      if (!nextEpoch) return;
      requestEpoch = nextEpoch;
      const successes = response.records.filter((record) => record.status === "success").length;
      const failures = response.records.length - successes;
      if (operations.isOperationCurrent(requestEpoch)) notify(`备份完成：成功 ${successes} 台，失败 ${failures} 台`, failures ? "warning" : "success");
      await refresh();
    } catch (err) {
      if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "备份失败"));
    } finally {
      if (operations.isCurrentSession(requestEpoch.sessionGeneration)) {
        backupInProgressRef.current = false;
        setBackupInProgress(false);
      }
    }
  };

  const clearAuthenticatedState = () => {
    workspaceRefreshInFlightRef.current = null;
    taskRefreshInFlight.current = null;
    taskDataRequestsRef.current.invalidate();
    testingConnectionsRef.current.clear();
    backupInProgressRef.current = false;
    exportingReportRef.current = false;
    deletingTaskIdsRef.current.clear();
    cancellingTaskIdsRef.current.clear();
    retryingTaskIdsRef.current.clear();
    deletingDeviceIdsRef.current.clear();
    confirmationResolverRef.current?.(false);
    confirmationResolverRef.current = null;
    taskNavigationIntentRef.current += 1;
    activeTaskIdRef.current = null;
    setDevices([]);
    setTasks([]);
    setSettings(null);
    setCatalog({ vendors: [], device_types: [] });
    setSelectedIds([]);
    setDeletingTaskIds([]);
    setCancellingTaskIds([]);
    setRetryingTaskIds([]);
    setDeletingDeviceIds([]);
    setTestingConnections(new Set());
    setBackupInProgress(false);
    setExportingReport(false);
    setExclusiveBusy(false);
    importingRef.current = false;
    setImporting(false);
    setImportIssues([]);
    setLoading(false);
    setActiveTask(null);
    setResults([]);
    setNotice(null);
    setError(null);
    setConfirmation(null);
    setDeviceDialogOpen(false);
    setTaskDialogOpen(false);
  };

  const logout = async () => {
    const logoutRequest = api<void>("/auth/logout", { method: "POST" });
    operationEpochRef.current.endSession();
    clearAuthenticatedState();
    localStorage.removeItem("inspection-token");
    setToken(null);
    setAuthNotice("已退出登录。");
    try {
      await logoutRequest;
    } catch {
      // Clearing the local token is sufficient even if the local service is unavailable.
    }
  };

  const reconnectBackend = () => {
    if (reconnectingBackendRef.current) return;
    reconnectingBackendRef.current = true;
    setReconnectingBackend(true);
    setBackendError(null);
    setBackendWaitSeconds(0);
    void restartLocalBackend()
      .then(() => setBackendAttempt((value) => value + 1))
      .catch(() => setBackendError("本机巡检服务重启失败，请关闭软件后重新打开。"))
      .finally(() => {
        reconnectingBackendRef.current = false;
        setReconnectingBackend(false);
      });
  };

  if (authInitialized === null) {
    if (backendError) {
      return <div className="loading-screen" role="alert"><span>{backendError}</span><Button variant="outline" disabled={reconnectingBackend} onClick={reconnectBackend}>{reconnectingBackend ? "重启中…" : "重新连接"}</Button></div>;
    }
    return <main className="startup-shell" role="status" aria-live="polite">
      <div className="startup-shell__body">
        <span className="startup-shell__mark" aria-hidden="true">巡</span>
        <strong>正在启动网络巡检</strong>
        <small>{backendWaitSeconds >= 3 ? "首次启动需要更长时间，请稍候…" : "正在准备本机巡检服务…"}</small>
        <span className="startup-shell__progress" aria-hidden="true"><span /></span>
        {backendWaitSeconds >= 3 && <span className="loading-elapsed">已等待 {backendWaitSeconds} 秒</span>}
      </div>
    </main>;
  }
  if (!token) {
    return <LoginScreen initialized={authInitialized} onAuthenticated={(value) => {
      operationEpochRef.current.beginSession();
      clearAuthenticatedState();
      localStorage.setItem("inspection-token", value);
      setAuthNotice(null);
      setError(null);
      setAuthInitialized(true);
      setToken(value);
    }} onError={setError} notice={authNotice} />;
  }
  const renderedSessionGeneration = operationEpochRef.current.sessionGeneration;

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand"><MonitorCog size={23} /><span>网络巡检</span></div>
        <div className="workspace-label">运维工作台</div>
        <nav>
          {navItems.map(({ id, label, icon: Icon }) => (
            <button key={id} className={`nav-item ${page === id ? "nav-item--active" : ""}`} onClick={() => navigateToPage(id)}>
              <Icon size={18} /><span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot"><span className="online-dot" /> 本机服务 {loading ? "同步中" : "已连接"}</div>
      </aside>

      <section className="main-panel">
        <header className="topbar">
          <div><div className="crumb">运维 / <b>{navItems.find((item) => item.id === page)?.label}</b></div><p>单机网络设备巡检</p></div>
          <div className="topbar-actions"><Button variant="outline" size="icon" onClick={() => setTheme(nextTheme(theme))} title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}>{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}</Button><Button variant="outline" size="icon" onClick={() => void refresh()} disabled={loading} title={loading ? "正在刷新" : "刷新"} aria-label={loading ? "正在刷新工作区" : "刷新工作区"}><RefreshCw className={loading ? "icon-spin" : undefined} size={17} /></Button><Button variant="outline" size="icon" onClick={() => void logout()} title="退出登录" aria-label="退出登录"><LogOut size={17} /></Button><span className="user-avatar">A</span></div>
        </header>

        {(notice || error) && <NoticeBanner message={error ?? notice?.message ?? ""} tone={error ? "error" : notice?.tone ?? "progress"} onDismiss={() => { setNotice(null); setError(null); }} />}

        <div ref={contentRef} className="content">
          {page === "dashboard" && <Dashboard devices={devices} tasks={tasks} onNewDevice={() => { setDeviceToEdit(null); setDeviceDialogOpen(true); }} onNewTask={() => setTaskDialogOpen(true)} onSelectTask={selectTask} />}
          {page === "devices" && <DevicesPage catalog={catalog} devices={filteredDevices} selectedIds={selectedIds} deletingDeviceIds={deletingDeviceIds} search={deviceSearch} onSearch={setDeviceSearch} onToggle={toggleDevice} onSelectAll={selectAll} onClearSelection={() => setSelectedIds([])} onNew={() => { setDeviceToEdit(null); setDeviceDialogOpen(true); }} onNewTask={() => setTaskDialogOpen(true)} onEdit={(device) => { setDeviceToEdit(device); setDeviceDialogOpen(true); }} onDelete={deleteDevice} onImport={importExcel} importing={importing} onTemplate={downloadTemplate} onExport={exportDevices} onTestConnection={testConnection} testingConnections={testingConnections} onBackup={createBackup} backupInProgress={backupInProgress} />}
          {page === "tasks" && <TasksPage tasks={tasks} devices={devices} deletingTaskIds={deletingTaskIds} cancellingTaskIds={cancellingTaskIds} retryingTaskIds={retryingTaskIds} onNew={() => setTaskDialogOpen(true)} onSelect={selectTask} onCancel={cancelTask} onRetry={retryTask} onDelete={deleteTasks} />}
          {page === "results" && <ResultsPage catalog={catalog} task={activeTask} results={results} operationEpoch={operationEpochRef.current.capture()} isReadCurrent={operationEpochRef.current.isCurrent} isOperationCurrent={operationEpochRef.current.isOperationCurrent} onExport={exportReport} exporting={exportingReport} onGoTasks={() => navigateToPage("tasks")} onNotice={(message, tone) => { if (operationEpochRef.current.isCurrentSession(renderedSessionGeneration)) notify(message, tone); }} onError={(message) => { if (operationEpochRef.current.isCurrentSession(renderedSessionGeneration)) setError(message); }} />}
          {page === "adapters" && <AdaptersPage catalog={catalog} />}
          {page === "backup" && <BackupPage devices={devices} onBackup={createBackup} backupInProgress={backupInProgress} onConfirm={confirmAction} onNotice={notify} />}
          {page === "settings" && <SettingsPage
            settings={settings}
            exclusiveBusy={exclusiveBusy}
            onSave={async (value) => {
              const operations = operationEpochRef.current;
              if (operations.requestsBlocked) {
                setError("独占操作正在进行，暂时无法保存设置");
                return;
              }
              let requestEpoch = operations.capture();
              try {
                const saved = await api<InspectionSettings>("/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) });
                if (!operations.isOperationCurrent(requestEpoch)) return;
                const nextEpoch = invalidateWorkspaceRequests(requestEpoch);
                if (!nextEpoch) return;
                requestEpoch = nextEpoch;
                setSettings(saved);
                if (operations.isOperationCurrent(requestEpoch)) notify("系统设置已保存，后续巡检任务将使用新参数", "success");
                await refresh();
              } catch (err) {
                if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "设置保存失败"));
              }
            }}
            onDatabaseBackup={async () => {
              const operations = operationEpochRef.current;
              if (operations.requestsBlocked) {
                setError("独占操作正在进行，暂时无法备份数据库");
                return;
              }
              const requestEpoch = operations.capture();
              try {
                const result = await downloadFile("/system/database-backup", "network_inspection_backup.db");
                if (operations.isOperationCurrent(requestEpoch) && result.status === "saved") notify(`数据库备份已保存：${result.path}`, "success");
              } catch (err) {
                if (operations.isOperationCurrent(requestEpoch)) setError(userErrorMessage(err, "数据库备份失败"));
              }
            }}
            onDatabaseRestore={async (file) => {
              const operations = operationEpochRef.current;
              if (operations.requestsBlocked) {
                setError("独占操作正在进行，暂时无法恢复数据库");
                return;
              }
              if (!await confirmAction({
                title: "恢复数据库",
                message: "恢复会覆盖当前数据库。系统会先创建一份恢复前备份，恢复成功后需要重新登录。",
                confirmLabel: "确认恢复",
                danger: true,
              })) return;
              if (hasPendingParentOperation()) {
                setError("请等待当前设备、任务、备份或连接操作完成后再恢复数据库");
                return;
              }
              const exclusiveOperation = operations.beginExclusive();
              if (!exclusiveOperation) {
                setError("独占操作正在进行，暂时无法恢复数据库");
                return;
              }
              setExclusiveBusy(true);
              taskDataRequestsRef.current.invalidate();
              workspaceRefreshInFlightRef.current = null;
              taskRefreshInFlight.current = null;
              setLoading(false);
              const body = new FormData();
              body.append("file", file);
              body.append("confirmed", "true");
              let sessionEnded = false;
              let failure: unknown = null;
              try {
                await api<{ ok: boolean }>("/system/database-restore", { method: "POST", body });
                if (!operations.isExclusiveCurrent(exclusiveOperation)) return;
                operations.finishExclusive(exclusiveOperation, { endSession: true });
                sessionEnded = true;
                clearAuthenticatedState();
                localStorage.removeItem("inspection-token");
                setPage("dashboard");
                setAuthNotice("数据库已恢复，请重新登录。");
                setToken(null);
              } catch (err) {
                if (operations.isExclusiveCurrent(exclusiveOperation)) failure = err;
              } finally {
                if (!sessionEnded && operations.finishExclusive(exclusiveOperation)) {
                  setExclusiveBusy(false);
                  await refresh();
                }
              }
              if (failure && operations.isOperationCurrent(exclusiveOperation)) {
                setError(userErrorMessage(failure, "数据库恢复失败"));
              }
            }}
            onPasswordChange={async (currentPassword, newPassword) => {
              const operations = operationEpochRef.current;
              if (operations.requestsBlocked) throw new Error("独占操作正在进行，暂时无法修改密码");
              if (hasPendingParentOperation()) throw new Error("请等待当前设备、任务、备份或连接操作完成后再修改密码");
              const exclusiveOperation = operations.beginExclusive();
              if (!exclusiveOperation) throw new Error("独占操作正在进行，暂时无法修改密码");
              setExclusiveBusy(true);
              taskDataRequestsRef.current.invalidate();
              workspaceRefreshInFlightRef.current = null;
              taskRefreshInFlight.current = null;
              setLoading(false);
              let sessionEnded = false;
              let failure: unknown = null;
              try {
                await api<void>("/auth/change-password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
                if (!operations.isExclusiveCurrent(exclusiveOperation)) return;
                operations.finishExclusive(exclusiveOperation, { endSession: true });
                sessionEnded = true;
                clearAuthenticatedState();
                localStorage.removeItem("inspection-token");
                setPage("dashboard");
                setAuthNotice("密码已修改，请使用新密码重新登录。");
                setToken(null);
              } catch (err) {
                if (operations.isExclusiveCurrent(exclusiveOperation)) failure = err;
              } finally {
                if (!sessionEnded && operations.finishExclusive(exclusiveOperation)) {
                  setExclusiveBusy(false);
                  await refresh();
                }
              }
              if (failure && operations.isOperationCurrent(exclusiveOperation)) throw failure;
            }}
          />}
          {page === "logs" && <LogsPage onConfirm={confirmAction} onNotice={notify} />}
        </div>
      </section>

      {isDeviceDialogOpen && <DeviceDialog catalog={catalog} device={deviceToEdit} operationEpoch={operationEpochRef.current.capture()} isOperationCurrent={operationEpochRef.current.isOperationCurrent} onClose={() => setDeviceDialogOpen(false)} onSaved={async (requestEpoch) => { if (!operationEpochRef.current.isOperationCurrent(requestEpoch)) return; const nextEpoch = invalidateWorkspaceRequests(requestEpoch); if (!nextEpoch) return; setDeviceDialogOpen(false); notify(deviceToEdit ? "设备已更新，未修改的凭据保持加密保存" : "设备已保存，凭据已使用 Windows DPAPI 加密", "success"); await refresh(); }} />}
      {isTaskDialogOpen && <TaskDialog catalog={catalog} devices={devices} selectedIds={selectedIds} operationEpoch={operationEpochRef.current.capture()} isOperationCurrent={operationEpochRef.current.isOperationCurrent} defaultCollectionMode={settings?.default_collection_mode ?? "auto"} onClose={() => setTaskDialogOpen(false)} onCreated={async (task, requestEpoch) => { if (!operationEpochRef.current.isOperationCurrent(requestEpoch)) return; const nextEpoch = invalidateWorkspaceRequests(requestEpoch); if (!nextEpoch) return; setTaskDialogOpen(false); navigateToPage("tasks"); notify(`巡检任务“${task.name}”已创建，正在执行`, "success"); await refresh(); }} />}
      {importIssues.length > 0 && <ImportErrorsDialog issues={importIssues} onClose={() => setImportIssues([])} />}
      {confirmation && <ConfirmationDialog options={confirmation} onCancel={() => settleConfirmation(false)} onConfirm={() => settleConfirmation(true)} />}
    </main>
  );
}

function NoticeBanner({ message, tone, onDismiss }: { message: string; tone: NoticeTone | "error"; onDismiss: () => void }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" || tone === "error" ? CircleAlert : Info;
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;
  useEffect(() => {
    // A completed action needs no acknowledgement, so it clears itself. Keying
    // the effect on the message and tone restarts the timer when a new one
    // arrives and stops a stale timer from wiping the next message early.
    const delay = noticeAutoDismissMs(tone);
    if (delay === null) return;
    const timer = window.setTimeout(() => dismissRef.current(), delay);
    return () => window.clearTimeout(timer);
  }, [message, tone]);
  return <div className={`notice notice--${tone}`} role={tone === "error" ? "alert" : "status"}>
    <span className="notice__body"><span className="notice__icon"><Icon size={16} /></span><span>{message}</span></span>
    <button type="button" onClick={onDismiss} aria-label="关闭提示"><X size={16} /></button>
  </div>;
}

function ImportErrorsDialog({ issues, onClose }: { issues: ImportIssue[]; onClose: () => void }) {
  const dialogRef = useModalBehavior(onClose);
  return <div className="modal-layer"><section ref={dialogRef} className="dialog import-errors-dialog" role="dialog" aria-modal="true" aria-labelledby="import-errors-title" tabIndex={-1}>
    <div className="dialog-heading"><div><h2 id="import-errors-title">Excel 导入未执行</h2><p>请修正以下数据后重新导入，当前文件中的设备均未保存。</p></div><button type="button" onClick={onClose} aria-label="关闭"><X /></button></div>
    <div className="import-error-list">{issues.map((issue, index) => <div key={`${issue.row}-${index}`} className="import-error-item"><strong>第 {issue.row} 行</strong><span>{issue.reason}</span></div>)}</div>
    <div className="dialog-actions"><Button type="button" onClick={onClose}>知道了</Button></div>
  </section></div>;
}

function useModalBehavior(onClose: () => void, closeDisabled = false) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const closeDisabledRef = useRef(closeDisabled);
  closeRef.current = onClose;
  closeDisabledRef.current = closeDisabled;
  useEffect(() => {
    const dialog = dialogRef.current;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!dialog) return undefined;
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex='-1'])",
    ));
    (focusable()[0] ?? dialog).focus();
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!closeDisabledRef.current) closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const elements = focusable();
      if (!elements.length) {
        event.preventDefault();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      if (!dialog.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previous?.focus();
    };
  }, []);
  return dialogRef;
}

function ConfirmationDialog({ options, onCancel, onConfirm }: { options: ConfirmationOptions; onCancel: () => void; onConfirm: () => void }) {
  const onClose = onCancel;
  const dialogRef = useModalBehavior(onClose);
  return <div className="modal-layer"><section ref={dialogRef} className="dialog confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-dialog-title" tabIndex={-1}>
    <div className="dialog-heading"><div><h2 id="confirmation-dialog-title">{options.title}</h2></div><button type="button" onClick={onCancel} aria-label="关闭"><X /></button></div>
    <p className="confirmation-message">{options.message}</p>
    <div className="dialog-actions"><Button type="button" variant="outline" onClick={onCancel}>取消</Button><Button type="button" variant={options.danger ? "destructive" : "default"} onClick={onConfirm}>{options.confirmLabel}</Button></div>
  </section></div>;
}


function Dashboard({ devices, tasks, onNewDevice, onNewTask, onSelectTask }: { devices: Device[]; tasks: Task[]; onNewDevice: () => void; onNewTask: () => void; onSelectTask: (task: Task) => void }) {
  const latest = tasks.slice(0, 5);
  return <>
    <section className="page-heading"><div><h1>巡检概览</h1><p>查看最近一次采集情况并快速开始巡检</p></div><div className="button-row"><Button variant="outline" onClick={onNewDevice}><Plus size={17} />添加设备</Button><Button onClick={onNewTask} disabled={!devices.some((device) => device.is_active)}><Play size={17} />新建巡检</Button></div></section>
    <div className="metric-grid">
      <Metric icon={<Network />} label="设备总数" value={devices.length} note={`${devices.filter((d) => d.is_active).length} 台启用`} tone="blue" />
      <Metric icon={<CheckCircle2 />} label="最近采集完成" value={tasks[0]?.success_devices ?? 0} note={tasks[0] ? tasks[0].name : "暂无任务"} tone="green" />
      <Metric icon={<CircleAlert />} label="最近任务异常" value={tasks[0]?.failed_devices ?? 0} note={tasks[0] ? tasks[0].name : "暂无任务"} tone="amber" />
      <Metric icon={<Activity />} label="任务总数" value={tasks.length} note="本地历史已保存" tone="purple" />
    </div>
    <Card className="flat-card"><CardHeader><CardTitle>最近任务</CardTitle></CardHeader><CardContent className="table-wrap"><TaskTable tasks={latest} onSelect={onSelectTask} /></CardContent></Card>
  </>;
}

function Metric({ icon, label, value, note, tone }: { icon: React.ReactNode; label: string; value: number; note: string; tone: string }) {
  return <Card className="metric-card"><CardContent><div className={`metric-icon metric-icon--${tone}`}>{icon}</div><div><p>{label}</p><strong>{value}</strong><small>{note}</small></div></CardContent></Card>;
}

function DevicesPage({ catalog, devices, selectedIds, deletingDeviceIds, search, onSearch, onToggle, onSelectAll, onClearSelection, onNew, onNewTask, onEdit, onDelete, onImport, importing, onTemplate, onExport, onTestConnection, testingConnections, onBackup, backupInProgress }: { catalog: DeviceCatalog; devices: Device[]; selectedIds: number[]; deletingDeviceIds: number[]; search: string; onSearch: (value: string) => void; onToggle: (id: number) => void; onSelectAll: (ids: number[]) => void; onClearSelection: () => void; onNew: () => void; onNewTask: () => void; onEdit: (device: Device) => void; onDelete: (device: Device) => void; onImport: (event: ChangeEvent<HTMLInputElement>) => void; importing: boolean; onTemplate: () => void; onExport: () => void; onTestConnection: (device: Device, protocol: "snmp" | "ssh") => Promise<void>; testingConnections: Set<string>; onBackup: () => void; backupInProgress: boolean }) {
  const [vendor, setVendor] = useState("");
  const [group, setGroup] = useState("");
  const [deviceType, setDeviceType] = useState("");
  const [activeState, setActiveState] = useState("");
  const [sortField, setSortField] = useState<"name" | "ip" | "vendor" | "device_type" | "group_name">("ip");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("asc");
  const [currentPage, setCurrentPage] = useState(0);
  const pageSize = 20;
  const vendors = vendorOptions(catalog);
  const types = deviceTypeOptions(catalog);
  const groups = useMemo(() => [...new Set(devices.map((device) => device.group_name).filter((value): value is string => Boolean(value)))].sort(), [devices]);
  const visibleDevices = useMemo(() => devices.filter((device) =>
    (!vendor || device.vendor === vendor)
    && (!group || device.group_name === group)
    && (!deviceType || device.device_type === deviceType)
    && (!activeState || String(device.is_active) === activeState)
  ), [devices, vendor, group, deviceType, activeState]);
  const sortedDevices = useMemo(() => [...visibleDevices].sort((left, right) => {
    const displayValue = (device: Device) => sortField === "vendor"
      ? vendorText(catalog, device.vendor)
      : sortField === "device_type"
        ? deviceTypeText(catalog, device.device_type)
        : String(device[sortField] ?? "");
    return displayValue(left).localeCompare(displayValue(right), "zh-CN", { numeric: true }) * (sortDirection === "asc" ? 1 : -1);
  }), [visibleDevices, catalog, sortField, sortDirection]);
  const pageCount = Math.max(1, Math.ceil(sortedDevices.length / pageSize));
  const pageDevices = sortedDevices.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  const allVisibleSelected = sortedDevices.length > 0 && sortedDevices.every((device) => selectedIds.includes(device.id));
  useEffect(() => setCurrentPage(0), [search, vendor, group, deviceType, activeState]);
  useEffect(() => setCurrentPage((page) => Math.min(page, pageCount - 1)), [pageCount]);
  const changeSort = (field: typeof sortField) => {
    if (sortField === field) setSortDirection((direction) => direction === "asc" ? "desc" : "asc");
    else { setSortField(field); setSortDirection("asc"); }
  };
  const sortHeader = (field: typeof sortField, label: string) => <th aria-sort={sortField === field ? (sortDirection === "asc" ? "ascending" : "descending") : undefined}><button type="button" className="table-sort" onClick={() => changeSort(field)}>{label}{sortField === field && <span aria-hidden="true">{sortDirection === "asc" ? "↑" : "↓"}</span>}</button></th>;
  return <>
    <section className="page-heading"><div><h1>设备管理</h1><p>维护厂商、连接方式与加密凭据</p></div><div className="button-row"><Button variant="outline" onClick={onTemplate}><Download size={16} />模板</Button><Button variant="outline" onClick={onExport}><Download size={16} />导出设备</Button><label className={`button-label ${importing ? "button-label--disabled" : ""}`}><Upload size={16} />{importing ? "导入中…" : "导入 Excel"}<input type="file" accept=".xlsx" disabled={importing} onChange={onImport} /></label><Button onClick={onNew}><Plus size={17} />添加设备</Button></div></section>
    <Card className="flat-card"><CardContent className="device-toolbar"><div className="search-box"><Search size={17} /><Input value={search} onChange={(e) => onSearch(e.target.value)} placeholder="搜索名称、IP、厂商或分组" /></div><div className="device-filters"><select aria-label="按厂商筛选" value={vendor} onChange={(event) => setVendor(event.target.value)}><option value="">全部厂商</option>{vendors.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select><select aria-label="按分组筛选" value={group} onChange={(event) => setGroup(event.target.value)}><option value="">全部分组</option>{groups.map((item) => <option key={item} value={item}>{item}</option>)}</select><select aria-label="按类型筛选" value={deviceType} onChange={(event) => setDeviceType(event.target.value)}><option value="">全部类型</option>{types.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select><select aria-label="按状态筛选" value={activeState} onChange={(event) => setActiveState(event.target.value)}><option value="">全部状态</option><option value="true">启用</option><option value="false">停用</option></select></div></CardContent>
      {selectedIds.length > 0 && <CardContent className="device-bulk-bar"><span>已选择 <strong>{selectedIds.length}</strong> 台设备</span><div><Button size="sm" onClick={onNewTask}><Play size={15} />发起巡检</Button><Button size="sm" variant="outline" disabled={backupInProgress} onClick={onBackup}><Archive size={15} />{backupInProgress ? "备份中…" : "备份配置"}</Button><Button size="sm" variant="ghost" onClick={onClearSelection}>清空选择</Button></div></CardContent>}
      <CardContent className="table-wrap"><table className="device-table"><thead><tr><th><input aria-label="选择当前筛选设备" type="checkbox" checked={allVisibleSelected} onChange={() => onSelectAll(sortedDevices.map((device) => device.id))} /></th>{sortHeader("name", "设备 / 管理 IP")}{sortHeader("vendor", "厂商 / 类型")}<th>采集方式</th>{sortHeader("group_name", "分组")}<th>状态</th><th>操作</th></tr></thead><tbody>{pageDevices.map((device) => <tr key={device.id}><td><input aria-label={`选择 ${device.ip}`} type="checkbox" checked={selectedIds.includes(device.id)} onChange={() => onToggle(device.id)} /></td><td className="device-identity"><strong>{device.name ?? device.ip}</strong><span className="mono">{device.ip}</span></td><td className="device-meta-nowrap"><strong>{vendorText(catalog, device.vendor)}</strong><span>{deviceTypeText(catalog, device.device_type)}</span></td><td className="device-meta-nowrap"><span className="protocols">{device.has_snmp_credentials && <b>SNMP {device.snmp_version}</b>}{device.has_ssh && <b>SSH</b>}</span></td><td className="device-group-cell">{device.group_name ?? "-"}</td><td className="device-meta-nowrap"><span className={`status-pill status-pill--${device.is_active ? "neutral-on" : "neutral"}`}>{device.is_active ? "已启用" : "已停用"}</span></td><td className="row-actions">{device.has_snmp_credentials && <button className="row-action" disabled={testingConnections.has(`${device.id}:snmp`)} onClick={() => void onTestConnection(device, "snmp")} title="测试 SNMP 连通性" aria-label={`测试 ${device.ip} 的 SNMP 连通性`}><Network size={16} /></button>}{device.has_ssh && <button className="row-action" disabled={testingConnections.has(`${device.id}:ssh`)} onClick={() => void onTestConnection(device, "ssh")} title="测试 SSH 连通性" aria-label={`测试 ${device.ip} 的 SSH 连通性`}><Terminal size={16} /></button>}<button className="row-action" onClick={() => onEdit(device)} title="编辑设备" aria-label={`编辑 ${device.ip}`}><Pencil size={16} /></button><button className="row-action row-action--danger" disabled={deletingDeviceIds.includes(device.id)} onClick={() => onDelete(device)} title={deletingDeviceIds.includes(device.id) ? "正在删除设备" : "删除设备"} aria-label={deletingDeviceIds.includes(device.id) ? `正在删除 ${device.ip}` : `删除 ${device.ip}`}><Trash2 size={16} /></button></td></tr>)}{!pageDevices.length && <tr><td colSpan={7} className="empty">没有符合当前筛选条件的设备。</td></tr>}</tbody></table></CardContent><CardContent className="log-pagination"><span>共 {sortedDevices.length} 台，已选择 {selectedIds.length} 台</span><div><Button variant="outline" size="sm" disabled={currentPage === 0} onClick={() => setCurrentPage((page) => Math.max(0, page - 1))}>上一页</Button><span className="table-page">{currentPage + 1} / {pageCount}</span><Button variant="outline" size="sm" disabled={currentPage >= pageCount - 1} onClick={() => setCurrentPage((page) => Math.min(pageCount - 1, page + 1))}>下一页</Button></div></CardContent></Card>
  </>;
}

function TasksPage({ tasks, devices, deletingTaskIds, cancellingTaskIds, retryingTaskIds, onNew, onSelect, onCancel, onRetry, onDelete }: { tasks: Task[]; devices: Device[]; deletingTaskIds: number[]; cancellingTaskIds: number[]; retryingTaskIds: number[]; onNew: () => void; onSelect: (task: Task) => void; onCancel: (task: Task) => Promise<void>; onRetry: (task: Task) => Promise<void>; onDelete: (tasks: Task[]) => Promise<boolean> }) {
  const [selectedTaskIds, setSelectedTaskIds] = useState<number[]>([]);
  const [taskFilter, setTaskFilter] = useState("all");
  const summary = useMemo(() => summarizeTasks(tasks), [tasks]);
  const visibleTasks = useMemo(() => filterTasks(tasks, taskFilter), [tasks, taskFilter]);
  const presentedTasks = useMemo(() => partitionVisibleTasks(visibleTasks), [visibleTasks]);
  const deletableVisibleIds = useMemo(() => deletableTaskIds(presentedTasks.history), [presentedTasks.history]);
  // A task can be filtered away or finish deleting while still selected, so the
  // selection is narrowed to what is actually on screen before it is used.
  const selectedVisibleIds = useMemo(() => selectedTaskIds.filter((id) => deletableVisibleIds.includes(id)), [selectedTaskIds, deletableVisibleIds]);
  const toggleTask = (id: number) => setSelectedTaskIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const allVisibleSelected = deletableVisibleIds.length > 0 && selectedVisibleIds.length === deletableVisibleIds.length;
  const deletingSelectedTasks = selectedVisibleIds.some((id) => deletingTaskIds.includes(id));
  const toggleAllVisible = () => setSelectedTaskIds((current) => allVisibleSelected ? current.filter((id) => !deletableVisibleIds.includes(id)) : [...new Set([...current, ...deletableVisibleIds])]);
  const deleteSelected = async () => {
    const selected = presentedTasks.history.filter((task) => selectedVisibleIds.includes(task.id));
    if (!selected.length) return;
    if (await onDelete(selected)) setSelectedTaskIds([]);
  };
  return <>
    <section className="page-heading">
      <div><h1>巡检任务</h1><p>共 {tasks.length} 个任务，实时跟踪执行进度与结果</p></div>
      <Button disabled={!devices.length} onClick={onNew}><Plus size={16} />新建任务</Button>
    </section>
    {presentedTasks.active.length > 0 && <section className="active-task-list" aria-label="正在执行的巡检任务">
      {presentedTasks.active.map((task) => <ActiveTaskCard key={task.id} task={task} cancellingTaskIds={cancellingTaskIds} onCancel={onCancel} />)}
    </section>}
    <section className="task-summary">
      <div className="task-summary__card task-summary__card--total"><span className="task-summary__icon"><ClipboardCheck size={19} /></span><div><strong>{summary.total}</strong><span>任务总数</span></div></div>
      <div className="task-summary__card task-summary__card--active"><span className="task-summary__icon"><Activity size={19} /></span><div><strong>{summary.active}</strong><span>正在执行</span></div></div>
      <div className="task-summary__card task-summary__card--completed"><span className="task-summary__icon"><CheckCircle2 size={19} /></span><div><strong>{summary.completed}</strong><span>已完成</span></div></div>
      <div className="task-summary__card task-summary__card--attention"><span className="task-summary__icon"><CircleAlert size={19} /></span><div><strong>{summary.attention}</strong><span>异常 / 取消</span></div></div>
    </section>
    <div className="task-filters" aria-label="任务状态筛选">
      {TASK_FILTERS.map((filter) => <button key={filter.id} type="button" className={`task-filter ${taskFilter === filter.id ? "task-filter--active" : ""}`} onClick={() => setTaskFilter(filter.id)} aria-pressed={taskFilter === filter.id}>{filter.label}</button>)}
    </div>
    {presentedTasks.history.length > 0 && <Card className="flat-card task-list">{selectedVisibleIds.length > 0 && <CardContent className="task-bulk-bar"><span>已选择 {selectedVisibleIds.length} 个任务</span><Button variant="outline" size="sm" className="task-bulk-bar__delete" disabled={deletingSelectedTasks} onClick={() => void deleteSelected()}><Trash2 size={16} />{deletingSelectedTasks ? "删除中…" : "删除所选"}</Button></CardContent>}<CardContent className="table-wrap"><TaskTable tasks={presentedTasks.history} onSelect={onSelect} onCancel={onCancel} onRetry={onRetry} onDelete={onDelete} deletingTaskIds={deletingTaskIds} cancellingTaskIds={cancellingTaskIds} retryingTaskIds={retryingTaskIds} selectedIds={selectedVisibleIds} onToggle={toggleTask} onToggleAll={toggleAllVisible} allSelected={allVisibleSelected} selectableCount={deletableVisibleIds.length} /></CardContent></Card>}
    {visibleTasks.length === 0 && <Card className="flat-card task-list"><div className="empty">暂无符合当前状态的巡检任务。</div></Card>}
  </>;
}

function ActiveTaskCard({ task, cancellingTaskIds, onCancel }: { task: Task; cancellingTaskIds: number[]; onCancel: (task: Task) => Promise<void> }) {
  const percent = taskProgressPercent(task);
  const running = task.status === "running";
  const cancelling = task.status === "cancelled" && !task.finished_at;
  return <article className="active-task-card">
    <div className="active-task-card__heading">
      <div className="active-task-card__status"><span className={`active-task-card__dot ${running ? "active-task-card__dot--running" : ""}`} /><div><strong>{cancelling ? "正在取消，等待当前设备收尾..." : running ? "正在执行巡检..." : "等待执行..."}</strong><span>{task.name}</span></div></div>
      {!cancelling && <button type="button" className="active-task-card__cancel" disabled={cancellingTaskIds.includes(task.id)} onClick={() => void onCancel(task)}><X size={14} />{cancellingTaskIds.includes(task.id) ? "取消中…" : "取消"}</button>}
    </div>
    <div className="active-task-progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={`${task.name} 巡检进度`}>
      <div className={`active-task-progress__fill active-task-progress__fill--${taskProgressTone(task.status)}`} style={{ width: `${percent}%` }} />
    </div>
    <div className="active-task-card__meta"><strong>{percent}%</strong><span>{task.completed_devices}/{task.total_devices} 台</span><span>已采集 {task.success_devices}</span><span>采集失败 {task.failed_devices}</span></div>
  </article>;
}

function TaskTable({ tasks, onSelect, onCancel, onRetry, onDelete, deletingTaskIds = [], cancellingTaskIds = [], retryingTaskIds = [], selectedIds, onToggle, onToggleAll, allSelected, selectableCount }: { tasks: Task[]; onSelect: (task: Task) => void; onCancel?: (task: Task) => Promise<void>; onRetry?: (task: Task) => Promise<void>; onDelete?: (tasks: Task[]) => Promise<boolean>; deletingTaskIds?: number[]; cancellingTaskIds?: number[]; retryingTaskIds?: number[]; selectedIds?: number[]; onToggle?: (id: number) => void; onToggleAll?: () => void; allSelected?: boolean; selectableCount?: number }) {
  const selectable = Boolean(onToggle);
  return <table className={`task-table ${selectable ? "task-table--selectable" : ""}`}><thead><tr>{selectable && <th className="task-select-column"><input aria-label="选择当前可删除的任务" type="checkbox" checked={Boolean(allSelected)} disabled={!selectableCount} onChange={() => onToggleAll?.()} /></th>}<th>任务</th><th>巡检方案</th><th>设备</th><th className="progress-cell">执行进度</th><th>结果分布</th><th>状态</th><th className="task-actions-column">操作</th></tr></thead><tbody>{tasks.map((task) => {
    const percent = taskProgressPercent(task);
    const uncovered = task.unsupported_devices + task.not_applicable_devices + task.cancelled_devices;
    return <tr key={task.id}>
      {selectable && <td className="task-select-column"><input aria-label={`选择任务 ${task.name}`} type="checkbox" disabled={!canDeleteInspectionTask(task)} checked={Boolean(selectedIds?.includes(task.id))} onChange={() => onToggle?.(task.id)} /></td>}
      <td className="task-name-cell"><strong>{task.name}</strong><span>{formatDateTime(task.created_at)}</span></td>
      <td><div className="task-plan"><strong>{task.inspection_level === "quick" ? "快速巡检" : "完整巡检"}</strong><span>{task.collection_mode === "auto" ? "自动采集" : `仅 ${task.collection_mode.toUpperCase()}`}</span></div></td>
      <td><div className="task-device-count"><strong>{task.total_devices}</strong><span>{task.completed_devices} 已处理</span></div></td>
      <td className="progress-cell">
        <div className="task-progress__labels"><span>{task.completed_devices}/{task.total_devices} 台</span><strong>{percent}%</strong></div>
        <div className="task-progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100} aria-label={`${task.name} 巡检进度`}>
          <div className={`task-progress__fill task-progress__fill--${taskProgressTone(task.status)}`} style={{ width: `${percent}%` }} />
        </div>
      </td>
      <td><div className="task-result-counts"><span className="task-result-counts__success" title="采集完成"><CheckCircle2 size={13} />{task.success_devices}</span><span className="task-result-counts__failed" title="采集失败"><X size={13} />{task.failed_devices}</span><span className="task-result-counts__uncovered" title={`未覆盖：不支持 ${task.unsupported_devices}、不适用 ${task.not_applicable_devices}、已取消 ${task.cancelled_devices}`}><Info size={13} />{uncovered}</span></div></td>
      <td><StatusPill value={task.status} /></td>
      <td className="row-actions task-actions-column">{onCancel && ["queued", "running"].includes(task.status) && <button className="row-action row-action--danger" disabled={cancellingTaskIds.includes(task.id)} onClick={() => void onCancel(task)} title={cancellingTaskIds.includes(task.id) ? "正在取消任务" : "取消任务"} aria-label={cancellingTaskIds.includes(task.id) ? `正在取消任务 ${task.name}` : `取消任务 ${task.name}`}><X size={16} /></button>}{onRetry && canRetryInspectionTask(task) && <button className="row-action" disabled={retryingTaskIds.includes(task.id)} onClick={() => void onRetry(task)} title={retryingTaskIds.includes(task.id) ? "正在重试任务" : "重新执行任务"} aria-label={retryingTaskIds.includes(task.id) ? `正在重试任务 ${task.name}` : `重新执行任务 ${task.name}`}><RotateCcw size={16} /></button>}{onDelete && canDeleteInspectionTask(task) && <button className="row-action row-action--danger" disabled={deletingTaskIds.includes(task.id)} onClick={() => void onDelete([task])} title={deletingTaskIds.includes(task.id) ? "正在删除任务" : "删除任务"} aria-label={deletingTaskIds.includes(task.id) ? `正在删除任务 ${task.name}` : `删除任务 ${task.name}`}><Trash2 size={16} /></button>}<button className="row-action" onClick={() => onSelect(task)} title="查看结果" aria-label={`查看任务 ${task.name} 的巡检结果`}><ChevronRight size={18} /></button></td>
    </tr>;
  })}{!tasks.length && <tr><td colSpan={selectable ? 8 : 7} className="empty">暂无巡检任务。</td></tr>}</tbody></table>;
}

// The device message is copied from a failed item's reason; name that item
// so "no parsable value" says which check could not be read.
function failedInspectionItemId(result: Result) {
  const items = result.metrics?.inspection_items;
  if (!items || typeof items !== "object" || Array.isArray(items)) return null;
  const matches = Object.entries(items as Record<string, { conclusion?: string; downgrade_reason?: string | null }>)
    .filter(([, item]) => item?.conclusion === result.conclusion && item.downgrade_reason === result.message);
  // When every check failed for the same reason the device itself failed;
  // naming one check would wrongly narrow the problem.
  return matches.length === 1 ? matches[0][0] : null;
}

function resultFindingText(result: Result) {
  const finding = primaryInspectionFinding(result);
  // A device problem is the finding even when the result also carries an
  // informational note such as "SSH supplementary collection skipped".
  if (finding && ["warning", "critical"].includes(result.conclusion)) {
    return `${inspectionItemLabel(finding.id)}：${formatInspectionItemValue(finding.id, finding.value)}`;
  }
  if (result.message) {
    const text = userErrorMessage(result.message, "设备返回了未识别的异常，请查看运行日志");
    const failedItem = failedInspectionItemId(result);
    return failedItem ? `${inspectionItemLabel(failedItem)}：${text}` : text;
  }
  if (finding) return `${inspectionItemLabel(finding.id)}：${formatInspectionItemValue(finding.id, finding.value)}`;
  if (["connection_failed", "authentication_failed", "data_fetch_failed"].includes(result.conclusion)) {
    return result.failure_stage
      ? userErrorMessage(result.failure_stage, "未获取到巡检数据，请检查连接配置")
      : "未获取到巡检数据，请检查连接配置";
  }
  if (result.conclusion === "unsupported") return "当前设备或系统版本不在已验证的采集范围内";
  if (result.conclusion === "not_applicable") return "该检查不适用于当前设备";
  if (result.conclusion === "cancelled") return "任务取消前未完成该设备采集";
  return "本次检查未发现异常";
}

function ResultsPage({ catalog, task, results, operationEpoch, isReadCurrent, isOperationCurrent, onExport: exportReport, exporting, onGoTasks, onNotice, onError }: { catalog: DeviceCatalog; task: Task | null; results: Result[]; operationEpoch: OperationEpoch; isReadCurrent: (epoch: OperationEpoch) => boolean; isOperationCurrent: (epoch: OperationEpoch) => boolean; onExport: () => Promise<void>; exporting: boolean; onGoTasks: () => void; onNotice: (message: string, tone: NoticeTone) => void; onError: (message: string) => void }) {
  const [selectedResultId, setSelectedResultId] = useState<number | null>(null);
  const [resultFilter, setResultFilter] = useState("all");
  const [resultSearch, setResultSearch] = useState("");
  const [rawOutput, setRawOutput] = useState<string | null>(null);
  const [rawOutputStatus, setRawOutputStatus] = useState("not_available");
  const [rawOutputError, setRawOutputError] = useState<string | null>(null);
  const [loadingRawOutput, setLoadingRawOutput] = useState(false);
  const [wrapRawOutput, setWrapRawOutput] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const screenshotRef = useRef<HTMLDivElement>(null);
  const normalizedSearch = resultSearch.trim().toLocaleLowerCase("zh-CN");
  const visibleResults = useMemo(() => sortResultsByAttention(filterResults(results, resultFilter)).filter((result) => (
    !normalizedSearch
    || (result.device_name ?? "").toLocaleLowerCase("zh-CN").includes(normalizedSearch)
    || result.device_ip.toLocaleLowerCase("zh-CN").includes(normalizedSearch)
  )), [results, resultFilter, normalizedSearch]);
  const visibleSelectedId = keepSelectionVisible(selectedResultId, visibleResults);
  const selectedResult = results.find((result) => result.id === visibleSelectedId) ?? null;

  useEffect(() => {
    if (selectedResultId !== null && visibleSelectedId === null) setSelectedResultId(null);
  }, [selectedResultId, visibleSelectedId]);

  useEffect(() => {
    let cancelled = false;
    setRawOutput(null);
    setRawOutputError(null);
    setLoadingRawOutput(false);
    setRawOutputStatus(selectedResult?.raw_output_status ?? "not_available");
    if (!task || !selectedResult) return () => { cancelled = true; };
    if (selectedResult.raw_output) {
      setRawOutput(selectedResult.raw_output);
      setRawOutputStatus(selectedResult.raw_output_status ?? "complete");
      return () => { cancelled = true; };
    }
    if (["not_available", "expired"].includes(selectedResult.raw_output_status ?? "not_available")) return () => { cancelled = true; };
    setLoadingRawOutput(true);
    void api<{ status: string; content: string | null }>(`/tasks/${task.id}/results/${selectedResult.id}/raw-output`)
      .then((response) => {
        if (cancelled || !isReadCurrent(operationEpoch)) return;
        setRawOutputStatus(response.status);
        setRawOutput(response.content);
      })
      .catch((err) => {
        if (!cancelled && isReadCurrent(operationEpoch)) setRawOutputError(userErrorMessage(err, "无法读取原始采集输出"));
      })
      .finally(() => {
        if (!cancelled && isReadCurrent(operationEpoch)) setLoadingRawOutput(false);
      });
    return () => { cancelled = true; };
  }, [task?.id, selectedResult?.id, selectedResult?.raw_output_status, selectedResult?.raw_output, operationEpoch.sessionGeneration, operationEpoch.workspaceGeneration, operationEpoch.operationGeneration]);

  const captureResult = async () => {
    if (capturing || !task || !selectedResult || !screenshotRef.current) return;
    setCapturing(true);
    try {
      const canvas = await renderScreenshotCanvas(screenshotRef.current);
      const image = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
      if (!image) throw new Error("无法生成 PNG 截图");
      const body = new FormData();
      body.append("task_id", String(task.id));
      body.append("result_id", String(selectedResult.id));
      body.append("image", image, "inspection-result.png");
      if (!isOperationCurrent(operationEpoch)) return;
      const saved = await api<{ filename: string; file_path: string }>("/screenshots", { method: "POST", body });
      if (isOperationCurrent(operationEpoch)) onNotice(`截图已保存：${saved.file_path}`, "success");
    } catch (err) {
      if (isOperationCurrent(operationEpoch)) onError(userErrorMessage(err, "截图保存失败"));
    } finally {
      setCapturing(false);
    }
  };

  const copyRawOutput = async () => {
    if (!rawOutput) return;
    try {
      await navigator.clipboard.writeText(rawOutput);
      onNotice("原始采集输出已复制", "success");
    } catch (err) {
      onError(userErrorMessage(err, "无法复制原始采集输出"));
    }
  };

  if (!task) return <div className="empty-state"><ClipboardCheck size={32} /><h2>选择一个巡检任务</h2><p>任务完成后可查看每台设备的采集来源、状态与原始结果。</p><Button onClick={onGoTasks}>前往巡检任务</Button></div>;

  const summary = summarizeResults(results);
  const secondaryFilters = RESULT_FILTERS.filter((filter) => !["all", "normal"].includes(filter.id));
  return <>
    <section className="page-heading result-page-heading">
      <div><h1>{task.name}</h1><p>{task.completed_devices}/{task.total_devices} 台已处理 · {formatDateTime(task.finished_at ?? task.created_at)}</p></div>
      <div className="button-row"><Button variant="outline" onClick={onGoTasks}>返回任务</Button><Button disabled={!canExportInspectionReport(task) || exporting} onClick={() => void exportReport()}><FileSpreadsheet size={16} />{exporting ? "导出中…" : "导出 Excel"}</Button></div>
    </section>

    <section className="result-summary result-summary--five" aria-label="巡检结果汇总">
      <button type="button" className="result-summary__card" onClick={() => setResultFilter("all")}><span>检查设备</span><strong>{summary.total}</strong></button>
      <button type="button" className="result-summary__card result-summary__card--critical" onClick={() => setResultFilter("critical")}><span>严重</span><strong>{resultFilterCount(results, "critical")}</strong></button>
      <button type="button" className="result-summary__card result-summary__card--warning" onClick={() => setResultFilter("warning")}><span>警告</span><strong>{resultFilterCount(results, "warning")}</strong></button>
      <button type="button" className="result-summary__card result-summary__card--failed" onClick={() => setResultFilter("failed")}><span>采集失败</span><strong>{summary.failed}</strong></button>
      <button type="button" className="result-summary__card result-summary__card--normal" onClick={() => setResultFilter("normal")}><span>正常</span><strong>{summary.normal}</strong></button>
    </section>
    {(summary.uncovered > 0 || summary.devicesWithUncoveredItems > 0 || summary.cancelled > 0) && <div className="result-coverage-note"><Info size={15} /><span>设备级未覆盖 {summary.uncovered} 台（不支持或不适用）；检查项未覆盖 {summary.devicesWithUncoveredItems} 台；已取消 {summary.cancelled} 台</span></div>}

    <Card className="flat-card result-list-card">
      <CardContent className="result-toolbar">
        <div className="result-filters" aria-label="主要结果筛选">
          {PRIMARY_RESULT_FILTERS.map((filter) => <button key={filter.id} type="button" className={`result-filter ${resultFilter === filter.id ? "result-filter--active" : ""}`} onClick={() => setResultFilter(filter.id)} aria-pressed={resultFilter === filter.id}>{filter.label}<span>{resultFilterCount(results, filter.id)}</span></button>)}
          <select aria-label="更多结果筛选" value={PRIMARY_RESULT_FILTERS.some((filter) => filter.id === resultFilter) ? "" : resultFilter} onChange={(event) => event.target.value && setResultFilter(event.target.value)}><option value="">更多筛选</option>{secondaryFilters.map((filter) => <option key={filter.id} value={filter.id}>{filter.label}（{resultFilterCount(results, filter.id)}）</option>)}</select>
        </div>
        <div className="search-box result-search"><Search size={16} /><Input aria-label="搜索巡检结果" value={resultSearch} onChange={(event) => setResultSearch(event.target.value)} placeholder="搜索设备名称 / IP" /></div>
      </CardContent>
      <CardContent className="table-wrap"><table className="result-table">
        <thead><tr><th>设备 / 管理 IP</th><th>厂商</th><th>结论</th><th>关键发现</th><th>采集时间</th><th>操作</th></tr></thead>
        <tbody>{visibleResults.map((result) => <tr key={result.id}>
          <td className="result-device-cell"><strong>{result.device_name ?? result.device_ip}</strong><span className="mono">{result.device_ip}</span></td>
          <td className="result-nowrap">{result.vendor ? vendorText(catalog, result.vendor) : "-"}</td>
          <td className="result-nowrap"><StatusPill value={result.conclusion} /></td>
          <td className="result-finding" title={resultFindingText(result)}>{resultFindingText(result)}</td>
          <td className="result-time">{formatDateTime(result.created_at)}</td>
          <td className="result-nowrap"><button type="button" className="result-detail-link" onClick={() => setSelectedResultId(result.id)} aria-label={`查看 ${result.device_name ?? result.device_ip} 的巡检详情`}>详情<ChevronRight size={15} /></button></td>
        </tr>)}{!visibleResults.length && <tr><td colSpan={6} className="empty">{results.length ? "没有符合筛选条件的设备，请清空搜索或切换筛选。" : "暂无巡检结果。"}</td></tr>}</tbody>
      </table></CardContent>
    </Card>

    {selectedResult && <ResultDrawer catalog={catalog} task={task} result={selectedResult} rawOutput={rawOutput} rawOutputStatus={rawOutputStatus} rawOutputError={rawOutputError} loadingRawOutput={loadingRawOutput} wrapRawOutput={wrapRawOutput} capturing={capturing} screenshotRef={screenshotRef} onToggleRawOutputWrap={() => setWrapRawOutput((value) => !value)} onCopyRawOutput={() => void copyRawOutput()} onCapture={() => void captureResult()} onClose={() => setSelectedResultId(null)} />}
  </>;
}

function ResultDrawer({ catalog, task, result, rawOutput, rawOutputStatus, rawOutputError, loadingRawOutput, wrapRawOutput, capturing, screenshotRef, onToggleRawOutputWrap, onCopyRawOutput, onCapture, onClose }: { catalog: DeviceCatalog; task: Task; result: Result; rawOutput: string | null; rawOutputStatus: string; rawOutputError: string | null; loadingRawOutput: boolean; wrapRawOutput: boolean; capturing: boolean; screenshotRef: RefObject<HTMLDivElement>; onToggleRawOutputWrap: () => void; onCopyRawOutput: () => void; onCapture: () => void; onClose: () => void }) {
  const dialogRef = useModalBehavior(onClose, capturing);
  const metrics = result.metrics ?? {};
  const items = inspectionItemRows(metrics);
  const rawStatusText: Record<string, string> = { complete: "完整", truncated: "已截断", expired: "已按保留策略清理", unavailable: "不可用", not_available: "无原始回显" };
  return <div className="result-drawer-layer" onMouseDown={(event) => { if (event.target === event.currentTarget && !capturing) onClose(); }}>
    <section ref={dialogRef} className="result-drawer" role="dialog" aria-modal="true" aria-labelledby="result-drawer-title" tabIndex={-1}>
      <div ref={screenshotRef} className="result-drawer__capture" aria-label="设备巡检截图内容">
        <header className="result-drawer__header"><div><span>设备巡检详情</span><h2 id="result-drawer-title">{result.device_name ?? result.device_ip}</h2><p>{result.device_ip} · {result.vendor ? vendorText(catalog, result.vendor) : "未知厂商"}</p></div><button type="button" onClick={onClose} disabled={capturing} aria-label="关闭设备详情"><X size={20} /></button></header>
        <div className="result-drawer__body">
          <section className={`result-finding-card result-finding-card--${result.conclusion}`}><StatusPill value={result.conclusion} /><strong>{resultFindingText(result)}</strong><span>{task.name} · {formatDateTime(result.created_at)}</span></section>
          <h3>关键指标</h3>
          <dl className="result-detail__grid">
            <div><dt>CPU</dt><dd>{formatMetric(metrics.cpu, "%")}</dd></div><div><dt>内存</dt><dd>{formatMetric(metrics.memory, "%")}</dd></div><div><dt>运行时间</dt><dd>{formatMetric(metrics.uptime)}</dd></div><div><dt>接口 UP / DOWN</dt><dd>{metrics.interfaces_up == null && metrics.interfaces_down == null ? "未采集" : `${metrics.interfaces_up ?? "-"} / ${metrics.interfaces_down ?? "-"}`}</dd></div><div><dt>接口总数</dt><dd>{formatMetric(metrics.interfaces_total)}</dd></div><div><dt>数据来源</dt><dd>{result.collection_source ?? "未采集"}</dd></div>
          </dl>
          {items.length > 0 && <div className="inspection-items"><h3>检查项明细</h3><div className="inspection-items__table"><table><thead><tr><th>检查项 / 采集依据</th><th>检查值</th><th>结论</th><th>来源</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td className="inspection-item-context"><strong>{inspectionItemLabel(item.id)}</strong><small>{inspectionReferenceText(item.reference)}</small></td><td className="inspection-item-value"><span>{formatInspectionItemValue(item.id, item.value)}</span>{item.downgradeReason && <small>降级：{translateApiMessage(item.downgradeReason)}</small>}</td><td><StatusPill value={item.conclusion} /></td><td>{conclusionText[item.source] ?? item.source}</td></tr>)}</tbody></table></div></div>}
          <details className="diagnostic-details"><summary>查看采集依据与诊断信息</summary><dl><div><dt>适配器</dt><dd>{items[0]?.adapterVersion ?? "-"}</dd></div><div><dt>命令或 OID</dt><dd className="mono">{items[0] ? inspectionReferenceText(items[0].reference) : "-"}</dd></div></dl><div className="raw-output"><div className="raw-output__heading"><strong>原始采集输出（{rawStatusText[rawOutputStatus] ?? rawOutputStatus}）</strong><div><Button type="button" variant="outline" size="sm" aria-pressed={wrapRawOutput} onClick={onToggleRawOutputWrap}>自动换行</Button><Button type="button" variant="outline" size="sm" disabled={!rawOutput} onClick={onCopyRawOutput}>复制输出</Button></div></div>{rawOutputError ? <p className="inline-error" role="alert">{rawOutputError}</p> : loadingRawOutput ? <p>正在读取原始采集输出…</p> : rawOutput ? <pre className={wrapRawOutput ? "raw-output__content--wrap" : undefined}>{rawOutput}</pre> : <p>当前没有可查看的原始采集输出。</p>}</div></details>
        </div>
      </div>
      <footer className="result-drawer__actions"><Button variant="outline" onClick={onClose} disabled={capturing}>关闭</Button><Button onClick={onCapture} disabled={capturing}><Camera size={16} />{capturing ? "截图中…" : "保存截图"}</Button></footer>
    </section>
  </div>;
}

function AdaptersPage({ catalog }: { catalog: DeviceCatalog }) {
  const [capabilities, setCapabilities] = useState<AdapterCapability[]>([]);
  const [verifiedRecords, setVerifiedRecords] = useState<VerifiedAdapter[]>([]);
  const [activeTab, setActiveTab] = useState<"capabilities" | "verified">("capabilities");
  const [loadingAdapters, setLoadingAdapters] = useState(true);
  const [adapterError, setAdapterError] = useState<string | null>(null);
  const [vendor, setVendor] = useState("");
  const [keyword, setKeyword] = useState("");
  const loadAdapters = async () => {
    setLoadingAdapters(true);
    try {
      const [capabilityResponse, verifiedResponse] = await Promise.all([
        api<AdapterCapability[]>("/adapters/capabilities"),
        api<VerifiedAdapter[]>("/adapters/verified"),
      ]);
      setCapabilities(capabilityResponse);
      setVerifiedRecords(verifiedResponse);
      setAdapterError(null);
    } catch (err) {
      setAdapterError(userErrorMessage(err, "无法读取设备适配范围"));
    } finally {
      setLoadingAdapters(false);
    }
  };
  useEffect(() => { void loadAdapters(); }, []);
  const capabilityRows = filterAdapterRows(adapterCapabilityRows(capabilities), vendor, keyword);
  const verifiedRows = filterAdapterRows(verifiedAdapterRows(verifiedRecords), vendor, keyword);
  return <>
    <section className="page-heading"><div><h1>设备适配范围</h1><p>查看软件已实现的平台能力，以及经过真实设备或脱敏样本验证的型号清单。</p></div><Button variant="outline" onClick={() => void loadAdapters()} disabled={loadingAdapters}><RefreshCw size={16} />刷新</Button></section>
    {adapterError && <div className="inline-error" role="alert">{adapterError}</div>}
    <Card className="flat-card adapter-card">
      <CardContent className="adapter-tabs" role="tablist" aria-label="设备适配清单类型">
        <button type="button" role="tab" aria-selected={activeTab === "capabilities"} className={activeTab === "capabilities" ? "active" : ""} onClick={() => setActiveTab("capabilities")}>内置适配范围 <span>{capabilities.length}</span></button>
        <button type="button" role="tab" aria-selected={activeTab === "verified"} className={activeTab === "verified" ? "active" : ""} onClick={() => setActiveTab("verified")}>已验证清单 <span>{verifiedRecords.length}</span></button>
      </CardContent>
      <CardContent className="device-toolbar">
        <div className="device-filters"><select aria-label="按厂商筛选设备适配" value={vendor} onChange={(event) => setVendor(event.target.value)}><option value="">全部厂商</option>{vendorOptions(catalog).map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></div>
        <label className="search-box"><Search size={16} /><input aria-label="搜索设备适配" value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="平台、型号、版本或检查项" /></label>
      </CardContent>
      {activeTab === "capabilities" ? <>
        <CardContent className="adapter-scope-note"><Info size={16} /><span>“待验证”表示代码已包含采集和解析能力，但尚未覆盖所有真实型号及软件版本；建议先用“设备连接测试”和小范围巡检确认。</span></CardContent>
        <CardContent className="table-wrap"><table className="adapter-table"><thead><tr><th>厂商 / 平台</th><th>设备类型</th><th>采集协议</th><th>配置备份命令</th><th>代码覆盖检查项</th><th>状态</th></tr></thead><tbody>{capabilityRows.map((record) => <tr key={record.platformId}><td className="device-identity"><strong>{vendorText(catalog, record.vendor)}</strong><span>{record.platformLabel}</span></td><td>{record.deviceTypes.map((type) => deviceTypeText(catalog, type)).join("、") || "-"}</td><td><span className="adapter-protocols">{record.protocols.join(" / ") || "-"}</span></td><td className="mono adapter-command">{record.backupCommand}</td><td className="adapter-items">{record.inspectionItems.map(inspectionItemLabel).join("、") || "-"}</td><td><span className="status-pill status-pill--warning">待验证</span></td></tr>)}{!capabilityRows.length && <tr><td colSpan={6} className="empty">{loadingAdapters ? "正在读取内置适配范围..." : "没有符合筛选条件的内置适配平台。"}</td></tr>}</tbody></table></CardContent>
      </> : <>
        <CardContent className="adapter-scope-note"><CheckCircle2 size={16} /><span>这里只展示具备厂商、型号、软件版本与检查项证据的验证记录，不会把一次巡检成功自动标记为已验证。</span></CardContent>
        <CardContent className="table-wrap"><table className="adapter-table adapter-table--verified"><thead><tr><th>厂商</th><th>型号范围</th><th>软件版本范围</th><th>已验证检查项</th></tr></thead><tbody>{verifiedRows.map((record) => <tr key={`${record.vendor}-${record.modelRange}-${record.versionRange}`}><td>{vendorText(catalog, record.vendor)}</td><td>{record.modelRange}</td><td>{record.versionRange}</td><td className="adapter-items">{record.inspectionItems.map(inspectionItemLabel).join("、") || "-"}</td></tr>)}{!verifiedRows.length && <tr><td colSpan={4} className="empty adapter-empty">{loadingAdapters ? "正在读取已验证清单..." : "尚未随安装包发布经过证据验证的型号。内置采集框架仍可使用，但不代表所有型号和版本均已验证。"}</td></tr>}</tbody></table></CardContent>
      </>}
    </Card>
  </>;
}

function BackupPage({ devices, onBackup, backupInProgress, onConfirm, onNotice }: { devices: Device[]; onBackup: (deviceIds: number[]) => Promise<void>; backupInProgress: boolean; onConfirm: (options: ConfirmationOptions) => Promise<boolean | null>; onNotice: (message: string, tone: NoticeTone) => void }) {
  const backupPageSize = 20;
  const backupFileName = (filePath: string | null) => filePath?.split(/[\\/]/).pop() || "-";
  const [records, setRecords] = useState<BackupRecord[]>([]);
  const [selectedBackup, setSelectedBackup] = useState<BackupRecord | null>(null);
  const [diff, setDiff] = useState<string[]>([]);
  const [diffMessage, setDiffMessage] = useState<string | null>(null);
  const [loadingBackups, setLoadingBackups] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [openDirectoryError, setOpenDirectoryError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [comparingBackupId, setComparingBackupId] = useState<number | null>(null);
  const [openingBackupId, setOpeningBackupId] = useState<number | null>(null);
  const [selectedBackupIds, setSelectedBackupIds] = useState<number[]>([]);
  const [deletingBackupIds, setDeletingBackupIds] = useState<number[]>([]);
  const [backupPageIndex, setBackupPageIndex] = useState(0);
  const [backupDeviceIds, setBackupDeviceIds] = useState<number[]>([]);
  const comparingBackupIdRef = useRef<number | null>(null);
  const openingBackupIdRef = useRef<number | null>(null);
  const deletingBackupIdsRef = useRef(new Set<number>());
  const backupRequestIdRef = useRef(0);
  const backupPageCount = Math.max(1, Math.ceil(records.length / backupPageSize));
  const pageRecords = records.slice(backupPageIndex * backupPageSize, (backupPageIndex + 1) * backupPageSize);
  const pageRecordIds = pageRecords.map((record) => record.id);
  const allPageRecordsSelected = pageRecordIds.length > 0 && pageRecordIds.every((id) => selectedBackupIds.includes(id));
  const readyDevices = useMemo(() => devices.filter((device) => device.is_active && device.has_ssh), [devices]);
  const readyDeviceIds = readyDevices.map((device) => device.id);
  const allReadySelected = readyDeviceIds.length > 0 && readyDeviceIds.every((id) => backupDeviceIds.includes(id));
  const loadBackups = async () => {
    const requestId = ++backupRequestIdRef.current;
    setLoadingBackups(true);
    try {
      const response = await api<BackupRecord[]>("/backup");
      if (backupRequestIdRef.current !== requestId) return;
      setRecords(response);
      setSelectedBackupIds((current) => current.filter((id) => response.some((record) => record.id === id)));
      setListError(null);
    } catch (err) {
      if (backupRequestIdRef.current === requestId) setListError(userErrorMessage(err, "无法读取备份记录"));
    } finally {
      if (backupRequestIdRef.current === requestId) setLoadingBackups(false);
    }
  };
  useEffect(() => { void loadBackups(); return () => { backupRequestIdRef.current += 1; }; }, []);
  useEffect(() => { setBackupPageIndex((page) => Math.min(page, backupPageCount - 1)); }, [backupPageCount]);
  useEffect(() => {
    const ready = new Set(readyDeviceIds);
    setBackupDeviceIds((current) => current.filter((id) => ready.has(id)));
  }, [devices]);
  const toggleBackupDevice = (id: number) => setBackupDeviceIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleAllReadyDevices = () => setBackupDeviceIds((current) => allReadySelected ? current.filter((id) => !readyDeviceIds.includes(id)) : [...new Set([...current, ...readyDeviceIds])]);
  const toggleBackupRecord = (id: number) => setSelectedBackupIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleVisibleBackups = () => setSelectedBackupIds((current) => allPageRecordsSelected ? current.filter((id) => !pageRecordIds.includes(id)) : [...new Set([...current, ...pageRecordIds])]);
  const runBackup = async () => {
    if (!backupDeviceIds.length || backupInProgress) return;
    backupRequestIdRef.current += 1;
    setLoadingBackups(false);
    await onBackup(backupDeviceIds);
    await loadBackups();
  };
  const compare = async (record: BackupRecord) => {
    if (comparingBackupIdRef.current !== null) return;
    comparingBackupIdRef.current = record.id;
    setComparingBackupId(record.id);
    setSelectedBackup(record);
    setDiff([]);
    setDiffMessage(null);
    setCompareError(null);
    try {
      const response = await api<{ diff: string[]; message?: string }>(`/backup/${record.id}/diff`);
      setDiff(response.diff);
      setDiffMessage(response.message ?? null);
    } catch (err) {
      setCompareError(userErrorMessage(err, "无法比较配置"));
    } finally {
      if (comparingBackupIdRef.current === record.id) comparingBackupIdRef.current = null;
      setComparingBackupId((current) => current === record.id ? null : current);
    }
  };
  const openDirectory = async (record: BackupRecord) => {
    if (openingBackupIdRef.current !== null) return;
    openingBackupIdRef.current = record.id;
    setOpeningBackupId(record.id);
    setOpenDirectoryError(null);
    try {
      await api<{ directory: string }>(`/backup/${record.id}/open-directory`, { method: "POST" });
    } catch (err) {
      setOpenDirectoryError(userErrorMessage(err, "无法打开备份目录"));
    } finally {
      if (openingBackupIdRef.current === record.id) openingBackupIdRef.current = null;
      setOpeningBackupId((current) => current === record.id ? null : current);
    }
  };
  const deleteBackups = async (targetRecords: BackupRecord[]) => {
    if (!targetRecords.length || deletingBackupIdsRef.current.size || comparingBackupIdRef.current !== null || openingBackupIdRef.current !== null) return;
    const successfulFiles = targetRecords.filter((record) => record.status === "success" && record.file_path).length;
    const confirmed = await onConfirm({
      title: targetRecords.length === 1 ? "删除备份记录" : `删除 ${targetRecords.length} 条备份记录`,
      message: `删除后无法恢复。其中 ${successfulFiles} 条成功记录会同时删除本机备份文件；失败记录仅删除历史，不会影响上一份有效配置。`,
      confirmLabel: targetRecords.length === 1 ? "确认删除" : "删除选中记录",
      danger: true,
    });
    if (!confirmed) return;
    const targetIds = targetRecords.map((record) => record.id);
    deletingBackupIdsRef.current = new Set(targetIds);
    setDeletingBackupIds(targetIds);
    setDeleteError(null);
    const deletedIds: number[] = [];
    const failedIds: number[] = [];
    let firstFailure: string | null = null;
    for (const record of targetRecords) {
      try {
        await api<{ deleted: number }>(`/backup/${record.id}`, { method: "DELETE" });
        deletedIds.push(record.id);
      } catch (err) {
        failedIds.push(record.id);
        firstFailure ??= userErrorMessage(err, `无法删除 ${record.device_ip} 的备份记录`);
      }
    }
    setDeleteError(firstFailure);
    if (deletedIds.includes(selectedBackup?.id ?? -1)) {
      setSelectedBackup(null);
      setDiff([]);
      setDiffMessage(null);
      setCompareError(null);
    }
    setSelectedBackupIds(failedIds);
    deletingBackupIdsRef.current.clear();
    setDeletingBackupIds([]);
    await loadBackups();
    if (!failedIds.length) onNotice(`已删除 ${deletedIds.length} 条备份记录`, "success");
    else if (deletedIds.length) onNotice(`已删除 ${deletedIds.length} 条，另有 ${failedIds.length} 条删除失败`, "warning");
  };
  return <>
    <section className="page-heading"><div><h1>配置备份</h1><p>仅使用 SSH 读取设备配置；差异展示会自动隐藏凭据内容。</p></div><div className="button-row"><Button variant="outline" onClick={() => void loadBackups()} disabled={loadingBackups || backupInProgress || deletingBackupIds.length > 0}><RefreshCw size={16} />刷新记录</Button><Button disabled={loadingBackups || backupInProgress || deletingBackupIds.length > 0 || !backupDeviceIds.length} onClick={() => void runBackup()}><HardDriveDownload size={16} />{backupInProgress ? "正在备份…" : `执行备份${backupDeviceIds.length ? ` (${backupDeviceIds.length})` : ""}`}</Button></div></section>
    {listError && <div className="inline-error">{listError}</div>}
    {openDirectoryError && <div className="inline-error">{openDirectoryError}</div>}
    {deleteError && <div className="inline-error" role="alert">{deleteError}</div>}
    <Card className="flat-card backup-device-selector"><CardContent className="backup-selector-heading"><div><h2>选择备份设备</h2><p>共 {devices.length} 台，{readyDevices.length} 台已启用并配置 SSH。</p></div><div className="button-row"><Button type="button" variant="outline" size="sm" disabled={!readyDeviceIds.length || backupInProgress} onClick={toggleAllReadyDevices}>选择全部可备份设备</Button><Button type="button" variant="ghost" size="sm" disabled={!backupDeviceIds.length || backupInProgress} onClick={() => setBackupDeviceIds([])}>清空选择</Button></div></CardContent><CardContent className="table-wrap"><table className="backup-device-table"><thead><tr><th><input type="checkbox" aria-label="选择全部可备份设备" checked={allReadySelected} disabled={!readyDeviceIds.length || backupInProgress} onChange={toggleAllReadyDevices} /></th><th>设备 / 管理 IP</th><th>厂商</th><th>SSH 状态</th></tr></thead><tbody>{devices.map((device) => { const ready = device.is_active && device.has_ssh; const reason = !device.is_active ? "设备已停用" : !device.has_ssh ? "未配置 SSH" : "可以备份"; return <tr key={device.id} className={!ready ? "backup-device-row--disabled" : undefined}><td><input type="checkbox" aria-label={`选择 ${device.ip}`} checked={backupDeviceIds.includes(device.id)} disabled={!ready || backupInProgress} onChange={() => toggleBackupDevice(device.id)} /></td><td className="device-identity"><strong>{device.name ?? device.ip}</strong><span className="mono">{device.ip}</span></td><td>{device.vendor}</td><td><span className={`backup-readiness ${ready ? "backup-readiness--ready" : "backup-readiness--blocked"}`}>{reason}</span></td></tr>; })}{!devices.length && <tr><td colSpan={4} className="empty">暂无设备，请先添加设备并配置 SSH。</td></tr>}</tbody></table></CardContent></Card>
    <Card className="flat-card backup-history">
      <CardContent className="backup-tip"><Archive size={22} /><span>已选择 {backupDeviceIds.length} 台；备份失败不会覆盖上一份有效配置，保存内容会自动隐藏凭据。</span></CardContent>
      {records.length > 0 && <CardContent className="task-bulk-bar"><span>已选 {selectedBackupIds.length} 条备份记录</span><Button type="button" size="sm" variant="outline" className="task-bulk-bar__delete" disabled={!selectedBackupIds.length || deletingBackupIds.length > 0 || comparingBackupId !== null || openingBackupId !== null} onClick={() => void deleteBackups(records.filter((record) => selectedBackupIds.includes(record.id)))}><Trash2 size={15} />{deletingBackupIds.length ? "正在删除…" : "删除选中记录"}</Button></CardContent>}
      <CardContent className="table-wrap"><table className="backup-history-table"><thead><tr><th className="backup-select-column"><input type="checkbox" aria-label="选择本页全部备份记录" checked={allPageRecordsSelected} disabled={!pageRecordIds.length || deletingBackupIds.length > 0} onChange={toggleVisibleBackups} /></th><th>时间</th><th>设备 IP</th><th>状态</th><th>文件</th><th>说明</th><th>操作</th></tr></thead><tbody>
        {pageRecords.map((record) => <tr key={record.id} className={selectedBackup?.id === record.id ? "result-row--selected" : ""}><td className="backup-select-column"><input type="checkbox" aria-label={`选择 ${record.device_ip} 的备份记录`} checked={selectedBackupIds.includes(record.id)} disabled={deletingBackupIds.length > 0} onChange={() => toggleBackupRecord(record.id)} /></td><td>{formatDateTime(record.created_at)}</td><td className="mono">{record.device_ip}</td><td><StatusPill value={record.status === "success" ? "normal" : "data_fetch_failed"} /></td><td className="message-cell" title={record.file_path ?? undefined}>{backupFileName(record.file_path)}</td><td className="message-cell" title={record.message ? translateApiMessage(record.message) : undefined}>{record.message ? translateApiMessage(record.message) : "-"}</td><td><div className="row-actions"><Button size="sm" variant="outline" disabled={record.status !== "success" || comparingBackupId !== null || deletingBackupIds.length > 0} onClick={() => void compare(record)}><GitCompareArrows size={15} />{comparingBackupId === record.id ? "正在对比…" : "对比上一版"}</Button><Button type="button" size="icon" variant="outline" title={openingBackupId === record.id ? "正在打开目录" : "打开备份所在目录"} aria-label={openingBackupId === record.id ? `正在打开 ${record.device_ip} 的备份目录` : `打开 ${record.device_ip} 的备份目录`} disabled={record.status !== "success" || openingBackupId !== null || deletingBackupIds.length > 0} onClick={() => void openDirectory(record)}><FolderOpen size={16} /></Button><Button type="button" size="icon" variant="outline" className="danger-button" title="删除此记录" aria-label={`删除 ${record.device_ip} 的备份记录`} disabled={deletingBackupIds.length > 0 || comparingBackupId !== null || openingBackupId !== null} onClick={() => void deleteBackups([record])}><Trash2 size={16} /></Button></div></td></tr>)}
        {!records.length && <tr><td colSpan={7} className="empty">{loadingBackups ? "正在读取备份记录..." : "暂无配置备份记录。"}</td></tr>}
      </tbody></table></CardContent>
      {records.length > 0 && <CardContent className="log-pagination"><span>共 {records.length} 条备份记录</span><div><Button variant="outline" size="sm" disabled={backupPageIndex === 0 || deletingBackupIds.length > 0} onClick={() => setBackupPageIndex((page) => Math.max(0, page - 1))}>上一页</Button><span className="table-page">{backupPageIndex + 1} / {backupPageCount}</span><Button variant="outline" size="sm" disabled={backupPageIndex >= backupPageCount - 1 || deletingBackupIds.length > 0} onClick={() => setBackupPageIndex((page) => Math.min(backupPageCount - 1, page + 1))}>下一页</Button></div></CardContent>}
    </Card>
    {selectedBackup && <section className="diff-panel"><div className="diff-panel__heading"><div><span>配置差异</span><h2>{selectedBackup.device_ip}</h2></div><Button variant="outline" size="sm" disabled={comparingBackupId !== null} onClick={() => { setSelectedBackup(null); setCompareError(null); }}>关闭</Button></div>{comparingBackupId === selectedBackup.id ? <p>正在读取并对比配置…</p> : compareError ? <p className="inline-error" role="alert">{compareError}</p> : diffMessage ? <p>{translateApiMessage(diffMessage)}</p> : <pre>{diff.length ? diff.join("\n") : "没有检测到配置变化。"}</pre>}</section>}
  </>;
}

function SettingsPage({ settings, exclusiveBusy, onSave, onDatabaseBackup, onDatabaseRestore, onPasswordChange }: { settings: InspectionSettings | null; exclusiveBusy: boolean; onSave: (value: InspectionSettings) => Promise<void>; onDatabaseBackup: () => Promise<void>; onDatabaseRestore: (file: File) => Promise<void>; onPasswordChange: (currentPassword: string, newPassword: string) => Promise<void> }) {
  const [draft, setDraft] = useState<InspectionSettings | null>(settings);
  const [saving, setSaving] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [backingUpDatabase, setBackingUpDatabase] = useState(false);
  const [isPasswordDialogOpen, setPasswordDialogOpen] = useState(false);
  const [settingsError, setSettingsError] = useState<string | null>(null);
  useEffect(() => setDraft(settings), [settings]);
  if (!draft) return <div className="empty-state"><Settings size={32} /><p>正在读取本机设置...</p></div>;
  const setNumber = (field: keyof InspectionSettings, value: string) => setDraft((current) => current ? { ...current, [field]: Number(value) } : current);
  const setText = (field: keyof InspectionSettings, value: string) => setDraft((current) => current ? { ...current, [field]: value || null } : current);
  const setBackupIgnorePatterns = (value: string) => setDraft((current) => current ? { ...current, backup_ignore_patterns: value.split("\n").map((pattern) => pattern.trim()).filter(Boolean) } : current);
  const writeBusy = saving || restoring || backingUpDatabase || exclusiveBusy;
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (writeBusy) return;
    const validationMessage = validateThresholdRanges(draft);
    if (validationMessage) { setSettingsError(validationMessage); return; }
    setSettingsError(null);
    setSaving(true);
    try { await onSave(draft); } finally { setSaving(false); }
  };
  const restore = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || writeBusy) { event.target.value = ""; return; }
    setRestoring(true);
    try {
      await onDatabaseRestore(file);
    } finally {
      setRestoring(false);
      event.target.value = "";
    }
  };
  const backupDatabase = async () => {
    if (writeBusy) return;
    setBackingUpDatabase(true);
    try {
      await onDatabaseBackup();
    } finally {
      setBackingUpDatabase(false);
    }
  };
  return <><section className="page-heading"><div><h1>系统设置</h1><p>保存后应用于后续巡检任务，正在执行的任务保持原参数。</p></div></section><Card className="flat-card"><form className="settings-form" onSubmit={submit}><div className="settings-grid"><h2 className="settings-section-heading setting--wide">采集策略</h2><Setting label="最大并发数" value={draft.max_concurrency} min={1} max={50} onChange={(value) => setNumber("max_concurrency", value)} /><label className="setting"><span>默认采集方式</span><select value={draft.default_collection_mode} onChange={(event) => setDraft({ ...draft, default_collection_mode: event.target.value as InspectionSettings["default_collection_mode"] })}><option value="auto">自动</option><option value="snmp">仅 SNMP</option><option value="ssh">仅 SSH</option></select></label><Setting label="SNMP 超时（秒）" value={draft.snmp_timeout_seconds} min={1} max={60} onChange={(value) => setNumber("snmp_timeout_seconds", value)} /><Setting label="SNMP 重试次数" value={draft.snmp_retries} min={0} max={5} onChange={(value) => setNumber("snmp_retries", value)} /><Setting label="SSH 连接超时（秒）" value={draft.connection_timeout_seconds} min={1} max={120} onChange={(value) => setNumber("connection_timeout_seconds", value)} /><Setting label="命令读取超时（秒）" value={draft.command_timeout_seconds} min={1} max={300} onChange={(value) => setNumber("command_timeout_seconds", value)} /><Setting label="网络错误重试次数" value={draft.network_retry_count} min={0} max={5} onChange={(value) => setNumber("network_retry_count", value)} /><h2 className="settings-section-heading setting--wide">告警阈值</h2><Setting label="CPU 警告阈值（%）" value={draft.cpu_warning_threshold} min={1} max={100} onChange={(value) => setNumber("cpu_warning_threshold", value)} /><Setting label="CPU 严重阈值（%）" value={draft.cpu_critical_threshold} min={1} max={100} onChange={(value) => setNumber("cpu_critical_threshold", value)} /><Setting label="内存警告阈值（%）" value={draft.memory_warning_threshold} min={1} max={100} onChange={(value) => setNumber("memory_warning_threshold", value)} /><Setting label="内存严重阈值（%）" value={draft.memory_critical_threshold} min={1} max={100} onChange={(value) => setNumber("memory_critical_threshold", value)} /><Setting label="存储空间警告阈值（%）" value={draft.storage_warning_threshold} min={1} max={100} onChange={(value) => setNumber("storage_warning_threshold", value)} /><Setting label="存储空间严重阈值（%）" value={draft.storage_critical_threshold} min={1} max={100} onChange={(value) => setNumber("storage_critical_threshold", value)} /><h2 className="settings-section-heading setting--wide">文件与保留</h2><Setting label="原始回显保留天数" value={draft.raw_output_retention_days} min={1} max={365} onChange={(value) => setNumber("raw_output_retention_days", value)} /><Setting label="日志保留天数" value={draft.log_retention_days} min={1} max={3650} onChange={(value) => setNumber("log_retention_days", value)} /><Setting label="每台设备备份保留份数" value={draft.backup_retention_count} min={1} max={1000} onChange={(value) => setNumber("backup_retention_count", value)} /><label className="setting setting--wide"><span>报告与截图目录</span><Input value={draft.report_directory ?? ""} onChange={(event) => setText("report_directory", event.target.value)} placeholder="留空时使用程序默认目录" /></label><label className="setting setting--wide"><span>配置备份目录</span><Input value={draft.backup_directory ?? ""} onChange={(event) => setText("backup_directory", event.target.value)} placeholder="留空时使用程序默认目录" /></label><label className="setting setting--wide"><span>配置差异忽略规则</span><textarea value={draft.backup_ignore_patterns.join("\n")} onChange={(event) => setBackupIgnorePatterns(event.target.value)} placeholder="每行一个正则表达式，例如：^# Generated at " /></label></div>{settingsError && <div className="inline-error settings-error" role="alert">{settingsError}</div>}<div className="dialog-actions"><Button type="button" variant="outline" disabled={writeBusy} onClick={() => { setDraft(settings); setSettingsError(null); }}>恢复已保存值</Button><Button type="submit" disabled={writeBusy}>{saving ? "保存中..." : "保存设置"}</Button></div></form><div className="database-actions"><div><Database size={20} /><div><h2>数据库维护</h2><p>备份包含设备、任务、结果、设置与日志。恢复前请确认备份来自同版本软件。</p></div></div><div className="button-row"><Button type="button" variant="outline" disabled={writeBusy} onClick={() => void backupDatabase()}><Download size={16} />{backingUpDatabase ? "备份中..." : "备份数据库"}</Button><label className={`button-label ${writeBusy ? "button-label--disabled" : ""}`}>{restoring ? "恢复中..." : "恢复数据库"}<Upload size={16} /><input type="file" accept=".db,.sqlite,.sqlite3" disabled={writeBusy} onChange={(event) => void restore(event)} /></label></div></div><div className="database-actions"><div><KeyRound size={20} /><div><h2>本地管理员</h2><p>修改后会退出当前登录，旧会话将立即失效。</p></div></div><Button type="button" variant="outline" disabled={writeBusy} onClick={() => setPasswordDialogOpen(true)}>修改登录密码</Button></div></Card>{isPasswordDialogOpen && <PasswordChangeDialog disabled={exclusiveBusy} onClose={() => setPasswordDialogOpen(false)} onSubmit={onPasswordChange} />}</>;
}
function Setting({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (value: string) => void }) { return <label className="setting"><span>{label}</span><Input type="number" value={value} min={min} max={max} onChange={(event) => onChange(event.target.value)} required /></label>; }

function PasswordChangeDialog({ disabled, onClose, onSubmit }: { disabled: boolean; onClose: () => void; onSubmit: (currentPassword: string, newPassword: string) => Promise<void> }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    if (disabled) return;
    const form = new FormData(event.currentTarget);
    const currentPassword = String(form.get("current_password") ?? "");
    const newPassword = String(form.get("new_password") ?? "");
    const confirmPassword = String(form.get("confirm_password") ?? "");
    const invalid = validateAdminCredentials({ username: "admin", password: newPassword, confirmPassword });
    if (invalid) {
      setError(invalid);
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(currentPassword, newPassword);
    } catch (err) {
      setError(userErrorMessage(err, "修改密码失败"));
    } finally {
      setSubmitting(false);
    }
  };
  const dialogRef = useModalBehavior(onClose, submitting);
  return <div className="modal-layer"><form ref={dialogRef} className="dialog" role="dialog" aria-modal="true" aria-labelledby="password-dialog-title" tabIndex={-1} onSubmit={submit}><div className="dialog-heading"><div><h2 id="password-dialog-title">修改登录密码</h2><p>修改成功后需要使用新密码重新登录。</p></div><button type="button" onClick={onClose} disabled={submitting} aria-label="关闭"><X /></button></div><div className="form-grid"><Field name="current_password" label="当前密码" type="password" autoComplete="current-password" required /><Field name="new_password" label="新密码" type="password" autoComplete="new-password" required hint={`至少 ${ADMIN_PASSWORD_MIN_LENGTH} 位字符`} /><Field name="confirm_password" label="确认新密码" type="password" autoComplete="new-password" required /></div>{error && <div className="inline-error" role="alert">{error}</div>}<div className="dialog-actions"><Button type="button" variant="outline" onClick={onClose} disabled={submitting}>取消</Button><Button type="submit" disabled={submitting || disabled}>{submitting ? "提交中..." : "确认修改"}</Button></div></form></div>;
}

function LogsPage({ onConfirm, onNotice }: { onConfirm: (options: ConfirmationOptions) => Promise<boolean | null>; onNotice: (message: string, tone: NoticeTone) => void }) {
  const [pageData, setPageData] = useState<LogPage>({ total: 0, snapshot_id: 0, items: [] });
  const [keyword, setKeyword] = useState("");
  const [appliedKeyword, setAppliedKeyword] = useState("");
  const [level, setLevel] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedLogIds, setSelectedLogIds] = useState<number[]>([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [deletingLogs, setDeletingLogs] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const logRequestIdRef = useRef(0);
  const limit = 50;
  const loadLogs = async () => {
    const requestId = ++logRequestIdRef.current;
    setLoadingLogs(true);
    try {
      const parameters = new URLSearchParams({ limit: String(limit), offset: String(offset) });
      if (appliedKeyword) parameters.set("keyword", appliedKeyword);
      if (level) parameters.set("level", level);
      const response = await api<LogPage>(`/logs?${parameters.toString()}`);
      if (logRequestIdRef.current !== requestId) return;
      setPageData(response);
      setSelectedLogIds((current) => current.filter((id) => response.items.some((item) => item.id === id)));
      setLoadError(null);
    } catch (err) {
      if (logRequestIdRef.current === requestId) setLoadError(userErrorMessage(err, "无法读取运行日志"));
    } finally {
      if (logRequestIdRef.current === requestId) setLoadingLogs(false);
    }
  };
  useEffect(() => { void loadLogs(); }, [offset, level, appliedKeyword]);
  const search = () => {
    const nextKeyword = keyword.trim();
    if (offset === 0 && appliedKeyword === nextKeyword) void loadLogs();
    else {
      setOffset(0);
      setAppliedKeyword(nextKeyword);
    }
  };
  const visibleIds = pageData.items.map((item) => item.id);
  const allPageSelected = visibleIds.length > 0 && visibleIds.every((id) => selectedLogIds.includes(id));
  const toggleLog = (id: number) => setSelectedLogIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleVisibleLogs = () => setSelectedLogIds((current) => allPageSelected ? current.filter((id) => !visibleIds.includes(id)) : [...new Set([...current, ...visibleIds])]);
  const deleteLogs = async (deleteAll: boolean) => {
    if (deletingLogs) return;
    const selectedCount = selectedLogIds.length;
    const deleteCount = deleteAll ? pageData.total : selectedCount;
    if (!deleteCount || (deleteAll && !pageData.snapshot_id)) return;
    const confirmed = await onConfirm({
      title: deleteAll ? "清空当前筛选日志" : "删除所选日志",
      message: deleteAll
        ? `将删除当前筛选结果中的 ${deleteCount} 条运行记录。该操作不会删除设备配置或巡检结果。`
        : `将删除选中的 ${deleteCount} 条运行记录。该操作无法撤销。`,
      confirmLabel: deleteAll ? "确认清空" : "确认删除",
      danger: true,
    });
    if (!confirmed) return;
    setDeletingLogs(true);
    setLoadError(null);
    try {
      const payload = deleteAll
        ? { delete_all: true, before_id: pageData.snapshot_id, level: level || null, keyword: appliedKeyword || null }
        : { ids: selectedLogIds };
      const response = await api<LogDeleteResult>("/logs", { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setSelectedLogIds([]);
      onNotice(`已删除 ${response.deleted} 条运行日志`, "success");
      const nextTotal = Math.max(0, pageData.total - response.deleted);
      const nextOffset = nextTotal ? Math.min(offset, Math.floor((nextTotal - 1) / limit) * limit) : 0;
      if (nextOffset !== offset) setOffset(nextOffset);
      else await loadLogs();
    } catch (err) {
      setLoadError(userErrorMessage(err, "删除运行日志失败"));
    } finally {
      setDeletingLogs(false);
    }
  };
  return <><section className="page-heading"><div><h1>运行日志</h1><p>查看和清理本机登录、设备、任务、备份、导出与连接记录。</p></div><Button variant="outline" onClick={() => void loadLogs()} disabled={loadingLogs || deletingLogs}><RefreshCw size={16} />刷新</Button></section>{loadError && <div className="inline-error" role="alert">{loadError}</div>}<Card className="flat-card"><CardContent className="log-toolbar"><div className="search-box"><Search size={17} /><Input value={keyword} onChange={(event) => setKeyword(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") search(); }} placeholder="搜索事件、IP 或错误信息" /></div><select aria-label="日志等级" value={level} onChange={(event) => { setLevel(event.target.value); setOffset(0); }}><option value="">全部等级</option><option value="INFO">信息</option><option value="WARNING">警告</option><option value="ERROR">错误</option></select><Button variant="outline" onClick={search} disabled={deletingLogs}>搜索</Button><div className="log-delete-actions"><Button variant="outline" disabled={!selectedLogIds.length || deletingLogs} onClick={() => void deleteLogs(false)}><Trash2 size={16} />{deletingLogs ? "删除中…" : `删除所选${selectedLogIds.length ? ` (${selectedLogIds.length})` : ""}`}</Button><Button variant="outline" className="danger-button" disabled={!pageData.total || deletingLogs} onClick={() => void deleteLogs(true)}><Trash2 size={16} />清空当前筛选</Button></div></CardContent><CardContent className="table-wrap"><table className="log-table"><thead><tr><th><input type="checkbox" aria-label="选择当前页日志" checked={allPageSelected} disabled={!visibleIds.length || deletingLogs} onChange={toggleVisibleLogs} /></th><th>时间</th><th>等级</th><th>事件</th><th>设备 IP</th><th>任务</th><th>信息</th></tr></thead><tbody>{pageData.items.map((item) => <tr key={item.id}><td><input type="checkbox" aria-label={`选择日志 ${item.id}`} checked={selectedLogIds.includes(item.id)} disabled={deletingLogs} onChange={() => toggleLog(item.id)} /></td><td>{formatDateTime(item.created_at)}</td><td><span className={`log-level log-level--${item.level.toLowerCase()}`}>{translateApiMessage(item.level)}</span></td><td>{translateApiMessage(item.event)}</td><td className="mono">{item.device_ip ?? "-"}</td><td>{item.task_id ?? "-"}</td><td className="message-cell" title={item.message ? translateApiMessage(item.message) : undefined}>{item.message ? translateApiMessage(item.message) : "-"}</td></tr>)}{!pageData.items.length && <tr><td colSpan={7} className="empty">{loadingLogs ? "正在读取日志..." : "没有符合条件的日志。"}</td></tr>}</tbody></table></CardContent><CardContent className="log-pagination"><span>共 {pageData.total} 条，当前页已选择 {selectedLogIds.length} 条</span><div><Button variant="outline" size="sm" disabled={offset === 0 || loadingLogs || deletingLogs} onClick={() => setOffset(Math.max(0, offset - limit))}>上一页</Button><Button variant="outline" size="sm" disabled={offset + limit >= pageData.total || loadingLogs || deletingLogs} onClick={() => setOffset(offset + limit)}>下一页</Button></div></CardContent></Card></>;
}

function DeviceDialog({ catalog, device, operationEpoch, isOperationCurrent, onClose, onSaved }: { catalog: DeviceCatalog; device: Device | null; operationEpoch: OperationEpoch; isOperationCurrent: (epoch: OperationEpoch) => boolean; onClose: () => void; onSaved: (operationEpoch: OperationEpoch) => Promise<void> }) {
  const isEditing = Boolean(device);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const initialVendor = device?.vendor ?? catalog.vendors[0]?.id ?? "";
  const [selectedVendor, setSelectedVendor] = useState(initialVendor);
  const [selectedPlatform, setSelectedPlatform] = useState(
    device?.platform_id ?? defaultPlatformId(catalog, initialVendor),
  );
  const initialDeviceTypes = deviceTypeOptions(catalog, initialVendor);
  const [selectedDeviceType, setSelectedDeviceType] = useState(
    device?.device_type ?? initialDeviceTypes[0]?.value ?? "",
  );
  const vendors = vendorOptions(catalog);
  const platforms = platformOptions(catalog, selectedVendor);
  const deviceTypes = deviceTypeOptions(catalog, selectedVendor);
  const [snmpMode, setSnmpMode] = useState<"v2c" | "v3" | null>(device ? device.snmp_version : "v2c");
  const [snmpV3SecurityLevel, setSnmpV3SecurityLevel] = useState<"noAuthNoPriv" | "authNoPriv" | "authPriv">(device?.snmp_v3_security_level ?? DEFAULT_SNMP_V3_SECURITY_LEVEL);
  const [snmpV3Username, setSnmpV3Username] = useState(device?.snmp_v3_username ?? "");
  const [sshEnabled, setSshEnabled] = useState(Boolean(device?.has_ssh));
  const [clearEnablePassword, setClearEnablePassword] = useState(false);
  const dialogRef = useModalBehavior(onClose, submitting);
  const changeVendor = (event: ChangeEvent<HTMLSelectElement>) => {
    const nextVendor = event.target.value;
    const nextDeviceTypes = deviceTypeOptions(catalog, nextVendor);
    setSelectedVendor(nextVendor);
    setSelectedPlatform(defaultPlatformId(catalog, nextVendor));
    if (!nextDeviceTypes.some((item) => item.value === selectedDeviceType)) {
      setSelectedDeviceType(nextDeviceTypes[0]?.value ?? "");
    }
  };
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    if (!snmpMode && !sshEnabled) {
      setFormError("至少保留一套 SNMP 或 SSH 凭据");
      return;
    }
    setFormError(null);
    const form = new FormData(event.currentTarget);
    const data = buildDevicePayload(Object.fromEntries(form.entries()), { isEditing, clearEnablePassword }) as Record<string, unknown>;
    const clearProtocols: Array<"snmp" | "ssh"> = [];
    if (!snmpMode && device?.has_snmp_credentials) clearProtocols.push("snmp");
    if (!sshEnabled && device?.has_ssh) clearProtocols.push("ssh");
    if (device?.snmp_version && snmpMode && device.snmp_version !== snmpMode) clearProtocols.push("snmp");
    if (device?.snmp_version === "v3" && snmpMode === "v3" && device.snmp_v3_security_level !== snmpV3SecurityLevel) clearProtocols.push("snmp");
    if (snmpMode) data.snmp_version = snmpMode;
    if (clearProtocols.length) data.clear_protocols = [...new Set(clearProtocols)];
    setSubmitting(true);
    try {
      await api<Device>(isEditing ? `/devices/${device.id}` : "/devices", { method: isEditing ? "PUT" : "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
      if (!isOperationCurrent(operationEpoch)) return;
      await onSaved(operationEpoch);
    } catch (err) {
      if (isOperationCurrent(operationEpoch)) {
        const message = userErrorMessage(err, "保存失败");
        setFormError(message);
      }
    } finally {
      setSubmitting(false);
    }
  };
  const requiresNewV2cCredentials = !isEditing || device?.snmp_version !== "v2c";
  const requiresNewV3Credentials = !isEditing || device?.snmp_version !== "v3";
  const requiresNewV3Secrets = needsNewSnmpV3Secrets({
    isEditing,
    previousVersion: device?.snmp_version,
    previousSecurityLevel: device?.snmp_v3_security_level,
    nextSecurityLevel: snmpV3SecurityLevel,
    usernameChanged: snmpV3Username !== (device?.snmp_v3_username ?? ""),
  });
  const requiresNewSshCredentials = !isEditing || !device?.has_ssh;
  return (
    <div className="modal-layer">
       <form ref={dialogRef} className="dialog dialog--scrollable" role="dialog" aria-modal="true" aria-labelledby="device-dialog-title" tabIndex={-1} onSubmit={submit}>
        <div className="dialog-heading">
          <div>
            <h2 id="device-dialog-title">{isEditing ? "编辑设备" : "添加设备"}</h2>
            <p>{isEditing ? "留空的凭据保持原值；关闭协议会清除该协议凭据。" : "至少填写一套 SNMP 或 SSH 凭据"}</p>
          </div>
          <button type="button" onClick={onClose} disabled={submitting} aria-label="关闭"><X /></button>
        </div>
        <div className="dialog-body">
        <div className="form-grid">
          <Field name="name" label="设备名称" defaultValue={device?.name ?? ""} />
          <Field name="ip" label="管理 IP" required defaultValue={device?.ip} placeholder="192.168.1.1" disabled={isEditing} />
          <Select name="vendor" label="厂商" options={vendors.map((item) => item.value)} optionLabels={vendors.map((item) => item.label)} value={selectedVendor} onChange={changeVendor} />
          <Select name="platform_id" label="系统平台" options={platforms.map((item) => item.value)} optionLabels={platforms.map((item) => item.label)} value={selectedPlatform} onChange={(event) => setSelectedPlatform(event.target.value)} />
          <Select name="device_type" label="设备类型" options={deviceTypes.map((item) => item.value)} optionLabels={deviceTypes.map((item) => item.label)} value={selectedDeviceType} onChange={(event) => setSelectedDeviceType(event.target.value)} />
          <Field name="group_name" label="分组" defaultValue={device?.group_name ?? ""} placeholder="核心 / 汇聚 / 接入" />
          <Field name="note" label="备注" defaultValue={device?.note ?? ""} />
        </div>
        <section className="protocol-section">
          <label className="protocol-toggle"><input type="checkbox" checked={snmpMode !== null} onChange={(event) => setSnmpMode(event.target.checked ? (device?.snmp_version ?? "v2c") : null)} /><span>SNMP 采集</span></label>
          {snmpMode && <>
            <div className="mode-tabs">
              <button type="button" className={snmpMode === "v2c" ? "active" : ""} onClick={() => setSnmpMode("v2c")}>SNMP v2c</button>
              <button type="button" className={snmpMode === "v3" ? "active" : ""} onClick={() => setSnmpMode("v3")}>SNMP v3</button>
            </div>
            {snmpMode === "v2c" ? <div key="snmp-v2c" className="form-grid">
              <Field name="snmp_community" label="SNMP Community" required={requiresNewV2cCredentials} type="password" autoComplete="new-password" placeholder={isEditing ? "留空则不修改" : undefined} />
              <Field name="snmp_port" label="SNMP 端口" defaultValue={String(device?.snmp_port ?? 161)} />
            </div> : <div key="snmp-v3" className="form-grid">
              <Field name="snmp_v3_username" label="SNMPv3 用户名" required={requiresNewV3Credentials} value={snmpV3Username} onChange={(event) => setSnmpV3Username(event.target.value)} />
              <label className="field"><span>安全级别</span><select name="snmp_v3_security_level" value={snmpV3SecurityLevel} onChange={(event) => setSnmpV3SecurityLevel(event.target.value as typeof snmpV3SecurityLevel)}>{SNMP_V3_SECURITY_LEVELS.map((level) => <option key={level} value={level}>{level === "authPriv" ? "authPriv（推荐）" : level === "authNoPriv" ? "authNoPriv（不加密）" : "noAuthNoPriv（不建议）"}</option>)}</select></label>
              <Field name="snmp_port" label="SNMP 端口" defaultValue={String(device?.snmp_port ?? 161)} />
              {snmpV3SecurityLevel !== "noAuthNoPriv" && <>
                <Select name="snmp_v3_auth_protocol" label="认证协议" options={SNMP_V3_AUTH_PROTOCOLS} defaultValue={device?.snmp_v3_auth_protocol ?? "SHA256"} />
                <Field name="snmp_v3_auth_key" label="认证密钥" required={requiresNewV3Secrets} type="password" autoComplete="new-password" placeholder={isEditing ? "留空则不修改" : undefined} />
              </>}
              {snmpV3SecurityLevel === "authPriv" && <>
                <Select name="snmp_v3_priv_protocol" label="加密协议" options={SNMP_V3_PRIV_PROTOCOLS} defaultValue={device?.snmp_v3_priv_protocol ?? "AES"} />
                <Field name="snmp_v3_priv_key" label="加密密钥" required={requiresNewV3Secrets} type="password" autoComplete="new-password" placeholder={isEditing ? "留空则不修改" : undefined} />
              </>}
            </div>}
          </>}
        </section>
        <section className="protocol-section">
          <label className="protocol-toggle"><input type="checkbox" checked={sshEnabled} onChange={(event) => setSshEnabled(event.target.checked)} /><span>SSH 补充采集</span></label>
          {sshEnabled && <div className="form-grid">
            <Field name="ssh_username" label="SSH 用户名" required={requiresNewSshCredentials} />
            <Field name="ssh_password" label="SSH 密码" required={requiresNewSshCredentials} type="password" autoComplete="new-password" placeholder={isEditing ? "留空则不修改" : undefined} />
            <Field name="enable_password" label="Enable / Super 密码" type="password" autoComplete="new-password" disabled={clearEnablePassword} placeholder={isEditing ? "留空则保留当前密码" : "设备无需提权时留空"} />
            {isEditing && <label className="protocol-toggle"><input type="checkbox" checked={clearEnablePassword} onChange={(event) => setClearEnablePassword(event.target.checked)} /><span>清除已保存的 Enable / Super 密码</span></label>}
            <Field name="ssh_port" label="SSH 端口" defaultValue={String(device?.ssh_port ?? 22)} />
          </div>}
        </section>
        {formError && <div className="dialog-error" role="alert">{formError}</div>}
        </div>
        <div className="dialog-actions"><Button type="button" variant="outline" onClick={onClose} disabled={submitting}>取消</Button><Button type="submit" disabled={submitting}>{submitting ? "正在保存…" : isEditing ? "保存修改" : "保存设备"}</Button></div>
      </form>
    </div>
  );
}
function TaskDialog({ catalog, devices, selectedIds, operationEpoch, isOperationCurrent, defaultCollectionMode, onClose, onCreated }: { catalog: DeviceCatalog; devices: Device[]; selectedIds: number[]; operationEpoch: OperationEpoch; isOperationCurrent: (epoch: OperationEpoch) => boolean; defaultCollectionMode: "auto" | "snmp" | "ssh"; onClose: () => void; onCreated: (task: Task, operationEpoch: OperationEpoch) => Promise<void> }) {
  const activeDevices = useMemo(() => devices.filter((device) => device.is_active), [devices]);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ids, setIds] = useState<number[]>(selectedIds.filter((id) => activeDevices.some((device) => device.id === id)));
  const [groupName, setGroupName] = useState("");
  const dialogRef = useModalBehavior(onClose, submitting);
  const groups = useMemo(() => [...new Set(activeDevices.map((device) => device.group_name).filter((value): value is string => Boolean(value)))].sort(), [activeDevices]);
  const visibleDevices = groupName ? activeDevices.filter((device) => device.group_name === groupName) : activeDevices;
  const visibleIds = visibleDevices.map((device) => device.id);
  const allVisibleSelected = visibleIds.length > 0 && visibleIds.every((id) => ids.includes(id));
  const toggleDevice = (id: number) => setIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleVisible = () => setIds((current) => allVisibleSelected ? current.filter((id) => !visibleIds.includes(id)) : [...new Set([...current, ...visibleIds])]);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    const form = new FormData(event.currentTarget);
    setFormError(null);
    setSubmitting(true);
    try {
      const task = await api<Task>("/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: form.get("name"), inspection_level: form.get("inspection_level"), collection_mode: form.get("collection_mode"), device_ids: ids }) });
      if (!isOperationCurrent(operationEpoch)) return;
      await onCreated(task, operationEpoch);
    } catch (err) {
      if (isOperationCurrent(operationEpoch)) {
        const message = userErrorMessage(err, "任务创建失败");
        setFormError(message);
      }
    } finally {
      setSubmitting(false);
    }
  };
  return <div className="modal-layer"><form ref={dialogRef} className="dialog dialog--scrollable" role="dialog" aria-modal="true" aria-labelledby="task-dialog-title" tabIndex={-1} onSubmit={submit}><div className="dialog-heading"><div><h2 id="task-dialog-title">新建巡检任务</h2><p>SNMP 优先，自动模式会在必要时降级 SSH</p></div><button type="button" onClick={onClose} disabled={submitting} aria-label="关闭"><X /></button></div><div className="dialog-body"><div className="form-grid"><Field name="name" label="任务名称" required defaultValue={`巡检_${new Date().toLocaleDateString("zh-CN")}`} /><Select name="inspection_level" label="检查级别" options={["quick", "full"]} optionLabels={["快速巡检", "完整巡检"]} /><Select name="collection_mode" label="采集方式" options={["auto", "snmp", "ssh"]} optionLabels={["自动", "仅 SNMP", "仅 SSH"]} defaultValue={defaultCollectionMode} /></div><div className="device-picker"><div className="picker-title">选择设备 <span>{ids.length}/{activeDevices.length}</span></div><div className="picker-toolbar"><select aria-label="按分组筛选设备" value={groupName} onChange={(event) => setGroupName(event.target.value)}><option value="">全部启用设备</option>{groups.map((group) => <option key={group} value={group}>{group}</option>)}</select><Button type="button" variant="outline" size="sm" disabled={submitting || !visibleIds.length} onClick={toggleVisible}>{allVisibleSelected ? "取消当前选择" : groupName ? "选择当前分组" : "选择全部"}</Button></div>{visibleDevices.map((device) => <label key={device.id}><input type="checkbox" disabled={submitting} checked={ids.includes(device.id)} onChange={() => toggleDevice(device.id)} />{device.name ?? device.ip}<small>{device.ip} · {vendorText(catalog, device.vendor)}</small></label>)}{!visibleDevices.length && <div className="picker-empty">没有可执行巡检的启用设备。</div>}</div>{formError && <div className="dialog-error" role="alert">{formError}</div>}</div><div className="dialog-actions"><Button type="button" variant="outline" onClick={onClose} disabled={submitting}>取消</Button><Button type="submit" disabled={submitting || !ids.length}><Play size={16} />{submitting ? "正在创建…" : "开始巡检"}</Button></div></form></div>
}
function Field({ label, name, required, type = "text", defaultValue, value, placeholder, disabled, hint, autoComplete, onChange }: { label: string; name: string; required?: boolean; type?: string; defaultValue?: string; value?: string; placeholder?: string; disabled?: boolean; hint?: string; autoComplete?: string; onChange?: (event: ChangeEvent<HTMLInputElement>) => void }) { return <label className="field"><span>{label}{required && <em>*</em>}</span><Input name={name} type={type} required={required} defaultValue={value === undefined ? defaultValue : undefined} value={value} placeholder={placeholder} disabled={disabled} autoComplete={autoComplete} onChange={onChange} />{hint && <small className="field-hint">{hint}</small>}</label>; }
function Select({ label, name, options, optionLabels, defaultValue, value, onChange }: { label: string; name: string; options: string[]; optionLabels?: string[]; defaultValue?: string; value?: string; onChange?: (event: ChangeEvent<HTMLSelectElement>) => void }) { return <label className="field"><span>{label}</span><select name={name} defaultValue={value === undefined ? defaultValue : undefined} value={value} onChange={onChange}>{options.map((option, index) => <option key={option} value={option}>{optionLabels?.[index] ?? option}</option>)}</select></label>; }


function LoginScreen({ initialized, onAuthenticated, onError, notice }: { initialized: boolean; onAuthenticated: (token: string) => void; onError: (message: string) => void; notice?: string | null }) {
  const [loginMode, setLoginMode] = useState(initialized);
  const [submitting, setSubmitting] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const invalid = loginMode
      ? validateAdminCredentials({ username, password })
      : validateAdminCredentials({ username, password, confirmPassword });
    if (invalid) {
      setFormError(invalid);
      return;
    }
    setFormError(null);
    setSubmitting(true);
    const credentials = { username, password };
    try {
      if (!loginMode) {
        await api("/auth/bootstrap", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials) });
        setLoginMode(true);
        setConfirmPassword("");
      }
      const session = await api<{ access_token: string }>("/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(credentials) });
      onAuthenticated(session.access_token);
    } catch (err) {
      const message = userErrorMessage(err, "登录失败");
      setFormError(message);
      onError(message);
    } finally {
      setSubmitting(false);
    }
  };
  const passwordTooShort = !loginMode && password.length > 0 && password.length < ADMIN_PASSWORD_MIN_LENGTH;
  return <main className="login-screen"><form className="login-card" onSubmit={submit} noValidate>
    <div className="login-brand"><MonitorCog size={27} /><span>网络巡检</span></div>
    <h1>{loginMode ? "管理员登录" : "初始化管理员"}</h1>
    <p>{loginMode ? "验证本地管理员账号后进入巡检工作台。" : "首次启动，请创建本地管理员账号。"}</p>
    {notice && <div className="notice">{notice}</div>}
    <label className="field"><span>用户名<em>*</em></span><Input name="username" autoComplete="username" value={username} onChange={(event: ChangeEvent<HTMLInputElement>) => { setUsername(event.target.value); setFormError(null); }} /></label>
    <label className="field">
      <span>密码<em>*</em></span>
      <Input name="password" type="password" autoComplete={loginMode ? "current-password" : "new-password"} value={password} onChange={(event: ChangeEvent<HTMLInputElement>) => { setPassword(event.target.value); setFormError(null); }} aria-describedby={loginMode ? undefined : "password-rule"} />
      {!loginMode && <small id="password-rule" className={`field-hint ${passwordTooShort ? "field-hint--warn" : ""}`}>密码至少 {ADMIN_PASSWORD_MIN_LENGTH} 位字符{passwordTooShort ? `，当前 ${password.length} 位` : ""}</small>}
    </label>
    {!loginMode && <label className="field"><span>确认密码<em>*</em></span><Input name="confirm_password" type="password" autoComplete="new-password" value={confirmPassword} onChange={(event: ChangeEvent<HTMLInputElement>) => { setConfirmPassword(event.target.value); setFormError(null); }} /></label>}
    {formError && <div className="inline-error" role="alert">{formError}</div>}
    <Button type="submit" disabled={submitting}>{submitting ? "处理中..." : loginMode ? "登录" : "创建并登录"}</Button>
  </form></main>;
}
export default App;
