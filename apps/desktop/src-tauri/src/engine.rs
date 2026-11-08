use std::io::{self, BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{RwLock, mpsc};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};
use thiserror::Error;

use crate::windows_job::WindowsJob;

const READY_LINE_LIMIT: usize = 1024;
const STARTUP_TIMEOUT: Duration = Duration::from_secs(10);
const REQUEST_TIMEOUT: Duration = Duration::from_millis(500);
const PROCESS_EXIT_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct EngineConnection {
    pub base_url: String,
    pub token: String,
}

#[derive(Default)]
pub struct EngineState {
    connection: RwLock<Option<EngineConnection>>,
}

impl EngineState {
    pub fn connection(&self) -> Option<EngineConnection> {
        self.connection.read().ok()?.clone()
    }

    pub fn set_connection(&self, connection: EngineConnection) -> Result<(), EngineError> {
        let mut stored = self
            .connection
            .write()
            .map_err(|_| EngineError::StateUnavailable)?;
        *stored = Some(connection);
        Ok(())
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReadyLine {
    event: String,
    pub(crate) port: u16,
}

#[derive(Debug, Deserialize)]
struct HealthResponse {
    status: String,
    mode: String,
}

#[derive(Debug, Error)]
pub enum EngineError {
    #[error("local_engine_randomness_unavailable")]
    RandomnessUnavailable,
    #[error("local_engine_resource_unavailable")]
    ResourceUnavailable,
    #[error("local_engine_process_start_failed")]
    ProcessStartFailed,
    #[error("local_engine_process_supervision_failed")]
    ProcessSupervisionFailed,
    #[error("local_engine_output_unavailable")]
    OutputUnavailable,
    #[error("local_engine_ready_line_invalid")]
    ReadyLineInvalid,
    #[error("local_engine_health_timeout")]
    HealthTimeout,
    #[error("local_engine_state_unavailable")]
    StateUnavailable,
}

pub struct EngineSupervisor {
    connection: EngineConnection,
    child: Option<Child>,
    job: Option<WindowsJob>,
    drain_threads: Vec<JoinHandle<()>>,
}

impl EngineSupervisor {
    pub fn start(app: &AppHandle) -> Result<Self, EngineError> {
        let startup_deadline = Instant::now() + STARTUP_TIMEOUT;
        let token = generate_launch_token()?;
        let executable = resolve_engine_executable(app)?;
        let job = WindowsJob::new().map_err(|_| EngineError::ProcessSupervisionFailed)?;
        let mut command = Command::new(executable);
        command
            .env("AI4MUSIC_LAUNCH_TOKEN", &token)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        configure_no_console(&mut command);

        let mut child = command
            .spawn()
            .map_err(|_| EngineError::ProcessStartFailed)?;
        if job.assign(&child).is_err() {
            let _ = child.kill();
            let _ = child.wait();
            return Err(EngineError::ProcessSupervisionFailed);
        }

        let mut supervisor = Self {
            connection: EngineConnection {
                base_url: String::new(),
                token,
            },
            child: Some(child),
            job: Some(job),
            drain_threads: Vec::new(),
        };

        let stdout = supervisor
            .child
            .as_mut()
            .and_then(|child| child.stdout.take())
            .ok_or(EngineError::OutputUnavailable)?;
        let stderr = supervisor
            .child
            .as_mut()
            .and_then(|child| child.stderr.take())
            .ok_or(EngineError::OutputUnavailable)?;

        supervisor.drain_threads.push(drain(stderr));
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
        let ready_thread = thread::spawn(move || {
            let mut stdout = BufReader::new(stdout);
            let result = read_bounded_line(&mut stdout);
            let _ = ready_sender.send((result, stdout));
        });
        let remaining = startup_deadline.saturating_duration_since(Instant::now());
        let (line, stdout) = match ready_receiver.recv_timeout(remaining) {
            Ok((line, stdout)) => {
                let _ = ready_thread.join();
                (line?, stdout)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                supervisor.drain_threads.push(ready_thread);
                return Err(EngineError::HealthTimeout);
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                let _ = ready_thread.join();
                return Err(EngineError::OutputUnavailable);
            }
        };
        let ready = parse_ready_line(&line)?;
        supervisor.connection.base_url = format!("http://127.0.0.1:{}", ready.port);
        supervisor.drain_threads.push(drain(stdout));

        wait_until_healthy(&supervisor.connection, startup_deadline)?;
        Ok(supervisor)
    }

    pub fn connection(&self) -> EngineConnection {
        self.connection.clone()
    }

    pub fn shutdown(&mut self) {
        self.job.take();

        if let Some(mut child) = self.child.take() {
            let deadline = Instant::now() + PROCESS_EXIT_TIMEOUT;
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) if Instant::now() < deadline => {
                        thread::sleep(Duration::from_millis(25));
                    }
                    _ => {
                        let _ = child.kill();
                        let _ = child.wait();
                        break;
                    }
                }
            }
        }

        for drain_thread in self.drain_threads.drain(..) {
            let _ = drain_thread.join();
        }
    }
}

impl Drop for EngineSupervisor {
    fn drop(&mut self) {
        self.shutdown();
    }
}

pub(crate) fn parse_ready_line(line: &str) -> Result<ReadyLine, EngineError> {
    let ready: ReadyLine = serde_json::from_str(line).map_err(|_| EngineError::ReadyLineInvalid)?;
    if ready.event != "engine_bound" || ready.port == 0 {
        return Err(EngineError::ReadyLineInvalid);
    }
    Ok(ready)
}

fn read_bounded_line<R: BufRead>(reader: &mut R) -> Result<String, EngineError> {
    let mut bytes = Vec::with_capacity(128);
    loop {
        let available = reader
            .fill_buf()
            .map_err(|_| EngineError::OutputUnavailable)?;
        if available.is_empty() {
            return Err(EngineError::ReadyLineInvalid);
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let consumed = newline.map_or(available.len(), |position| position + 1);
        if bytes.len() + consumed > READY_LINE_LIMIT {
            return Err(EngineError::ReadyLineInvalid);
        }
        bytes.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if newline.is_some() {
            break;
        }
    }

    if bytes.last() == Some(&b'\n') {
        bytes.pop();
    }
    if bytes.last() == Some(&b'\r') {
        bytes.pop();
    }
    String::from_utf8(bytes).map_err(|_| EngineError::ReadyLineInvalid)
}

fn generate_launch_token() -> Result<String, EngineError> {
    let mut random = [0_u8; 32];
    getrandom::fill(&mut random).map_err(|_| EngineError::RandomnessUnavailable)?;
    Ok(URL_SAFE_NO_PAD.encode(random))
}

fn resolve_engine_executable(app: &AppHandle) -> Result<PathBuf, EngineError> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|_| EngineError::ResourceUnavailable)?;
    engine_executable_candidates(&resource_dir)
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or(EngineError::ResourceUnavailable)
}

pub(crate) fn engine_executable_candidates(resource_dir: &Path) -> [PathBuf; 2] {
    [
        resource_dir.join("engine").join("ai4music-engine.exe"),
        resource_dir
            .join("resources")
            .join("engine")
            .join("ai4music-engine.exe"),
    ]
}

fn wait_until_healthy(
    connection: &EngineConnection,
    startup_deadline: Instant,
) -> Result<(), EngineError> {
    let client = Client::builder()
        .timeout(REQUEST_TIMEOUT)
        .build()
        .map_err(|_| EngineError::HealthTimeout)?;
    let endpoint = format!("{}/api/v1/health/live", connection.base_url);
    while Instant::now() < startup_deadline {
        let response = client
            .get(&endpoint)
            .header("X-AI4Music-Token", &connection.token)
            .send();
        if let Ok(response) = response
            && response.status().is_success()
            && let Ok(health) = response.json::<HealthResponse>()
            && health.status == "ok"
            && health.mode == "local"
        {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }

    Err(EngineError::HealthTimeout)
}

fn drain<R>(mut reader: R) -> JoinHandle<()>
where
    R: Read + Send + 'static,
{
    thread::spawn(move || {
        let _ = io::copy(&mut reader, &mut io::sink());
    })
}

#[cfg(windows)]
fn configure_no_console(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    use windows::Win32::System::Threading::CREATE_NO_WINDOW;

    command.creation_flags(CREATE_NO_WINDOW.0);
}

#[cfg(not(windows))]
fn configure_no_console(_command: &mut Command) {}
