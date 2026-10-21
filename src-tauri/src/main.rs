#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{Ipv4Addr, SocketAddr, SocketAddrV4, TcpStream};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};

use hmac::{Hmac, Mac};
use sha2::Sha256;
use tauri::{Manager, WindowEvent};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

mod backend_port;
mod app_updates;
#[cfg(test)]
mod updater_tests;

const SIDECAR_START_ATTEMPTS: usize = 5;
const SIDECAR_READY_TIMEOUT: Duration = Duration::from_secs(12);
const HEALTH_POLL_INTERVAL: Duration = Duration::from_millis(100);
const HEALTH_IO_TIMEOUT: Duration = Duration::from_millis(400);
const MAX_HEALTH_RESPONSE_BYTES: u64 = 16 * 1024;
const MAX_DIAGNOSTIC_BYTES: usize = 8 * 1024;
const DESKTOP_LOG_MAX_BYTES: u64 = 1024 * 1024;
static DESKTOP_LOG_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

struct BackendState {
    runtime: Arc<Mutex<BackendRuntime>>,
    restart: Arc<Mutex<()>>,
}

struct BackendRuntime {
    active: Option<ManagedBackend>,
    config: BackendConfig,
    availability: BackendAvailability,
    generation: u64,
    last_diagnostic: Option<String>,
    closing: bool,
}

impl BackendRuntime {
    fn unavailable() -> Self {
        Self {
            active: None,
            config: BackendConfig::unavailable(),
            availability: BackendAvailability::Unavailable,
            generation: 0,
            last_diagnostic: None,
            closing: false,
        }
    }
}

struct ManagedBackend {
    child: CommandChild,
    observation: Arc<Mutex<ProcessObservation>>,
}

struct CandidateBackend {
    child: CommandChild,
    observation: Arc<Mutex<ProcessObservation>>,
    config: BackendConfig,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum BackendAvailability {
    Ready,
    Unavailable,
}

#[derive(Clone, Debug, Eq, PartialEq, serde::Serialize)]
struct BackendConfig {
    url: String,
    runtime_token: String,
}

impl BackendConfig {
    fn unavailable() -> Self {
        Self {
            url: String::new(),
            runtime_token: String::new(),
        }
    }
}

#[derive(Default)]
struct ProcessObservation {
    stderr: String,
    termination: Option<String>,
    event_error: Option<String>,
}

impl ProcessObservation {
    fn append_stderr(&mut self, bytes: &[u8]) {
        self.stderr.push_str(&String::from_utf8_lossy(bytes));
        if self.stderr.len() > MAX_DIAGNOSTIC_BYTES {
            let mut remove_to = self.stderr.len() - MAX_DIAGNOSTIC_BYTES;
            while !self.stderr.is_char_boundary(remove_to) {
                remove_to += 1;
            }
            self.stderr.drain(..remove_to);
        }
    }

    fn failure(&self) -> Option<String> {
        self.termination
            .as_ref()
            .or(self.event_error.as_ref())
            .map(|reason| {
                let stderr = self.stderr.trim();
                if stderr.is_empty() {
                    reason.clone()
                } else {
                    format!("{reason}; stderr: {stderr}")
                }
            })
    }
}

#[derive(Debug)]
struct LocalHttpResponse {
    status: u16,
    body: Vec<u8>,
}

#[tauri::command]
fn backend_config(state: tauri::State<'_, BackendState>) -> BackendConfig {
    let mut runtime = match state.runtime.lock() {
        Ok(runtime) => runtime,
        Err(_) => return BackendConfig::unavailable(),
    };

    let failure = runtime
        .active
        .as_ref()
        .and_then(|active| active.observation.lock().ok()?.failure());
    let failed_backend = failure.map(|failure| {
        runtime.availability = BackendAvailability::Unavailable;
        runtime.config = BackendConfig::unavailable();
        runtime.last_diagnostic = Some(failure);
        runtime.active.take()
    });
    let config = publishable_config(&runtime.config, runtime.availability);
    drop(runtime);

    if let Some(Some(failed_backend)) = failed_backend {
        if let Err(error) =
            terminate_managed_backend(failed_backend, "Failed to terminate an unavailable sidecar")
        {
            append_desktop_log(&error);
        }
    }
    config
}

fn publishable_config(config: &BackendConfig, availability: BackendAvailability) -> BackendConfig {
    match availability {
        BackendAvailability::Ready => config.clone(),
        BackendAvailability::Unavailable => BackendConfig::unavailable(),
    }
}

fn generate_random_bytes(error_message: &'static str) -> io::Result<[u8; 32]> {
    let mut bytes = [0_u8; 32];
    getrandom::getrandom(&mut bytes).map_err(|_| io::Error::other(error_message))?;
    Ok(bytes)
}

fn generate_runtime_token() -> io::Result<String> {
    generate_random_bytes("Could not generate the local runtime token").map(hex::encode)
}

fn generate_health_nonce() -> io::Result<([u8; 32], String)> {
    let bytes = generate_random_bytes("Could not generate the health-check nonce")?;
    let encoded = hex::encode(bytes);
    Ok((bytes, encoded))
}

fn desktop_log_path() -> PathBuf {
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir);
    base.join("NetworkInspection")
        .join("logs")
        .join("desktop-startup.log")
}

fn backup_log_path(path: &Path) -> PathBuf {
    let mut backup = path.as_os_str().to_os_string();
    backup.push(".1");
    PathBuf::from(backup)
}

fn append_rotating_log(path: &Path, message: &str, max_bytes: u64) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= max_bytes)
    {
        let backup = backup_log_path(path);
        match fs::remove_file(&backup) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        fs::rename(path, backup)?;
    }
    let mut stream = OpenOptions::new().create(true).append(true).open(path)?;
    writeln!(stream, "[{:?}] {message}", SystemTime::now())
}

fn append_desktop_log(message: &str) {
    let path = desktop_log_path();
    let _guard = DESKTOP_LOG_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Err(error) = append_rotating_log(&path, message, DESKTOP_LOG_MAX_BYTES) {
        eprintln!("Could not write desktop log {}: {error}", path.display());
    }
}

fn observation_failure(observation: &Arc<Mutex<ProcessObservation>>) -> Option<String> {
    observation.lock().ok()?.failure()
}

fn runtime_is_closing(runtime: &Arc<Mutex<BackendRuntime>>) -> bool {
    runtime
        .lock()
        .map(|runtime| runtime.closing)
        .unwrap_or(true)
}

fn mark_active_unavailable(
    runtime: &Arc<Mutex<BackendRuntime>>,
    observation: &Arc<Mutex<ProcessObservation>>,
    reason: String,
    terminate_process: bool,
) {
    let child = {
        let mut runtime = match runtime.lock() {
            Ok(runtime) => runtime,
            Err(_) => {
                append_desktop_log(
                    "Backend state lock was poisoned while handling a sidecar event",
                );
                return;
            }
        };
        let is_active = runtime
            .active
            .as_ref()
            .is_some_and(|active| Arc::ptr_eq(&active.observation, observation));
        if !is_active {
            return;
        }

        runtime.availability = BackendAvailability::Unavailable;
        runtime.config = BackendConfig::unavailable();
        runtime.last_diagnostic = Some(reason.clone());
        runtime.active.take().map(|active| active.child)
    };

    append_desktop_log(&reason);
    if terminate_process {
        if let Some(child) = child {
            if let Err(error) = terminate_child_process_tree(
                child,
                "Failed to terminate sidecar after a process event",
            ) {
                append_desktop_log(&error);
            }
        }
    }
}

fn spawn_event_monitor(
    mut receiver: tauri::async_runtime::Receiver<CommandEvent>,
    runtime: Arc<Mutex<BackendRuntime>>,
    observation: Arc<Mutex<ProcessObservation>>,
) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(_) => {}
                CommandEvent::Stderr(bytes) => {
                    if let Ok(mut current) = observation.lock() {
                        current.append_stderr(&bytes);
                    }
                }
                CommandEvent::Error(error) => {
                    let reason = format!("Sidecar process event error: {error}");
                    if let Ok(mut current) = observation.lock() {
                        current.event_error = Some(reason.clone());
                    }
                    let diagnostic = observation_failure(&observation).unwrap_or(reason);
                    mark_active_unavailable(&runtime, &observation, diagnostic, true);
                }
                CommandEvent::Terminated(payload) => {
                    let reason = format!(
                        "Sidecar exited (code {:?}, signal {:?})",
                        payload.code, payload.signal
                    );
                    if let Ok(mut current) = observation.lock() {
                        current.termination = Some(reason.clone());
                    }
                    let diagnostic = observation_failure(&observation).unwrap_or(reason);
                    mark_active_unavailable(&runtime, &observation, diagnostic, true);
                }
                _ => {}
            }
        }

        if observation_failure(&observation).is_none() {
            let reason = "Sidecar process event channel closed unexpectedly".to_string();
            if let Ok(mut current) = observation.lock() {
                current.event_error = Some(reason.clone());
            }
            mark_active_unavailable(&runtime, &observation, reason, true);
        }
    });
}

fn parse_http_response(bytes: Vec<u8>) -> io::Result<LocalHttpResponse> {
    let header_end = bytes
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Incomplete HTTP response"))?;
    let status_line_end = bytes
        .windows(2)
        .position(|window| window == b"\r\n")
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Missing HTTP status line"))?;
    let status_line = std::str::from_utf8(&bytes[..status_line_end])
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Invalid HTTP status line"))?;
    let status = status_line
        .split_whitespace()
        .nth(1)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "Missing HTTP status code"))?
        .parse::<u16>()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Invalid HTTP status code"))?;

    Ok(LocalHttpResponse {
        status,
        body: bytes[(header_end + 4)..].to_vec(),
    })
}

fn request_health(port: u16, nonce: &str) -> io::Result<LocalHttpResponse> {
    let address = SocketAddr::V4(SocketAddrV4::new(Ipv4Addr::LOCALHOST, port));
    let mut stream = TcpStream::connect_timeout(&address, HEALTH_IO_TIMEOUT)?;
    stream.set_read_timeout(Some(HEALTH_IO_TIMEOUT))?;
    stream.set_write_timeout(Some(HEALTH_IO_TIMEOUT))?;
    write!(
        stream,
        "GET /health?nonce={nonce} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
    )?;
    stream.flush()?;

    let mut bytes = Vec::new();
    stream
        .take(MAX_HEALTH_RESPONSE_BYTES + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_HEALTH_RESPONSE_BYTES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Health response exceeded its size limit",
        ));
    }
    parse_http_response(bytes)
}

fn json_field_equals(response: &LocalHttpResponse, field: &str, expected: &str) -> bool {
    serde_json::from_slice::<serde_json::Value>(&response.body)
        .ok()
        .and_then(|body| body.get(field)?.as_str().map(|value| value == expected))
        .unwrap_or(false)
}

fn health_proof(response: &LocalHttpResponse) -> Option<Vec<u8>> {
    let body = serde_json::from_slice::<serde_json::Value>(&response.body).ok()?;
    hex::decode(body.get("proof")?.as_str()?).ok()
}

fn is_authenticated_health_response(
    response: &LocalHttpResponse,
    runtime_token: &str,
    nonce: &[u8],
) -> bool {
    if response.status != 200 || !json_field_equals(response, "status", "ok") {
        return false;
    }
    let Some(proof) = health_proof(response) else {
        return false;
    };
    let Ok(mut verifier) = Hmac::<Sha256>::new_from_slice(runtime_token.as_bytes()) else {
        return false;
    };
    verifier.update(nonce);
    verifier.verify_slice(&proof).is_ok()
}

fn wait_until_ready(
    port: u16,
    runtime_token: &str,
    observation: &Arc<Mutex<ProcessObservation>>,
    runtime: &Arc<Mutex<BackendRuntime>>,
) -> Result<(), String> {
    let (nonce_bytes, nonce) = generate_health_nonce().map_err(|error| error.to_string())?;
    let deadline = Instant::now() + SIDECAR_READY_TIMEOUT;
    let mut last_connection_error = None;

    while Instant::now() < deadline {
        if runtime_is_closing(runtime) {
            return Err("Application is closing".to_string());
        }
        if let Some(failure) = observation_failure(observation) {
            return Err(failure);
        }

        match request_health(port, &nonce) {
            Ok(response)
                if is_authenticated_health_response(&response, runtime_token, &nonce_bytes) =>
            {
                if let Some(failure) = observation_failure(observation) {
                    return Err(failure);
                }
                if runtime_is_closing(runtime) {
                    return Err("Application is closing".to_string());
                }
                return Ok(());
            }
            Ok(response) => {
                return Err(format!(
                    "Port {port} returned an invalid sidecar health proof (HTTP {})",
                    response.status
                ));
            }
            Err(error) => last_connection_error = Some(error.to_string()),
        }
        std::thread::sleep(HEALTH_POLL_INTERVAL);
    }

    Err(format!(
        "Sidecar did not become healthy on port {port} before timeout{}",
        last_connection_error
            .map(|error| format!(": {error}"))
            .unwrap_or_default()
    ))
}

fn spawn_candidate(
    app: &tauri::AppHandle,
    runtime: &Arc<Mutex<BackendRuntime>>,
) -> Result<CandidateBackend, String> {
    if runtime_is_closing(runtime) {
        return Err("Application is closing".to_string());
    }
    let reservation = backend_port::BackendPortReservation::reserve()
        .map_err(|error| format!("Could not reserve a backend port: {error}"))?;
    let port = reservation.port();
    let port_argument = port.to_string();
    let parent_process_id = std::process::id().to_string();
    let runtime_token = generate_runtime_token().map_err(|error| error.to_string())?;
    let command = app
        .shell()
        .sidecar("inspection-api")
        .map_err(|error| error.to_string())?
        .env("INSPECTION_RUNTIME_TOKEN", &runtime_token)
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &port_argument,
            "--parent-pid",
            &parent_process_id,
        ]);

    let released_port = reservation.release();
    debug_assert_eq!(released_port, port);
    let (receiver, child) = command
        .spawn()
        .map_err(|error| format!("Could not start the sidecar on port {port}: {error}"))?;
    let observation = Arc::new(Mutex::new(ProcessObservation::default()));
    spawn_event_monitor(receiver, Arc::clone(runtime), Arc::clone(&observation));

    let config = BackendConfig {
        url: format!("http://127.0.0.1:{port}"),
        runtime_token,
    };
    if let Err(error) = wait_until_ready(port, &config.runtime_token, &observation, runtime) {
        let kill_error =
            terminate_child_process_tree(child, "Failed to terminate rejected candidate")
                .err()
                .map(|kill_error| format!("; {kill_error}"))
                .unwrap_or_default();
        return Err(format!("{error}{kill_error}"));
    }

    Ok(CandidateBackend {
        child,
        observation,
        config,
    })
}

fn activate_candidate(
    runtime: &Arc<Mutex<BackendRuntime>>,
    candidate: CandidateBackend,
) -> Result<(BackendConfig, Option<ManagedBackend>), String> {
    let mut current = match runtime.lock() {
        Ok(current) => current,
        Err(_) => {
            let error = terminate_child_process_tree(
                candidate.child,
                "Failed to terminate untracked candidate",
            )
            .err();
            return Err(format!(
                "Could not lock the backend process state{}",
                error.map(|error| format!("; {error}")).unwrap_or_default()
            ));
        }
    };

    if current.closing {
        drop(current);
        let kill_error = terminate_child_process_tree(
            candidate.child,
            "Failed to terminate candidate during application shutdown",
        )
        .err();
        return Err(format!(
            "Application is closing{}",
            kill_error
                .map(|error| format!("; {error}"))
                .unwrap_or_default()
        ));
    }

    if let Some(failure) = observation_failure(&candidate.observation) {
        drop(current);
        let kill_error = terminate_child_process_tree(
            candidate.child,
            "Failed to terminate candidate that exited before publication",
        )
        .err();
        return Err(format!(
            "Sidecar exited before its configuration could be published: {failure}{}",
            kill_error
                .map(|error| format!("; {error}"))
                .unwrap_or_default()
        ));
    }

    let CandidateBackend {
        child,
        observation,
        config,
    } = candidate;
    let old = current
        .active
        .replace(ManagedBackend { child, observation });
    current.config = config.clone();
    current.availability = BackendAvailability::Ready;
    current.generation = current.generation.wrapping_add(1);
    current.last_diagnostic = None;
    Ok((config, old))
}

fn start_and_activate_backend(
    app: &tauri::AppHandle,
    runtime: &Arc<Mutex<BackendRuntime>>,
) -> Result<(BackendConfig, Option<ManagedBackend>), String> {
    let mut failures = Vec::new();
    for attempt in 1..=SIDECAR_START_ATTEMPTS {
        if runtime_is_closing(runtime) {
            return Err("Application is closing".to_string());
        }
        match spawn_candidate(app, runtime) {
            Ok(candidate) => match activate_candidate(runtime, candidate) {
                Ok(activated) => return Ok(activated),
                Err(error) => failures.push(format!("attempt {attempt}: {error}")),
            },
            Err(error) => failures.push(format!("attempt {attempt}: {error}")),
        }
        if runtime_is_closing(runtime) {
            return Err("Application is closing".to_string());
        }
    }

    Err(format!(
        "Could not start a verified local sidecar after {SIDECAR_START_ATTEMPTS} attempts. {}",
        failures.join(" | ")
    ))
}

fn terminate_child_process_tree(child: CommandChild, context: &str) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        let process_id = child.pid();
        let taskkill = std::process::Command::new("taskkill.exe")
            .args(["/PID", &process_id.to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000)
            .output();
        match taskkill {
            Ok(output) if output.status.success() => return Ok(()),
            Ok(output) => {
                let taskkill_error = String::from_utf8_lossy(&output.stderr).trim().to_string();
                return match child.kill() {
                    Ok(()) => Err(format!(
                        "{context}: taskkill failed with code {:?}; only the direct sidecar process was killed and descendants may remain: {taskkill_error}",
                        output.status.code()
                    )),
                    Err(kill_error) => Err(format!(
                        "{context}: taskkill failed with code {:?}: {taskkill_error}; direct kill also failed: {kill_error}; the process tree may remain",
                        output.status.code()
                    )),
                };
            }
            Err(taskkill_error) => {
                return match child.kill() {
                    Ok(()) => Err(format!(
                        "{context}: taskkill could not start; only the direct sidecar process was killed and descendants may remain: {taskkill_error}"
                    )),
                    Err(kill_error) => Err(format!(
                        "{context}: taskkill could not start: {taskkill_error}; direct kill also failed: {kill_error}; the process tree may remain"
                    )),
                };
            }
        }
    }

    #[cfg(not(windows))]
    child.kill().map_err(|error| format!("{context}: {error}"))
}

fn terminate_managed_backend(backend: ManagedBackend, context: &str) -> Result<(), String> {
    terminate_child_process_tree(backend.child, context)
}

fn restart_backend_blocking(
    app: tauri::AppHandle,
    runtime: Arc<Mutex<BackendRuntime>>,
    restart: Arc<Mutex<()>>,
) -> Result<BackendConfig, String> {
    let _restart_guard = restart
        .lock()
        .map_err(|_| "Could not lock the backend restart transaction".to_string())?;
    if runtime_is_closing(&runtime) {
        return Err("Application is closing".to_string());
    }
    let (config, old_backend) = start_and_activate_backend(&app, &runtime)?;

    if let Some(old_backend) = old_backend {
        if let Err(error) =
            terminate_managed_backend(old_backend, "Failed to terminate old sidecar")
        {
            append_desktop_log(&error);
            if let Ok(mut current) = runtime.lock() {
                current.last_diagnostic = Some(error);
            }
        }
    }
    if runtime_is_closing(&runtime) {
        return Err("Application is closing".to_string());
    }
    Ok(config)
}

#[tauri::command]
async fn restart_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, BackendState>,
) -> Result<BackendConfig, String> {
    let runtime = Arc::clone(&state.runtime);
    let restart = Arc::clone(&state.restart);
    tauri::async_runtime::spawn_blocking(move || restart_backend_blocking(app, runtime, restart))
        .await
        .map_err(|error| format!("Backend restart worker failed: {error}"))?
}

fn show_fatal_error(message: &str) {
    #[cfg(windows)]
    {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;

        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(
                window: *mut std::ffi::c_void,
                text: *const u16,
                caption: *const u16,
                kind: u32,
            ) -> i32;
        }

        let text: Vec<u16> = OsStr::new(message).encode_wide().chain(Some(0)).collect();
        let caption: Vec<u16> = OsStr::new("Network Inspection startup error")
            .encode_wide()
            .chain(Some(0))
            .collect();
        unsafe {
            MessageBoxW(std::ptr::null_mut(), text.as_ptr(), caption.as_ptr(), 0x10);
        }
    }

    #[cfg(not(windows))]
    eprintln!("{message}");
}

fn main() {
    let result = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(app_updates::PendingUpdate::default())
        .setup(|app| {
            let runtime = Arc::new(Mutex::new(BackendRuntime::unavailable()));
            let restart = Arc::new(Mutex::new(()));
            let app_handle = app.handle().clone();
            app.manage(BackendState {
                runtime: Arc::clone(&runtime),
                restart: Arc::clone(&restart),
            });
            tauri::async_runtime::spawn(async move {
                let started_at = Instant::now();
                let startup = tauri::async_runtime::spawn_blocking(move || {
                    restart_backend_blocking(app_handle, runtime, restart)
                })
                .await;
                match startup {
                    Ok(Ok(_)) => append_desktop_log(&format!(
                        "Sidecar became ready after {:.2}s",
                        started_at.elapsed().as_secs_f64()
                    )),
                    Ok(Err(error)) => {
                        append_desktop_log(&format!("Background sidecar startup failed: {error}"))
                    }
                    Err(error) => append_desktop_log(&format!(
                        "Background sidecar startup worker failed: {error}"
                    )),
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                let backend = window.state::<BackendState>();
                let active = match backend.runtime.lock() {
                    Ok(mut runtime) => {
                        runtime.closing = true;
                        runtime.availability = BackendAvailability::Unavailable;
                        runtime.config = BackendConfig::unavailable();
                        runtime.active.take()
                    }
                    Err(_) => {
                        append_desktop_log(
                            "Backend state lock was poisoned while closing the application",
                        );
                        None
                    }
                };
                if let Some(active) = active {
                    if let Err(error) =
                        terminate_managed_backend(active, "Failed to stop sidecar during shutdown")
                    {
                        append_desktop_log(&error);
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![backend_config, restart_backend, app_updates::check_app_update, app_updates::download_app_update, app_updates::install_app_update])
        .run(tauri::generate_context!());

    if let Err(error) = result {
        let log_path = desktop_log_path();
        let message = format!(
            "The local inspection service could not be started. Close any other instance, check security software, and review the logs in {}.\n\nDetails: {error}",
            log_path
                .parent()
                .unwrap_or(log_path.as_path())
                .display()
        );
        append_desktop_log(&format!("Application startup failed: {error}"));
        show_fatal_error(&message);
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::{
        append_rotating_log, backup_log_path, is_authenticated_health_response, publishable_config,
        BackendAvailability, BackendConfig, LocalHttpResponse,
    };

    fn response(status: u16, body: &str) -> LocalHttpResponse {
        LocalHttpResponse {
            status,
            body: body.as_bytes().to_vec(),
        }
    }

    #[test]
    fn unavailable_backend_never_publishes_connection_secrets() {
        let config = BackendConfig {
            url: "http://127.0.0.1:12345".to_string(),
            runtime_token: "secret-token".to_string(),
        };

        assert_eq!(
            publishable_config(&config, BackendAvailability::Unavailable),
            BackendConfig::unavailable()
        );
    }

    #[test]
    fn health_check_requires_a_valid_nonce_hmac() {
        let nonce = b"The quick brown fox jumps over the lazy dog";
        let valid = response(
            200,
            r#"{"status":"ok","proof":"f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"}"#,
        );

        assert!(is_authenticated_health_response(&valid, "key", nonce));
        assert!(!is_authenticated_health_response(
            &response(200, r#"{"status":"ok","proof":"00"}"#),
            "key",
            nonce
        ));
        assert!(!is_authenticated_health_response(
            &response(
                503,
                r#"{"status":"ok","proof":"f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"}"#
            ),
            "key",
            nonce
        ));
    }

    #[test]
    fn desktop_log_rotates_to_one_backup_at_its_size_limit() {
        let directory = std::env::temp_dir().join(format!(
            "network-inspection-log-test-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        fs::create_dir_all(&directory).expect("temporary log directory");
        let path = directory.join("desktop-startup.log");
        fs::write(&path, b"1234").expect("seed log");

        append_rotating_log(&path, "next", 4).expect("append rotating log");

        assert_eq!(fs::read(backup_log_path(&path)).unwrap(), b"1234");
        assert!(fs::read_to_string(&path).unwrap().contains("next"));
        fs::remove_dir_all(directory).expect("remove temporary log directory");
    }
}
