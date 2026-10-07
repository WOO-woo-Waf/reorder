use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc, Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{AppHandle, Manager};

/// Python rejects any stdin frame longer than this, the trailing newline included.
const MAX_FRAME: usize = 1024 * 1024;
/// Python may answer with a payload of MAX_FRAME bytes plus one trailing newline.
const MAX_RESPONSE: usize = MAX_FRAME + 1;
/// Outgoing frames wait in at most this many slots; a full queue counts as a slow engine.
const WRITE_QUEUE: usize = 32;
const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(20);

type Reply = Result<Value, String>;
type Pending = Arc<Mutex<HashMap<u64, mpsc::Sender<Reply>>>>;

pub fn allowed_method(method: &str) -> bool {
    matches!(method, "system.info" | "plans.create" | "jobs.start" | "jobs.get" |
        "jobs.list" | "jobs.cancel" | "jobs.retry" | "jobs.events" | "jobs.logs" |
        "settings.get" | "settings.update" | "passwords.replace" | "passwords.import" | "results.get")
}

/// Budget left before the request deadline; never negative.
fn remaining(deadline: Instant) -> Duration {
    deadline.saturating_duration_since(Instant::now())
}

/// Encode one request frame, enforcing Python's inclusive input limit.
fn encode_request_frame(id: u64, method: &str, params: &Value) -> Result<Vec<u8>, String> {
    let mut bytes = serde_json::to_vec(&json!({
        "jsonrpc": "2.0", "id": id, "method": method, "params": params
    }))
    .map_err(|_| "参数无法编码".to_string())?;
    if bytes.len() + 1 > MAX_FRAME {
        return Err("请求超过 1 MiB。".into());
    }
    bytes.push(b'\n');
    Ok(bytes)
}

/// Validate one response frame. A payload of MAX_FRAME bytes plus its newline is valid.
fn parse_response_frame(bytes: &[u8]) -> Result<Value, String> {
    if bytes.len() > MAX_RESPONSE {
        return Err("引擎响应超过 1 MiB。".into());
    }
    let body = bytes.strip_suffix(b"\n").ok_or_else(|| "引擎响应缺少换行。".to_string())?;
    let frame = serde_json::from_slice::<Value>(body).map_err(|_| "引擎响应不是合法 JSON。".to_string())?;
    if frame.get("jsonrpc") != Some(&json!("2.0")) {
        return Err("引擎响应协议版本错误。".into());
    }
    Ok(frame)
}

/// Turn a protocol frame into the caller-facing reply.
fn decode_reply(frame: &Value) -> Reply {
    if let Some(error) = frame.get("error") {
        let code = error.pointer("/data/code").and_then(Value::as_str).unwrap_or("ENGINE_ERROR");
        let message = error.get("message").and_then(Value::as_str).unwrap_or("引擎操作失败");
        Err(format!("{code}: {message}"))
    } else if let Some(result) = frame.get("result") {
        Ok(result.clone())
    } else {
        Err("引擎响应格式错误".into())
    }
}

/// Outcome of queuing one frame for the writer thread.
#[derive(Debug, PartialEq, Eq)]
enum Enqueue {
    Sent,
    /// The queue stayed full for the whole budget: the engine is not draining stdin.
    Busy,
    /// The queue is closed because the engine or the bridge already stopped.
    Closed,
}

/// Clone the writer queue out and release its lock before any blocking wait, so a full
/// queue can never pin the lock that `stop` needs in order to end the engine.
fn take_queue(
    sender: &Mutex<Option<mpsc::SyncSender<Vec<u8>>>>,
) -> Result<Option<mpsc::SyncSender<Vec<u8>>>, String> {
    match sender.lock() {
        Ok(guard) => Ok(guard.clone()),
        Err(_) => Err("引擎输入队列异常，将在下次请求时重新启动。".into()),
    }
}

/// Queue one frame for the writer thread, spending at most `budget`.
/// `SyncSender::send_timeout` is not on the stable surface this host builds with,
/// so a full queue is polled with short waits that are still bounded by the budget.
fn enqueue_frame(queue: &mpsc::SyncSender<Vec<u8>>, bytes: Vec<u8>, budget: Duration) -> Enqueue {
    let deadline = Instant::now() + budget;
    let mut frame = bytes;
    loop {
        match queue.try_send(frame) {
            Ok(()) => return Enqueue::Sent,
            Err(mpsc::TrySendError::Disconnected(_)) => return Enqueue::Closed,
            Err(mpsc::TrySendError::Full(returned)) => {
                if Instant::now() >= deadline {
                    return Enqueue::Busy;
                }
                frame = returned;
                thread::sleep(Duration::from_millis(2));
            }
        }
    }
}

/// Explicit liveness of one bridge instance; the host rebuilds a dead one.
struct Health {
    alive: AtomicBool,
    reason: Mutex<Option<String>>,
}

impl Health {
    fn new() -> Self {
        Self { alive: AtomicBool::new(true), reason: Mutex::new(None) }
    }

    fn is_alive(&self) -> bool {
        self.alive.load(Ordering::Acquire)
    }

    fn reason(&self) -> Option<String> {
        match self.reason.lock() {
            Ok(guard) => guard.clone(),
            Err(poisoned) => poisoned.into_inner().clone(),
        }
    }

    fn mark_down(&self, reason: &str) {
        let mut guard = match self.reason.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        if guard.is_none() {
            *guard = Some(reason.to_string());
        }
        self.alive.store(false, Ordering::Release);
    }
}

fn fail_pending(pending: &Pending, message: &str) {
    if let Ok(mut requests) = pending.lock() {
        for (_, sender) in requests.drain() {
            let _ = sender.send(Err(message.to_string()));
        }
    }
}

/// Mark the connection dead, release every waiter, and end a still-running engine.
fn shutdown(health: &Health, pending: &Pending, child: &Arc<Mutex<Option<Child>>>, reason: &str) {
    health.mark_down(reason);
    fail_pending(pending, &format!("引擎连接中断：{reason} 重新打开不会自动重做文件。"));
    terminate(child);
}

fn terminate(child_slot: &Arc<Mutex<Option<Child>>>) {
    let taken = match child_slot.lock() {
        Ok(mut slot) => slot.take(),
        Err(poisoned) => poisoned.into_inner().take(),
    };
    if let Some(mut child) = taken {
        if matches!(child.try_wait(), Ok(Some(_))) {
            return;
        }
        hard_kill(&mut child);
    }
}

fn hard_kill(child: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill.exe")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .creation_flags(0x08000000)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// Portable mode: state lives in `data/` next to the executable and must be writable.
fn portable_data_root() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|_| "无法定位程序所在目录，便携模式数据目录不可用。")?;
    let base = exe.parent().ok_or("程序路径没有上级目录，便携模式数据目录不可用。")?;
    let data_root = base.join("data");
    ensure_writable(&data_root)?;
    Ok(data_root)
}

fn ensure_writable(directory: &Path) -> Result<(), String> {
    let message = format!("便携模式数据目录不可写：{}", directory.display());
    std::fs::create_dir_all(directory).map_err(|_| message.clone())?;
    let probe = directory.join(format!(".reorder-write-probe-{}", std::process::id()));
    std::fs::write(&probe, b"reorder").map_err(|_| message.clone())?;
    let _ = std::fs::remove_file(&probe);
    Ok(())
}

pub struct EngineBridge {
    sender: Mutex<Option<mpsc::SyncSender<Vec<u8>>>>,
    child: Arc<Mutex<Option<Child>>>,
    pending: Pending,
    sequence: AtomicU64,
    health: Arc<Health>,
    timeout: Duration,
}

impl EngineBridge {
    pub fn start(app: &AppHandle, portable: bool) -> Result<Self, String> {
        let resource_dir = app.path().resource_dir().map_err(|_| "无法定位软件资源目录")?;
        let bundled = resource_dir.join("engine");
        let name = if cfg!(windows) { "reorder-engine.exe" } else { "reorder-engine" };
        let binary = bundled.join(name);
        let data_root = if portable {
            portable_data_root()?
        } else {
            let root = app.path().app_local_data_dir().map_err(|_| "无法定位用户数据目录")?;
            std::fs::create_dir_all(&root).map_err(|_| "无法创建用户数据目录")?;
            root
        };
        let mut command;
        if binary.is_file() {
            command = Command::new(&binary);
            command.arg("--app-root").arg(&bundled);
        } else if cfg!(debug_assertions) {
            let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize()
                .map_err(|_| "无法定位开发仓库")?;
            let python = std::env::var_os("REORDER_PYTHON").unwrap_or_else(|| "python".into());
            command = Command::new(python);
            command.args(["-m", "reorder_engine.desktop_engine"])
                .arg("--app-root").arg(&repo).env("PYTHONPATH", repo.join("src"));
        } else {
            return Err("Python 引擎资源缺失，请保留便携目录完整或重新安装。".into());
        }
        command.arg("--data-root").arg(&data_root);
        if portable {
            command.arg("--session-secrets");
        }
        command.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW
        }
        let mut child = command.spawn().map_err(|_| "无法启动 Python 引擎，请检查安装或开发环境。")?;
        let stdin = child.stdin.take().ok_or("引擎输入不可用")?;
        let output = child.stdout.take().ok_or("引擎输出不可用")?;
        let diagnostics = child.stderr.take().ok_or("引擎诊断不可用")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let child = Arc::new(Mutex::new(Some(child)));
        let health = Arc::new(Health::new());
        let (writer, queue) = mpsc::sync_channel::<Vec<u8>>(WRITE_QUEUE);

        // Dedicated writer owns stdin, so a slow pipe never pins the request or stop path.
        {
            let health = health.clone();
            let pending = pending.clone();
            let child = child.clone();
            thread::spawn(move || {
                let mut stdin: ChildStdin = stdin;
                for bytes in queue {
                    if stdin.write_all(&bytes).and_then(|_| stdin.flush()).is_err() {
                        shutdown(&health, &pending, &child, "引擎写入失败。");
                        return;
                    }
                }
                // Queue closed: dropping stdin signals EOF, the engine cancels a running
                // job only at a safe boundary and commits the current file first.
                drop(stdin);
            });
        }
        {
            let health = health.clone();
            let pending = pending.clone();
            let child = child.clone();
            thread::spawn(move || {
                let mut reader = BufReader::new(output);
                loop {
                    let mut bytes = Vec::new();
                    match reader.by_ref().take((MAX_RESPONSE + 1) as u64).read_until(b'\n', &mut bytes) {
                        Ok(0) => break,
                        Ok(_) => {}
                        Err(_) => {
                            shutdown(&health, &pending, &child, "引擎读取失败。");
                            return;
                        }
                    }
                    let frame = match parse_response_frame(&bytes) {
                        Ok(frame) => frame,
                        Err(reason) => {
                            shutdown(&health, &pending, &child, &reason);
                            return;
                        }
                    };
                    let Some(id) = frame.get("id").and_then(Value::as_u64) else { continue };
                    let sender = match pending.lock() {
                        Ok(mut requests) => requests.remove(&id),
                        Err(poisoned) => poisoned.into_inner().remove(&id),
                    };
                    if let Some(sender) = sender {
                        let _ = sender.send(decode_reply(&frame));
                    }
                }
                // Engine left on its own: fail fast and let the host start a fresh one.
                shutdown(&health, &pending, &child, "引擎已退出。");
            });
        }
        thread::spawn(move || {
            let path = data_root.join("engine-diagnostics.log");
            let mut reader = diagnostics;
            let mut bytes = [0u8; 8192];
            while let Ok(count) = reader.read(&mut bytes) {
                if count == 0 { break; }
                let too_large = std::fs::metadata(&path).map(|m| m.len() > 2 * 1024 * 1024).unwrap_or(false);
                if let Ok(mut file) = std::fs::OpenOptions::new().create(true).write(true)
                    .append(!too_large).truncate(too_large).open(&path) {
                    let _ = file.write_all(&bytes[..count]);
                }
            }
        });
        Ok(Self {
            sender: Mutex::new(Some(writer)),
            child,
            pending,
            sequence: AtomicU64::new(1),
            health,
            timeout: REQUEST_TIMEOUT,
        })
    }

    pub fn is_alive(&self) -> bool {
        self.health.is_alive()
    }

    pub fn health_reason(&self) -> Option<String> {
        self.health.reason()
    }

    pub fn request(&self, method: &str, params: Value) -> Reply {
        if !allowed_method(method) || !params.is_object() {
            return Err("不允许的引擎操作或参数格式。".into());
        }
        if !self.health.is_alive() {
            return Err(self.unavailable_message());
        }
        let id = self.sequence.fetch_add(1, Ordering::Relaxed);
        let bytes = encode_request_frame(id, method, &params)?;
        let deadline = Instant::now() + self.timeout;
        let (sender, receiver) = mpsc::channel();
        self.pending.lock().map_err(|_| "引擎请求锁异常")?.insert(id, sender);
        // Clone the queue and release the lock first: waiting for a free slot must never
        // hold the lock that stop() needs, and the write shares the request deadline.
        let queue = match take_queue(&self.sender) {
            Ok(queue) => queue,
            Err(error) => {
                if let Ok(mut pending) = self.pending.lock() { pending.remove(&id); }
                return Err(error);
            }
        };
        let Some(queue) = queue else {
            if let Ok(mut pending) = self.pending.lock() { pending.remove(&id); }
            return Err("引擎已停止，将在下次请求时重新启动。".into());
        };
        match enqueue_frame(&queue, bytes, remaining(deadline)) {
            Enqueue::Sent => {}
            Enqueue::Closed => {
                if let Ok(mut pending) = self.pending.lock() { pending.remove(&id); }
                return Err("引擎输入已关闭，将在下次请求时重新启动。".into());
            }
            Enqueue::Busy => {
                // A queue that stayed full for the whole budget means a stuck engine, so
                // fail fast, mark it dead, and end the stale child. Files are never redone.
                if let Ok(mut pending) = self.pending.lock() { pending.remove(&id); }
                self.abort("引擎写入队列持续繁忙，连接已失效。");
                return Err("引擎写入队列繁忙，连接已失效；请刷新任务列表确认中断记录，不会自动重做文件。".into());
            }
        }
        let result = receiver.recv_timeout(remaining(deadline));
        if let Ok(mut pending) = self.pending.lock() { pending.remove(&id); }
        match result {
            Ok(reply) => reply,
            Err(mpsc::RecvTimeoutError::Timeout) => {
                self.abort("引擎响应超时。");
                Err("引擎响应超时，连接已失效；请在重新打开后刷新任务列表确认中断记录，不会自动重做文件。".into())
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                Err("引擎连接已结束，请在重新打开后刷新任务列表确认中断记录，不会自动重做文件。".into())
            }
        }
    }

    fn unavailable_message(&self) -> String {
        match self.health_reason() {
            Some(reason) => format!("引擎连接已失效（{reason}）已记录为中断，请刷新任务列表确认，不会自动重做文件。"),
            None => "引擎连接已失效，已记录为中断，请刷新任务列表确认，不会自动重做文件。".into(),
        }
    }

    fn abort(&self, reason: &str) {
        shutdown(&self.health, &self.pending, &self.child, reason);
    }

    pub fn stop(&self) {
        // Closing the queue drops stdin in the writer thread: EOF asks Python to cancel
        // and finish the current file commit first, without waiting on a write lock.
        if let Ok(mut guard) = self.sender.lock() { guard.take(); }
        let child = match self.child.lock() {
            Ok(mut slot) => slot.take(),
            Err(poisoned) => poisoned.into_inner().take(),
        };
        let Some(mut child) = child else { return; };
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            if matches!(child.try_wait(), Ok(Some(_))) { return; }
            thread::sleep(Duration::from_millis(100));
        }
        hard_kill(&mut child);
    }
}

impl Drop for EngineBridge { fn drop(&mut self) { self.stop(); } }

#[cfg(test)]
mod tests {
    use super::*;

    fn padded_response_body(target: usize) -> Vec<u8> {
        let base = serde_json::to_vec(&json!({"jsonrpc": "2.0", "id": 1, "result": ""})).unwrap();
        assert!(target > base.len(), "target must leave room for filler");
        let filler = "x".repeat(target - base.len());
        let body = serde_json::to_vec(&json!({"jsonrpc": "2.0", "id": 1, "result": filler})).unwrap();
        assert_eq!(body.len(), target);
        body
    }

    #[test]
    fn method_boundary() {
        assert!(allowed_method("jobs.cancel"));
        assert!(allowed_method("passwords.import"));
        assert!(allowed_method("results.get"));
        assert!(!allowed_method("shell.exec"));
        assert!(!allowed_method("fs.remove"));
        assert!(!allowed_method("jobs.start "));
    }

    #[test]
    fn request_frame_stays_within_input_limit() {
        let frame = encode_request_frame(1, "jobs.get", &json!({"job_id": "x"})).unwrap();
        assert!(frame.ends_with(b"\n"));
        assert!(frame.len() <= MAX_FRAME);

        let huge = json!({"blob": "y".repeat(MAX_FRAME)});
        assert!(encode_request_frame(2, "plans.create", &huge).is_err());
    }

    #[test]
    fn response_frame_accepts_max_payload_and_rejects_bad_frames() {
        // Payload exactly MAX_FRAME plus the newline is what Python can legally emit.
        let mut full = padded_response_body(MAX_FRAME);
        full.push(b'\n');
        assert_eq!(full.len(), MAX_RESPONSE);
        assert!(parse_response_frame(&full).unwrap().get("result").is_some());

        let mut oversized = padded_response_body(MAX_FRAME + 1);
        oversized.push(b'\n');
        assert!(parse_response_frame(&oversized).is_err());

        assert!(parse_response_frame(b"{\"jsonrpc\":\"2.0\",\"id\":1}").is_err()); // no newline
        assert!(parse_response_frame(b"not json\n").is_err());
        assert!(parse_response_frame(b"{\"jsonrpc\":\"1.0\",\"id\":1,\"result\":1}\n").is_err());
    }

    #[test]
    fn failure_marks_health_and_releases_pending() {
        let health = Health::new();
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let (sender, receiver) = mpsc::channel();
        pending.lock().unwrap().insert(7u64, sender);
        let child: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));

        shutdown(&health, &pending, &child, "引擎响应超时。");

        assert!(!health.is_alive());
        assert_eq!(health.reason().as_deref(), Some("引擎响应超时。"));
        assert!(receiver.recv().unwrap().is_err());
        assert!(pending.lock().unwrap().is_empty());
    }

    fn test_bridge(queue: mpsc::SyncSender<Vec<u8>>, timeout: Duration) -> EngineBridge {
        EngineBridge {
            sender: Mutex::new(Some(queue)),
            child: Arc::new(Mutex::new(None)),
            pending: Arc::new(Mutex::new(HashMap::new())),
            sequence: AtomicU64::new(1),
            health: Arc::new(Health::new()),
            timeout,
        }
    }

    #[test]
    fn bounded_write_queue_reports_busy_then_closed() {
        let (queue, receiver) = mpsc::sync_channel::<Vec<u8>>(1);
        assert_eq!(enqueue_frame(&queue, vec![b'a'], Duration::from_secs(1)), Enqueue::Sent);
        assert_eq!(enqueue_frame(&queue, vec![b'b'], Duration::from_millis(30)), Enqueue::Busy);
        drop(receiver);
        assert_eq!(enqueue_frame(&queue, vec![b'c'], Duration::from_secs(1)), Enqueue::Closed);
    }

    #[test]
    fn stop_can_take_the_queue_lock_while_a_write_waits() {
        let (tx, rx) = mpsc::sync_channel::<Vec<u8>>(1);
        tx.try_send(vec![b'x']).unwrap(); // full: the writer has no free slot
        let sender = Mutex::new(Some(tx));
        let waiting = {
            let queue = take_queue(&sender).unwrap().unwrap();
            thread::spawn(move || enqueue_frame(&queue, vec![b'y'], Duration::from_secs(2)))
        };
        let started = Instant::now();
        let taken = take_queue(&sender).unwrap();
        assert!(taken.is_some(), "a waiting request must not swallow the queue slot");
        assert!(started.elapsed() < Duration::from_millis(500), "the queue lock was held by a waiting write");
        assert!(rx.recv().is_ok()); // free one slot so the waiting write completes
        assert_eq!(waiting.join().unwrap(), Enqueue::Sent);
    }

    #[test]
    fn busy_queue_deadline_marks_dead_and_releases_pending() {
        let (tx, _rx) = mpsc::sync_channel::<Vec<u8>>(1);
        tx.try_send(vec![b'x']).unwrap(); // receiver stays alive, so the queue stays full
        let bridge = test_bridge(tx, Duration::from_millis(60));
        let error = bridge.request("jobs.get", json!({"job_id": "job-1"})).unwrap_err();
        assert!(error.contains("连接已失效"), "unexpected message: {error}");
        assert!(!bridge.is_alive());
        assert!(bridge.health_reason().is_some());
        assert!(bridge.pending.lock().unwrap().is_empty());
    }

    #[test]
    fn expired_deadline_leaves_no_budget() {
        assert!(remaining(Instant::now() - Duration::from_secs(1)) <= Duration::from_millis(1));
    }
}
