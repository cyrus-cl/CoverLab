mod engine;
#[cfg(test)]
mod engine_test;
mod window_style;
mod windows_job;

use std::sync::{Arc, Mutex};

use engine::{EngineConnection, EngineState, EngineSupervisor};
use tauri::{Manager, State};

#[tauri::command]
fn engine_connection(state: State<'_, EngineState>) -> Result<EngineConnection, String> {
    state
        .connection()
        .ok_or_else(|| "local_engine_not_ready".to_string())
}

pub fn run() {
    let supervisor = Arc::new(Mutex::new(None::<EngineSupervisor>));
    let setup_supervisor = Arc::clone(&supervisor);
    let app = tauri::Builder::default()
        .manage(EngineState::default())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![engine_connection])
        .setup(move |app| {
            if let Some(window) = app.get_webview_window("main") {
                window_style::apply(&window);
            }
            let running = EngineSupervisor::start(app.handle())?;
            app.state::<EngineState>()
                .set_connection(running.connection())?;
            *setup_supervisor
                .lock()
                .map_err(|_| "local_engine_supervisor_unavailable")? = Some(running);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build AI4Music desktop application");

    app.run(move |_app, event| {
        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) && let Ok(mut stored) = supervisor.lock()
            && let Some(mut running) = stored.take()
        {
            running.shutdown();
        }
    });
}
