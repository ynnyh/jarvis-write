# jarvis-write 项目规范

AI 小说/漫剧/宣传片/短片生成平台。后端 FastAPI(`backend/`),前端 React+Vite 手写 CSS(`frontend/`),
桌面版 Tauri(`src-tauri/`)。设计文档在 `docs/`,**改交互前先读 `docs/07` §0 铁律与 `docs/10`**。

## 门禁(每次改动收尾必跑,全绿才算完)

```bash
cd frontend && npx tsc --noEmit && npx eslint <改动文件> && npx vitest run
cd backend  && python -m pytest -q
```

其中 `frontend/src/test/uiConventions.test.ts` 是**版面公约门禁**:扫全量源码,挡住下面四类复发
(裸 `navigator.clipboard` / 影子组件 / 原生 `confirm`·`alert` / `.card-head` 当表单行)。
四条判据现在**都没有豁免清单**(`.card-head` 那条原先挂着 `DramaPanel.tsx`,已整改清空)。
被它拦住时**只有两条出路:整改,或者证明判据本身写错了**——别加豁免名单,那等于给复发开门。

## 前端版面公约(反复踩过的坑,新页面照抄这里,别自己发明)

- **表单一律用 `.form-grid` / `.field` / `.form-actions` 骨架**,禁止拿 `.card-head` 拼表单行。
  `.card-head` 是「标题 + 右侧按钮」的一行 flex,把 label 和控件塞进去,窗口一窄就把
  「时长」竖着拆成两行、主按钮混在字段中间——`宣传片工坊`/`情绪短片工坊` 都栽在这上面。
  正确写法:`.field` = 标签(`.fl`)在上、控件在下;`.field-full` 独占一行;
  长说明用 `.field-note`;主按钮放 `.form-actions`(自带上分界线)。
- **能复用就别手写**:复制按钮用 `ui/copy` 的 `CopyBtn`(三层兜底,HTTP 页面照样能复制——
  裸调 `navigator.clipboard` 在 `http://IP:8080` 下必失败,线上就是这么部署的);
  空态用 `ui/EmptyState`;确认框用 `ui/ConfirmDialog` 的 `confirmDialog`(别用原生 `confirm`);
  长任务用 `ui/useJob` + `ui/Banner`(跑批横幅);出片线的管线步骤条用 `ui/StepBar`,
  工作台外壳用 `.wb-shell` / `.wb-cols` / `.wb-rail`(锚资产)/ `.wb-main`(推进区)——
  漫剧与宣传片共用这一套,别再新起一套 `xxx-cols`;注意别用旧的 `.workbench`(那是横向 flex)。
  **新页面里出现和 `ui/` 同名的本地组件,就是抄漏了。**
- 新样式手写进 `styles.css`,只用现有令牌(`--sp-*`/`--fs-*`/`--ctl-h`/语义色),
  这样暗色主题自动跟着走;不引入 Tailwind/shadcn。
- 中文注释,文件头一行说明 + 关键块讲清「为什么这么写」。

## UI 改动的验收方式

**光看代码不算改好看了**——CSS 改完要落地看一眼:临时写个引用 `src/styles.css` 的
静态 HTML(照搬真实 className 结构),用 headless Chrome 截图,分别看宽屏与 ≤640px:

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --disable-gpu \
  --hide-scrollbars --window-size=1280,1200 --screenshot=".preview.png" \
  --virtual-time-budget=2500 "file:///<abs>/frontend/.preview.html"
```

注意:headless 窗口有最小宽度(约 500px),想验 420px 窄屏别只看截图宽度——
在 640px 处验证断点即可。验完删掉临时文件。

## 后端约定

- LLM 一律走 `adapter.ask()` / `ask_messages()`(内部 `complete_text_with_budget`:
  空正文与截断都放大预算重试),**不要裸调 `complete()`**。
- 新接口挂 `dependencies=[Depends(get_current_user)]`,取单条记录必须 `assert_project_owner`
  (多用户线上部署,归属隔离是硬要求);列表按 `user_id` 过滤。
- 长任务走 `jobs.spawn_job` + `list_running` 去重,worker 里**自己开 `SessionLocal`**,
  别跨 LLM 调用持有请求级 session(`database is locked` 的老根因)。
- 建表靠 `Base.metadata.create_all`(新表自动建);**改已有表的列必须在 `app/migrate.py` 里补 ALTER**。
- 三条出片线(漫剧/宣传片/情绪短片)的**确定性共用件放 `app/engines/media/`**,别再各写一份:
  切段与时间码用 `media.segments`(`group_by_limit` / `chunk_rows` / `plan_chunks`,
  更严的内聚条件传 `can_join` 加严,漫剧就是这么做的),画风锚与负面词兜底用
  `media.anchors`(`ensure_style_anchors` / `merge_negative`),音频分轨用
  `media.audio`(`ensure_audio_rules` 追进视频提示词 / `audio_track_note` 写进导出手册)。
  三条口径全站一致:段边界只落在镜头边界上;单格超上限独立成段并标 `over_limit`,不静默截断;
  音频**分轨不静音**——环境音让视频模型出,人声与 BGM 整片后期铺,
  且音频词只进提示词正文、**不许进负面词框**(那个框各站是给画面用的)。
