mod engine_bridge;

use engine_bridge::EngineBridge;
use serde_json::{json, Value};
use std::{path::PathBuf, sync::{Arc, Mutex}};
use tauri::{Manager, State};
use tauri_plugin_opener::OpenerExt;

pub struct EngineState {
    engine: Mutex<Option<Arc<EngineBridge>>>,
    portable: bool,
}

/// Reuse a healthy bridge; replace one whose pipe, write, or timeout already failed.
/// A replacement engine marks an interrupted job on startup; the host never re-runs files.
fn bridge(app: &tauri::AppHandle, state: &EngineState) -> Result<Arc<EngineBridge>, String> {
    let mut slot = state.engine.lock().map_err(|_| "引擎状态锁异常")?;
    if let Some(engine) = slot.as_ref() {
        if engine.is_alive() {
            return Ok(engine.clone());
        }
        if let Some(dead) = slot.take() {
            dead.stop();
        }
    }
    let engine = Arc::new(EngineBridge::start(app, state.portable)?);
    *slot = Some(engine.clone());
    Ok(engine)
}

// Portable mode is an explicit launch flag; the default keeps Tauri's app data directory.
fn portable_requested() -> bool {
    std::env::args().skip(1).any(|arg| arg == "--portable")
}

#[tauri::command]
async fn engine_request(app: tauri::AppHandle, state: State<'_, EngineState>, method: String, params: Value) -> Result<Value, String> {
    let engine = bridge(&app, &state)?;
    tauri::async_runtime::spawn_blocking(move || engine.request(&method, params)).await.map_err(|_| "引擎线程异常")?
}

#[tauri::command]
async fn open_result(app: tauri::AppHandle, state: State<'_, EngineState>, job_id: String, path: Option<String>) -> Result<(), String> {
    let engine = bridge(&app, &state)?;
    let results = tauri::async_runtime::spawn_blocking(move || engine.request("results.get", json!({"job_id": job_id})))
        .await.map_err(|_| "结果查询失败")??;
    let root = results.get("output_root").and_then(Value::as_str).ok_or("结果目录未登记")?;
    let selected = path.as_deref().unwrap_or(root);
    let registered = selected == root || results.get("paths").and_then(Value::as_array)
        .is_some_and(|paths| paths.iter().any(|p| p.as_str() == Some(selected)));
    if !registered { return Err("结果路径未登记。".into()); }
    let target = PathBuf::from(selected);
    let directory = if target.is_dir() { target } else { target.parent().ok_or("结果路径无父目录")?.to_path_buf() };
    if !directory.is_dir() { return Err("结果目录不存在。".into()); }
    app.opener().open_path(directory.to_string_lossy(), None::<&str>).map_err(|_| "无法打开结果目录".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let portable = portable_requested();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize(); let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(EngineState { engine: Mutex::new(None), portable })
        .invoke_handler(tauri::generate_handler![engine_request, open_result])
        .build(tauri::generate_context!())
        .expect("failed to initialize Hoshiribbon desktop");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            let state = app.state::<EngineState>();
            if let Ok(mut slot) = state.engine.lock() {
                if let Some(engine) = slot.take() { engine.stop(); }
            };
        }
    });
}
