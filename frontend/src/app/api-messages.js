/**
 * The local API answers in English. The UI must never show those strings to the
 * operator (requirements doc section 5.1), so translate them centrally here.
 *
 * Keys are matched exactly first, then by prefix for messages that embed a
 * variable part (file names, validation details, thresholds).
 */
const EXACT = {
  // activity log levels and event names
  INFO: "信息",
  WARNING: "警告",
  ERROR: "错误",
  admin_initialized: "管理员初始化",
  login_failed: "登录失败",
  login_success: "登录成功",
  admin_password_changed: "管理员密码修改",
  backup_device_failed: "设备配置备份失败",
  backup_save_failed: "配置备份保存失败",
  backup_finished: "配置备份完成",
  backup_deleted: "删除配置备份",
  backup_directory_open_failed: "打开备份目录失败",
  backup_directory_opened: "已打开备份目录",
  device_created: "添加设备",
  device_connectivity_test: "设备连接测试",
  ssh_host_fingerprint_confirmation_mismatch: "SSH 主机密钥确认不一致",
  ssh_host_fingerprint_trusted: "已信任 SSH 主机密钥",
  device_updated: "更新设备",
  device_deleted: "删除设备",
  device_export: "导出设备",
  device_import_failed: "导入设备失败",
  device_import: "导入设备",
  inspection_report_export_failed: "巡检报告导出失败",
  inspection_report_exported: "巡检报告已导出",
  result_screenshot_failed: "巡检截图保存失败",
  result_screenshot_saved: "巡检截图已保存",
  settings_updated: "更新系统设置",
  task_started: "巡检任务开始",
  device_collection_error: "设备采集异常",
  task_finished: "巡检任务结束",
  task_execution_error: "巡检任务执行异常",
  task_deleted: "删除巡检任务",
  database_backup: "数据库备份",
  database_restore_cancelled: "数据库恢复已取消",
  database_restored: "数据库已恢复",
  raw_output_retention_cleanup: "清理过期原始回显",
  task_cancelled_during_shutdown: "取消任务已完成收尾",
  task_interrupted: "任务异常中断",

  // authentication / session
  "Invalid username or password": "用户名或密码错误",
  "Username cannot be blank": "用户名不能为空",
  "Field cannot be null": "该字段不能为空",
  "Task name cannot be blank": "任务名称不能为空",
  "Administrator login required": "登录已失效，请重新登录",
  "Administrator has already been initialized": "管理员账号已初始化，无法重复创建",
  "Current password is incorrect": "当前密码不正确",
  "Local runtime token is required": "本机服务校验失败，请重启程序",

  // devices
  "Device not found": "设备不存在，可能已被删除",
  "Management IP already exists": "该管理 IP 已存在",
  "SSH credentials not configured": "未配置 SSH 凭据",
  "At least one complete SNMP or SSH credential set is required": "至少需要一套完整的 SNMP 或 SSH 凭据",
  "SSH host key changed after confirmation; inspect and confirm the current key again":
    "SSH 主机密钥在确认后发生变化，请核对并重新确认",
  "One or more devices were not found": "部分设备不存在",
  "One or more active devices were not found": "部分启用设备不存在",

  // SNMPv3 validation
  "SNMPv3 requires username and security level": "SNMPv3 需要用户名和安全级别",
  "SNMPv3 noAuthNoPriv must not include authentication or privacy parameters":
    "SNMPv3 noAuthNoPriv 不应填写认证或加密参数",
  "SNMPv3 authNoPriv requires authentication protocol and key": "SNMPv3 authNoPriv 需要认证协议和认证密钥",
  "SNMPv3 authNoPriv must not include privacy parameters": "SNMPv3 authNoPriv 不应填写加密参数",
  "SNMPv3 authPriv requires authentication and privacy protocols and keys":
    "SNMPv3 authPriv 需要完整的认证与加密协议及密钥",

  // tasks / reports
  "Task not found": "任务不存在",
  "Finished task cannot be cancelled": "任务已结束，无法取消",
  "Only finished tasks can be retried": "任务仍在执行，结束后才能重新执行",
  "No active devices from the original task remain": "原任务中的设备均已停用或删除，无法重新执行",
  "Running task must be cancelled before it can be deleted": "运行中的任务需先取消并等待收尾后才能删除",
  "Inspection report is only available after task completion": "任务结束后才能导出巡检报告",
  "Inspection result not found": "巡检结果不存在",

  // excel / screenshots
  "Please upload an .xlsx file": "请上传 .xlsx 格式的文件",
  "Excel import file is too large": "Excel 导入文件过大，请使用不超过 10 MB 的文件",
  "Screenshot must be a PNG image": "截图必须是 PNG 图片",
  "Screenshot is too large": "截图文件过大",
  "Could not save screenshot": "截图保存失败",

  // backup / database
  "Valid backup not found": "未找到可用的备份记录",
  "Backup file is no longer available": "备份文件已不存在",
  "Could not open backup directory": "无法打开备份目录",
  "Backup record not found": "备份记录不存在或已删除",
  "Backup file is outside the configured backup root": "备份文件路径超出允许范围，已拒绝删除",
  "Could not delete backup file": "无法删除备份文件，请确认文件未被占用且目录可写",
  "Configuration backup record deleted": "配置备份记录已删除",
  "Invalid SQLite backup file": "不是有效的 SQLite 备份文件",
  "SQLite backup failed integrity check": "SQLite 备份完整性校验失败，文件可能已损坏",
  "Backup file does not match the inspection database schema": "备份文件与当前数据库结构不匹配",
  "Database restore requires confirmation": "数据库恢复需要确认",
  "Please upload an SQLite backup file": "请上传 SQLite 备份文件",
  "Database backup file is too large": "数据库备份文件过大，无法恢复",
  "Cannot restore while an inspection task is running": "有巡检任务正在执行，无法恢复数据库",
  "Cannot restore while a configuration backup is running": "有配置备份正在执行，无法恢复数据库",
  "Backup ignore pattern is too long": "忽略规则过长",
  "Excel file contains too many archive entries": "Excel 文件包含过多压缩条目",
  "Excel file contains excessive uncompressed data": "Excel 解压后的数据量过大",
  "Excel file compression ratio is unsafe": "Excel 压缩比不安全",
  "Excel worksheet contains too many rows": "Excel 工作表行数过多",
  "Excel worksheet contains too many columns": "Excel 工作表列数过多",
  "Database restore failed and rollback failed; safety copy was retained": "数据库恢复失败且回滚失败，已保留安全副本",

  // credentials
  "Credential encryption requires Windows DPAPI": "凭据加密需要 Windows DPAPI 支持",
  "Credential decryption requires Windows DPAPI": "凭据解密需要 Windows DPAPI 支持",
  // collector / inspection result messages (shown in results and notices)
  "SSH host fingerprint is not trusted; administrator confirmation required":
    "SSH 主机密钥尚未信任，需管理员确认",
  "SSH host fingerprint changed; administrator confirmation required":
    "SSH 主机密钥已变化，需管理员重新确认",
  "SSH supplementary collection skipped: SSH credentials not configured":
    "未配置 SSH 凭据，已跳过 SSH 补充采集",
  "SNMPv2c Community not configured": "未配置 SNMPv2c Community",
  "SNMP credentials not configured": "未配置 SNMP 凭据",
  "Incomplete standard SNMP response": "SNMP 响应不完整",
  "SNMP sysName/sysDescr returned an invalid value": "SNMP 返回的设备名称或描述无效",
  "SNMP sysUpTime returned an invalid value": "SNMP 返回的运行时间无效",
  "SNMP interface status could not be collected": "无法采集 SNMP 接口状态",
  "SNMP metric collection failed": "SNMP 指标采集失败",
  "SNMP value is invalid": "SNMP 返回值无效",
  "No supported inspection metrics could be parsed from SSH output":
    "无法从 SSH 回显中解析出受支持的巡检指标",
  "Configuration output is empty or truncated": "配置回显为空或已被截断",
  "Configuration output contains a device error message": "配置回显包含设备错误信息",
  "Configuration output contains a paging marker and may be incomplete": "配置回显包含分页提示，可能不完整",
  "Configuration output contains a truncated output notice": "配置回显包含截断提示",
  "Configuration output contains an unterminated banner and is truncated": "配置回显中的横幅未结束，内容已截断",
  "Configuration validation does not support the selected platform": "当前平台不支持配置完整性校验",
  "Configuration output has no recognized configuration structure for this platform": "配置回显不符合当前平台的配置结构",
  "Configuration output is missing the expected configuration structure": "配置回显缺少必要的配置结构",
  "Configuration output is truncated or missing its final return marker": "配置回显已截断或缺少末尾 return 标记",
  "Configuration output is truncated or missing its final end marker": "配置回显已截断或缺少末尾 end 标记",
  "No previous valid backup": "没有上一份有效备份",
  "Device was deleted before task execution": "设备在任务执行前已被删除",
  "Task cancelled before collection started": "任务在采集开始前已取消",
  "SNMP interface metrics could not be collected": "无法采集 SNMP 接口指标",
  "SNMP probe returned no usable values": "SNMP 探测未返回可用数据",
  "No usable collection protocol configured": "未配置可用的采集协议",
  "Device platform configuration is invalid; edit the device and select a supported platform": "设备平台配置无效，请编辑设备并选择受支持的平台",

  // Inspection item downgrade reasons and data-source references. MIB and
  // protocol names stay verbatim because those are the identifiers printed in
  // vendor documentation; only the surrounding prose is translated.
  "No verified collector is available for this item": "该机型暂无已验证的采集方式",
  "No verified SSH command is available for this item": "该机型暂无已验证的 SSH 命令",
  "SSH output did not contain a supported value": "SSH 回显中没有可解析的数值",
  "SNMP value unavailable": "SNMP 未返回该数值，已改用 SSH 采集",
  "Device rejected the SSH command as unsupported": "设备不支持该 SSH 命令",
  "No active temperature alarms": "未发现活动温度告警",
  "No active device alarms": "未发现设备活动告警",
  "Active temperature alarm detected": "检测到活动温度告警",
  "Active power_supplies alarm detected": "检测到活动电源告警",
  "Active fans alarm detected": "检测到活动风扇告警",
  "No link aggregation is configured": "设备未配置链路聚合",
  "STP is not enabled": "设备未启用 STP",
  "OSPF is not configured or has no neighbors": "设备未启用动态路由协议或没有邻居",
  "Startup configuration file presence does not prove the current configuration is saved":
    "仅检测到启动配置文件，无法确认当前配置是否已保存",
  "vendor CPU metric": "厂商 CPU 指标",
  "vendor memory metric": "厂商内存指标",
  "vendor STP command or MIB": "厂商 STP 命令或 MIB",
  "vendor active alarm command or MIB": "厂商活动告警命令或 MIB",
  "vendor command or MIB": "厂商命令或 MIB",
  "ENTITY-MIB or vendor command": "ENTITY-MIB 或厂商命令",
  "ENTITY-SENSOR-MIB or vendor command": "ENTITY-SENSOR-MIB 或厂商命令",

  // SNMP probe failures (verbatim pysnmp errind strings)
  "No SNMP response received before timeout": "设备未在超时时间内应答 SNMP，请确认 IP、端口可达且已开启 SNMP",
  "Unknown SNMP community name encountered": "SNMP Community 错误，设备未接受该只读团体名",
  "Wrong SNMP PDU digest": "SNMPv3 认证失败，认证密钥不正确",
  "Unknown USM user": "SNMPv3 用户名在设备上不存在",
  "Unknown SNMP security name encountered": "SNMPv3 安全名称在设备上不存在",
  "Authenticator mismatched": "SNMPv3 认证不匹配，请核对认证协议与密钥",
  "SNMP message timing parameters not in windows of trust": "SNMPv3 时间窗口不同步，请核对本机与设备的时钟",
  "Unsupported SNMP security level": "设备不支持所选的 SNMPv3 安全级别",
  "Authentication protocol is not supported": "设备不支持所选的认证协议",
  "Privacy protocol is not supported": "设备不支持所选的加密协议",
  "Ciphering services not available or bad parameters": "认证参数有误或本机缺少对应的加密组件",
  "Ciphering services not available or ciphertext is broken": "解密失败，请核对加密协议与密钥",
  "Ciphering services not available": "本机缺少对应的加密组件",
  "Empty SNMP response message": "设备返回了空的 SNMP 应答",
  "SNMP engine ID mismatch encountered": "SNMPv3 引擎 ID 不匹配",
  "noAccess": "设备拒绝访问 MIB 节点，请核对只读视图权限",
  "authorizationError": "设备拒绝访问 MIB 节点，请核对只读视图权限",
  "Access to MIB node denied": "设备拒绝访问该 MIB 节点，请核对只读视图权限",
  "Requested OID is out of MIB view": "请求的 OID 不在设备允许的 MIB 视图内",
  "No such VACM group configured": "设备未配置对应的 VACM 用户组",
  "No such MIB view currently exists": "设备上不存在对应的 MIB 视图",
  "SNMP request/response parameters mismatched": "SNMP 请求与应答参数不匹配",
  "SNMP table returned a non-increasing OID": "SNMP 表返回了非递增 OID",
  "Remote SNMP engine reported error": "设备端 SNMP 引擎返回错误",
  "Unspecified SNMP engine error occurred": "SNMP 引擎发生未知错误",

  // SSH probe failures (verbatim paramiko / netmiko strings)
  "Authentication failed.": "SSH 认证失败，请核对用户名与密码",
  "No existing session": "SSH 会话未建立，设备可能拒绝了连接",
  "timed out": "连接设备超时，请确认 IP、SSH 端口与链路",
  "Raw output reached its storage limit": "原始回显已达存储上限",

  // persisted activity messages
  "Local administrator initialized": "本地管理员已初始化",
  "Local administrator login failed": "本地管理员登录失败",
  "Local administrator logged in": "本地管理员已登录",
  "Local administrator password changed": "本地管理员密码已修改",
  "Device added": "设备已添加",
  "Device updated": "设备已更新",
  "Device deleted": "设备已删除",
  "SSH host key changed after administrator confirmation was displayed":
    "管理员确认期间 SSH 主机密钥发生变化",
  "Administrator confirmed a new SSH host fingerprint": "管理员已确认新的 SSH 主机指纹",
  "Local inspection settings updated": "本机巡检设置已更新",
  "Database restore request was not confirmed": "数据库恢复请求未确认",
  "Cancelled task finalized after the desktop application restarted":
    "桌面程序重启后已完成取消任务的收尾",
  "Task marked failed after the desktop application exited before completion":
    "桌面程序在任务完成前退出，任务已标记为失败",
};

const REASON_PREFIX = [
  ["SNMP fallback:", "SNMP 失败，已降级使用 SSH："],
  ["SNMP failed, SSH not configured:", "SNMP 失败，且未配置 SSH："],
  ["SSH supplementary collection unavailable:", "SSH 补充采集不可用："],
];

const PREFIX = [
  ["Invalid Excel file:", "Excel 文件无法读取"],
  ["Duplicate Excel columns:", "Excel 存在重复列："],
  ["Missing required Excel columns:", "Excel 缺少必填列"],
  ["Invalid backup ignore pattern:", "忽略规则不是有效的正则表达式"],
  ["Database migration failed", "数据库升级失败，已从备份恢复"],
  ["Stored credential could not be decrypted", "设备凭据无法在当前 Windows 用户下解密，请重新录入"],
  ["SSH command output decoding failed:", "该次备份的 SSH 输出编码无法识别；新版已兼容常见中文编码，请重新执行备份"],
  ["SSH command output exceeded", "SSH 命令回显超过单次存储上限"],
  ["SSH device output exceeded", "SSH 设备回显超过存储上限"],
  ["SNMP device collection exceeded elapsed time limit", "SNMP 单设备采集超过总时间上限"],
  ["SNMP table exceeded elapsed time limit", "SNMP 表遍历超过时间上限"],
  ["SNMP table exceeded row limit", "SNMP 表遍历超过行数上限"],
  ["No SNMP response received before timeout", "设备未在超时时间内应答 SNMP，请确认 IP、端口可达且已开启 SNMP"],
  ["Authentication failed", "SSH 认证失败，请核对用户名与密码"],
  ["TCP connection to device failed", "无法建立到设备的 TCP 连接，请确认 IP、端口与链路"],
  ["Timed-out reading channel", "读取设备回显超时"],
  ["Pattern not detected", "未识别到设备提示符，该型号可能需要单独适配"],
  ["SSH command timed out before the device prompt was received", "SSH 命令等待设备提示符超时，后续命令未执行"],
  ["SSH command failed", "SSH 命令执行失败"],
  ["IF-MIB error counter columns unavailable:", "IF-MIB 错误计数列不可用："],
  ["Unexpected collection error", "采集过程出现未预期错误"],
  ["Task execution failed", "巡检任务执行异常"],
  ["Could not save backup", "配置备份保存失败"],
  ["Could not save inspection report", "巡检报告保存失败"],
  ["Could not save screenshot", "巡检截图保存失败"],
  ["Could not open backup directory", "无法打开备份目录"],
  ["Unsupported vendor:", "不支持该设备厂商"],
  ["Unsupported platform:", "不支持该设备系统平台"],
  ["Platform ", "所选系统平台不属于该厂商"],
  ["Device type ", "所选系统平台不支持该设备类型"],
  ["Settings fields cannot be null:", "系统设置字段不能为 null："],
  ["Could not read SSH host fingerprint", "无法读取 SSH 主机指纹"],
];

const SUFFIX = [
  ["directory is not writable", "目录不可写入"],
  ["critical threshold must not be lower than warning threshold", "严重阈值不能低于警告阈值"],
];

const TASK_STATUS = {
  queued: "等待执行",
  running: "正在执行",
  completed: "已完成",
  partial_failed: "部分失败",
  failed: "执行失败",
  cancelled: "已取消",
};

const ACTIVITY_PATTERNS = [
  [/^Backup completed for (\d+) devices$/, (match) => `已完成 ${match[1]} 台设备的配置备份`],
  [/^Task (.+) started$/, (match) => `巡检任务“${match[1]}”已开始`],
  [/^Task finished: ([a-z_]+)$/, (match) => `巡检任务结束：${TASK_STATUS[match[1]] ?? match[1]}`],
  [/^Database backup created: (.+)$/, (match) => `数据库备份已创建：${match[1]}`],
  [/^Database restored from (.+)$/, (match) => `已从备份恢复数据库：${match[1]}`],
  [/^Backup directory opened: (.+)$/, (match) => `已打开备份目录：${match[1]}`],
  [/^Exported (\d+) devices without credentials$/, (match) => `已导出 ${match[1]} 台设备，未包含凭据`],
  [/^Rejected import with (\d+) invalid rows$/, (match) => `设备导入已拒绝，其中 ${match[1]} 行无效`],
  [/^Imported (\d+) devices$/, (match) => `已导入 ${match[1]} 台设备`],
  [/^Inspection report saved: (.+)$/, (match) => `巡检报告已保存：${match[1]}`],
  [/^Screenshot saved: (.+)$/, (match) => `巡检截图已保存：${match[1]}`],
  [/^Expired raw output files removed: (\d+)$/, (match) => `已清理 ${match[1]} 个过期原始回显文件`],
  [
    /^Inspection task deleted: (.+) \(status=([a-z_]+), devices=(\d+), failed=(\d+)\)$/,
    (match) => `已删除巡检任务“${match[1]}”（状态：${TASK_STATUS[match[2]] ?? match[2]}，设备：${match[3]}，失败：${match[4]}）`,
  ],
  [
    /^(SNMP|SSH) (success|connection failed|authentication failed|credentials failed|collection failed|parsing failed|validation failed)(?:: (.+))?$/,
    (match) => {
      const status = {
        success: "连接成功",
        "connection failed": "连接失败",
        "authentication failed": "认证失败",
        "credentials failed": "凭据错误",
        "collection failed": "采集失败",
        "parsing failed": "解析失败",
        "validation failed": "校验失败",
      }[match[2]];
      return `${match[1]} ${status}${match[3] ? `：${translateApiMessage(match[3])}` : ""}`;
    },
  ],
];

export function translateApiMessage(message) {
  if (typeof message !== "string" || !message) return message;
  const text = message.trim();
  const protocolPair = text.match(/^SNMP:\s*(.+?);\s*SSH:\s*(.+)$/);
  if (protocolPair) {
    return `SNMP：${translateApiMessage(protocolPair[1])}；SSH：${translateApiMessage(protocolPair[2])}`;
  }
  if (EXACT[text]) return EXACT[text];
  const minimumLength = text.match(/^String should have at least (\d+) characters$/);
  if (minimumLength) return `长度至少需要 ${minimumLength[1]} 个字符`;
  const maximumLength = text.match(/^String should have at most (\d+) characters$/);
  if (maximumLength) return `长度不能超过 ${maximumLength[1]} 个字符`;
  if (text === "Field required") return "该字段为必填项";
  for (const [prefix, chinese] of REASON_PREFIX) {
    if (text.startsWith(prefix)) {
      return chinese + translateApiMessage(text.slice(prefix.length).trim());
    }
  }
  for (const [prefix, chinese] of PREFIX) {
    if (text.startsWith(prefix)) return chinese;
  }
  for (const [suffix, chinese] of SUFFIX) {
    if (text.endsWith(suffix)) return chinese;
  }
  for (const [pattern, render] of ACTIVITY_PATTERNS) {
    const match = text.match(pattern);
    if (match) return render(match);
  }
  if (/(?:winerror|error|failed|failure|exception|traceback|timed?\s*out|refused|denied|unreachable|reset by peer|not found|invalid|unsupported)/i.test(text)) {
    return "设备返回了未识别的错误，请查看运行日志获取诊断信息";
  }
  return message;
}

export function userErrorMessage(error, fallback = "操作失败，请重试；如问题持续，请查看运行日志") {
  const message = error instanceof Error
    ? error.message
    : typeof error === "string"
      ? error
      : "";
  const translated = translateApiMessage(message);
  return typeof translated === "string" && /[\u4e00-\u9fff]/.test(translated)
    ? translated
    : fallback;
}
