// ConceptForge — 概念打磨屏(确认链 L1)。
// 「选卡」和「定概念」之间的一间打磨房:六字段全貌逐项可改,可带话让 AI 重捏
// (refineConceptAsync,返回 changed 字段做 diff 高亮),满意才拍板。
// 拍板即硬约束:概念拍板后才进配置屏;内容再改(手改或重捏)自动回未拍板(后端逻辑)。
// 直接拍板不设强制停留——确认是权利不是门槛。
import { useState } from "react";
import {
  api, Concept, conceptIsEmpty, CONCEPT_FIELDS, RefineResult, StoryDNA, Tendency,
} from "../../api";
import { errMsg } from "../../pollJob";
import { DirectiveBar } from "../../ui/confirmKit";
import { useJob } from "../../ui/useJob";
import { ThinkingText } from "../../ui/ThinkingText";

// 概念字段普遍 2-4 行,固定 rows 的小 textarea 在移动端会把首行裁掉半截
// (实测 scrollHeight 102 vs clientHeight 76):auto-grow 按内容撑高,
// 挂载/重渲染(重捏回填、异步加载)/输入时都会重算。
function autoGrow(el: HTMLTextAreaElement | null) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = `${el.scrollHeight}px`;
}

export default function ConceptForge({ pid, concept, confirmed, tendency, dna, onChanged, onConfirmed, onUnconfirm }: {
  pid: number;
  concept: Concept;
  confirmed: boolean;
  tendency: Tendency;
  dna: StoryDNA | null;
  /** 内容变化(手改/重捏保存后)向上同步 dossier 侧栏 */
  onChanged: (c: Concept) => void;
  /** 拍板完成 → 父级飞入下一步 */
  onConfirmed: (c: Concept) => void;
  /** 撤回拍板 */
  onUnconfirm: () => void;
}) {
  const { run } = useJob();
  const [draft, setDraft] = useState<Concept>(concept);
  const [changedKeys, setChangedKeys] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  // 草稿与已保存概念有差异:拍板/重捏前先落库,保证 project.concept 不落后于所见
  const dirtyEdit = CONCEPT_FIELDS.some((f) => (draft[f.key] ?? "") !== (concept[f.key] ?? ""));

  function upd(key: keyof Concept, value: string) {
    setDraft((c) => ({ ...c, [key]: value }));
  }

  async function saveDraft(): Promise<Concept> {
    const r = await api.patchProject(pid, { concept: draft });
    onChanged(draft);
    return r.concept ?? draft;
  }

  // 带话重捏:AI 按修改要求重出概念,changed 字段高亮;重捏基于「所见即所存」的草稿
  async function reforge(directive: string) {
    setErr("");
    setBusy(true);
    try {
      const base = dirtyEdit ? await saveDraft() : draft;
      const r = await run<RefineResult>(
        () => api.refineConceptAsync(base, directive, tendency, dna),
        { kind: "inspire" },
      );
      if (!r) return; // 本地等待被中止(切走),任务在后台继续
      setDraft(r.concept);
      setChangedKeys((r.changed ?? []).map(String));
      const saved = await api.patchProject(pid, { concept: r.concept });
      onChanged(r.concept);
      // 后端在概念内容变化时已复位 concept_confirmed;同步父级拍板态
      if (confirmed && saved.concept_confirmed === false) onUnconfirm();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirmNow() {
    setErr("");
    try {
      const final = dirtyEdit ? await saveDraft() : draft;
      await api.patchProject(pid, { concept: final, concept_confirmed: true });
      onChanged(final);
      onConfirmed(final);
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  async function unconfirmNow() {
    try {
      await api.patchProject(pid, { concept_confirmed: false });
      onUnconfirm();
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  return (
    <div className="card mt-3 concept-forge" data-testid="concept-forge">
      <div className="card-head">
        <h3>打磨这个概念</h3>
        {confirmed
          ? <span className="ck-state">✓ 已拍板</span>
          : <span className="hint">改字段或带话重捏,满意就拍板;拍板后它就是全书的硬约束。</span>}
      </div>

      {busy && (
        <div className="muted mt-2">
          <span className="spin" />
          <ThinkingText phrases={["正在按你的要求重捏概念…", "在保留骨架的前提下调整…", "重新推敲字段的血肉…"]} />
        </div>
      )}

      <div className="forge-fields">
        {CONCEPT_FIELDS.map((f) => {
          const changed = changedKeys.includes(f.key);
          return (
            <div key={f.key} className={"forge-field" + (changed ? " changed" : "")}>
              <label className="fl">
                {f.label}
                {changed && <span className="forge-changed-flag">已按你的要求更新</span>}
                <span className="hint"> · {f.hint}</span>
              </label>
              <textarea ref={autoGrow} rows={1} disabled={busy}
                value={draft[f.key] ?? ""} placeholder={f.hint}
                onInput={(e) => autoGrow(e.currentTarget)}
                onChange={(e) => { setChangedKeys((ks) => ks.filter((k) => k !== f.key)); upd(f.key, e.target.value); }} />
            </div>
          );
        })}
      </div>

      <div className="mt-2">
        <DirectiveBar onSend={(t) => { void reforge(t); }} busy={busy}
          placeholder="带句话重捏,如「主角换成女性」「金手指兑现别太快」"
          sendText="带话重捏" sendTitle="AI 保留骨架,按这句话重出概念字段" />
      </div>

      {err && <div className="msg-err mt-2">{err}</div>}

      <div className="actions mt-3">
        {confirmed ? (
          <>
            <button className="primary" disabled={busy || conceptIsEmpty(draft)} onClick={() => { void confirmNow(); }}>
              保存并保持拍板 →
            </button>
            <button className="btn-sm" disabled={busy} onClick={() => { void unconfirmNow(); }}>撤回拍板</button>
          </>
        ) : (
          <button className="primary" disabled={busy || conceptIsEmpty(draft)}
            title="拍板后概念成为全书硬约束,进配置屏"
            onClick={() => { void confirmNow(); }}>
            拍板概念,去配置 →
          </button>
        )}
      </div>
    </div>
  );
}
