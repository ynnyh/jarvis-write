// jarvis-write 桌面壳:启动时拉起打包进来的后端(PyInstaller onedir),
// 读其 stdout 的 JARVIS_SERVER_URL 拿到本机地址,再把窗口导航过去。
// 关窗时杀掉后端子进程,避免残留。
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

// 后端子进程句柄:存进 Tauri 管理的状态,退出时终止。
struct Backend(Mutex<Option<Child>>);

#[cfg(windows)]
const BACKEND_EXE: &str = "jarvis-write-backend/jarvis-write-backend.exe";
#[cfg(not(windows))]
const BACKEND_EXE: &str = "jarvis-write-backend/jarvis-write-backend";

// 隐藏 Windows 下子进程的控制台窗口。
#[cfg(windows)]
fn hide_console(cmd: &mut Command) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    cmd.creation_flags(CREATE_NO_WINDOW);
}
#[cfg(not(windows))]
fn hide_console(_cmd: &mut Command) {}

// 启动后端,阻塞读取它打印的 JARVIS_SERVER_URL=... 行,返回 (子进程, url)。
fn spawn_backend(exe: std::path::PathBuf) -> Result<(Child, String), String> {
    let mut cmd = Command::new(&exe);
    cmd.stdout(Stdio::piped()).stderr(Stdio::inherit());
    hide_console(&mut cmd);
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("拉起后端失败({}): {e}", exe.display()))?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "无法读取后端 stdout".to_string())?;

    // 后端启动早期会打印一行 JARVIS_SERVER_URL=http://127.0.0.1:<port>
    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        let line = line.map_err(|e| format!("读后端输出失败: {e}"))?;
        if let Some(url) = line.strip_prefix("JARVIS_SERVER_URL=") {
            return Ok((child, url.trim().to_string()));
        }
    }
    let _ = child.kill();
    Err("后端未汇报服务地址就退出了".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Backend(Mutex::new(None)))
        .setup(|app| {
            // 定位打包进 resources 的后端 onedir。
            let resource_dir = app
                .path()
                .resource_dir()
                .map_err(|e| format!("找不到资源目录: {e}"))?;
            let exe = resource_dir.join(BACKEND_EXE);

            let (child, url) = spawn_backend(exe)?;
            app.state::<Backend>().0.lock().unwrap().replace(child);

            // 后端已汇报地址,但 uvicorn 可能还差几十毫秒就绪;窗口指过去后
            // 前端会自行轮询 /api/mode,短暂空白可接受。这里稍等一手更稳。
            std::thread::sleep(Duration::from_millis(300));

            let parsed = url.parse().map_err(|e| format!("后端地址非法({url}): {e}"))?;
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
                .title("jarvis · write")
                .inner_size(1200.0, 820.0)
                .min_inner_size(880.0, 600.0)
                .build()
                .map_err(|e| format!("创建窗口失败: {e}"))?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // 主窗口关闭 → 终止后端子进程,避免端口/进程残留。
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(backend) = window.app_handle().try_state::<Backend>() {
                    if let Some(mut child) = backend.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
