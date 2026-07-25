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
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
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

            // 窗口已创建,后台检查软件更新(仅发行版真正检查)
            spawn_update_check(app.handle().clone());

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

// 启动后后台检查软件更新。仅发行版生效;开发/调试构建直接跳过,
// 避免本地反复弹更新框。延迟几秒让窗口先显示,再异步检查,不拖慢启动。
fn spawn_update_check(handle: tauri::AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(3));
        tauri::async_runtime::spawn(async move {
            check_for_update(handle).await;
        });
    });
}

// 检查 → 询问 → 下载安装 → 重启。任何一步失败或无更新都静默返回,不打扰用户。
async fn check_for_update(handle: tauri::AppHandle) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
    use tauri_plugin_updater::UpdaterExt;

    let updater = match handle.updater_builder().build() {
        Ok(u) => u,
        Err(_) => return,
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        _ => return, // 无更新 / 离线 / 检查失败,静默
    };

    let version = update.version.clone();
    let notes = update.body.clone().unwrap_or_default();
    let msg = if notes.trim().is_empty() {
        format!("发现新版本 v{version}，是否立即更新？")
    } else {
        format!("发现新版本 v{version}\n\n{notes}\n\n是否立即更新？")
    };

    let dialog_handle = handle.clone();
    handle
        .dialog()
        .message(msg)
        .title("jarvis-write 软件更新")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "立即更新".into(),
            "稍后".into(),
        ))
        .show(move |confirmed| {
            if !confirmed {
                return;
            }
            let h = dialog_handle.clone();
            tauri::async_runtime::spawn(async move {
                // 下载并安装更新(进度回调略),完成后重启应用
                let _ = update.download_and_install(|_chunk, _total| {}, || {}).await;
                h.restart();
            });
        });
}
