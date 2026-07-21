// 一致性看板:人物卡管理 + 故事圣经(时序快照) + 伏笔四态面板
import { useCallback, useEffect, useState } from "react";
import {
  api, BibleSnapshot, CharacterCard, CharactersOut, FactOut, ForeshadowOut, Outline,
} from "../api";

interface Props { pid: number; outlines: Outline[]; }

const FS_CN: Record<string, string> = {
  planted: "已埋设", reinforced: "已强化", paid_off: "已回收", abandoned: "已弃用",
};
const IMP_BADGE: Record<string, string> = { critical: "err", major: "warn", minor: "" };
const FACT_PREVIEW = 3;

type Tab = "characters" | "bible" | "foreshadow";

export default function BoardPanel({ pid, outlines }: Props) {
  const [tab, setTab] = useState<Tab>("characters");
  return (
    <>
      <div className="chips board-tabs">
        {([["characters", "人物"], ["bible", "故事圣经"], ["foreshadow", "伏笔"]] as [Tab, string][]).map(
          ([key, label]) => (
            <span key={key} className={"chip" + (tab === key ? " on" : "")} onClick={() => setTab(key)}>
              {label}
            </span>
          ),
        )}
      </div>
      {tab === "characters" && <CharactersBoard pid={pid} />}
      {tab === "bible" && <BibleBoard pid={pid} outlines={outlines} />}
      {tab === "foreshadow" && <ForeshadowBoard pid={pid} outlines={outlines} />}
    </>
  );
}

/* ================= 人物卡 ================= */

function CharactersBoard({ pid }: { pid: number }) {
  const [data, setData] = useState<CharactersOut | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [aliases, setAliases] = useState("");
  const [profile, setProfile] = useState("");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // 待确认的操作:退场哪张卡 / 删哪条事实
  const [retireFor, setRetireFor] = useState<number | null>(null);
  const [delFact, setDelFact] = useState<{ cid: number; fid: number } | null>(null);

  const reload = useCallback(async () => {
    setErr("");
    try { setData(await api.characters(pid)); } catch (e) { setErr(String(e)); }
  }, [pid]);

  useEffect(() => { reload(); }, [reload]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); await reload(); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
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
                {c.retired
                  ? <button className="btn-sm" disabled={busy} onClick={() => toggleRetire(c, false)}>恢复</button>
                  : <button className="btn-sm danger" disabled={busy} onClick={() => setRetireFor(c.id)}>退场</button>}
              </div>
              {c.aliases.length > 0 && <div className="muted char-aliases">别名:{c.aliases.join("、")}</div>}

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

/* ================= 故事圣经 ================= */

function BibleBoard({ pid, outlines }: Props) {
  const maxCh = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 1;
  const [atChapter, setAtChapter] = useState(maxCh);
  // 输入框用字符串保存原始输入(允许清空重输),仅在解析合法时才切换章节时刻
  const [atInput, setAtInput] = useState(String(maxCh));
  const [bible, setBible] = useState<BibleSnapshot | null>(null);
  const [err, setErr] = useState("");

  const reload = useCallback(async (ch: number) => {
    setErr("");
    try { setBible(await api.bible(pid, ch)); } catch (e) { setErr(String(e)); }
  }, [pid]);

  useEffect(() => { reload(atChapter); }, [reload, atChapter]);

  const byEntity = new Map<string, FactOut[]>();
  bible?.facts.forEach((f) => {
    const list = byEntity.get(f.entity) ?? [];
    list.push(f);
    byEntity.set(f.entity, list);
  });

  return (
    <div className="card">
      <div className="card-head">
        <h2>故事圣经 · 时间机</h2>
        <span className="muted">查看任意章节时刻的世界状态</span>
        <div className="grow" />
        <span className="muted">第</span>
        <input type="number" min={1} max={maxCh} value={atInput} className="input-xs"
          onChange={(e) => {
            const v = e.target.value;
            setAtInput(v);
            const n = Number(v);
            if (v.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= maxCh) setAtChapter(n);
          }} />
        <span className="muted">章时刻 · {bible?.entities_count ?? 0} 实体 / {bible?.facts.length ?? 0} 条有效事实</span>
      </div>
      {err && <div className="msg-err mt-2">{err}</div>}
      <div className="mt-3">
        {[...byEntity.entries()].map(([entity, facts]) => (
          <div key={entity} className="entity">
            <b>{entity}</b>
            {facts.map((f, i) => (
              <div key={i} className="fact-line">
                <span className={"badge " + (IMP_BADGE[f.importance] ?? "")}>{f.importance}</span>
                {" "}{f.content}
                <span className="muted">(第{f.valid_from}{f.valid_until ? `-${f.valid_until}` : " 章起"}章有效)</span>
              </div>
            ))}
          </div>
        ))}
        {!bible?.facts.length && <div className="muted">该时刻暂无已登记事实(生成章节后自动抽取)。</div>}
      </div>
    </div>
  );
}

/* ================= 伏笔 ================= */

function ForeshadowBoard({ pid, outlines }: Props) {
  const maxCh = outlines.length ? Math.max(...outlines.map((o) => o.chapter_number)) : 1;
  const [foreshadows, setForeshadows] = useState<ForeshadowOut[]>([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    (async () => {
      setErr("");
      try { setForeshadows(await api.foreshadowings(pid, maxCh)); } catch (e) { setErr(String(e)); }
    })();
  }, [pid, maxCh]);

  const open = foreshadows.filter((f) => f.status === "planted" || f.status === "reinforced");
  const due = open.filter((f) => f.is_due);
  const paid = foreshadows.filter((f) => f.status === "paid_off");

  return (
    <div className="card">
      <h2>伏笔面板
        <span className="badge">{open.length} 未回收</span>
        {due.length > 0 && <span className="badge warn">{due.length} 条到期</span>}
        <span className="badge ok">{paid.length} 已回收</span>
      </h2>
      {err && <div className="msg-err mt-2">{err}</div>}
      <table className="tbl">
        <thead>
          <tr><th>状态</th><th>伏笔</th><th>埋设</th><th>预期回收</th><th>实际回收</th><th>强化于</th></tr>
        </thead>
        <tbody>
          {foreshadows.map((f) => (
            <tr key={f.id}>
              <td>
                <span className={"badge " + (f.status === "paid_off" ? "ok" : f.is_due ? "warn" : "")}>
                  {FS_CN[f.status] ?? f.status}{f.is_due ? " · 到期" : ""}
                </span>
              </td>
              <td>{f.description}</td>
              <td>第{f.chapter_planted}章</td>
              <td>{f.expected_payoff_chapter ? `第${f.expected_payoff_chapter}章` : "—"}</td>
              <td>{f.payoff_chapter ? `第${f.payoff_chapter}章` : "—"}</td>
              <td>{f.reinforcement_chapters.length ? f.reinforcement_chapters.map((c) => `第${c}章`).join("、") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {!foreshadows.length && <div className="muted">暂无登记伏笔。</div>}
    </div>
  );
}
