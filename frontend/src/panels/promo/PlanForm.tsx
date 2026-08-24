// ① 企划与素材点(侧栏常驻):研讨与解说词的输入。
// 素材点是全工坊的事实红线——解说词里的史实/数据/slogan 只许从这里来,
// 所以它空着的时候要显式警告,而不是让用户以为「不填也行」(不填 AI 只能写空话)。
import { useEffect, useState } from "react";
import { PromoAngle, PromoDirection, PromoPlan, promoApi } from "../../promoApi";
import { toast } from "../../ui/Toaster";
import { errMsg } from "../../pollJob";

export default function PlanForm({ pid, plan, meta, onSaved }: {
  pid: number;
  plan: PromoPlan;
  meta: { angles: PromoAngle[]; directions: PromoDirection[] } | null;
  onSaved: (p: PromoPlan) => void;
}) {
  const [draft, setDraft] = useState(plan);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  // 外部重拉企划(跑完一步 reload)后同步草稿;有未保存改动时不覆盖用户正在编辑的内容
  useEffect(() => { if (!dirty) setDraft(plan); }, [plan, dirty]);

  function edit(patch: Partial<PromoPlan>) {
    setDraft((d) => ({ ...d, ...patch }));
    setDirty(true);
  }

  async function save() {
    setSaving(true);
    try {
      const r = await promoApi.patch(pid, {
        subject: draft.subject, title: draft.title, angles: draft.angles,
        duration_s: draft.duration_s, direction: draft.direction,
        material_notes: draft.material_notes,
      });
      setDirty(false);
      onSaved(r.plan);
      toast.ok("企划已保存");
    } catch (e) {
      toast.err("保存失败", errMsg(e));
    } finally { setSaving(false); }
  }

  const noMaterial = !draft.material_notes.trim();

  return (
    <section className="card">
      <div className="card-head">
        <h3 className="grow">① 企划与素材点 <span className="muted">研讨与解说词的输入</span></h3>
      </div>
      <div className="form-grid">
        <div className="field">
          <label className="fl" htmlFor="pf-subject">主题</label>
          <input id="pf-subject" value={draft.subject} maxLength={60}
            onChange={(e) => edit({ subject: e.target.value })} />
        </div>
        <div className="field">
          <label className="fl" htmlFor="pf-title">企划名</label>
          <input id="pf-title" value={draft.title} maxLength={60} placeholder="如「西安·烟火食事」"
            onChange={(e) => edit({ title: e.target.value })} />
        </div>
        <div className="field">
          <label className="fl" htmlFor="pf-duration">成片时长</label>
          <select id="pf-duration" value={draft.duration_s}
            onChange={(e) => edit({ duration_s: Number(e.target.value) })}>
            <option value={60}>60 秒</option><option value={90}>90 秒</option>
            <option value={120}>2 分钟</option><option value={180}>3 分钟</option>
          </select>
        </div>
        <div className="field">
          <label className="fl" htmlFor="pf-direction">画风</label>
          <select id="pf-direction" value={draft.direction}
            onChange={(e) => edit({ direction: e.target.value })}>
            {(meta?.directions ?? []).map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
          </select>
        </div>
        <div className="field field-full">
          <span className="fl">角度<span className="hint">可多选</span></span>
          <div className="chips">
            {(meta?.angles ?? []).map((a) => (
              <button key={a.key} type="button"
                className={"chip" + (draft.angles.includes(a.key) ? " on" : "")}
                aria-pressed={draft.angles.includes(a.key)}
                onClick={() => edit({
                  angles: draft.angles.includes(a.key)
                    ? draft.angles.filter((x) => x !== a.key)
                    : [...draft.angles, a.key],
                })}>{a.label}</button>
            ))}
          </div>
        </div>
        <div className="field field-full">
          <label className="fl" htmlFor="pf-material">素材点</label>
          <p className="field-note">
            史实 / 数据 / slogan——解说词的唯一事实来源,拿不准的宁可不写。
          </p>
          <textarea id="pf-material" rows={5} value={draft.material_notes}
            placeholder="如:回民街头汤凌晨四点开熬;城墙明代扩建;slogan 候选「长安烟火,不散」"
            onChange={(e) => edit({ material_notes: e.target.value })} />
        </div>
      </div>
      {noMaterial && (
        <p className="hint warn-tip">
          ⚠ 素材点还空着:解说词只认这里的事实,空着 AI 只能写「历史悠久、人杰地灵」这类空话。
        </p>
      )}
      <div className="form-actions">
        <button className="primary" disabled={!dirty || saving} onClick={save}>
          {saving ? "保存中…" : "保存修改"}
        </button>
        <span className="form-actions-tip">
          {dirty ? "有未保存的修改。" : "已是保存状态——改动后这里会亮起。"}
        </span>
      </div>
    </section>
  );
}
