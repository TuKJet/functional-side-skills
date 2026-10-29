//! Native Windows/macOS shell. Business operations stay in the shared local service.
use super::Config;
use std::{collections::HashMap, ffi::c_void, path::Path};
use tao::{
    dpi::LogicalSize,
    event::{Event, WindowEvent},
    event_loop::{ControlFlow, EventLoopBuilder},
    platform::run_return::EventLoopExtRunReturn,
    window::{Theme, Window, WindowBuilder},
};
use wry::{NewWindowResponse, WebContext, WebView, WebViewBuilder};

#[derive(Debug)]
enum UserEvent {
    Original(String),
}

struct Preview {
    // Drop the WebView before its host window.
    _webview: WebView,
    _window: Window,
}

fn local_file_url(base: &str, url: &str) -> bool {
    url.strip_prefix(base)
        .is_some_and(|p| p.starts_with("api/file?path="))
}

pub fn run(url: &str, config: &Config, ready: impl FnOnce()) -> Result<(), String> {
    #[cfg(windows)]
    wry::webview_version().map_err(|e| {
        format!("未找到可用的 Microsoft Edge WebView2 Runtime，请让安装 Agent 补齐：{e}")
    })?;
    let mut events = EventLoopBuilder::<UserEvent>::with_user_event().build();
    let size = events
        .primary_monitor()
        .map(|monitor| {
            let screen = monitor.size().to_logical::<f64>(monitor.scale_factor());
            LogicalSize::new(
                (screen.width - 64.0).clamp(900.0, 1280.0),
                (screen.height - 100.0).clamp(620.0, 800.0),
            )
        })
        .unwrap_or(LogicalSize::new(1200.0, 720.0));
    let window = WindowBuilder::new()
        .with_title("招聘工作台")
        .with_inner_size(size)
        .with_min_inner_size(LogicalSize::new(900.0, 620.0))
        .with_theme(Some(Theme::Light))
        .with_background_color((248, 246, 241, 255))
        .build(&events)
        .map_err(|e| e.to_string())?;
    let profile = config
        .config_path
        .parent()
        .unwrap_or(Path::new("."))
        .join("webview-profile");
    let mut context = WebContext::new(Some(profile));
    let proxy = events.create_proxy();
    let base = url.to_owned();
    let nav_base = base.clone();
    let navigation_proxy = proxy.clone();
    let webview = WebViewBuilder::new_with_web_context(&mut context)
        .with_url(url)
        .with_background_color((248, 246, 241, 255))
        .with_devtools(false)
        .with_general_autofill_enabled(false)
        .with_navigation_handler(move |target| {
            if local_file_url(&nav_base, &target) {
                let _ = navigation_proxy.send_event(UserEvent::Original(target));
                false
            } else {
                target == nav_base || target.starts_with(&(nav_base.clone() + "#"))
            }
        })
        .with_new_window_req_handler(move |target, _| {
            if local_file_url(&base, &target) {
                let _ = proxy.send_event(UserEvent::Original(target));
            }
            NewWindowResponse::Deny
        })
        .with_download_started_handler(|_, _| false)
        .build(&window)
        .map_err(|e| format!("无法创建内嵌界面：{e}"))?;
    let main_id = window.id();
    let mut previews = HashMap::new();
    ready();
    events.run_return(|event, target, control| {
        *control = ControlFlow::Wait;
        match event {
            Event::WindowEvent {
                window_id,
                event: WindowEvent::CloseRequested,
                ..
            } => {
                if window_id == main_id {
                    *control = ControlFlow::Exit;
                } else {
                    previews.remove(&window_id);
                }
            }
            Event::UserEvent(UserEvent::Original(original)) => {
                let result = (|| -> Result<(), String> {
                    let relative = original
                        .strip_prefix(&format!("{url}api/file?path="))
                        .ok_or("仅允许打开当前工作副本的原件")?;
                    let file = super::safe(Path::new(&config.root), &super::decode(relative))?;
                    let extension = file
                        .extension()
                        .and_then(|x| x.to_str())
                        .unwrap_or("")
                        .to_ascii_lowercase();
                    if ["doc", "docx", "xls", "xlsx"].contains(&extension.as_str()) {
                        return open_office_document(&file);
                    }
                    let child = WindowBuilder::new()
                        .with_title("简历原件 · 招聘工作台")
                        .with_inner_size(LogicalSize::new(960.0, 760.0))
                        .with_theme(Some(Theme::Light))
                        .build(target)
                        .map_err(|e| e.to_string())?;
                    let permitted = format!("{url}api/file?path=");
                    let view = WebViewBuilder::new_with_web_context(&mut context)
                        .with_url(original)
                        .with_devtools(false)
                        .with_general_autofill_enabled(false)
                        .with_navigation_handler(move |uri| uri.starts_with(&permitted))
                        .with_new_window_req_handler(|_, _| NewWindowResponse::Deny)
                        .with_download_started_handler(|_, _| false)
                        .build(&child)
                        .map_err(|e| e.to_string())?;
                    previews.insert(
                        child.id(),
                        Preview {
                            _webview: view,
                            _window: child,
                        },
                    );
                    Ok(())
                })();
                if let Err(e) = result {
                    super::show_error(&e);
                }
            }
            _ => {}
        }
    });
    previews.clear();
    drop(webview);
    drop(window);
    Ok(())
}

/// Restore only the visible top-level window owned by the verified service PID.
pub fn activate(pid: u32) -> bool {
    #[cfg(target_os = "macos")]
    {
        let _ = pid;
        // tao does not expose a cross-platform PID lookup. The macOS launch
        // path remains safe because the service URL and instance key are
        // checked by the caller; a second process simply starts its own view.
        return false;
    }
    #[cfg(windows)]
    {
        struct Search {
            pid: u32,
            found: bool,
        }
        #[link(name = "user32")]
        extern "system" {
            fn EnumWindows(
                callback: unsafe extern "system" fn(*mut c_void, isize) -> i32,
                data: isize,
            ) -> i32;
            fn GetWindowThreadProcessId(hwnd: *mut c_void, pid: *mut u32) -> u32;
            fn IsWindowVisible(hwnd: *mut c_void) -> i32;
            fn GetWindowTextW(hwnd: *mut c_void, title: *mut u16, max: i32) -> i32;
            fn ShowWindow(hwnd: *mut c_void, mode: i32) -> i32;
            fn SetForegroundWindow(hwnd: *mut c_void) -> i32;
        }
        unsafe extern "system" fn visit(hwnd: *mut c_void, data: isize) -> i32 {
            let search = &mut *(data as *mut Search);
            let mut owner = 0;
            GetWindowThreadProcessId(hwnd, &mut owner);
            let mut title = [0u16; 128];
            let count = GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32);
            if owner == search.pid
                && IsWindowVisible(hwnd) != 0
                && String::from_utf16_lossy(&title[..count.max(0) as usize]) == "招聘工作台"
            {
                ShowWindow(hwnd, 9);
                SetForegroundWindow(hwnd);
                search.found = true;
                return 0;
            }
            1
        }
        let mut search = Search { pid, found: false };
        unsafe {
            EnumWindows(visit, &mut search as *mut Search as isize);
        }
        search.found
    }
}

// Office files use the installed document editor; HTTP URLs never reach ShellExecute.
fn open_office_document(file: &Path) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        let status = std::process::Command::new("open")
            .arg(file)
            .status()
            .map_err(|e| format!("无法调用 macOS 文件打开器：{e}"))?;
        return if status.success() {
            Ok(())
        } else {
            Err("无法打开 Office 原件，请确认已安装 Pages / Numbers / Office 或兼容软件。".into())
        };
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "shell32")]
        extern "system" {
            fn ShellExecuteW(
                hwnd: *mut c_void,
                verb: *const u16,
                file: *const u16,
                params: *const u16,
                dir: *const u16,
                show: i32,
            ) -> isize;
        }
        let raw = file.to_string_lossy();
        let path = raw.strip_prefix(r"\\?\").unwrap_or(&raw);
        let wide: Vec<u16> = std::ffi::OsStr::new(path)
            .encode_wide()
            .chain(Some(0))
            .collect();
        let verb: Vec<u16> = "open".encode_utf16().chain(Some(0)).collect();
        let result = unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                verb.as_ptr(),
                wide.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                1,
            )
        };
        if result <= 32 {
            Err("无法打开 Office 原件，请确认已安装 Word / Excel 或兼容软件。".into())
        } else {
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn original_windows_only_accept_current_origin() {
        let base = "http://127.0.0.1:1234/";
        assert!(local_file_url(
            base,
            "http://127.0.0.1:1234/api/file?path=pool%2Fa.pdf"
        ));
        assert!(!local_file_url(base, "https://example.com/api/file?path=x"));
        assert!(!local_file_url(
            base,
            "http://127.0.0.1:12345/api/file?path=x"
        ));
        assert!(!local_file_url(base, "file:///C:/private.txt"));
    }
}
