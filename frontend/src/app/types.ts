export type Device = {
  id: number;
  name: string | null;
  ip: string;
  vendor: string;
  platform_id: string;
  device_type: string;
  group_name: string | null;
  note: string | null;
  ssh_port: number;
  has_ssh: boolean;
  snmp_version: "v2c" | "v3" | null;
  snmp_port: number;
  has_snmp_credentials: boolean;
  snmp_v3_username: string | null;
  snmp_v3_security_level: "noAuthNoPriv" | "authNoPriv" | "authPriv" | null;
  snmp_v3_auth_protocol: "MD5" | "SHA" | "SHA224" | "SHA256" | "SHA384" | "SHA512" | null;
  snmp_v3_priv_protocol: "DES" | "3DES" | "AES" | "AES192" | "AES256" | null;
  is_active: boolean;
};

export type Result = {
  id: number;
  device_ip: string;
  device_name: string | null;
  vendor: string | null;
  collection_source: string | null;
  conclusion: string;
  failure_stage: string | null;
  message: string | null;
  metrics: Record<string, unknown>;
  raw_output?: string | null;
  raw_output_status?: string;
  raw_output_bytes?: number;
  created_at?: string;
};

export type Task = {
  id: number;
  name: string;
  status: string;
  inspection_level: string;
  collection_mode: string;
  total_devices: number;
  completed_devices: number;
  success_devices: number;
  failed_devices: number;
  normal_devices: number;
  warning_devices: number;
  critical_devices: number;
  connection_failed_devices: number;
  authentication_failed_devices: number;
  data_fetch_failed_devices: number;
  cancelled_devices: number;
  unsupported_devices: number;
  not_applicable_devices: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type ImportIssue = { row: number; reason: string };

export type InspectionSettings = {
  max_concurrency: number;
  default_collection_mode: "auto" | "snmp" | "ssh";
  snmp_timeout_seconds: number;
  snmp_retries: number;
  connection_timeout_seconds: number;
  command_timeout_seconds: number;
  network_retry_count: number;
  cpu_warning_threshold: number;
  cpu_critical_threshold: number;
  memory_warning_threshold: number;
  memory_critical_threshold: number;
  report_directory: string | null;
  backup_directory: string | null;
  log_retention_days: number;
  backup_retention_count: number;
  backup_ignore_patterns: string[];
  storage_warning_threshold: number;
  storage_critical_threshold: number;
  raw_output_retention_days: number;
};

export type ActivityLogEntry = {
  id: number;
  level: string;
  event: string;
  device_ip: string | null;
  task_id: number | null;
  message: string | null;
  created_at: string;
};

export type LogPage = { total: number; snapshot_id: number; items: ActivityLogEntry[] };

export type LogDeleteResult = { deleted: number };

export type ConnectionTestResult = {
  protocol: "SNMP" | "SSH";
  status: "success" | "connection_failed" | "authentication_failed" | "data_fetch_failed";
  failure_stage: string | null;
  message: string | null;
  host_fingerprint?: string | null;
  host_key_algorithm?: string | null;
};

export type BackupRecord = {
  id: number;
  device_id: number | null;
  device_ip: string;
  file_path: string | null;
  status: string;
  message: string | null;
  created_at: string;
};

export type VerifiedAdapter = {
  vendor?: string;
  status?: string;
  model_range?: string;
  version_range?: string;
  inspection_items?: string[];
};

export type AdapterCapability = {
  vendor: string;
  platform_id: string;
  platform_label: string;
  status: "unverified";
  device_types: string[];
  protocols: string[];
  backup_command: string;
  inspection_items: string[];
};

export type DeviceCatalog = {
  device_types: Array<{ id: string; label: string }>;
  vendors: Array<{
    id: string;
    label: string;
    aliases: string[];
    default_platform_id: string;
    device_types: string[];
    platforms: Array<{
      id: string;
      label: string;
      netmiko_type: string;
      device_types: string[];
    }>;
  }>;
};

export type NoticeTone = "success" | "warning" | "progress";
export type Notice = { message: string; tone: NoticeTone };

export type ConfirmationOptions = {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
};

export type Page = "dashboard" | "devices" | "tasks" | "results" | "adapters" | "backup" | "settings" | "logs";
