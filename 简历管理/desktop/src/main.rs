#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};

#[cfg(any(windows, target_os = "macos"))]
mod desktop_window;

const INDEX: &str = include_str!("../web/index.html");
const STYLE: &str = include_str!("../web/style.css");
const SCRIPT: &str = include_str!("../web/app.js");
const COMMANDS: &[&str] = &[
    "init",
    "snapshot",
    "doctor",
    "jd-put",
    "candidate-add",
    "candidate-update",
    "application-add",
    "application-update",
    "review-queue",
    "ranking",
    "interview-add",
    "ledger-export",
    "ledger-import",
    "sync",
    "sync-status",
];
#[derive(Clone, Debug, Serialize, Deserialize)]
struct Config {
    python: String,
    script: String,
    root: String,
    actor: String,
    #[serde(default)]
    remote_root: String,
    port: u16,
    config_path: PathBuf,
}
fn h(n: &str, v: &str) -> Header {
    Header::from_bytes(n.as_bytes(), v.as_bytes()).unwrap()
}
fn resp(v: Value, s: u16) -> Response<std::io::Cursor<Vec<u8>>> {
    Response::from_data(serde_json::to_vec(&v).unwrap())
        .with_status_code(StatusCode(s))
        .with_header(h("Content-Type", "application/json; charset=utf-8"))
        .with_header(h("X-Content-Type-Options", "nosniff"))
        .with_header(h("Cache-Control", "no-store"))
}
fn err(s: u16, e: impl Into<String>) -> Response<std::io::Cursor<Vec<u8>>> {
    resp(json!({"ok":false,"error":e.into()}), s)
}
fn package_root(exe: &Path) -> PathBuf {
    for ancestor in exe.ancestors() {
        if ancestor
            .join("skills/manage-hr-resumes/scripts/hr.py")
            .exists()
        {
            return ancestor.to_path_buf();
        }
    }
    exe.parent().unwrap_or(exe).to_path_buf()
}
fn defaults(exe: &Path) -> Config {
    let root = package_root(exe);
    let script = root.join("skills/manage-hr-resumes/scripts/hr.py");
    #[cfg(target_os = "macos")]
    let config_path = std::env::var_os("HOME")
        .map(PathBuf::from)
        .map(|home| home.join("Library/Application Support/HRResume/client-config.json"))
        .unwrap_or_else(|| root.join(".runtime/client-config.json"));
    #[cfg(windows)]
    let config_path = {
        let local = root.join(".runtime/client-config.json");
        if local.is_file() {
            local
        } else {
            std::env::var_os("LOCALAPPDATA")
                .map(PathBuf::from)
                .map(|base| base.join("HRResume/client-config.json"))
                .unwrap_or(local)
        }
    };
    #[cfg(not(any(windows, target_os = "macos")))]
    let config_path = root.join(".runtime/client-config.json");
    Config {
        python: if cfg!(target_os = "macos") {
            "python3".into()
        } else {
            "python".into()
        },
        script: script.to_string_lossy().into(),
        root: String::new(),
        actor: String::new(),
        remote_root: String::new(),
        port: 0,
        config_path,
    }
}
fn load(mut c: Config) -> Config {
    if let Ok(t) = fs::read_to_string(&c.config_path) {
        if let Ok(x) = serde_json::from_str::<Config>(&t) {
            c.python = x.python;
            c.script = x.script;
            c.root = x.root;
            c.actor = x.actor;
            c.remote_root = x.remote_root;
        }
    }
    c
}
fn save(c: &Config) -> Result<(), String> {
    if let Some(p) = c.config_path.parent() {
        fs::create_dir_all(p).map_err(|e| e.to_string())?
    }
    let mut disk = c.clone();
    disk.port = 0;
    fs::write(
        &c.config_path,
        serde_json::to_vec_pretty(&disk).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())
}
fn instance_path(c: &Config) -> PathBuf {
    c.config_path
        .parent()
        .unwrap_or(Path::new("."))
        .join("client-instance.json")
}
#[cfg(windows)]
struct InstanceMutex(*mut std::ffi::c_void);
#[cfg(windows)]
impl Drop for InstanceMutex {
    fn drop(&mut self) {
        unsafe {
            #[link(name = "kernel32")]
            extern "system" {
                fn CloseHandle(h: *mut std::ffi::c_void) -> i32;
            }
            let _ = CloseHandle(self.0);
        }
    }
}
#[cfg(windows)]
fn acquire_mutex(c: &Config) -> Option<InstanceMutex> {
    use std::{
        collections::hash_map::DefaultHasher,
        hash::{Hash, Hasher},
        os::windows::ffi::OsStrExt,
    };
    let mut h = DefaultHasher::new();
    c.config_path.to_string_lossy().hash(&mut h);
    let name: Vec<u16> = std::ffi::OsStr::new(&format!("Global\\HRResume_{:x}", h.finish()))
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        #[link(name = "kernel32")]
        extern "system" {
            fn CreateMutexW(
                a: *mut std::ffi::c_void,
                b: i32,
                n: *const u16,
            ) -> *mut std::ffi::c_void;
            fn GetLastError() -> u32;
            fn CloseHandle(h: *mut std::ffi::c_void) -> i32;
        }
        let handle = CreateMutexW(std::ptr::null_mut(), 0, name.as_ptr());
        if handle.is_null() {
            None
        } else if GetLastError() == 183 {
            let _ = CloseHandle(handle);
            None
        } else {
            Some(InstanceMutex(handle))
        }
    }
}
#[cfg(target_os = "macos")]
struct InstanceMutex(std::fs::File);
#[cfg(target_os = "macos")]
impl Drop for InstanceMutex {
    fn drop(&mut self) {
        use std::os::fd::AsRawFd;
        unsafe { flock(self.0.as_raw_fd(), 8) };
    }
}
#[cfg(target_os = "macos")]
fn acquire_mutex(c: &Config) -> Option<InstanceMutex> {
    use std::{fs::OpenOptions, os::fd::AsRawFd};
    let path = c.config_path.with_extension("instance.lock");
    let file = OpenOptions::new()
        .create(true)
        .read(true)
        .write(true)
        .open(path)
        .ok()?;
    // LOCK_EX | LOCK_NB; keep the descriptor alive for the GUI process.
    if unsafe { flock(file.as_raw_fd(), 2 | 4) } == 0 {
        Some(InstanceMutex(file))
    } else {
        None
    }
}
#[cfg(target_os = "macos")]
extern "C" {
    fn flock(fd: i32, operation: i32) -> i32;
}
#[cfg(not(any(windows, target_os = "macos")))]
fn acquire_mutex(_: &Config) -> Option<()> {
    Some(())
}
fn existing_instance(c: &Config) -> Option<(String, u32)> {
    let text = fs::read_to_string(instance_path(c)).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let url = v.get("url")?.as_str()?.to_string();
    let rest = url.strip_prefix("http://127.0.0.1:")?;
    let port = rest.strip_suffix('/')?.parse::<u16>().ok()?;
    let addr = format!("127.0.0.1:{port}").parse().ok()?;
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_millis(300)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    let _ = write!(
        stream,
        "GET /api/config HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n\r\n"
    );
    let mut b = Vec::new();
    let _ = stream.read_to_end(&mut b);
    let body = String::from_utf8_lossy(&b)
        .split("\r\n\r\n")
        .nth(1)?
        .to_string();
    let v: Value = serde_json::from_str(&body).ok()?;
    if v["data"]["instance_key"].as_str() == Some(c.config_path.to_string_lossy().as_ref()) {
        let pid = v["data"]["pid"].as_u64()?.try_into().ok()?;
        Some((url, pid))
    } else {
        None
    }
}
fn write_instance(c: &Config, url: &str) {
    if let Some(p) = instance_path(c).parent() {
        let _ = fs::create_dir_all(p);
    }
    let _ = fs::write(
        instance_path(c),
        serde_json::to_vec(&json!({"url":url,"pid":std::process::id()})).unwrap(),
    );
}
fn args(c: &Config, n: &str, p: Option<&str>) -> Vec<String> {
    let mut a = vec![
        c.script.clone(),
        "--root".into(),
        c.root.clone(),
        "--actor".into(),
        c.actor.clone(),
        n.into(),
    ];
    if let Some(x) = p {
        a.extend(["--payload".into(), x.into()]);
    }
    a
}
fn run_cli(c: &Config, n: &str, payload: &str) -> Value {
    let mut cmd = Command::new(&c.python);
    cmd.args(args(c, n, Some(payload)))
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let mut child = match cmd.spawn() {
        Ok(x) => x,
        Err(e) => return json!({"ok":false,"error":e.to_string()}),
    };
    let mut out_reader = child.stdout.take().unwrap();
    let mut err_reader = child.stderr.take().unwrap();
    let out_thread = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = out_reader.read_to_end(&mut b);
        b
    });
    let err_thread = std::thread::spawn(move || {
        let mut b = Vec::new();
        let _ = err_reader.read_to_end(&mut b);
        b
    });
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
            Ok(None) => {
                let _ = child.kill();
                return json!({"ok":false,"error":"CLI 超时（120 秒）"});
            }
            Err(e) => {
                let _ = child.kill();
                return json!({"ok":false,"error":e.to_string()});
            }
        }
    }
    let _ = child.wait();
    let stdout = out_thread.join().unwrap_or_default();
    let stderr = err_thread.join().unwrap_or_default();
    let b = if stdout.is_empty() { stderr } else { stdout };
    serde_json::from_slice(&b)
        .unwrap_or_else(|_| json!({"ok":false,"error":String::from_utf8_lossy(&b).to_string()}))
}
fn cli(c: &Config, n: &str, p: Value) -> Value {
    if !COMMANDS.contains(&n) {
        return json!({"ok":false,"error":"命令未允许；评审必须在 WorkBuddy"});
    }
    let x = p.to_string();
    run_cli(c, n, &x)
}
fn body(r: &mut Request) -> Result<Value, String> {
    let mut s = String::new();
    r.as_reader()
        .take(1_048_577)
        .read_to_string(&mut s)
        .map_err(|e| e.to_string())?;
    if s.len() > 1_048_576 {
        return Err("请求体过大".into());
    }
    serde_json::from_str(&s).map_err(|e| e.to_string())
}
fn allowed(r: &Request, n: &str, v: &str) -> bool {
    r.headers()
        .iter()
        .find(|x| x.field.to_string().eq_ignore_ascii_case(n))
        .map(|x| x.value.as_str() == v)
        .unwrap_or(false)
}
fn safe(root: &Path, rel: &str) -> Result<PathBuf, String> {
    let r = root.canonicalize().map_err(|e| e.to_string())?;
    let p = r
        .join(rel.trim_start_matches('/'))
        .canonicalize()
        .map_err(|e| e.to_string())?;
    if !p.starts_with(&r) || !p.is_file() {
        return Err("文件不存在或路径越界".into());
    }
    match p
        .extension()
        .and_then(|x| x.to_str())
        .unwrap_or("")
        .to_ascii_lowercase()
        .as_str()
    {
        "pdf" | "txt" | "md" | "doc" | "docx" | "xls" | "xlsx" | "png" | "jpg" | "jpeg" => Ok(p),
        _ => Err("禁止打开此文件类型".into()),
    }
}
fn decode(s: &str) -> String {
    let mut o = Vec::new();
    let mut it = s.chars();
    while let Some(c) = it.next() {
        if c == '%' {
            let a = it.next();
            let b = it.next();
            if let (Some(a), Some(b)) = (a, b) {
                if let Ok(x) = u8::from_str_radix(&format!("{a}{b}"), 16) {
                    o.push(x);
                    continue;
                }
            }
        }
        let mut b = [0; 4];
        o.extend(c.encode_utf8(&mut b).as_bytes());
    }
    String::from_utf8_lossy(&o).into()
}
fn serve(mut r: Request, st: &Arc<Mutex<Config>>, port: u16) {
    let host = allowed(&r, "Host", &format!("127.0.0.1:{port}"));
    let origin = r
        .headers()
        .iter()
        .find(|x| x.field.to_string().eq_ignore_ascii_case("Origin"))
        .map(|x| x.value.as_str() == format!("http://127.0.0.1:{port}"))
        .unwrap_or(true);
    if !host || !origin {
        let _ = r.respond(err(403, "仅允许同源 loopback 请求"));
        return;
    }
    let c = st.lock().unwrap().clone();
    let u = r.url().to_string();
    if r.method() == &Method::Get && u == "/" {
        let _ = r.respond(
            Response::from_string(INDEX)
                .with_header(h("Content-Type", "text/html; charset=utf-8"))
                .with_header(h(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'",
                )),
        );
        return;
    }
    if r.method() == &Method::Get && u == "/style.css" {
        let _ = r.respond(
            Response::from_string(STYLE).with_header(h("Content-Type", "text/css; charset=utf-8")),
        );
        return;
    }
    if r.method() == &Method::Get && u == "/app.js" {
        let _ = r.respond(
            Response::from_string(SCRIPT)
                .with_header(h("Content-Type", "text/javascript; charset=utf-8")),
        );
        return;
    }
    if u == "/api/config" {
        if r.method() == &Method::Get {
            let _=r.respond(resp(json!({"ok":true,"data":{"python":c.python,"script":c.script,"root":c.root,"actor":c.actor,"remote_root":c.remote_root,"instance_key":c.config_path.to_string_lossy(),"pid":std::process::id(),"configured":!c.root.is_empty()&&!c.python.is_empty()&&!c.script.is_empty()&&!c.actor.is_empty()}}),200));
            return;
        }
        if r.method() != &Method::Post
            || !allowed(&r, "Content-Type", "application/json")
            || !allowed(&r, "X-HR-Client", "1")
        {
            let _ = r.respond(err(403, "需要 JSON 与 X-HR-Client"));
            return;
        }
        match body(&mut r) {
            Ok(v) => {
                let mut n = c.clone();
                for k in ["python", "script", "root", "actor", "remote_root"] {
                    if let Some(x) = v.get(k).and_then(|x| x.as_str()) {
                        match k {
                            "python" => n.python = x.into(),
                            "script" => n.script = x.into(),
                            "root" => n.root = x.into(),
                            "remote_root" => n.remote_root = x.into(),
                            _ => n.actor = x.into(),
                        }
                    }
                }
                let z = save(&n);
                if z.is_ok() {
                    *st.lock().unwrap() = n
                }
                let _ = r.respond(match z {
                    Ok(_) => resp(json!({"ok":true}), 200),
                    Err(e) => err(500, e),
                });
            }
            Err(e) => {
                let _ = r.respond(err(400, e));
            }
        }
        return;
    }
    if let Some(q) = u.strip_prefix("/api/file?path=") {
        if r.method() != &Method::Get {
            let _ = r.respond(err(405, "方法不允许"));
            return;
        }
        match safe(Path::new(&c.root), &decode(q)) {
            Ok(p) => match fs::read(&p) {
                Ok(b) => {
                    let m = match p.extension().and_then(|x| x.to_str()).unwrap_or("").to_ascii_lowercase().as_str() { "pdf"=>"application/pdf", "doc"=>"application/msword", "docx"=>"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "xls"=>"application/vnd.ms-excel", "xlsx"=>"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "png"=>"image/png", "jpg"|"jpeg"=>"image/jpeg", _=>"text/plain; charset=utf-8" };
                    let _ = r.respond(Response::from_data(b).with_header(h("Content-Type", m)));
                }
                Err(e) => {
                    let _ = r.respond(err(404, e.to_string()));
                }
            },
            Err(e) => {
                let _ = r.respond(err(403, e));
            }
        }
        return;
    }
    if u == "/api/command" {
        if r.method() != &Method::Post
            || !allowed(&r, "Content-Type", "application/json")
            || !allowed(&r, "X-HR-Client", "1")
        {
            let _ = r.respond(err(403, "需要 JSON 与 X-HR-Client"));
            return;
        }
        match body(&mut r) {
            Ok(v) => {
                let n = v.get("command").and_then(|x| x.as_str()).unwrap_or("");
                let mut payload = v.get("payload").cloned().unwrap_or(json!({}));
                if n == "sync" {
                    if let Some(m) = payload.as_object_mut() {
                        m.entry("remote_root").or_insert(json!(c.remote_root));
                    }
                }
                let _ = r.respond(resp(cli(&c, n, payload), 200));
            }
            Err(e) => {
                let _ = r.respond(err(400, e));
            }
        }
        return;
    }
    let _ = r.respond(err(404, "未找到"));
}
fn main() {
    let a: Vec<String> = std::env::args().collect();
    let headless = a.iter().any(|x| x == "--headless");
    if a.iter().any(|x| x == "--check-runtime") {
        #[cfg(windows)]
        match wry::webview_version() {
            Ok(version) => println!(
                "{}",
                json!({"ok":true,"desktop":"embedded-webview","webview2":version})
            ),
            Err(e) => {
                println!(
                    "{}",
                    json!({"ok":false,"error":format!("请安装 Microsoft Edge WebView2 Runtime：{e}")})
                );
                std::process::exit(1);
            }
        }
        #[cfg(target_os = "macos")]
        println!(
            "{}",
            json!({"ok":true,"desktop":"embedded-webview","webview":"WebKit"})
        );
        #[cfg(not(any(windows, target_os = "macos")))]
        println!(
            "{}",
            json!({"ok":false,"error":"桌面客户端仅支持 Windows 和 macOS"})
        );
        return;
    }
    let mut c = defaults(&std::env::current_exe().unwrap_or_else(|_| PathBuf::from(".")));
    if let Some(i) = a.iter().position(|x| x == "--config") {
        if let Some(p) = a.get(i + 1) {
            c.config_path = p.clone().into();
        }
    }
    c = load(c);
    for w in a.windows(2) {
        match w[0].as_str() {
            "--python" => c.python = w[1].clone(),
            "--script" => c.script = w[1].clone(),
            "--root" => c.root = w[1].clone(),
            "--actor" => c.actor = w[1].clone(),
            "--remote-root" => c.remote_root = w[1].clone(),
            "--port" => c.port = w[1].parse().unwrap_or(0),
            "--config" => c.config_path = w[1].clone().into(),
            _ => {}
        }
    }
    let _instance_mutex = if !headless { acquire_mutex(&c) } else { None };
    if !headless && _instance_mutex.is_none() {
        let mut found = None;
        for _ in 0..400 {
            if let Some((url, pid)) = existing_instance(&c) {
                #[cfg(any(windows, target_os = "macos"))]
                if desktop_window::activate(pid) {
                    found = Some(url);
                    break;
                }
                #[cfg(target_os = "macos")]
                {
                    // The service identity was verified above. macOS does not
                    // expose a portable tao PID activation hook, so Finder
                    // keeps the existing window frontmost and this process
                    // exits immediately without starting a second service.
                    found = Some(url);
                    break;
                }
                #[cfg(not(any(windows, target_os = "macos")))]
                {
                    let _ = pid;
                    found = Some(url);
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        if let Some(url) = found {
            println!("{url}");
            return;
        } else {
            fail_start(
                "已有实例锁但服务标识无效，请结束残留 hr-resume-viewer.exe 后重试",
                false,
            );
        }
    }
    let l = match TcpListener::bind(("127.0.0.1", c.port)) {
        Ok(x) => x,
        Err(e) => fail_start(&format!("无法绑定本地服务：{e}"), headless),
    };
    let p = l.local_addr().unwrap().port();
    let s = match Server::from_listener(l, None) {
        Ok(x) => x,
        Err(e) => fail_start(&format!("无法启动本地服务：{e}"), headless),
    };
    let st = Arc::new(Mutex::new(Config {
        port: p,
        ..c.clone()
    }));
    let url = format!("http://127.0.0.1:{p}/");
    if headless {
        println!("{url}");
        for r in s.incoming_requests() {
            serve(r, &st, p)
        }
        return;
    }
    // The GUI owns the service. Finish any in-flight atomic write before exit.
    use std::sync::atomic::{AtomicBool, Ordering};
    let stop = Arc::new(AtomicBool::new(false));
    let stopping = stop.clone();
    let worker = std::thread::spawn(move || {
        while !stopping.load(Ordering::Acquire) {
            match s.recv_timeout(Duration::from_millis(100)) {
                Ok(Some(r)) => serve(r, &st, p),
                Ok(None) => {}
                Err(_) => break,
            }
        }
    });
    #[cfg(any(windows, target_os = "macos"))]
    let result = desktop_window::run(&url, &c, || {
        write_instance(&c, &url);
        println!("{url}");
    });
    #[cfg(not(any(windows, target_os = "macos")))]
    let result: Result<(), String> =
        Err("桌面客户端仅支持 Windows；服务测试请用 --headless".into());
    stop.store(true, Ordering::Release);
    let _ = worker.join();
    let _ = fs::remove_file(instance_path(&c));
    if let Err(e) = result {
        fail_start(&e, false);
    }
}

fn fail_start(message: &str, headless: bool) -> ! {
    if headless {
        eprintln!("{message}");
    } else {
        show_error(message);
    }
    std::process::exit(1)
}
#[cfg(windows)]
fn show_error(message: &str) {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(
            hwnd: *mut std::ffi::c_void,
            text: *const u16,
            caption: *const u16,
            flags: u32,
        ) -> i32;
    }
    let text: Vec<u16> = std::ffi::OsStr::new(message)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    let cap: Vec<u16> = std::ffi::OsStr::new("HR 简历服务启动失败")
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();
    unsafe {
        MessageBoxW(std::ptr::null_mut(), text.as_ptr(), cap.as_ptr(), 0x10);
    }
}
#[cfg(not(windows))]
fn show_error(message: &str) {
    eprintln!("{message}");
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn blocks_review_submit() {
        assert!(!COMMANDS.contains(&"review-submit"));
    }
    #[test]
    fn path_guard() {
        let d = std::env::temp_dir().join("hr-http-test");
        fs::create_dir_all(&d).unwrap();
        fs::write(d.join("x.txt"), "x").unwrap();
        assert!(safe(&d, "x.txt").is_ok());
        assert!(safe(&d, "../x.txt").is_err());
        let _ = fs::remove_dir_all(d);
    }
    #[test]
    fn package_root_follows_skill_ancestor() {
        let root = std::env::temp_dir().join(format!("hr-root-test-{}", std::process::id()));
        fs::create_dir_all(root.join("skills/manage-hr-resumes/scripts")).unwrap();
        fs::write(root.join("skills/manage-hr-resumes/scripts/hr.py"), "").unwrap();
        fs::create_dir_all(root.join(".runtime")).unwrap();
        fs::write(root.join(".runtime/client-config.json"), "{}").unwrap();
        let exe = root.join("desktop/target/release/hr-resume-viewer.exe");
        assert_eq!(package_root(&exe), root);
        #[cfg(windows)]
        assert_eq!(
            defaults(&exe).config_path,
            root.join(".runtime/client-config.json")
        );
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(windows)]
    #[test]
    fn standalone_executable_uses_user_configuration() {
        let exe = Path::new(r"C:\standalone-client-test\hr-resume-viewer.exe");
        if let Some(base) = std::env::var_os("LOCALAPPDATA") {
            assert_eq!(
                defaults(exe).config_path,
                PathBuf::from(base).join("HRResume/client-config.json")
            );
        }
    }
}
