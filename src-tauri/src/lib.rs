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
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

// 后端子进程句柄:存进 Tauri 管理的状态,退出时终止。
struct Backend(Mutex<Option<Child>>);

// 前端是否已接管更新 UI:设置页挂载后会调 check_update,把它置真。
// 启动兜底检查(8 秒后)据此决定是否弹原生框——桥通则前端漂亮 UI 接管,
// 桥不通(前端没调成)则原生框兜底,保证任何情况下都能收到更新提示。
struct FrontendActive(AtomicBool);

// 关闭守卫:前端 CloseGuard 组件挂载后调 enable_close_guard 置 enabled=true,
// 此后点 X 不再直接销毁窗口,而是拦下并发 close://requested 事件交前端决定
// (有任务弹确认框 / 按偏好进托盘 / 直接关,见 frontend/src/ui/CloseGuard.tsx)。
// enabled 默认关:前端没挂载(锁屏早期/JS 崩溃)时点 X 行为照旧,窗口永远关得掉。
// approved:前端决定「直接关闭」或托盘菜单「退出」时置真,防止拦截逻辑把
// 自己发起的关闭又拦一次。
struct CloseGuard {
    enabled: AtomicBool,
    approved: AtomicBool,
}

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

// ===== 更新代理 =====
// 用户自定义更新代理,持久化在 app 配置目录的 update_proxy.txt(纯文本一行,
// 形如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080;文件不存在/为空=直连)。
// 落盘而非只存内存:启动 8 秒后的后台静默检查没有前端参与,也得读到同一份配置。
const UPDATE_PROXY_FILE: &str = "update_proxy.txt";

// 读取已保存的更新代理;未设置/读不到/内容为空都返回 None(=直连)。
fn read_update_proxy(handle: &tauri::AppHandle) -> Option<String> {
    let dir = handle.path().app_config_dir().ok()?;
    let content = std::fs::read_to_string(dir.join(UPDATE_PROXY_FILE)).ok()?;
    let proxy = content.trim();
    if proxy.is_empty() {
        None
    } else {
        Some(proxy.to_string())
    }
}

// 校验代理地址:空串=清除代理(返回 None);非空必须形如 http://host:port
// 或 socks5://host:port,非法直接报错,不写盘。
fn validate_update_proxy(proxy: &str) -> Result<Option<url::Url>, String> {
    let proxy = proxy.trim();
    if proxy.is_empty() {
        return Ok(None);
    }
    let url = proxy
        .parse::<url::Url>()
        .map_err(|e| format!("代理地址格式非法:{e}"))?;
    let scheme_ok = matches!(url.scheme(), "http" | "socks5");
    let host_ok = url.host_str().is_some_and(|h| !h.is_empty());
    let port_ok = url.port().is_some();
    if !scheme_ok || !host_ok || !port_ok {
        return Err("代理地址须形如 http://host:port 或 socks5://host:port".to_string());
    }
    Ok(Some(url))
}

// 前端设置页调用:保存更新代理。空串=清除(恢复直连),下次检查/下载生效。
#[tauri::command]
fn set_update_proxy(handle: tauri::AppHandle, proxy: String) -> Result<(), String> {
    let validated = validate_update_proxy(&proxy)?;
    let dir = handle
        .path()
        .app_config_dir()
        .map_err(|e| format!("拿不到配置目录:{e}"))?;
    std::fs::create_dir_all(&dir).map_err(|e| format!("创建配置目录失败:{e}"))?;
    let path = dir.join(UPDATE_PROXY_FILE);
    match validated {
        Some(url) => {
            ulog(&handle, &format!("更新代理已设置:{url}"));
            std::fs::write(&path, format!("{url}\n")).map_err(|e| format!("保存代理失败:{e}"))
        }
        None => {
            ulog(&handle, "更新代理已清除,恢复直连");
            match std::fs::remove_file(&path) {
                Ok(_) => Ok(()),
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
                Err(e) => Err(format!("清除代理失败:{e}")),
            }
        }
    }
}

// 前端设置页调用:读取已保存的更新代理(未设置返回空串),用于设置页回显。
#[tauri::command]
fn get_update_proxy(handle: tauri::AppHandle) -> String {
    read_update_proxy(&handle).unwrap_or_default()
}

// ===== 关闭守卫 / 托盘 =====
// 三个命令配套前端 CloseGuard:点 X 被 on_window_event 拦下并发事件,
// 前端决定去向后回调其中一个命令。窗口操作全走自定义命令而非 window ACL,
// 与现有更新命令同一模式(远程源只需 allow-<cmd> 权限)。

// 前端 CloseGuard 挂载后调用:开启关闭拦截。此后点 X 拦下并发 close://requested。
#[tauri::command]
fn enable_close_guard(handle: tauri::AppHandle) {
    handle
        .state::<CloseGuard>()
        .enabled
        .store(true, Ordering::SeqCst);
}

// 前端决定「直接关闭」:置 approved 放行 CloseRequested 再关窗;
// Destroyed 事件里 kill_backend 照旧执行。
#[tauri::command]
fn close_app(handle: tauri::AppHandle) {
    handle
        .state::<CloseGuard>()
        .approved
        .store(true, Ordering::SeqCst);
    if let Some(w) = handle.get_webview_window("main") {
        let _ = w.close();
    }
}

// 前端决定「最小化到托盘」:只隐藏窗口(不销毁),后端子进程与后台任务继续跑;
// 点托盘图标或菜单「显示」恢复。
#[tauri::command]
fn hide_to_tray(handle: tauri::AppHandle) {
    if let Some(w) = handle.get_webview_window("main") {
        let _ = w.hide();
    }
}

// 从托盘隐藏/最小化状态恢复主窗口并聚焦。
fn show_main_window(handle: &tauri::AppHandle) {
    if let Some(w) = handle.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

// 构建系统托盘:左键点图标恢复窗口;菜单「显示」恢复、「退出」彻底退出。
// 「退出」先置 approved(避免退出触发的 CloseRequested 被自己拦下)、显式杀后端
// 再 exit——app.exit 不保证走窗口 Destroyed 事件,不显式杀会留孤儿后端。
fn build_tray(app: &tauri::App) -> Result<(), String> {
    let show = MenuItemBuilder::with_id("show", "显示 jarvis-write")
        .build(app)
        .map_err(|e| format!("{e}"))?;
    let quit = MenuItemBuilder::with_id("quit", "退出")
        .build(app)
        .map_err(|e| format!("{e}"))?;
    let menu = MenuBuilder::new(app)
        .item(&show)
        .item(&quit)
        .build()
        .map_err(|e| format!("{e}"))?;
    // 图标复用 tauri.conf.json bundle.icon(解码由 Tauri 内建,无需额外 image feature)
    let icon = app
        .default_window_icon()
        .cloned()
        .ok_or_else(|| "配置里没有可用的应用图标".to_string())?;
    TrayIconBuilder::with_id("main-tray")
        .icon(icon)
        .tooltip("jarvis · write")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => show_main_window(app),
            "quit" => {
                app.state::<CloseGuard>()
                    .approved
                    .store(true, Ordering::SeqCst);
                kill_backend(app);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        })
        .build(app)
        .map_err(|e| format!("{e}"))?;
    Ok(())
}

// 构建更新器:proxy 为 Some 时检查与下载都走该代理——插件会把代理设置带进
// check 返回的 Update,后续 download 沿用同一通道,无需再配。
fn build_updater(
    handle: &tauri::AppHandle,
    proxy: Option<&url::Url>,
) -> Result<tauri_plugin_updater::Updater, String> {
    use tauri_plugin_updater::UpdaterExt;
    let builder = handle.updater_builder();
    let builder = match proxy {
        Some(p) => {
            ulog(handle, &format!("更新走代理:{p}"));
            builder.proxy(p.clone())
        }
        None => builder,
    };
    builder
        .build()
        .map_err(|e| format!("更新器构建失败:{e}"))
}

// updater.check() 的重试封装:国内直连 GitHub 不稳定,瞬时失败大多是网络抖动,
// 失败后等 2s/4s 再试,最多 3 次。download 沿用 check 出来的 Update,不受影响。
async fn check_with_retry(
    updater: &tauri_plugin_updater::Updater,
) -> Result<Option<tauri_plugin_updater::Update>, tauri_plugin_updater::Error> {
    let mut last_err = None;
    for attempt in 0..3 {
        match updater.check().await {
            Ok(update) => return Ok(update),
            Err(err) => {
                if attempt < 2 {
                    tokio::time::sleep(std::time::Duration::from_secs(2 << attempt)).await;
                }
                last_err = Some(err);
            }
        }
    }
    Err(last_err.unwrap())
}

// 检查更新,带「代理失败回退直连」:已配置代理时先走代理,重试全失败后
// 改用直连再查一轮——避免用户填错代理(或代理没开)后永远收不到更新。
// 返回的 Update 携带最终成功的通道,download 沿用;错误信息区分两种失败。
async fn check_with_fallback(
    handle: &tauri::AppHandle,
) -> Result<Option<tauri_plugin_updater::Update>, String> {
    // 配置内容必然已通过 validate_update_proxy 校验,这里解析失败按无代理处理。
    let proxy = read_update_proxy(handle).and_then(|p| p.parse::<url::Url>().ok());
    let updater = build_updater(handle, proxy.as_ref())?;
    match check_with_retry(&updater).await {
        Ok(update) => Ok(update),
        Err(e) => {
            if proxy.is_none() {
                return Err(format!("{e}"));
            }
            ulog(handle, &format!("代理检查失败:{e},回退直连重试"));
            let direct = build_updater(handle, None)?;
            check_with_retry(&direct)
                .await
                .map_err(|e2| format!("代理失败({e});直连也失败({e2})"))
        }
    }
}

// 下载更新包,进度经 update://progress 事件推给前端。与检查同样的代理回退:
// update 自带通道(代理/直连)下载失败且配了代理时,直连重新 check 再下一次。
async fn download_with_fallback(
    handle: &tauri::AppHandle,
    update: &tauri_plugin_updater::Update,
) -> Result<Vec<u8>, String> {
    let ev_handle = handle.clone();
    let mut downloaded: u64 = 0;
    let first = update
        .download(
            move |chunk, total| {
                downloaded += chunk as u64;
                let _ = ev_handle.emit("update://progress", (downloaded, total.unwrap_or(0)));
            },
            || {},
        )
        .await;
    match first {
        Ok(bytes) => Ok(bytes),
        Err(e) => {
            if read_update_proxy(handle).is_none() {
                return Err(format!("{e}"));
            }
            ulog(handle, &format!("代理下载失败:{e},回退直连重新下载"));
            let direct = build_updater(handle, None)?;
            let update2 = check_with_retry(&direct)
                .await
                .map_err(|e2| format!("代理下载失败({e});直连检查也失败({e2})"))?
                .ok_or_else(|| format!("代理下载失败({e});直连检查不到更新"))?;
            let ev_handle = handle.clone();
            let mut downloaded: u64 = 0;
            update2
                .download(
                    move |chunk, total| {
                        downloaded += chunk as u64;
                        let _ = ev_handle.emit("update://progress", (downloaded, total.unwrap_or(0)));
                    },
                    || {},
                )
                .await
                .map_err(|e2| format!("代理下载失败({e});直连下载也失败({e2})"))
        }
    }
}

// 前端设置页调用:检查是否有新版本。同时把 FrontendActive 置真,
// 让启动兜底检查(原生框)让位给前端的自定义 UI,避免双重弹窗。
// 不做下载,只返回结果供前端渲染「关于&更新」。
#[tauri::command]
async fn check_update(handle: tauri::AppHandle) -> Result<UpdateInfo, String> {
    handle
        .state::<FrontendActive>()
        .0
        .store(true, Ordering::SeqCst);

    let current = env!("CARGO_PKG_VERSION").to_string();
    ulog(&handle, "前端触发检查更新");

    match check_with_fallback(&handle).await {
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
    let update = check_with_fallback(&handle)
        .await
        .map_err(|e| format!("检查更新失败:{e}"))?
        .ok_or_else(|| "已是最新版,无需更新".to_string())?;

    ulog(&handle, &format!("前端触发下载 v{}", update.version));

    // 先下载,进度经 tauri 事件推给前端(设置页监听 update://progress 画进度条)。
    let bytes = download_with_fallback(&handle, &update).await.map_err(|e| {
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
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            // 重复启动:把已有窗口唤到前台(可能正藏在托盘),不再起第二份后端
            show_main_window(app);
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            desktop_ping,
            check_update,
            download_and_install_update,
            restart_app,
            set_update_proxy,
            get_update_proxy,
            enable_close_guard,
            close_app,
            hide_to_tray
        ])
        .manage(Backend(Mutex::new(None)))
        .manage(FrontendActive(AtomicBool::new(false)))
        .manage(CloseGuard {
            enabled: AtomicBool::new(false),
            approved: AtomicBool::new(false),
        })
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

            // 系统托盘:「最小化到托盘」关闭偏好的载体。左键点图标恢复窗口;
            // 菜单提供「显示 / 退出」。图标用打包的同一份 icon.png,不新增资源。
            // 托盘构建失败不该拖垮启动(极少数桌面环境无托盘区),记日志继续。
            match build_tray(app) {
                Ok(_) => {}
                Err(e) => ulog(&app.handle(), &format!("托盘创建失败,托盘相关功能不可用:{e}")),
            }

            // 窗口已创建,后台检查软件更新(仅发行版真正检查)
            spawn_update_check(app.handle().clone());

            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                // 点了 X:守卫开启且未获放行 → 拦下,发 close://requested 让前端
                // 决定去向(确认框/托盘/直接关)。守卫未开(前端没挂载/JS 崩了)
                // 保持原有直接关闭行为,保证任何情况下窗口都关得掉。
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    let guard = window.app_handle().state::<CloseGuard>();
                    if guard.enabled.load(Ordering::SeqCst)
                        && !guard.approved.load(Ordering::SeqCst)
                    {
                        api.prevent_close();
                        let _ = window.emit("close://requested", ());
                    }
                }
                // 主窗口销毁 → 终止后端子进程,避免端口/进程残留。
                tauri::WindowEvent::Destroyed => kill_backend(window.app_handle()),
                _ => {}
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

    let current = env!("CARGO_PKG_VERSION");
    ulog(&handle, &format!("启动检查:当前版本 v{current},开始向更新端点查询"));

    // 与前端路径同一套逻辑:有代理走代理,失败回退直连(见 check_with_fallback)。
    let update = match check_with_fallback(&handle).await {
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
                match download_with_fallback(&h, &update).await {
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

#[cfg(test)]
mod tests {
    use super::validate_update_proxy;

    // 代理地址校验:空=清除;http/socks5 + host:port 合法;其余一律拒绝。
    #[test]
    fn validate_update_proxy_rules() {
        assert!(validate_update_proxy("").unwrap().is_none());
        assert!(validate_update_proxy("   ").unwrap().is_none());

        let http = validate_update_proxy("http://127.0.0.1:7890").unwrap().unwrap();
        assert_eq!(http.scheme(), "http");
        assert_eq!(http.port(), Some(7890));
        let socks = validate_update_proxy("socks5://127.0.0.1:1080").unwrap().unwrap();
        assert_eq!(socks.scheme(), "socks5");

        // 缺端口、缺 host、非支持协议都非法
        assert!(validate_update_proxy("http://127.0.0.1").is_err());
        assert!(validate_update_proxy("http://:7890").is_err());
        assert!(validate_update_proxy("https://127.0.0.1:7890").is_err());
        assert!(validate_update_proxy("127.0.0.1:7890").is_err());
        assert!(validate_update_proxy("不是地址").is_err());
    }
}
