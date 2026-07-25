// jarvis-write 桌面壳:启动时拉起打包进来的后端(PyInstaller onedir),
// 读其 stdout 的 JARVIS_SERVER_URL 拿到本机地址,再把窗口导航过去。
// 关窗时杀掉后端子进程,避免残留。
use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

// 后端子进程句柄:存进 Tauri 管理的状态,退出时终止。
struct Backend(Mutex<Option<Child>>);

// 前端是否已接管更新 UI:设置页挂载后会调 check_update,把它置真。
// 启动兜底检查(8 秒后)据此决定是否弹原生框——桥通则前端漂亮 UI 接管,
// 桥不通(前端没调成)则原生框兜底,保证任何情况下都能收到更新提示。
struct FrontendActive(AtomicBool);

// 更新日志:release 构建没有控制台,eprintln 看不到;把更新链路的每一步
// 追加写到 app 日志目录的 updater.log,排查"更新不生效"时有据可查。
// 拿不到日志目录 / 写失败都静默忽略(日志本身不该拖垮主流程)。
fn ulog(handle: &tauri::AppHandle, msg: &str) {
    let ts = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let line = format!("[{ts}] {msg}\n");
    // 同时打到 stderr(dev / 带控制台调试时可见)
    eprint!("{line}");
    if let Ok(dir) = handle.path().app_log_dir() {
        let _ = std::fs::create_dir_all(&dir);
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(dir.join("updater.log"))
        {
            let _ = f.write_all(line.as_bytes());
        }
    }
}

// 终止后端子进程并等它真正退出。两个场景必须先调:
// 1) 安装更新前——NSIS 要覆盖 resources 下的后端 exe/dll,进程活着会被
//    Windows 文件锁挡住,留下新旧混杂的半更新;
// 2) restart() 前——restart 不保证触发 WindowEvent::Destroyed,不显式杀
//    会留孤儿后端:占端口、持 SQLite WAL 句柄、以旧版本继续服务。
// kill 后 wait,确保文件锁/端口/句柄都释放再往下走。
fn kill_backend(handle: &tauri::AppHandle) {
    let Some(backend) = handle.try_state::<Backend>() else {
        return;
    };
    let Some(mut child) = backend.0.lock().unwrap().take() else {
        return;
    };
    let _ = child.kill();
    let _ = child.wait();
    ulog(handle, "后端子进程已终止并等待退出完成");
}

// 启动失败的兜底:弹原生错误框(带日志位置)再退出,不再无声 panic——
// release 构建没有控制台,用户双击图标"没反应"是最差体验。
fn fatal_setup_error(handle: &tauri::AppHandle, msg: &str) -> ! {
    use tauri_plugin_dialog::DialogExt;
    ulog(handle, &format!("启动失败:{msg}"));
    let log_hint = handle
        .path()
        .app_log_dir()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|_| "应用日志目录".to_string());
    handle
        .dialog()
        .message(format!(
            "后端启动失败,应用无法继续。\n\n{msg}\n\n日志目录:{log_hint}\n若反复出现,请重启电脑后重试或联系支持。"
        ))
        .title("jarvis-write 启动失败")
        .blocking_show();
    std::process::exit(1);
}

// 冒烟命令:验证运行在本机后端页面(远程源)里的前端能否调到 Tauri IPC。
// 返回当前应用版本;设置页「关于」也用它显示版本号。
#[tauri::command]
fn desktop_ping() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

// check_update 的返回:有更新时带版本与更新说明,无更新时 available=false。
#[derive(Serialize)]
struct UpdateInfo {
    available: bool,
    version: String,
    notes: String,
    current: String,
}

// 前端设置页调用:检查是否有新版本。同时把 FrontendActive 置真,
// 让启动兜底检查(原生框)让位给前端的自定义 UI,避免双重弹窗。
// 不做下载,只返回结果供前端渲染「关于&更新」。
#[tauri::command]
async fn check_update(handle: tauri::AppHandle) -> Result<UpdateInfo, String> {
    use tauri_plugin_updater::UpdaterExt;

    handle
        .state::<FrontendActive>()
        .0
        .store(true, Ordering::SeqCst);

    let current = env!("CARGO_PKG_VERSION").to_string();
    ulog(&handle, "前端触发检查更新");

    let updater = handle
        .updater_builder()
        .build()
        .map_err(|e| format!("更新器构建失败:{e}"))?;

    match updater.check().await {
        Ok(Some(u)) => {
            ulog(&handle, &format!("前端检查:发现新版本 v{}", u.version));
            Ok(UpdateInfo {
                available: true,
                version: u.version.clone(),
                notes: u.body.clone().unwrap_or_default(),
                current,
            })
        }
        Ok(None) => {
            ulog(&handle, "前端检查:已是最新版");
            Ok(UpdateInfo {
                available: false,
                version: String::new(),
                notes: String::new(),
                current,
            })
        }
        Err(e) => {
            ulog(&handle, &format!("前端检查失败:{e}"));
            Err(format!("检查更新失败:{e}"))
        }
    }
}

// 前端设置页调用:下载并安装更新(静默,进度经事件推给前端),完成后不自动重启,
// 由前端提示用户「重启生效」再调 restart_app。装好返回 Ok。
//
// 顺序刻意拆成 download → kill_backend → install:下载期间后端照常服务;
// 安装前杀后端,避开 NSIS 覆盖 resources 下后端文件时的 Windows 文件锁。
#[tauri::command]
async fn download_and_install_update(handle: tauri::AppHandle) -> Result<(), String> {
    use tauri_plugin_updater::UpdaterExt;

    let updater = handle
        .updater_builder()
        .build()
        .map_err(|e| format!("更新器构建失败:{e}"))?;
    let update = updater
        .check()
        .await
        .map_err(|e| format!("检查更新失败:{e}"))?
        .ok_or_else(|| "已是最新版,无需更新".to_string())?;

    ulog(&handle, &format!("前端触发下载 v{}", update.version));

    // 先下载,进度经 tauri 事件推给前端(设置页监听 update://progress 画进度条)。
    let ev_handle = handle.clone();
    let mut downloaded: u64 = 0;
    let bytes = update
        .download(
            move |chunk, total| {
                downloaded += chunk as u64;
                let _ = ev_handle.emit("update://progress", (downloaded, total.unwrap_or(0)));
            },
            || {},
        )
        .await
        .map_err(|e| {
            ulog(&handle, &format!("前端下载失败:{e}"));
            format!("下载失败:{e}")
        })?;

    // 安装前停后端:此后本机 API 断开属预期——装完前端即提示重启。
    kill_backend(&handle);
    ulog(&handle, "下载完成,后端已停,开始安装");

    update.install(&bytes).map_err(|e| {
        ulog(&handle, &format!("前端安装失败:{e}"));
        format!("安装失败:{e}")
    })?;

    ulog(&handle, "前端:更新已安装,等待用户重启");
    Ok(())
}

// 前端设置页调用:重启应用使更新生效(下载安装完成后用户点「立即重启」)。
// restart 不保证触发 WindowEvent::Destroyed,先显式杀后端,避免孤儿残留。
#[tauri::command]
fn restart_app(handle: tauri::AppHandle) {
    ulog(&handle, "前端触发重启以应用更新");
    kill_backend(&handle);
    handle.restart();
}

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

// 启动后端,读它打印的 JARVIS_SERVER_URL=... 行,返回 (子进程, url)。
// 读 stdout 放独立线程 + 30 秒超时:后端卡住不打印(DB 被锁/端口被占)时
// 不再永久阻塞(窗口永不出现、无任何提示)。读线程在拿到 URL 后继续保持
// 管道开启并丢弃后续行,避免后端后续 print 触发 BrokenPipe。
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

    let (tx, rx) = std::sync::mpsc::channel::<String>();
    std::thread::spawn(move || {
        let reader = BufReader::new(stdout);
        let mut sender = Some(tx);
        for line in reader.lines() {
            let Ok(line) = line else { return };
            if let Some(t) = &sender {
                if t.send(line).is_err() {
                    // 主端已拿到 URL 离开:丢弃后续行,仅保持管道开启。
                    sender = None;
                }
            }
        }
    });

    // 后端启动早期会打印一行 JARVIS_SERVER_URL=http://127.0.0.1:<port>
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let now = Instant::now();
        if now >= deadline {
            let _ = child.kill();
            return Err(
                "后端启动超时(30 秒未汇报服务地址),可能端口被占用或数据目录被锁".to_string()
            );
        }
        match rx.recv_timeout(deadline - now) {
            Ok(line) => {
                if let Some(url) = line.strip_prefix("JARVIS_SERVER_URL=") {
                    return Ok((child, url.trim().to_string()));
                }
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                let _ = child.kill();
                return Err(
                    "后端启动超时(30 秒未汇报服务地址),可能端口被占用或数据目录被锁".to_string(),
                );
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                let _ = child.kill();
                return Err("后端未汇报服务地址就退出了".to_string());
            }
        }
    }
}

// 轮询直到后端的 TCP 端口可连接(上限 15 秒)。后端打印 URL 时 uvicorn
// 可能尚未 bind 完成;这里确认就绪再开窗口,取代过去的固定 sleep。
// 超时静默返回:窗口照样打开,前端会自行轮询 /api/mode 等待就绪。
fn wait_backend_ready(url: &str) {
    let Some(addr) = url
        .strip_prefix("http://")
        .or_else(|| url.strip_prefix("https://"))
    else {
        return;
    };
    let addr = addr.trim_end_matches('/').to_string();
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if TcpStream::connect(&addr).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            desktop_ping,
            check_update,
            download_and_install_update,
            restart_app
        ])
        .manage(Backend(Mutex::new(None)))
        .manage(FrontendActive(AtomicBool::new(false)))
        .setup(|app| {
            // 定位打包进 resources 的后端 onedir。
            let exe = match app.path().resource_dir() {
                Ok(dir) => dir.join(BACKEND_EXE),
                Err(e) => fatal_setup_error(&app.handle(), &format!("找不到资源目录: {e}")),
            };

            let (child, url) = match spawn_backend(exe) {
                Ok(v) => v,
                Err(e) => fatal_setup_error(&app.handle(), &e),
            };
            app.state::<Backend>().0.lock().unwrap().replace(child);

            wait_backend_ready(&url);

            let parsed = match url.parse() {
                Ok(p) => p,
                Err(e) => fatal_setup_error(&app.handle(), &format!("后端地址非法({url}): {e}")),
            };
            if let Err(e) = WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
                .title("jarvis · write")
                .inner_size(1200.0, 820.0)
                .min_inner_size(880.0, 600.0)
                .build()
            {
                fatal_setup_error(&app.handle(), &format!("创建窗口失败: {e}"));
            }

            // 窗口已创建,后台检查软件更新(仅发行版真正检查)
            spawn_update_check(app.handle().clone());

            Ok(())
        })
        .on_window_event(|window, event| {
            // 主窗口关闭 → 终止后端子进程,避免端口/进程残留。
            if let tauri::WindowEvent::Destroyed = event {
                kill_backend(window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

// 启动后后台检查软件更新。仅发行版生效;开发/调试构建直接跳过,
// 避免本地反复弹更新框。延迟几秒让窗口先显示,再异步检查,不拖慢启动。
fn spawn_update_check(handle: tauri::AppHandle) {
    if cfg!(debug_assertions) {
        ulog(&handle, "启动检查:debug 构建,跳过自动更新检查");
        return;
    }
    // 延到 8 秒:给设置页/前端足够时间挂载并调 check_update 接管更新 UI。
    // 到点时若前端已接管(FrontendActive=true)就不再弹原生框,交给前端漂亮 UI;
    // 否则(桥没通/前端没调成)用原生框兜底,保证仍能收到更新提示。
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        if handle.state::<FrontendActive>().0.load(Ordering::SeqCst) {
            ulog(&handle, "启动兜底检查:前端已接管更新 UI,跳过原生框");
            return;
        }
        ulog(&handle, "启动兜底检查:前端未接管,走原生框");
        tauri::async_runtime::spawn(async move {
            check_for_update(handle).await;
        });
    });
}

// 检查 → 询问 → 下载 → 停后端 → 安装 → 重启。相比旧版:每条失败路径都写
// 日志(updater.log),排查"更新不生效"时不再是黑盒。无更新也记一笔,
// 能区分"已是最新"与"检查失败"。
async fn check_for_update(handle: tauri::AppHandle) {
    use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
    use tauri_plugin_updater::UpdaterExt;

    let current = env!("CARGO_PKG_VERSION");
    ulog(&handle, &format!("启动检查:当前版本 v{current},开始向更新端点查询"));

    let updater = match handle.updater_builder().build() {
        Ok(u) => u,
        Err(e) => {
            ulog(&handle, &format!("更新器构建失败:{e}"));
            return;
        }
    };
    let update = match updater.check().await {
        Ok(Some(u)) => u,
        Ok(None) => {
            ulog(&handle, "检查完成:已是最新版,无更新");
            return;
        }
        Err(e) => {
            ulog(&handle, &format!("检查失败(离线/清单缺失/签名不匹配等):{e}"));
            return;
        }
    };

    let version = update.version.clone();
    let notes = update.body.clone().unwrap_or_default();
    ulog(&handle, &format!("发现新版本 v{version},弹窗询问用户"));
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
                ulog(&dialog_handle, "用户选择稍后更新");
                return;
            }
            let h = dialog_handle.clone();
            tauri::async_runtime::spawn(async move {
                ulog(&h, "开始下载更新…");
                match update.download(|_chunk, _total| {}, || {}).await {
                    Ok(bytes) => {
                        // 安装前停后端,避开 Windows 文件锁(见 kill_backend 注释)。
                        kill_backend(&h);
                        ulog(&h, "下载完成,后端已停,开始安装");
                        match update.install(&bytes) {
                            Ok(_) => {
                                ulog(&h, "更新安装完成,重启应用");
                                h.restart();
                            }
                            Err(e) => ulog(&h, &format!("安装失败:{e}")),
                        }
                    }
                    Err(e) => ulog(&h, &format!("下载失败:{e}")),
                }
            });
        });
}
