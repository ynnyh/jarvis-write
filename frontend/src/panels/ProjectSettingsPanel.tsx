// settings 区:书级设置。每章目标字数 / 字数守卫 / 审校把关(达标线·回炉上限·连写要求)/ 世界观硬规则 / 故事宪法。
// 字数守卫与审校把关两张卡从原 ChaptersPanel 侧栏搬来,世界观硬规则从原 EditorialPanel audit 页签搬来,功能文案不变。
// 故事宪法(结构化 canon:刻意留白/常驻装置/倒计时)紧挨世界观硬规则——两者在后端合并成一张「宪法块」全程注入+门禁比对。
import { useState } from "react";
import { api, CanonDevice, EMPTY_CANON, IMPORTANCE_OPTIONS, Project, StoryCanon } from "../api";
import { useInvalidateProject } from "../hooks/queries";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";

// 每章目标字数的合法区间(前端自校验,后端 ProjectPatch 不设上下界;区间来自交互改造计划)
const TARGET_WORDS_MIN = 200;
const TARGET_WORDS_MAX = 20000;

interface Props { pid: number; project: Project; }

export default function ProjectSettingsPanel({ pid, project }: Props) {
  const invalidateProject = useInvalidateProject(pid);
  // 世界观硬规则编辑态(整段覆盖,空串清空);初始值取自项目(进入本区时 project 已就绪)
  const [worldRules, setWorldRules] = useState(project.world_rules ?? "");
  const [rulesSaving, setRulesSaving] = useState(false);
  // 每章目标字数编辑态(字符串保存原始输入,允许清空重输;保存时校验区间)
  const [targetWords, setTargetWords] = useState(String(project.target_words_per_chapter));
  const [wordsSaving, setWordsSaving] = useState(false);
  // 故事宪法编辑态(结构化 canon):留白走多行文本框,装置走可增删行,倒计时留空「名称」=不设。
  const canon0 = project.canon ?? EMPTY_CANON;
  const [absencesText, setAbsencesText] = useState((canon0.absences ?? []).join("\n"));
  const [devices, setDevices] = useState<CanonDevice[]>(canon0.devices ?? []);
  const [dlName, setDlName] = useState(canon0.deadline?.name ?? "");
  const [dlDays, setDlDays] = useState(canon0.deadline ? String(canon0.deadline.total_days) : "");
  const [dlAnchor, setDlAnchor] = useState(canon0.deadline ? String(canon0.deadline.anchor_chapter) : "1");
  const [canonSaving, setCanonSaving] = useState(false);

  // 出片模式切换(lite/full):即点即存;完整档未点亮时后端行为不变
  async function saveRenderMode(mode: "lite" | "full") {
    if (project.render_mode === mode) return;
    try {
      await api.patchProject(pid, { render_mode: mode });
      await invalidateProject();
      toast.ok(mode === "full" ? "已切到完整档" : "已切回轻量档",
        mode === "full" ? "完整档模块分期点亮中,当前行为与轻量档一致" : "逐镜手动出片与筛选");
    } catch (e) { toast.err("出片模式保存失败", errMsg(e)); }
  }

  // 保存每章目标字数:后端 ProjectPatch 已支持 target_words_per_chapter,纯前端接线
  async function saveTargetWords() {
    const n = Number(targetWords);
    if (!Number.isInteger(n) || n < TARGET_WORDS_MIN || n > TARGET_WORDS_MAX) {
      toast.err("每章目标字数无效", `请输入 ${TARGET_WORDS_MIN}-${TARGET_WORDS_MAX} 之间的整数`);
      return;
    }
    setWordsSaving(true);
    try {
      await api.patchProject(pid, { target_words_per_chapter: n });
      await invalidateProject();
      toast.ok("每章目标字数已保存", "将作用于后续生成与字数守卫");
    } catch (e) { toast.err("每章目标字数保存失败", errMsg(e)); } finally { setWordsSaving(false); }
  }

  // 字数守卫开关:超标自动压缩/拆章。一个开关同时管压缩与拆章,默认关闭。
  async function toggleGuard(on: boolean) {
    try {
      await api.patchProject(pid, { word_guard_enabled: on, auto_split_enabled: on });
      await invalidateProject();
      toast.ok(on ? "已开启字数守卫" : "已关闭字数守卫",
        on ? "章节超出目标字数较多时会自动压缩或拆章" : "字数只做宽松参考,不再自动压缩/拆章");
    } catch (e) { toast.err("开关保存失败", errMsg(e)); }
  }

  // 编辑部审校把关配置:达标线 / 自动回炉开关 / 回炉上限 / 连写前置审核。改完即存,失效缓存重拉。
  async function patchReview(patch: {
    review_pass_threshold?: number;
    review_auto_revise?: boolean;
    review_max_revisions?: number;
    queue_require_approved?: boolean;
  }) {
    try {
      await api.patchProject(pid, patch);
      await invalidateProject();
    } catch (e) { toast.err("审校配置保存失败", errMsg(e)); }
  }

  // 保存世界观硬规则(整段覆盖,空串清空)
  async function saveWorldRules() {
    setRulesSaving(true);
    try {
      await api.patchProject(pid, { world_rules: worldRules });
      await invalidateProject();
      toast.ok("世界观硬规则已保存", "将注入后续所有生成,可用于规则扫描体检正文");
    } catch (e) { toast.err("世界观硬规则保存失败", errMsg(e)); } finally { setRulesSaving(false); }
  }

  // 常驻装置行的增/删/改(本地态,保存时统一清洗)
  const addDevice = () => setDevices((ds) => [...ds, { name: "", cadence: "", importance: "major" }]);
  const removeDevice = (i: number) => setDevices((ds) => ds.filter((_, j) => j !== i));
  const patchDevice = (i: number, patch: Partial<CanonDevice>) =>
    setDevices((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));

  // 保存故事宪法(结构化 canon):留白按行拆、装置剔无名、倒计时留空名=null,整体覆盖。
  // 与后端 coerce_canon 同口径清洗,存进去就是干净数据。
  async function saveCanon() {
    const absences = absencesText.split("\n").map((s) => s.trim()).filter(Boolean);
    const cleanDevices = devices
      .map((d) => ({ name: d.name.trim(), cadence: d.cadence.trim(), importance: d.importance || "major" }))
      .filter((d) => d.name);
    const dlNameTrim = dlName.trim();
    const deadline = dlNameTrim
      ? {
          name: dlNameTrim,
          total_days: Math.max(0, Math.floor(Number(dlDays) || 0)),
          anchor_chapter: Math.max(1, Math.floor(Number(dlAnchor) || 1)),
          importance: "critical",
        }
      : null;
    const canon: StoryCanon = { absences, devices: cleanDevices, deadline };
    setCanonSaving(true);
    try {
      await api.patchProject(pid, { canon });
      await invalidateProject();
      toast.ok("故事宪法已保存", "留白/常驻装置/倒计时将全程注入生成并参与一致性门禁");
    } catch (e) { toast.err("故事宪法保存失败", errMsg(e)); } finally { setCanonSaving(false); }
  }

  return (
    <>
      {/* 出片模式(docs/adr/0003):轻量=文+图出片、逐镜人工筛;完整=对白配音链/
          首尾帧自动接力/一键合成。完整档模块分期点亮,未点亮时行为与轻量档一致,
          切换不丢数据——两档共用同一份镜头、草片与任务记录。 */}
      <div className="card card-compact">
        <label className="fl">出片模式</label>
        <div className="hint mb-1">轻量档在漫剧分镜格 / 情绪短片工作台点「出片」即出视频草片(需在顶部「设置 → 出片引擎」配令牌)。</div>
        <div className="chips">
          <button type="button" className={"chip" + (project.render_mode !== "full" ? " on" : "")}
            onClick={() => void saveRenderMode("lite")}>
            轻量档 · 文+图出片
          </button>
          <button type="button" className={"chip" + (project.render_mode === "full" ? " on" : "")}
            onClick={() => void saveRenderMode("full")}>
            完整档 · 全自动闭环 <span className="badge">未启用</span>
          </button>
        </div>
        {project.render_mode === "full" && (
          <p className="hint mt-1">
            完整档的对白配音链、首尾帧自动接力、一键合成正在分期上线;当前版本切到完整档,
            行为与轻量档一致(已出的草片与进度两种模式下通用)。
          </p>
        )}
      </div>
      {/* 每章目标字数(死路 #3:此前只能在创建向导设,成书后无处可改) */}
      <div className="card card-compact">
        <label className="fl">每章目标字数</label>
        <div className="hint mb-1">生成与字数守卫的基准,保存后作用于后续章节。</div>
        <div className="input-row">
          <input type="number" min={TARGET_WORDS_MIN} max={TARGET_WORDS_MAX}
            value={targetWords} onChange={(e) => setTargetWords(e.target.value)} />
          <button className="primary btn-sm" disabled={wordsSaving}
            onClick={saveTargetWords}>
            {wordsSaving && <span className="spin spin-sm" />}保存
          </button>
        </div>
      </div>
      <div className="card card-compact mt-2">
        <label className="guard-toggle">
          <input type="checkbox" checked={!!project.word_guard_enabled}
            onChange={(e) => toggleGuard(e.target.checked)} />
          <span>
            字数守卫
            <b className="hint">超标自动压缩/拆章,默认关闭</b>
          </span>
        </label>
      </div>
      <div className="card card-compact mt-2">
        <label className="guard-toggle">
          <input type="checkbox" checked={project.review_auto_revise !== false}
            onChange={(e) => patchReview({ review_auto_revise: e.target.checked })} />
          <span>
            生成时审校把关
            <b className="hint">定稿后自动校对修硬伤 + 主审打分,不达标带意见回炉</b>
          </span>
        </label>
        <div className="mt-2 review-config">
          <label className="hint">
            达标线 四维均≥{" "}
            <select value={project.review_pass_threshold ?? 7}
              onChange={(e) => patchReview({ review_pass_threshold: Number(e.target.value) })}>
              {[6, 7, 8, 9].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label className="hint">
            回炉上限{" "}
            <select value={project.review_max_revisions ?? 3}
              onChange={(e) => patchReview({ review_max_revisions: Number(e.target.value) })}>
              {[0, 1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} 轮</option>)}
            </select>
          </label>
        </div>
        <label className="guard-toggle mt-2">
          <input type="checkbox" checked={!!project.queue_require_approved}
            onChange={(e) => patchReview({ queue_require_approved: e.target.checked })} />
          <span>
            连写要求上一章审核通过
            <b className="hint">开启后队列遇待审章会暂停,先人工通过该章再继续</b>
          </span>
        </label>
      </div>
      <div className="card card-compact mt-2">
        <label className="fl">世界观硬规则(每行一条)</label>
        <div className="hint mb-1">
          钉死本书不可违背的设定/常识(如:2024 新高考,理科不考政治;高考只考 6.7-6.8 两天)。
          保存后注入后续所有生成,并可在「全书 → 体检」发起规则扫描体检正文。
        </div>
        <textarea rows={4} value={worldRules}
          placeholder={"2024 新高考,理科不考政治\n高考只考 6.7-6.8 两天"}
          onChange={(e) => setWorldRules(e.target.value)} />
        <div className="actions mt-2">
          <button className="primary btn-sm" disabled={rulesSaving}
            onClick={saveWorldRules}>
            {rulesSaving && <span className="spin spin-sm" />}保存规则
          </button>
        </div>
      </div>
      <div className="card card-compact mt-2">
        <label className="fl">故事宪法(结构化,书级恒真)</label>
        <div className="hint mb-1">
          与上方「世界观硬规则」合并成本书宪法,全程注入生成并参与一致性门禁——治「早章立下的
          留白 / 金手指 / 倒计时,写到后面被违背」(如凭空冒出仆役、系统消失多章、倒计时天数算不清)。
        </div>

        <label className="fl mt-2">刻意留白(每行一条:这些「没有」是硬设定)</label>
        <textarea rows={3} value={absencesText}
          placeholder={"大院里只有主人、保镖、女主三人,没有仆役\n女主没有家人在世"}
          onChange={(e) => setAbsencesText(e.target.value)} />

        <label className="fl mt-2">常驻装置 / 金手指(立了就该长期在、反复现身)</label>
        {devices.length === 0 && (
          <div className="hint">(暂无。如「系统」「随身空间」「贴身信物」等,立后应按节奏复现)</div>
        )}
        {devices.map((d, i) => (
          <div className="input-row mt-1" key={i}>
            <input value={d.name} placeholder="装置名(如:系统)"
              onChange={(e) => patchDevice(i, { name: e.target.value })} />
            <input value={d.cadence} placeholder="复现节奏(如:每章都应有存在感)"
              onChange={(e) => patchDevice(i, { cadence: e.target.value })} />
            <select className="input-md" value={d.importance}
              onChange={(e) => patchDevice(i, { importance: e.target.value })}>
              {IMPORTANCE_OPTIONS.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
            <button className="btn-sm" title="删除该装置" onClick={() => removeDevice(i)}>✕</button>
          </div>
        ))}
        <div className="mt-1">
          <button className="btn-sm" onClick={addDevice}>+ 添加装置</button>
        </div>

        <label className="fl mt-2">倒计时(可选:留空「名称」= 不设)</label>
        <div className="input-row mt-1">
          <input value={dlName} placeholder="倒计时名(如:任务倒计时)"
            onChange={(e) => setDlName(e.target.value)} />
          <input className="input-md" type="number" min={0} value={dlDays} placeholder="总天数"
            onChange={(e) => setDlDays(e.target.value)} />
          <input className="input-md" type="number" min={1} value={dlAnchor} placeholder="起算章"
            onChange={(e) => setDlAnchor(e.target.value)} />
        </div>
        <div className="hint mt-1">总天数 = 倒计时的全程天数(如 31);起算章 = 从第几章开始计时。</div>

        <div className="actions mt-2">
          <button className="primary btn-sm" disabled={canonSaving} onClick={saveCanon}>
            {canonSaving && <span className="spin spin-sm" />}保存宪法
          </button>
        </div>
      </div>
    </>
  );
}
