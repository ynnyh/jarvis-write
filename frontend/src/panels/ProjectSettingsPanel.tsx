// settings 区:书级设置。字数守卫 / 审校把关(达标线·回炉上限·连写要求)/ 世界观硬规则。
// 前两张卡从原 ChaptersPanel 侧栏搬来,世界观硬规则从原 EditorialPanel audit 页签搬来,功能文案不变。
import { useState } from "react";
import { api, Project } from "../api";
import { useInvalidateProject } from "../hooks/queries";
import { errMsg } from "../pollJob";
import { toast } from "../ui/Toaster";

interface Props { pid: number; project: Project; }

export default function ProjectSettingsPanel({ pid, project }: Props) {
  const invalidateProject = useInvalidateProject(pid);
  // 世界观硬规则编辑态(整段覆盖,空串清空);初始值取自项目(进入本区时 project 已就绪)
  const [worldRules, setWorldRules] = useState(project.world_rules ?? "");
  const [rulesSaving, setRulesSaving] = useState(false);

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

  return (
    <>
      <div className="card card-compact">
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
    </>
  );
}
