// 人物卡看板:新增/编辑/退场/恢复人物,展开事实、删除抽错事实,展示关系与出场章。
// 简介变更保存后追问「扫描全书影响」:走设定级级联(扫描→勾选冲突段→定点修→diff 验收)。
// P1/P2:结构化人物画像(底色/说话/底线)展示与编辑;P3:立绘提示词生成与头像回贴。
import { useCallback, useEffect, useState } from "react";
import { api, CharacterCard, CharactersOut, Persona, SettingChange } from "../../api";
import { FACT_PREVIEW, IMP_BADGE } from "./shared";
import { errMsg } from "../../pollJob";
import { confirmDialog } from "../../ui/ConfirmDialog";
import { toast } from "../../ui/Toaster";
import { CopyBtn } from "../../ui/copy";
import SettingCascade from "../SettingCascade";

// 画像字段渲染/编辑的顺序即展示顺序;label 后的 hint 说明写什么(说人话)
const PERSONA_FIELDS: { key: keyof Persona; label: string; hint: string; rows?: number }[] = [
  { key: "logline", label: "一句话人设", hint: "身份 + 最鲜明的特质", rows: 2 },
  { key: "appearance", label: "形象气质", hint: "年龄感/穿着/气场", rows: 2 },
  { key: "traits", label: "性格底色", hint: "2-4 个词,逗号分隔(如:隐忍腹黑、热烈直球)" },
  { key: "speech", label: "说话方式", hint: "句式/口癖/语气", rows: 2 },
  { key: "motive", label: "动机", hint: "表层目标与深层动机", rows: 2 },
  { key: "fear", label: "恐惧软肋", hint: "最怕失去什么", rows: 2 },
  { key: "arc", label: "人物弧光", hint: "从哪里成长/沉沦到哪里", rows: 2 },
  { key: "never_do", label: "底线禁忌", hint: "绝不会做的事,逗号分隔;门禁会据此查崩人设" },
];
const PERSONA_SOURCE_CN: Record<string, string> = {
  architecture: "AI 提炼", author: "作者手订", extract: "抽取补充",
};

export default function CharactersBoard({ pid }: { pid: number }) {
  const [data, setData] = useState<CharactersOut | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [profile, setProfile] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // 待确认的操作:退场哪张卡 / 删哪条事实 / 编辑哪张卡
  const [retireFor, setRetireFor] = useState<number | null>(null);
  const [delFact, setDelFact] = useState<{ cid: number; fid: number } | null>(null);
  // 编辑态:编辑哪张卡 + 草稿(别名/简介)
  const [editing, setEditing] = useState<number | null>(null);
  const [editAliases, setEditAliases] = useState("");
  const [editProfile, setEditProfile] = useState("");
  // 画像编辑草稿:统一存 string(traits/never_do 用逗号分隔文本,提交时再拆)
  const [editPersona, setEditPersona] = useState<Partial<Record<keyof Persona, string>>>({});
  // 立绘提示词生成中(按卡禁用按钮)
  const [portraitBusy, setPortraitBusy] = useState<number | null>(null);
  // 级联流程卡(简介变更确认扫描后挂载)
  const [cascade, setCascade] = useState<{ key: number; changes: SettingChange[] } | null>(null);

  const reload = useCallback(async () => {
    setErr("");
    try { setData(await api.characters(pid)); } catch (e) { setErr(errMsg(e)); }
  }, [pid]);

  useEffect(() => { reload(); }, [reload]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); await reload(); } catch (e) { setErr(errMsg(e)); } finally { setBusy(false); }
  };

  const save = () => {
    const nm = name.trim();
    if (!nm) { setErr("人物名字不能为空"); return; }
    run(async () => {
      await api.createCharacter(pid, {
        name: nm,
        aliases: aliases.split(/[,、,]/).map((s) => s.trim()).filter(Boolean),
        profile: profile.trim(),
      });
      setShowForm(false); setName(""); setAliases(""); setProfile("");
    });
  };

  const toggleRetire = (c: CharacterCard, retired: boolean) =>
    run(() => api.setCharacterRetired(pid, c.id, retired)).then(() => setRetireFor(null));

  const removeFact = (fid: number) =>
    run(() => api.deleteFact(pid, fid)).then(() => setDelFact(null));

  // 进入编辑态:草稿从当前卡初始化(名字不可改——改名影响全书识别,不在看板做)
  const startEdit = (c: CharacterCard) => {
    setEditing(c.id);
    setEditAliases(c.aliases.join("、"));
    setEditProfile(c.profile);
    const p = c.persona ?? {};
    const draft: Partial<Record<keyof Persona, string>> = {};
    for (const f of PERSONA_FIELDS) {
      const v = p[f.key];
      draft[f.key] = Array.isArray(v) ? v.join("、") : (v ?? "");
    }
    draft.avatar = p.avatar ?? "";
    setEditPersona(draft);
  };

  // 保存编辑:简介有实质变更时后端返回句级 diff → 追问是否全书级联;
  // 画像随 PATCH 一并提交(空行丢弃在后端 coerce 里,这里只拆列表字段)
  const saveEdit = async (c: CharacterCard) => {
    const persona: Persona = {};
    for (const f of PERSONA_FIELDS) {
      const raw = (editPersona[f.key] ?? "").trim();
      if (!raw) continue;
      (persona as Record<string, unknown>)[f.key] = f.key === "traits" || f.key === "never_do"
        ? raw.split(/[,、,]/).map((s) => s.trim()).filter(Boolean)
        : raw;
    }
    const avatar = (editPersona.avatar ?? "").trim();
    if (avatar) persona.avatar = avatar;
    const payload: { aliases: string[]; profile: string; persona?: Persona } = {
      aliases: editAliases.split(/[,、,]/).map((s) => s.trim()).filter(Boolean),
      profile: editProfile,
      persona,
    };
    await run(async () => {
      const updated = await api.editCharacter(pid, c.id, payload);
      setEditing(null);
      if (updated.changes.length) {
        const names = [...new Set(updated.changes.map((ch) => ch.entity).filter(Boolean))];
        const ok = await confirmDialog({
          title: `扫描全书影响?`,
          body: `${names.join("、") || "该人物"} 的设定已修改。可以让系统逐章扫描已写正文,`
            + "找出与新设定冲突的段落并生成定点修提案(每处需你逐条验收)。",
          confirmText: "扫描全书影响",
        });
        if (ok) setCascade({ key: Date.now(), changes: updated.changes });
      }
    });
  };

  // P3 立绘提示词:后端从画像拼中英双语提示词并落库;生成后拿去即梦/MJ 出图,
  // 图的 URL 填回画像「头像」字段即可在看板直显
  const genPortrait = async (c: CharacterCard) => {
    setPortraitBusy(c.id);
    try {
      const r = await api.characterPortraitPrompt(pid, c.id);
      toast.ok(`${c.name} 的立绘提示词已生成`, "中英各一版,复制去绘图站即可");
      void reload();
      return r;
    } catch (e) {
      toast.err("立绘提示词生成失败", errMsg(e));
      return null;
    } finally {
      setPortraitBusy(null);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2>人物</h2>
        <span className="muted">
          {data?.characters.length ?? 0} 位人物
          {data && data.other_entities_count > 0 && ` · 另有 ${data.other_entities_count} 个非人物实体`}
        </span>
        <div className="grow" />
        <button className="btn-sm primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "收起" : "+ 新增人物"}
        </button>
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}

      {cascade && (
        <div className="mt-2">
          <SettingCascade pid={pid} presetChanges={cascade.changes}
            onClose={() => setCascade(null)} />
        </div>
      )}

      {showForm && (
        <div className="char-form">
          <div className="input-row">
            <input type="text" placeholder="名字(必填)" value={name} onChange={(e) => setName(e.target.value)} />
            <input type="text" placeholder="别名,逗号或顿号分隔" value={aliases} onChange={(e) => setAliases(e.target.value)} />
          </div>
          <textarea rows={2} placeholder="简介:身份/状态/关键设定,会作为初始事实进入故事圣经"
            value={profile} onChange={(e) => setProfile(e.target.value)} />
          <div className="actions mt-2">
            <button className="btn-sm primary" disabled={busy} onClick={save}>保存</button>
            <button className="btn-sm" disabled={busy}
              onClick={() => { setShowForm(false); setName(""); setAliases(""); setProfile(""); }}>
              取消
            </button>
          </div>
        </div>
      )}

      <div className="char-grid mt-3">
        {(data?.characters ?? []).map((c) => {
          const facts = expanded.has(c.id) ? c.key_facts : c.key_facts.slice(0, FACT_PREVIEW);
          return (
            <div key={c.id} className={"char-card" + (c.retired ? " retired" : "")}>
              <div className="card-head">
                {c.persona?.avatar && (
                  <img className="char-avatar" src={c.persona.avatar} alt={`${c.name} 头像`}
                    onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }} />
                )}
                <h3>{c.name}</h3>
                <span className={"badge " + (c.retired ? "" : "ok")}>{c.retired ? "已退场" : "活跃"}</span>
                <div className="grow" />
                {editing !== c.id && (
                  <button className="btn-sm" disabled={busy} onClick={() => startEdit(c)}>编辑</button>
                )}
                {c.retired
                  ? <button className="btn-sm" disabled={busy} onClick={() => toggleRetire(c, false)}>恢复</button>
                  : <button className="btn-sm danger" disabled={busy} onClick={() => setRetireFor(c.id)}>退场</button>}
              </div>
              {c.aliases.length > 0 && <div className="muted char-aliases">别名:{c.aliases.join("、")}</div>}

              {editing === c.id ? (
                <div className="char-form mt-2">
                  <input type="text" placeholder="别名,逗号或顿号分隔"
                    value={editAliases} onChange={(e) => setEditAliases(e.target.value)} />
                  <textarea rows={3} placeholder="简介:身份/性格/关键设定。修改后可扫描全书,定点修冲突段落"
                    value={editProfile} onChange={(e) => setEditProfile(e.target.value)} />
                  <details className="persona-edit mt-1" open={!!c.persona?.logline}>
                    <summary className="muted">人物画像(底色/说话/底线——生成与门禁都会遵守)</summary>
                    {PERSONA_FIELDS.map((f) => (
                      <div key={f.key} className="mt-2">
                        <div className="hint">{f.label} · {f.hint}</div>
                        {f.rows
                          ? <textarea rows={f.rows} value={editPersona[f.key] ?? ""}
                              onChange={(e) => setEditPersona((p) => ({ ...p, [f.key]: e.target.value }))} />
                          : <input type="text" value={editPersona[f.key] ?? ""}
                              onChange={(e) => setEditPersona((p) => ({ ...p, [f.key]: e.target.value }))} />}
                      </div>
                    ))}
                    <div className="mt-2">
                      <div className="hint">头像 URL · 在绘图站出图后把链接贴回来,看板直显</div>
                      <input type="text" value={editPersona.avatar ?? ""}
                        onChange={(e) => setEditPersona((p) => ({ ...p, avatar: e.target.value }))} />
                    </div>
                  </details>
                  <div className="actions mt-2">
                    <button className="btn-sm primary" disabled={busy}
                      onClick={() => void saveEdit(c)}>保存</button>
                    <button className="btn-sm" disabled={busy} onClick={() => setEditing(null)}>取消</button>
                  </div>
                </div>
              ) : (
                <>
                  {c.profile && <div className="hint">{c.profile}</div>}
                  {/* 画像只读展示:底色/底线是最常看的两行,其余字段收进折叠 */}
                  {c.persona?.logline && (
                    <details className="persona-view mt-1" open>
                      <summary className="muted">
                        画像{c.persona.source && ` · ${PERSONA_SOURCE_CN[c.persona.source] ?? c.persona.source}`}
                      </summary>
                      {c.persona.traits?.length ? (
                        <div className="mt-1">{c.persona.traits.map((t) => (
                          <span key={t} className="badge">{t}</span>
                        ))}</div>
                      ) : null}
                      <div className="fact-line">{c.persona.logline}</div>
                      {c.persona.speech && <div className="fact-line muted">说话:{c.persona.speech}</div>}
                      {c.persona.never_do?.length ? (
                        <div className="fact-line">绝不做:{c.persona.never_do.join(";")}</div>
                      ) : null}
                      <details className="mt-1">
                        <summary className="muted">更多画像字段</summary>
                        {(["appearance", "motive", "fear", "arc"] as const).map((k) => (
                          c.persona?.[k] ? <div key={k} className="fact-line muted">
                            {{ appearance: "形象", motive: "动机", fear: "软肋", arc: "弧光" }[k]}:{c.persona[k] as string}
                          </div> : null
                        ))}
                      </details>
                      {c.persona.portrait_prompt && (
                        <details className="mt-1">
                          <summary className="muted">立绘提示词(中/英)</summary>
                          <div className="hint" style={{ whiteSpace: "pre-wrap" }}>{c.persona.portrait_prompt}</div>
                          <CopyBtn text={c.persona.portrait_prompt} label="复制提示词" />
                        </details>
                      )}
                    </details>
                  )}
                  {!c.persona?.logline && (
                    <button className="btn-sm mt-1" disabled={busy} onClick={() => startEdit(c)}>
                      + 补画像(底色/说话/底线)
                    </button>
                  )}
                  {/* P3 立绘:有画像才可生成;生成的提示词落库后在上面折叠区可复制 */}
                  {c.persona?.logline && (
                    <button className="btn-sm mt-1" disabled={busy || portraitBusy === c.id}
                      title="按画像生成中英双语立绘提示词(拿去即梦/MJ 出图,不出站)"
                      onClick={() => void genPortrait(c)}>
                      {portraitBusy === c.id && <span className="spin spin-sm" />}🎨 立绘提示词
                    </button>
                  )}
                </>
              )}

              {retireFor === c.id && (
                <div className="notice notice-warn">
                  退场后历史正文与事实全部保留,后续章节生成不再注入该人物,可随时恢复。
                  <div className="actions mt-2">
                    <button className="btn-sm danger" disabled={busy} onClick={() => toggleRetire(c, true)}>确认退场</button>
                    <button className="btn-sm" disabled={busy} onClick={() => setRetireFor(null)}>取消</button>
                  </div>
                </div>
              )}

              {facts.map((f) => (
                <div key={f.id} className="fact-line fact-row">
                  <span className={"badge " + (IMP_BADGE[f.importance] ?? "")}>{f.importance}</span>
                  <span className="fact-title">
                    {f.content} <span className="muted">(自第{f.valid_from}章起)</span>
                  </span>
                  {delFact?.fid === f.id ? (
                    <span className="fact-confirm">
                      删这条?
                      <button className="btn-sm danger" disabled={busy} onClick={() => removeFact(f.id)}>删</button>
                      <button className="btn-sm" disabled={busy} onClick={() => setDelFact(null)}>留</button>
                    </span>
                  ) : (
                    <button className="fact-del" title="删除这条事实(修正抽错的内容)"
                      disabled={busy} onClick={() => setDelFact({ cid: c.id, fid: f.id })}>
                      ×
                    </button>
                  )}
                </div>
              ))}
              {c.key_facts.length > FACT_PREVIEW && (
                <button className="linkbtn" onClick={() => setExpanded((s) => {
                  const n = new Set(s);
                  if (n.has(c.id)) n.delete(c.id); else n.add(c.id);
                  return n;
                })}>
                  {expanded.has(c.id) ? "收起" : `展开全部 ${c.key_facts.length} 条`}
                </button>
              )}
              {!c.key_facts.length && <div className="muted">暂无有效事实。</div>}

              {c.relations.length > 0 && (
                <div className="char-relations">
                  <div className="muted char-rel-head">关系</div>
                  {c.relations.map((r, i) => (
                    <div key={i} className={"fact-line" + (r.other_retired ? " retired" : "")}>
                      → {r.other_name}:{r.description}
                      <span className="muted">
                        (自第{r.valid_from}章起{r.other_retired ? ",对方已退场" : ""})
                      </span>
                      {(r.evidence ?? []).map((ev, j) => (
                        <div key={j} className="char-rel-ev">
                          └ 第{ev.chapter}章:{ev.content}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {c.appearance_chapters.length > 0 && (
                <div className="muted char-chapters">
                  出场:{c.appearance_chapters.map((n) => `第${n}章`).join("、")}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {data && !data.characters.length && !showForm && (
        <div className="muted">暂无人物。点右上角「+ 新增人物」登记,或生成章节后自动抽取。</div>
      )}
    </div>
  );
}
