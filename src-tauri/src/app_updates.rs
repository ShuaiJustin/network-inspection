use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{ipc::Channel, AppHandle, Manager, State};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Default)]
pub struct PendingUpdate(pub tauri::async_runtime::Mutex<UpdateState>);

#[derive(Default)]
pub struct UpdateState {
    update: Option<Update>,
    verified_bytes: Option<Vec<u8>>,
}

fn preparation_is_current(
    current: &crate::BackendConfig,
    prepared: &crate::BackendConfig,
    age: Duration,
) -> bool {
    current == prepared && age < Duration::from_secs(30)
}

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data")]
pub enum DownloadEvent {
    #[serde(rename_all = "camelCase")]
    Started {
        content_length: Option<u64>,
    },
    #[serde(rename_all = "camelCase")]
    Progress {
        chunk_length: usize,
    },
    Finished,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateMetadata {
    version: String,
    current_version: String,
    body: Option<String>,
    downloaded: bool,
}

#[tauri::command]
pub async fn check_app_update(
    app: AppHandle,
    pending: State<'_, PendingUpdate>,
) -> Result<Option<UpdateMetadata>, String> {
    let mut state = pending
        .0
        .try_lock()
        .map_err(|_| "更新操作正在进行，请稍后重试")?;
    if state.verified_bytes.is_some() {
        return Ok(state.update.as_ref().map(|u| UpdateMetadata {
            version: u.version.clone(),
            current_version: u.current_version.clone(),
            body: u.body.clone(),
            downloaded: true,
        }));
    }
    // No generic updater capability is granted to the renderer: all installation
    // goes through our task-admission and sidecar-shutdown checks below.
    let mut update = app
        .updater_builder()
        .timeout(Duration::from_secs(20))
        .build()
        .map_err(|e| format!("无法初始化更新检查：{e}"))?
        .check()
        .await
        .map_err(|e| format!("检查更新失败，请检查网络或稍后重试：{e}"))?;
    if let Some(candidate) = update.as_mut() {
        if candidate.download_url.scheme() != "https" {
            return Err("更新安装包必须使用 HTTPS 下载地址".into());
        }
        // The small manifest check should finish quickly, but installers may
        // take several minutes on a slow connection.
        candidate.timeout = Some(Duration::from_secs(15 * 60));
    }
    let metadata = update.as_ref().map(|u| UpdateMetadata {
        version: u.version.clone(),
        current_version: u.current_version.clone(),
        body: u.body.clone(),
        downloaded: false,
    });
    state.update = update;
    Ok(metadata)
}

#[tauri::command]
pub async fn download_app_update(
    pending: State<'_, PendingUpdate>,
    on_event: Channel<DownloadEvent>,
) -> Result<(), String> {
    let mut state = pending
        .0
        .try_lock()
        .map_err(|_| "更新操作正在进行，请稍后重试")?;
    if state.verified_bytes.is_some() {
        return Ok(());
    }
    let update = state.update.as_ref().ok_or("请先检查更新")?;
    let mut started = false;
    let bytes = update
        .download(
            |chunk_length, content_length| {
                if !started {
                    let _ = on_event.send(DownloadEvent::Started { content_length });
                    started = true;
                }
                let _ = on_event.send(DownloadEvent::Progress { chunk_length });
            },
            || {},
        )
        .await
        .map_err(|e| format!("下载或签名验证失败，可重试：{e}"))?;
    // download() verifies the signature. Verify again before recording readiness
    // so the cached bytes can never bypass verification through a future refactor.
    update
        .verify(&bytes)
        .map_err(|e| format!("更新签名无效，已阻止安装：{e}"))?;
    state.verified_bytes = Some(bytes);
    let _ = on_event.send(DownloadEvent::Finished);
    Ok(())
}

async fn prepare_backend(
    app: &AppHandle,
    session_token: &str,
) -> Result<crate::BackendConfig, String> {
    if session_token.is_empty() {
        return Err("请先登录后再安装更新".into());
    }
    let config = {
        let state = app.state::<crate::BackendState>();
        let runtime = state.runtime.lock().map_err(|_| "无法读取本地服务状态")?;
        if runtime.closing || runtime.availability != crate::BackendAvailability::Ready {
            return Err("本地服务尚未就绪，请稍后重试".into());
        }
        runtime.config.clone()
    };
    // Configuration originates from our own child-process startup, never IPC input.
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|e| e.to_string())?;
    let response = client
        .post(format!("{}/system/update/prepare", config.url))
        .bearer_auth(session_token)
        .header("X-Inspection-Run-Token", &config.runtime_token)
        .send()
        .await
        .map_err(|_| "无法确认后台任务状态，已暂缓安装")?;
    match response.status().as_u16() {
        200 => {
            let body: serde_json::Value = response.json().await.map_err(|_| "本地服务响应无效")?;
            if body.get("ready").and_then(|v| v.as_bool()) != Some(true) {
                return Err("本地服务尚未允许安装".into());
            }
            Ok(config)
        }
        401 => Err("登录已过期，请重新登录后安装更新".into()),
        409 | 503 => Err("存在执行中的巡检、备份或维护操作，请结束后再安装".into()),
        _ => Err("无法确认后台任务状态，已暂缓安装".into()),
    }
}

#[tauri::command]
pub async fn install_app_update(
    app: AppHandle,
    pending: State<'_, PendingUpdate>,
    session_token: String,
) -> Result<(), String> {
    let state = pending
        .0
        .try_lock()
        .map_err(|_| "更新操作正在进行，请稍后重试")?;
    let update = state.update.as_ref().ok_or("请先检查更新")?;
    let bytes = state.verified_bytes.as_ref().ok_or("请先下载并验证更新")?;
    update
        .verify(bytes)
        .map_err(|e| format!("更新签名无效：{e}"))?;
    let prepared_at = Instant::now();
    let prepared_config = prepare_backend(&app, &session_token).await?;
    let backend_state = app.state::<crate::BackendState>();
    {
        // Hold the restart transaction until the old sidecar is gone.
        let _restart = backend_state
            .restart
            .lock()
            .map_err(|_| "无法锁定本地服务")?;
        let active = {
            let mut runtime = backend_state
                .runtime
                .lock()
                .map_err(|_| "无法关闭本地服务")?;
            if runtime.closing
                || !preparation_is_current(&runtime.config, &prepared_config, prepared_at.elapsed())
            {
                return Err(
                    "本地服务已变化或安装准备已超时，请稍后重新尝试，后台任务未被中断".into(),
                );
            }
            runtime.closing = true;
            runtime.availability = crate::BackendAvailability::Unavailable;
            runtime.config = crate::BackendConfig::unavailable();
            runtime.active.take()
        };
        if let Some(active) = active {
            if let Err(error) =
                crate::terminate_managed_backend(active, "Failed to stop sidecar for update")
            {
                crate::append_desktop_log(&error);
                return Err("无法停止本地服务，已暂缓安装；请退出并重启软件".into());
            }
        }
    }
    // On Windows, the official plugin launches the installer and exits on success.
    // An installer-launch failure must restore a usable local backend.
    if let Err(error) = update.install(bytes) {
        if let Ok(mut runtime) = backend_state.runtime.lock() {
            runtime.closing = false;
        }
        let runtime = std::sync::Arc::clone(&backend_state.runtime);
        let restart = std::sync::Arc::clone(&backend_state.restart);
        let handle = app.clone();
        let recovered = tauri::async_runtime::spawn_blocking(move || {
            crate::restart_backend_blocking(handle, runtime, restart)
        })
        .await;
        if !matches!(recovered, Ok(Ok(_))) {
            return Err(format!(
                "安装启动失败：{error}；本地服务恢复失败，请退出后重启软件"
            ));
        }
        return Err(format!(
            "安装启动失败：{error}；本地服务已重启，请重新登录后重试"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn no_update_starts_without_verified_data() {
        let state = UpdateState::default();
        assert!(state.update.is_none());
        assert!(state.verified_bytes.is_none());
    }
    #[test]
    fn progress_matches_renderer_contract() {
        let progress = serde_json::to_value(DownloadEvent::Progress { chunk_length: 42 }).unwrap();
        assert_eq!(progress["event"], "Progress");
        assert_eq!(progress["data"]["chunkLength"], 42);
    }
    #[test]
    fn replaced_sidecar_or_expired_preparation_never_authorizes_shutdown() {
        let prepared = crate::BackendConfig {
            url: "http://127.0.0.1:12345".into(),
            runtime_token: "old-child".into(),
        };
        let replacement = crate::BackendConfig {
            url: prepared.url.clone(),
            runtime_token: "new-child".into(),
        };
        assert!(preparation_is_current(
            &prepared,
            &prepared,
            Duration::from_secs(1)
        ));
        assert!(!preparation_is_current(
            &replacement,
            &prepared,
            Duration::from_secs(1)
        ));
        assert!(!preparation_is_current(
            &prepared,
            &prepared,
            Duration::from_secs(60)
        ));
    }
}
