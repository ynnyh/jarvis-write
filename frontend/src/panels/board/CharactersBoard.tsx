// 人物卡看板:新增/编辑/退场/恢复人物,展开事实、删除抽错事实,展示关系与出场章。
// 简介变更保存后追问「扫描全书影响」:走设定级级联(扫描→勾选冲突段→定点修→diff 验收)。
import { useCallback, useEffect, useState } from "react";
import { api, CharacterCard, CharactersOut, SettingChange } from "../../api";
import { FACT_PREVIEW, IMP_BADGE } from "./shared";
import { errMsg } from "../../pollJob";
import { confirmDialog } from "../../ui/ConfirmDialog";
import SettingCascade from "../SettingCascade";

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
  };

  // 保存编辑:简介有实质变更时后端返回句级 diff → 追问是否全书级联
  const saveEdit = async (c: CharacterCard) => {
    const payload = {
      aliases: editAliases.split(/[,、,]/).map((s) => s.trim()).filter(Boolean),
      profile: editProfile,
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
                  <div className="actions mt-2">
                    <button className="btn-sm primary" disabled={busy}
                      onClick={() => void saveEdit(c)}>保存</button>
                    <button className="btn-sm" disabled={busy} onClick={() => setEditing(null)}>取消</button>
                  </div>
                </div>
              ) : (
                c.profile && <div className="hint">{c.profile}</div>
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
